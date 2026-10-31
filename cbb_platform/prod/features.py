"""Point-in-time pregame features for target games, from the FROZEN Stage 3 SQL (no feature definition is changed).

1. compact_canonical(): one small DuckDB with exactly the canonical relations Stage 3 reads, assembled from
       the frozen historical canonical DB (read-only; seasons <= 2025-26)   +   the current-season increment
   and masked at prediction_as_of: a result row enters only when its historical_knowable_at (canonical PIT rule HR-1:
   tip + 4 h, or 06:00 ET next day for inexact tips) <= prediction_as_of; target games enter as schedule rows with every
   outcome field NULL and no box rows. Nothing learned after prediction_as_of can reach a feature.
2. build_features(): stage3.sources.bind (verbatim) over that DB with the season window extended to the target season,
   then the verbatim Stage 3 universe SQL (restricted to the target games), team_state.build and player_state.build.
   Event / lineup / shot families are not built: no frozen JM1_FINAL input reads them.
3. model_frame(): the frozen v1 loader SELECTs (cbb_platform/v2/marginal.load_frames / load_participation) WITHOUT the
   target-game label join (production has no labels; the label columns were attached after forecasting and never used
   to forecast). Equivalence to the frozen loaders is tested on historical replays (tests/test_prod_replay.py).

Production adaptation P-1 (the only one): Stage 3 derives team-season existence (`ts`, which admits preseason
membership evidence) from the season's box rows. Historically that set came from the completed season; prospectively a
team has no box row before its first game, so `ts` additionally includes the target games' team-seasons. For any
team that has already played this season the result is identical.
"""
from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from cbb_platform import props_v0 as P, props_v0_participation as Q
from cbb_platform.prod import config as PC
from cbb_platform.stage3 import build as S3B, player_state, sources, spec, team_state, universe

KNOWABLE = ("CASE WHEN {t}.tip_time_quality = 'EXACT' THEN {t}.scheduled_tip_utc + INTERVAL 4 HOUR "
            "ELSE timezone('America/New_York', CAST({t}.game_date_et + INTERVAL 1 DAY AS TIMESTAMP) + INTERVAL 6 HOUR) END")
RESULT_COLS = ("home_points", "away_points", "home_period_points", "away_period_points", "overtime_count")
EMPTY_RELATIONS = ("flagship.plays", "flagship.shots_trusted", "flagship.on_floor_trusted")
UNION_RELATIONS = ("flagship.rosters", "core.transfers", "core.recruits", "core.team_seasons", "core.teams",
                   "core.players", "metadata.identity_link_quarantine")
TS_ORIGINAL = "ts AS (SELECT DISTINCT canonical_team_id, season FROM src.team_games)"
TS_PRODUCTION = ("ts AS (SELECT DISTINCT canonical_team_id, season FROM src.team_games\n"
                 "        UNION SELECT DISTINCT canonical_team_id, season FROM ex.team_game_examples)")


class FeatureBuildError(RuntimeError):
    pass


def _ts(x) -> str:
    t = pd.Timestamp(x)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.strftime("%Y-%m-%d %H:%M:%S.%f+00")


@contextlib.contextmanager
def stage3_window(last_season: int):
    """Stage 3 reads its season window from spec.MODEL_SEASONS at bind time; extend it to the target season for this
    build only (history still starts at 2013-14), restoring the frozen value afterwards."""
    old = spec.MODEL_SEASONS
    spec.MODEL_SEASONS = tuple(range(PC.HISTORY_FIRST_SEASON, last_season + 1))
    try:
        yield
    finally:
        spec.MODEL_SEASONS = old


def _has(con, db: str, rel: str) -> bool:
    return bool(con.execute(f"SELECT count(*) FROM duckdb_tables() WHERE database_name = '{db}' "
                            f"AND schema_name || '.' || table_name = '{rel}'").fetchone()[0] or
                con.execute(f"SELECT count(*) FROM duckdb_views() WHERE database_name = '{db}' "
                            f"AND schema_name || '.' || view_name = '{rel}'").fetchone()[0])


