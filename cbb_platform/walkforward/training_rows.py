"""2026-27 TRAINING ROWS in exactly the frozen loaders' shapes, from production state only (no API call, no historical
rebuild). The historical rows (2013-14 .. 2025-26) are read unchanged from the frozen lockbox store by the frozen loaders.

FEATURES (pregame, per game, computed ONCE and cached immutably):
    prod.features.build at the game's validated cutoff (TIP_MINUS_1H) over the state version as-of that cutoff, with
    the target games' outcomes stripped -- the production scoring path itself, so a training row holds exactly what was
    legally knowable when the game would have been priced. tests/test_prod_replay.py proves this path reproduces the
    lockbox Stage 3 store rows exactly; a correction that arrives later can never be backdated into a feature row.
LABELS (per refit):
    one build over the state version as-of the daily refit cutoff with blank_targets=False + the frozen Stage 3 label
    SQL (stage3/labels.py). A corrected box therefore enters the first daily version whose cutoff sees it, and only
    as a label / later-season history; old versions are never touched.
FINALS (per refit): the frozen player-season final SELECT (marginal.load_frames `fin`) over that build's
    feat.player_box_state: 2026-27 season-to-date totals at the refit cutoff.
OOS (per game): the Phase 1B true-OOS store row of season F = forecasts by the annual state trained through F-1. For
    2026-27 that is the OPENING marginal (through 2025-26), applied with inputs.marginal_rows (= marginal.stage_oos).

Every frame is checked against the frozen loaders on a replayed 2025-26 game (tests/test_walkforward_training_rows.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cbb_platform import props_v0 as P, props_v0_participation as Q
from cbb_platform.prod import features as PF, inputs as IN, slate as SL
from cbb_platform.stage3 import build as S3B, labels as LB, sources
from cbb_platform.v2 import latent as L, phase3 as P3
from cbb_platform.walkforward import config as WC, eligibility as EL

LABEL_COLS = ("participation_status", "box_row_present", "minutes_label_available", "counting_labels_available", "min")


class TrainingRowsError(RuntimeError):
    pass


def _utc(x) -> pd.Timestamp:
    return EL._utc(x)


def frame_columns(cfg: dict) -> list[str]:
    """marginal.load_frames(population="scored") column order."""
    targets = list(cfg["count_targets"].values())
    hist = [f"h{j}_{c}" for j in range(1, cfg["recency"]["prior_seasons_used"] + 1) for c in ["min", "n_app"] + targets]
    return (["player_game_example_id", "canonical_player_id", "canonical_team_id", "season", "prediction_cutoff_utc",
             "is_home", "pos", "cold_start_class", "cur_min", "cur_n_app"] + [f"cur_{t}" for t in targets]
            + list(cfg["minutes_features"]) + hist + ["y_min"] + [f"y_{t}" for t in targets] + ["counts_ok"])


PART_COLS = (["player_game_example_id", "canonical_game_id", "canonical_team_id", "canonical_player_id", "season",
              "prediction_cutoff_utc", "participation_status"] + list(Q.NUMERIC)
             + ["last_played", "last_dnp", "last_unknown", "ev_prior_season_roster", "ev_transfer_preseason",
                "ev_recruit_preseason", "cold_no_history", "cold_transfer_in", "y"])
TEAM_COLS = ["season", "game_id", "team_id", "opp_id", "is_home", "neutral", "prediction_cutoff_utc",
             "res_state_available_at", "box_state_available_at", "n_boxed", "pace40", "off_eff", "def_eff",
             "prev_off_eff", "prev_def_eff", "pts", "poss", "game_minutes"]


def _sha_df(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df.reset_index(drop=True), index=False).to_numpy().tobytes()
                          + "|".join(map(str, df.columns)).encode()).hexdigest()


def game_cutoffs(increment_db: Path, game_ids) -> pd.DataFrame:
    con = duckdb.connect(str(increment_db), read_only=True)
    con.register("ids", pd.DataFrame({"canonical_game_id": sorted(set(game_ids))}))
    g = con.execute("""SELECT canonical_game_id, scheduled_tip_utc, tip_time_quality, game_date_et FROM flagship.games
                       WHERE canonical_game_id IN (SELECT canonical_game_id FROM ids)""").df()
    con.close()
    cut = [SL.feature_cutoff(r.scheduled_tip_utc, r.tip_time_quality, r.game_date_et) for r in g.itertuples(index=False)]
    g["prediction_cutoff_utc"] = [c[0] for c in cut]
    g["cutoff_rule"] = [c[1] for c in cut]
    return g.sort_values(["prediction_cutoff_utc", "canonical_game_id"]).reset_index(drop=True)


# ============================================================================ pregame features (cached per game)
def _pit_audit(fdb: Path, game_ids) -> dict:
    """Stage 3 availability columns of the built rows: every state used must predate the row's own cutoff, and no state
    may come from the target game itself."""
    con = duckdb.connect(str(fdb), read_only=True)
    con.register("ids", pd.DataFrame({"canonical_game_id": sorted(set(game_ids))}))
    q = lambda s: int(con.execute(s).fetchone()[0])
    w = "WHERE canonical_game_id IN (SELECT canonical_game_id FROM ids)"
    out = {"player_rows": q(f"SELECT count(*) FROM feat.player_features {w}"),
           "player_state_after_cutoff": q(f"""SELECT count(*) FROM feat.player_features {w} AND
                 (pbox_state_available_at > prediction_cutoff_utc OR part_state_available_at > prediction_cutoff_utc)"""),
           "player_state_from_target_game": q(f"""SELECT count(*) FROM feat.player_features {w} AND
                 (pbox_state_source_game_id = canonical_game_id OR part_state_source_game_id = canonical_game_id)"""),
           "team_rows": q(f"SELECT count(*) FROM feat.team_features {w}"),
           "team_state_after_cutoff": q(f"""SELECT count(*) FROM feat.team_features {w} AND
                 (res_state_available_at > prediction_cutoff_utc OR box_state_available_at > prediction_cutoff_utc)""")}
    con.close()
    out["violations"] = out["player_state_after_cutoff"] + out["player_state_from_target_game"] + out["team_state_after_cutoff"]
    return out


def feature_rows(cache_root: Path, state_root: Path, games: pd.DataFrame, history_db: Path, cfg: dict,
                 season: int = WC.PRODUCTION_SEASON, log=print) -> dict:
    """games: canonical_game_id + prediction_cutoff_utc. Returns {"cand", "team", "meta"} for every game (cached rows
    are reused byte-for-byte; a game whose cutoff has no state version is reported, never built from later data)."""
    root = Path(cache_root) / "features"
    root.mkdir(parents=True, exist_ok=True)
    todo, metas = [], {}
    for r in games.itertuples(index=False):
        mp = root / r.canonical_game_id / "meta.json"
        if mp.exists():
            metas[r.canonical_game_id] = json.loads(mp.read_text())
        else:
            todo.append(r)
    groups: dict = {}
    for r in todo:
        st = EL.state_asof(state_root, r.prediction_cutoff_utc)
        if st is None:
            metas[r.canonical_game_id] = {"game_id": r.canonical_game_id, "status": "NO_STATE_AT_CUTOFF",
                                          "prediction_cutoff_utc": _utc(r.prediction_cutoff_utc).isoformat()}
            continue
        groups.setdefault((_utc(r.prediction_cutoff_utc), st["state_version"]), (st, []))[1].append(r.canonical_game_id)
    for (cut, sv), (st, gids) in sorted(groups.items()):
        work = root / f".work-{uuid.uuid4().hex}"
        try:
            fr = PF.build(work, gids, season, cut, cfg, history_db, st["increment_db"])
            prov = fr["provenance"]["canonical"]
            if prov["target_outcome_rows"] or prov["post_as_of_rows"]:
                raise TrainingRowsError(f"FUTURE_LEAKAGE in pregame build at {cut}: {prov}")
            pit = _pit_audit(fr["features_db"], gids)
            cand, team = fr["cand"], fr["team"]
            for gid in gids:
                c = cand[cand["canonical_game_id"] == gid].sort_values("player_game_example_id", kind="mergesort")
                t = team[team["game_id"] == gid].sort_values("team_id", kind="mergesort")
                gdir = root / gid
                tmp = root / f".{gid}.{uuid.uuid4().hex}"
                tmp.mkdir()
                c.to_parquet(tmp / "cand.parquet", index=False)
                t.to_parquet(tmp / "team.parquet", index=False)
                meta = {"game_id": gid, "status": "BUILT" if len(c) and len(t) == 2 else "NO_PREGAME_EXAMPLES",
                        "prediction_cutoff_utc": cut.isoformat(), "state_version": sv,
                        "state_created_at": st["created_at"].isoformat(), "candidate_rows": int(len(c)),
                        "team_rows": int(len(t)), "cand_sha256": _sha_df(c), "team_sha256": _sha_df(t),
                        "build": {"target_outcome_rows": prov["target_outcome_rows"], "post_as_of_rows": prov["post_as_of_rows"],
                                  "as_of": prov["as_of"], "group_games": len(gids)}, "pit_audit": pit}
                (tmp / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
                if gdir.exists():                                   # concurrent builder won: keep the first (immutable)
                    shutil.rmtree(tmp)
                else:
                    os.replace(tmp, gdir)
                metas[gid] = json.loads((gdir / "meta.json").read_text())
            log(f"pregame features {cut.isoformat()} ({len(gids)} games, state {sv})")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    built = [g for g, m in metas.items() if m.get("status") == "BUILT"]
    cand = pd.concat([pd.read_parquet(root / g / "cand.parquet") for g in sorted(built)], ignore_index=True) if built else pd.DataFrame()
    team = pd.concat([pd.read_parquet(root / g / "team.parquet") for g in sorted(built)], ignore_index=True) if built else pd.DataFrame()
    return {"cand": cand, "team": team, "meta": metas}


# ============================================================================ labels + finals (per refit)
def _fin_sql(cfg: dict, season: int) -> str:
    """marginal.load_frames `fin` (player-season final box state), verbatim columns, one season."""
    targets = list(cfg["count_targets"].values())
    hs = cfg["history_sums"]
    fin_cols = ", ".join(f"({' + '.join(f's_{c}' for c in hs[t])})::DOUBLE AS {t}" for t in targets)
    return f"""SELECT * EXCLUDE (available_at) FROM (
        SELECT canonical_player_id, season, available_at, s_min::DOUBLE AS min, n_app::DOUBLE AS n_app,
               {P._pos_sql('last_position')} AS pos, {fin_cols}
        FROM feat.player_box_state WHERE season = {int(season)}
        QUALIFY row_number() OVER (PARTITION BY canonical_player_id, season ORDER BY available_at DESC, source_game_id DESC) = 1)
        ORDER BY canonical_player_id, season"""


# The label relations label_rows reads. stage3.labels.build also writes three Stage 3.1 DIAGNOSTIC relations (target box
# rows outside the pregame universe / unresolved, and their pool-miss classification); the walk-forward never reads them
# and deletes the scratch label DB right after this read, so they are not built here. lab.player_pool_misses (correlated
# subqueries over all player box rows) was the one label operator whose hash table outgrew the 700MB history cap as the
# season's target games accumulated (2026-11-12 morning OOM: 954 MB measured for 414 games; every consumed relation
# < 460 MB). The frozen lockbox label store, which the coverage / eligibility audits read, is not affected.
LABEL_RELATIONS = ("lab.player_game_labels", "lab.team_game_labels")
LABEL_DIAGNOSTIC_RELATIONS = ("lab.target_box_rows_outside_universe", "lab.player_pool_misses",
                              "lab.target_box_rows_unresolved")
_CREATE = re.compile(r"^(?:--[^\n]*\n\s*)*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP\s+)?(TABLE|SCHEMA)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                     r"([\w.]+)", re.IGNORECASE)


def label_statements() -> list[str]:
    """stage3.labels.build's statements, rendered by the frozen function itself (stage3/labels.py is not edited), minus
    the diagnostic relations. Every kept statement is verbatim; refuses if the frozen build's relation set changed or a
    kept statement reads a dropped relation."""
    calls: list[str] = []

    class _Record:
        def execute(self, sql, *a, **k):
            calls.append(sql)
    LB.build(_Record())
    keep, dropped = [], []
    for s in (x.strip() for c in calls for x in c.split(";\n") if x.strip()):
        m = _CREATE.match(s)
        if m is None:
            raise TrainingRowsError(f"unexpected statement in stage3.labels.build: {s[:80]!r}")
        (dropped if m.group(2) in LABEL_DIAGNOSTIC_RELATIONS else keep).append((m.group(2), s))
    if sorted(r for r, _ in dropped) != sorted(LABEL_DIAGNOSTIC_RELATIONS) or \
            not set(LABEL_RELATIONS) <= {r for r, _ in keep}:
        raise TrainingRowsError("stage3.labels.build relation set changed: label_statements must be re-verified")
    for r, s in keep:
        if any(d.split(".")[1] in s for d in LABEL_DIAGNOSTIC_RELATIONS):
            raise TrainingRowsError(f"{r} reads a diagnostic relation")
    return [s for _, s in keep]


def build_labels(db_path: Path, features_db: Path, canonical: Path) -> None:
    """stage3.build.build_labels with label_statements() (same connection, binding, attach and file swap; the unused
    fingerprints are not computed)."""
    tmp = Path(str(db_path) + ".building")
    if tmp.exists():
        tmp.unlink()
    con = S3B.connect(tmp)
    sources.bind(con, canonical)
    con.execute(f"ATTACH '{features_db}' AS fx (READ_ONLY)")
    for s in label_statements():
        con.execute(s)
    con.execute("DETACH fx")
    sources.unbind(con)
    con.close()
    os.replace(tmp, db_path)


def label_rows(work: Path, state: dict | None, cutoff, game_ids, history_db: Path, cfg: dict,
               season: int = WC.PRODUCTION_SEASON) -> dict:
    """Labels of the completed target games + season-to-date finals, from the state as-of the refit cutoff."""
    work = Path(work)
    shutil.rmtree(work, ignore_errors=True)
    fr = PF.build(work, game_ids, season, cutoff, cfg, history_db, state["increment_db"] if state else None,
                  blank_targets=False)
    ldb = work / "labels.duckdb"
    with PF.stage3_window(season):
        build_labels(ldb, fr["features_db"], canonical=work / "canonical_asof.duckdb")
    con = duckdb.connect(str(ldb), read_only=True)
    targets = list(cfg["count_targets"].values())
    lab = con.execute(f"""SELECT canonical_game_id, canonical_team_id, canonical_player_id, {", ".join(LABEL_COLS)},
                                 {", ".join(targets)} FROM lab.player_game_labels ORDER BY 1, 2, 3""").df()
    tlab = con.execute("""SELECT canonical_game_id AS game_id, canonical_team_id AS team_id, team_points::DOUBLE AS pts,
                                 possessions_source::DOUBLE AS poss, game_minutes::DOUBLE AS game_minutes
                          FROM lab.team_game_labels ORDER BY 1, 2""").df()
    con.close()
    con = duckdb.connect(str(fr["features_db"]), read_only=True)
    finals = con.execute(_fin_sql(cfg, season)).df()
    con.close()
    return {"labels": lab, "team_labels": tlab, "finals": finals, "one_pass_cand": fr["cand"],
            "provenance": fr["provenance"]["canonical"]}


# ============================================================================ assembly (frozen loader shapes)
def assemble(cand: pd.DataFrame, team: pd.DataFrame, lab: dict, cfg: dict, oos_state: dict, oos_through: int,
             season: int = WC.PRODUCTION_SEASON) -> dict:
    """(frame, finals, participation, team frame, OOS store rows) for the fit, + label coverage accounting."""
    targets = list(cfg["count_targets"].values())
    key = ["canonical_game_id", "canonical_team_id", "canonical_player_id"]
    lb = lab["labels"]
    c = cand.merge(lb, on=key, how="left", validate="one_to_one", indicator=True)
    cover = {"candidate_rows": int(len(c)), "label_rows_matched": int((c["_merge"] == "both").sum()),
             "candidates_without_label": int((c["_merge"] == "left_only").sum())}
    c = c.drop(columns="_merge")
    # ---- frame (marginal.load_frames population "scored")
    sc = c[c["participation_status"].eq("PLAYED") & c["box_row_present"].fillna(False).astype(bool)
           & c["minutes_label_available"].fillna(False).astype(bool)].copy()
    sc["y_min"] = sc["min"].astype(float)
    for t in targets:
        sc[f"y_{t}"] = sc[t].astype(float)
    sc["counts_ok"] = sc["counting_labels_available"].astype(bool)
    for t in targets:
        sc.loc[~sc["counts_ok"], f"y_{t}"] = np.nan
    frame = sc[frame_columns(cfg)].sort_values("player_game_example_id", kind="mergesort").reset_index(drop=True)
    # ---- participation (marginal.load_participation)
    part = c.copy()
    for col in Q.FLAGS:
        part[col] = part[col].fillna(0.0)
    part["y"] = part["participation_status"].map(Q.LABEL)
    part = part[PART_COLS].sort_values("player_game_example_id", kind="mergesort").reset_index(drop=True)
    # ---- team frame (structural.load_team_frame labels=True)
    tf = team.merge(lab["team_labels"], on=["game_id", "team_id"], how="inner", validate="one_to_one")
    tf = tf[TEAM_COLS].sort_values(["season", "game_id", "team_id"], kind="mergesort").reset_index(drop=True)
    # ---- Phase 1B OOS store rows (marginal.stage_oos with the annual state through season - 1)
    mr = IN.marginal_rows(cand, cfg, oos_state["v0_state"], oos_state["participation_state"])
    mr = mr.merge(c[["player_game_example_id", "participation_status", "min", "counting_labels_available"] + targets],
                  on="player_game_example_id", how="left", validate="one_to_one")
    mr["forecast_season"], mr["training_through_season"], mr["p_play_status"] = season, oos_through, "OK"
    mr["y_min"] = mr["min"].astype(float)
    ok = mr["counting_labels_available"].fillna(False).astype(bool)
    for s, t in cfg["count_targets"].items():
        mr[f"y_{s}"] = np.where(ok, mr[t].astype(float), np.nan)
    oos = mr[P3.STORE_COLS].sort_values(["game_id", "team_id", "player_id"], kind="mergesort").reset_index(drop=True)
    if not (oos["training_through_season"] < oos["forecast_season"]).all():
        raise TrainingRowsError("an OOS base forecast was not out of sample")
    fin = lab["finals"].reset_index(drop=True)
    return {"frame": frame, "finals": fin, "part": part, "team": tf, "oos": oos, "coverage": cover,
            "hashes": {k: _sha_df(v) for k, v in (("frame", frame), ("finals", fin), ("part", part), ("team", tf), ("oos", oos))}}


def label_hashes(lab: pd.DataFrame) -> dict:
    """Per-game label content hash (correction detection between daily versions)."""
    return {g: _sha_df(d.drop(columns=["canonical_game_id"]).reset_index(drop=True)) for g, d in lab.groupby("canonical_game_id")}


def label_diff(old: pd.DataFrame, new: pd.DataFrame, cols) -> list[dict]:
    """ORIGINAL_VALUE / CORRECTED_VALUE per changed (player, column) of one game."""
    key = ["canonical_team_id", "canonical_player_id"]
    m = old.merge(new, on=key, how="outer", suffixes=("_old", "_new"), indicator=True)
    out = []
    for r in m.to_dict("records"):
        for c in cols:
            a, b = r.get(f"{c}_old"), r.get(f"{c}_new")
            if not ((pd.isna(a) and pd.isna(b)) or a == b):
                out.append({"team_id": r["canonical_team_id"], "player_id": r["canonical_player_id"], "field": c,
                            "original_value": None if pd.isna(a) else a, "corrected_value": None if pd.isna(b) else b})
    return out