def compact_canonical(out_path: Path, target_game_ids, as_of, target_season: int,
                      history_db: Path = PC.HISTORY_CANONICAL, increment_db: Path | None = None,
                      blank_targets: bool = True) -> dict:
    """Write the as_of-masked canonical input DB. Returns counts + provenance.

    blank_targets=False (walk-forward LABEL builds only, cbb_platform/walkforward/training_rows.py): the target games are
    COMPLETED games whose own outcomes are needed as training labels; they keep their result / box / substitution rows,
    which must be knowable at as_of (a target that is not is refused). The as_of mask is unchanged. Pregame features
    are never taken from such a build."""
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()
    targets = sorted(set(target_game_ids))
    if not targets:
        raise FeatureBuildError("no target games")
    aso = _ts(as_of)
    con = duckdb.connect(str(out_path), config={"threads": 2})
    con.execute("SET TimeZone = 'UTC'")
    con.execute(f"ATTACH '{history_db}' AS hist (READ_ONLY)")
    srcs = ["hist"]
    if increment_db is not None:
        con.execute(f"ATTACH '{increment_db}' AS inc (READ_ONLY)")
        srcs.append("inc")
    con.register("targets_df", pd.DataFrame({"canonical_game_id": targets}))
    con.execute("CREATE TEMP TABLE tgt AS SELECT * FROM targets_df")
    for s in ("flagship", "core", "metadata"):
        con.execute(f"CREATE SCHEMA {s}")
    hi = PC.TRAINING_THROUGH
    # schedule rows of every source: history <= 2025-26 (frozen), increment = the production season only
    # history: the status-resolved played set (flagship.played_games) -- exactly what Stage 3 reads
    sched = " UNION ALL BY NAME ".join(
        f"SELECT * FROM {d}.flagship.played_games WHERE season BETWEEN {PC.HISTORY_FIRST_SEASON} AND {hi}" if d == "hist" else
        f"SELECT * FROM {d}.flagship.games WHERE season = {PC.PRODUCTION_SEASON}" for d in srcs)
    con.execute(f"CREATE TEMP TABLE sched AS SELECT *, {KNOWABLE.format(t='g')} AS _knowable FROM ({sched}) g")
    dup = con.execute("SELECT count(*) - count(DISTINCT canonical_game_id) FROM sched").fetchone()[0]
    if dup:
        raise FeatureBuildError(f"{dup} duplicate canonical_game_id across history / increment")
    missing = con.execute("SELECT count(*) FROM tgt WHERE canonical_game_id NOT IN (SELECT canonical_game_id FROM sched)").fetchone()[0]
    if missing:
        raise FeatureBuildError(f"{missing} target games are not in the schedule")
    have = {r[0] for r in con.execute("DESCRIBE sched").fetchall()}
    blank = [c for c in RESULT_COLS + ("has_team_box_score", "has_player_box_score") if c in have]
    nulls = ", ".join(f"NULL::{'BOOLEAN' if c.startswith('has_') else 'INTEGER' if c.endswith('_points') or c == 'overtime_count' else 'INTEGER[]'} AS {c}"
                      for c in blank)
    not_tgt = "AND canonical_game_id NOT IN (SELECT canonical_game_id FROM tgt)" if blank_targets else ""
    con.execute(f"""
    CREATE TABLE flagship.games AS
    SELECT * EXCLUDE (_knowable) FROM sched
    WHERE home_points IS NOT NULL AND _knowable <= TIMESTAMPTZ '{aso}' {not_tgt}
    {f'''UNION ALL BY NAME
    SELECT * EXCLUDE (_knowable, {", ".join(blank)}), {nulls}
    FROM sched WHERE canonical_game_id IN (SELECT canonical_game_id FROM tgt)''' if blank_targets else ""};
    CREATE TABLE flagship.played_games AS SELECT * FROM flagship.games;
    """)
    if not blank_targets:
        unknown = con.execute("SELECT count(*) FROM tgt WHERE canonical_game_id NOT IN (SELECT canonical_game_id FROM flagship.games)").fetchone()[0]
        if unknown:
            raise FeatureBuildError(f"{unknown} label targets have no result knowable at {aso}")
    not_tgt_r = "AND r.canonical_game_id NOT IN (SELECT canonical_game_id FROM tgt)" if blank_targets else ""
    for rel in ("team_game_outcomes", "player_game_outcomes"):
        body = " UNION ALL BY NAME ".join(f"SELECT * FROM {d}.flagship.{rel}" + (
            f" WHERE season BETWEEN {PC.HISTORY_FIRST_SEASON} AND {hi}" if d == "hist" else f" WHERE season = {PC.PRODUCTION_SEASON}")
            for d in srcs if d == "hist" or _has(con, d, f"flagship.{rel}"))
        con.execute(f"""CREATE TABLE flagship.{rel} AS SELECT r.* FROM ({body}) r
            WHERE r.historical_knowable_at <= TIMESTAMPTZ '{aso}'
              AND r.canonical_game_id IN (SELECT canonical_game_id FROM flagship.games)
              {not_tgt_r}""")
    for rel in UNION_RELATIONS:
        parts = [f"SELECT * FROM {d}.{rel}" for d in srcs if _has(con, d, rel)]
        con.execute(f"CREATE TABLE {rel} AS {' UNION ALL BY NAME '.join(parts)}")
    # substitutions: participation history (PLAYED via stint, DNP_CONFIRMED via a complete feed) of the TARGET season
    # only -- participation state is partitioned by (player, team, season), so no other season reaches a target row
    body = " UNION ALL BY NAME ".join(f"SELECT * FROM {d}.flagship.substitutions WHERE season = {int(target_season)}"
                                      for d in srcs if d == "hist" or _has(con, d, "flagship.substitutions"))
    con.execute(f"""CREATE TABLE flagship.substitutions AS SELECT s.* FROM ({body}) s
        WHERE s.historical_knowable_at <= TIMESTAMPTZ '{aso}'
          AND s.canonical_game_id IN (SELECT canonical_game_id FROM flagship.games)
          {not_tgt_r.replace("r.", "s.")}""")
    for rel in EMPTY_RELATIONS:                       # outcome-event streams: no frozen JM1_FINAL input reads them
        con.execute(f"CREATE TABLE {rel} AS SELECT * FROM hist.{rel} LIMIT 0")
    counts = {r: con.execute(f"SELECT count(*) FROM {r}").fetchone()[0]
              for r in ("flagship.games", "flagship.team_game_outcomes", "flagship.player_game_outcomes", "flagship.substitutions")}
    leak = 0 if not blank_targets else con.execute("""SELECT
        (SELECT count(*) FROM flagship.player_game_outcomes WHERE canonical_game_id IN (SELECT canonical_game_id FROM tgt))
      + (SELECT count(*) FROM flagship.team_game_outcomes WHERE canonical_game_id IN (SELECT canonical_game_id FROM tgt))
      + (SELECT count(*) FROM flagship.substitutions WHERE canonical_game_id IN (SELECT canonical_game_id FROM tgt))
      + (SELECT count(*) FROM flagship.games WHERE canonical_game_id IN (SELECT canonical_game_id FROM tgt)
              AND (home_points IS NOT NULL OR away_points IS NOT NULL))""").fetchone()[0]
    late = con.execute(f"""SELECT
        (SELECT count(*) FROM flagship.player_game_outcomes WHERE historical_knowable_at > TIMESTAMPTZ '{aso}')
      + (SELECT count(*) FROM flagship.team_game_outcomes WHERE historical_knowable_at > TIMESTAMPTZ '{aso}')
      + (SELECT count(*) FROM flagship.substitutions WHERE historical_knowable_at > TIMESTAMPTZ '{aso}')""").fetchone()[0]
    con.close()
    if leak or late:
        raise FeatureBuildError(f"FUTURE_LEAKAGE: target-game outcome rows {leak}, post-as_of rows {late}")
    return {"targets": len(targets), "as_of": aso, "counts": counts, "target_outcome_rows": leak, "post_as_of_rows": late,
            "sources": srcs, "blank_targets": blank_targets}


def _universe(con) -> None:
    if universe.MEMBERSHIP_SQL.count(TS_ORIGINAL) != 1:
        raise FeatureBuildError("frozen Stage 3 membership SQL changed: production adaptation P-1 no longer applies")
    con.execute("CREATE SCHEMA IF NOT EXISTS ex")
    con.execute("CREATE OR REPLACE TABLE ex.identity_conflicts AS SELECT * FROM src.identity_conflicts ORDER BY ALL")
    con.execute(universe.GAME_EXAMPLES_SQL)
    con.execute("DELETE FROM ex.game_examples WHERE canonical_game_id NOT IN (SELECT canonical_game_id FROM tgt)")
    con.execute(universe.TEAM_GAME_EXAMPLES_SQL)
    con.execute(universe.MEMBERSHIP_SQL.replace(TS_ORIGINAL, TS_PRODUCTION))
    con.execute(universe.PLAYER_GAME_EXAMPLES_SQL)


def build_features(canonical_db: Path, features_db: Path, target_game_ids, target_season: int) -> dict:
    """Run the frozen Stage 3 SQL for the target games; writes ex.* and feat.* into features_db."""
    features_db = Path(features_db)
    tmp = Path(str(features_db) + ".building")
    for p in (tmp, features_db):
        if p.exists():
            p.unlink()
    con = S3B.connect(tmp)
    con.register("targets_df", pd.DataFrame({"canonical_game_id": sorted(set(target_game_ids))}))
    con.execute("CREATE TEMP TABLE tgt AS SELECT * FROM targets_df")
    with stage3_window(target_season):
        sources.bind(con, canonical_db)
        _universe(con)
        team_state.build(con)
        player_state.build(con)
        sources.unbind(con)
    out = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
           for t in ("ex.game_examples", "ex.player_game_examples", "feat.player_features", "feat.team_features")}
    con.close()
    os.replace(tmp, features_db)
    return out


# ============================================================================ frozen-loader SELECTs, label-free
def _frame_sql(cfg: dict, lo: int, hi: int) -> tuple[str, str]:
    """marginal.load_frames(population="candidates") SELECT text with the lb.lab.player_game_labels join removed."""
    targets = list(cfg["count_targets"].values())
    hs = cfg["history_sums"]
    cur = ", ".join(f"({' + '.join(f'f.pstd_sum_{c}::DOUBLE' for c in hs[t])}) AS cur_{t}" for t in targets)
    fin_cols = ", ".join(f"({' + '.join(f's_{c}' for c in hs[t])})::DOUBLE AS {t}" for t in targets)
    hist = ", ".join(f"h{j}.{c} AS h{j}_{c}" for j in range(1, cfg["recency"]["prior_seasons_used"] + 1)
                     for c in ["min", "n_app"] + targets)
    hjoin = " ".join(f"""LEFT JOIN fin h{j} ON h{j}.canonical_player_id = f.canonical_player_id AND h{j}.season = f.season - {j}
                         AND h{j}.available_at <= f.prediction_cutoff_utc"""
                     for j in range(1, cfg["recency"]["prior_seasons_used"] + 1))
    feats = ", ".join(f"f.{c}::DOUBLE AS {c}" for c in cfg["minutes_features"])
    fin_sql = f"""fin AS (SELECT canonical_player_id, season, available_at, s_min::DOUBLE AS min, n_app::DOUBLE AS n_app,
                          {P._pos_sql('last_position')} AS pos, {fin_cols}
                   FROM feat.player_box_state WHERE season BETWEEN {lo} AND {hi}
                   QUALIFY row_number() OVER (PARTITION BY canonical_player_id, season ORDER BY available_at DESC, source_game_id DESC) = 1)"""
    frame = f"""WITH {fin_sql}
        SELECT f.player_game_example_id, f.canonical_player_id, f.canonical_team_id, f.season, f.prediction_cutoff_utc,
               f.is_home::DOUBLE AS is_home, {P._pos_sql('f.prof_last_box_position')} AS pos, f.cold_start_class,
               f.pstd_sum_min::DOUBLE AS cur_min, f.pstd_appearances::DOUBLE AS cur_n_app, {cur}, {feats}, {hist},
               f.canonical_game_id
        FROM feat.player_features f {hjoin}
        WHERE f.season BETWEEN {lo} AND {hi} AND f.canonical_game_id IN (SELECT canonical_game_id FROM tgt)
        ORDER BY f.player_game_example_id"""
    part = f"""
        SELECT f.player_game_example_id, f.canonical_game_id, f.canonical_team_id, f.canonical_player_id, f.season,
               f.prediction_cutoff_utc, {", ".join(f"f.{c}::DOUBLE AS {c}" for c in Q.NUMERIC)},
               (f.pp_last_game_participation = 'PLAYED')::DOUBLE AS last_played,
               (f.pp_last_game_participation = 'DNP_CONFIRMED')::DOUBLE AS last_dnp,
               (f.pp_last_game_participation = 'UNKNOWN_PARTICIPATION')::DOUBLE AS last_unknown,
               coalesce(x.ev_prior_season_roster, FALSE)::DOUBLE AS ev_prior_season_roster,
               coalesce(x.ev_transfer_preseason, FALSE)::DOUBLE AS ev_transfer_preseason,
               coalesce(x.ev_recruit_preseason, FALSE)::DOUBLE AS ev_recruit_preseason,
               (f.cold_start_class = 'COLD_NO_HISTORY')::DOUBLE AS cold_no_history,
               (f.cold_start_class = 'COLD_TRANSFER_IN')::DOUBLE AS cold_transfer_in
        FROM feat.player_features f JOIN ex.player_game_examples x USING (player_game_example_id)
        WHERE f.season = {hi} AND f.canonical_game_id IN (SELECT canonical_game_id FROM tgt)
        ORDER BY f.player_game_example_id"""
    return frame, part


TEAM_SQL = """SELECT f.season, f.canonical_game_id AS game_id, f.canonical_team_id AS team_id,
        f.opponent_team_id AS opp_id, f.is_home::DOUBLE AS is_home, f.neutral_site::DOUBLE AS neutral,
        f.prediction_cutoff_utc, f.res_state_available_at, f.box_state_available_at,
        f.te_games_boxed::DOUBLE AS n_boxed, f.te_pace_per40::DOUBLE AS pace40, f.te_off_eff::DOUBLE AS off_eff,
        f.te_def_eff::DOUBLE AS def_eff, f.tp_prev_off_eff::DOUBLE AS prev_off_eff, f.tp_prev_def_eff::DOUBLE AS prev_def_eff
    FROM feat.team_features f WHERE f.canonical_game_id IN (SELECT canonical_game_id FROM tgt)"""


def model_frame(features_db: Path, cfg: dict, target_game_ids, season: int) -> dict:
    """(candidate frame with participation inputs, team frame) for the target games: the frozen loader SELECTs minus
    the label join (structural.load_team_frame labels=False SELECT for the team frame)."""
    con = duckdb.connect(str(features_db), read_only=True, config={"threads": 2})
    con.register("targets_df", pd.DataFrame({"canonical_game_id": sorted(set(target_game_ids))}))
    con.execute("CREATE TEMP TABLE tgt AS SELECT * FROM targets_df")
    fsql, psql = _frame_sql(cfg, season - cfg["recency"]["prior_seasons_used"], season)
    cand = con.execute(fsql).df()
    part = con.execute(psql).df()
    tf = con.execute(TEAM_SQL).df()
    pool = con.execute("""SELECT player_game_example_id, pool_evidence FROM ex.player_game_examples
                          WHERE canonical_game_id IN (SELECT canonical_game_id FROM tgt)""").df()
    con.close()
    for c in Q.FLAGS:
        part[c] = part[c].fillna(0.0)
    cand = cand.merge(part[["player_game_example_id"] + [c for c in Q.NUMERIC + Q.FLAGS if c not in cand.columns]],
                      on="player_game_example_id", how="left", validate="one_to_one")
    cand = cand.merge(pool, on="player_game_example_id", how="left", validate="one_to_one")
    return {"cand": cand, "team": tf}


def build(run_dir: Path, target_game_ids, target_season: int, as_of, cfg: dict,
          history_db: Path = PC.HISTORY_CANONICAL, increment_db: Path | None = None, blank_targets: bool = True) -> dict:
    """compact canonical -> Stage 3 features -> model frames, inside run_dir (scratch, gitignored)."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    t0 = datetime.now(timezone.utc)
    cdb, fdb = run_dir / "canonical_asof.duckdb", run_dir / "features_asof.duckdb"
    canon = compact_canonical(cdb, target_game_ids, as_of, target_season, history_db, increment_db, blank_targets)
    feats = build_features(cdb, fdb, target_game_ids, target_season)
    frames = model_frame(fdb, cfg, target_game_ids, target_season)
    return {**frames, "provenance": {"canonical": canon, "features": feats, "seconds": (datetime.now(timezone.utc) - t0).total_seconds(),
                                     "stage3_version": spec.STAGE3_VERSION, "cutoff_rule": spec.CUTOFF_RULE,
                                     "production_adaptations": ["P-1 team-season existence includes target games"]},
            "features_db": fdb}


def dumps(x) -> str:
    return json.dumps(x, default=str, sort_keys=True)
