"""Incremental current-season state: frozen through-2025-26 canonical history (read-only, never rebuilt) + a small
2026-27 INCREMENT in the canonical flagship format.

    STATE_ROOT/versions/<state_version>/increment.duckdb   immutable once promoted
    STATE_ROOT/versions/<state_version>/state_manifest.json
    STATE_ROOT/CURRENT.json                                 last-known-good pointer, replaced atomically (os.replace)

update() copies the current increment, upserts the new rows, validates the WHOLE increment and only then swaps the
pointer. Any failure leaves CURRENT.json untouched (STATE_UPDATE_FAILED); nothing is refit, nothing historical is
rebuilt. Increment contract: REQUIRED columns below (the exact columns the frozen Stage 3 SQL reads), season 2026-27
only (portal year / recruit class 2026 feed season 2026-27), canonical ids, and point-in-time consistency.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from cbb_platform.prod import config as PC

GAME_COLS = ("canonical_game_id", "season", "season_type", "game_type", "tournament", "scheduled_tip_utc",
             "tip_time_quality", "game_date_et", "neutral_site", "conference_game", "home_team_id", "away_team_id",
             "home_conference_id", "away_conference_id", "canonical_venue_id", "home_points", "away_points",
             "game_universe", "has_team_box_score", "has_player_box_score", "source_system", "source_record_key")
TEAM_BOX_COLS = ("canonical_game_id", "canonical_team_id", "canonical_opponent_id", "season", "is_home", "game_minutes",
                 "points", "fgm", "fga", "two_pa", "three_pm", "three_pa", "ftm", "fta", "orb", "drb", "ast", "stl",
                 "blk", "tov_total", "pf", "historical_knowable_at")
PLAYER_BOX_COLS = ("canonical_game_id", "canonical_team_id", "canonical_player_id", "identity_status", "season",
                   "starter", "minutes", "points", "fga", "fta", "reb", "ast", "stl", "blk", "tov", "pf", "two_pm",
                   "two_pa", "three_pm", "three_pa", "ftm", "orb", "drb", "source_position", "accounting_issues",
                   "historical_knowable_at")
SUB_COLS = ("canonical_game_id", "canonical_team_id", "canonical_player_id", "season", "sub_in_period",
            "sub_in_seconds_remaining", "sub_out_period", "sub_out_seconds_remaining", "timing_complete",
            "historical_knowable_at")
REQUIRED = {
    "flagship.games": GAME_COLS,
    "flagship.team_game_outcomes": TEAM_BOX_COLS,
    "flagship.player_game_outcomes": PLAYER_BOX_COLS,
    "flagship.substitutions": SUB_COLS,
    "flagship.rosters": ("canonical_player_id", "canonical_team_id", "season", "start_season", "position",
                         "height_inches", "weight_lbs"),
    "core.transfers": ("source_transfer_id", "transfer_year", "canonical_player_id", "candidate_canonical_player_id",
                       "identity_method", "origin_team_id", "destination_team_id", "stars", "rating"),
    "core.recruits": ("source_recruit_id", "recruit_year", "canonical_player_id", "committed_team_id", "stars", "rating",
                      "ranking", "height_inches", "weight_lbs"),
    "core.team_seasons": ("canonical_team_id", "season", "is_division_one"),
    "core.teams": ("canonical_team_id", "school", "display_name"),
    "core.players": ("canonical_player_id", "display_name"),
    "metadata.identity_link_quarantine": ("canonical_player_id", "from_season", "to_season", "reason"),
    # production-only (never read by Stage 3): the provider schedule snapshot with status / source timestamps
    "metadata.prod_schedule": ("canonical_game_id", "provider_game_id", "season", "status", "start_time",
                               "tip_time_quality", "home_team_id", "away_team_id", "source_received_at",
                               "listed_in_latest_schedule", "schedule_as_of"),
}
KEYS = {"flagship.games": ("canonical_game_id",),
        "flagship.team_game_outcomes": ("canonical_game_id", "canonical_team_id"),
        "flagship.player_game_outcomes": ("canonical_game_id", "canonical_team_id", "canonical_player_id"),
        "flagship.substitutions": ("canonical_game_id", "canonical_team_id", "canonical_player_id", "sub_in_period",
                                   "sub_in_seconds_remaining"),
        "flagship.rosters": ("canonical_player_id", "canonical_team_id", "season"),
        "core.transfers": ("source_transfer_id",), "core.recruits": ("source_recruit_id",),
        "core.team_seasons": ("canonical_team_id", "season"), "core.teams": ("canonical_team_id",),
        "core.players": ("canonical_player_id",),
        "metadata.identity_link_quarantine": ("canonical_player_id", "from_season", "to_season"),
        "metadata.prod_schedule": ("canonical_game_id",)}
# A box / substitution row whose source athlete resolved to no canonical player (identity_status UNRESOLVED_*, e.g. a
# non-D-I opponent's player) carries canonical_player_id NULL. As in the frozen canonical build (build_canonical:
# coalesce(canonical_player_id, 'UNRESOLVED:' || player_game_key)), such rows are keyed by their source record, never
# collapsed onto one NULL player: one team's unresolved players are distinct rows, not duplicate keys.
UNRESOLVED_KEY = {"flagship.player_game_outcomes": "player_game_key", "flagship.substitutions": "source_athlete_id"}


def key_exprs(rel: str, alias: str = "", cols=None) -> list[str]:
    """Key expressions of `rel`. cols: the relation's columns; the unresolved fallback applies only when its source
    column is present (a relation without it keys exactly as before)."""
    a = f"{alias}." if alias else ""
    fb = UNRESOLVED_KEY.get(rel)
    use = fb is not None and (cols is None or fb in set(cols))
    return [f"coalesce({a}{k}, 'UNRESOLVED:' || {a}{fb})" if k == "canonical_player_id" and use else f"{a}{k}"
            for k in KEYS[rel]]


SEASONAL = ("flagship.games", "flagship.team_game_outcomes", "flagship.player_game_outcomes", "flagship.substitutions",
            "flagship.rosters", "core.team_seasons", "metadata.prod_schedule")
TIP_QUALITY = ("EXACT", "DATE_ONLY", "TBD", "TBD_FLAG")            # TBD_FLAG / DATE_ONLY: the canonical inexact values
UNIVERSES = ("DI_VS_DI", "DI_VS_NON_DI", "NON_DI", "NON_DI_ONLY", "UNKNOWN")
# snapshot relations: a newer provider snapshot REPLACES the whole relation (a player dropped from a roster, a portal
# record withdrawn or a game removed from the listing must not survive from an older snapshot)
SNAPSHOT_RELATIONS = ("flagship.rosters", "core.transfers", "core.recruits", "core.team_seasons", "metadata.prod_schedule")


class StateUpdateFailed(RuntimeError):
    pass


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _sha_df(df: pd.DataFrame) -> str:
    """Content hash of a relation; list / array cells (e.g. period scores) are hashed by their JSON text."""
    d = df.sort_index(axis=1).copy()
    for c in d.columns:
        if d[c].dtype == object and d[c].map(lambda v: isinstance(v, (list, tuple, dict)) or hasattr(v, "tolist")).any():
            d[c] = d[c].map(lambda v: json.dumps(v.tolist() if hasattr(v, "tolist") else v, default=str))
    return hashlib.sha256(pd.util.hash_pandas_object(d, index=False).values.tobytes()).hexdigest()


def current_pointer(root: Path = PC.STATE_ROOT) -> dict | None:
    p = Path(root) / "CURRENT.json"
    return json.loads(p.read_text()) if p.exists() else None


def current(root: Path = PC.STATE_ROOT) -> dict | None:
    """The last-known-good state: {manifest, increment_db} or None."""
    ptr = current_pointer(root)
    if ptr is None:
        return None
    d = Path(root) / "versions" / ptr["state_version"]
    man = json.loads((d / "state_manifest.json").read_text())
    db = d / "increment.duckdb"
    if hashlib.sha256(db.read_bytes()).hexdigest() != man["increment_sha256"]:
        raise StateUpdateFailed(f"state {ptr['state_version']}: increment.duckdb changed after promotion")
    return {"manifest": man, "increment_db": db, "dir": d, "pointer": ptr}


def _empty_tables(con) -> None:
    for s in ("flagship", "core", "metadata"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")


def _adopt_types(con, rel: str, df: pd.DataFrame) -> list:
    """A column CREATE TABLE AS inferred from an all-NULL first batch is typed INTEGER by DuckDB (2026-11-10: every
    accounting_issues of the first 2026-27 boxes was NULL, so the first 'PTS_IDENTITY' flag failed to cast and every
    sync after it raised STATE_UPDATE_FAILED). Such a column holds no value yet, so it takes the incoming column's type
    as soon as the new rows carry a value: lossless (only NULLs are re-typed); a column that holds values is never
    altered."""
    ttype = {r[0]: r[1] for r in con.execute(f"DESCRIBE {rel}").fetchall()}
    con.register("adopt_rows", df)
    ntype = {r[0]: r[1] for r in con.execute("DESCRIBE SELECT * FROM adopt_rows").fetchall()}
    con.unregister("adopt_rows")
    changed = []
    for c, t in ttype.items():
        if c not in ntype or ntype[c] == t or not df[c].notna().any():
            continue
        if con.execute(f'SELECT count("{c}") FROM {rel}').fetchone()[0] == 0:
            con.execute(f'ALTER TABLE {rel} ALTER COLUMN "{c}" SET DATA TYPE {ntype[c]}')
            changed.append((c, t, ntype[c]))
    return changed


def _upsert(con, rel: str, df: pd.DataFrame, replace: bool = False) -> None:
    exists = con.execute(f"SELECT count(*) FROM duckdb_tables() WHERE schema_name || '.' || table_name = '{rel}'").fetchone()[0]
    if exists and replace:
        con.execute(f"DROP TABLE {rel}")
        exists = 0
    if not len(df):
        return
    con.register("new_rows", df)
    if not exists:
        con.execute(f"CREATE TABLE {rel} AS SELECT * FROM new_rows")
    else:
        _adopt_types(con, rel, df)
        tcols = {r[0] for r in con.execute(f"DESCRIBE {rel}").fetchall()} & set(df.columns)
        key = " AND ".join(f"{t} IS NOT DISTINCT FROM {n}" for t, n in zip(key_exprs(rel, "t", tcols), key_exprs(rel, "n", tcols)))
        con.execute(f"DELETE FROM {rel} t WHERE EXISTS (SELECT 1 FROM new_rows n WHERE {key})")
        con.execute(f"INSERT INTO {rel} BY NAME SELECT * FROM new_rows")
    con.unregister("new_rows")


def apply_identity_links(con, links: pd.DataFrame | None, method: str = "PROD_P3_REISSUED_ATHLETE_ID") -> dict:
    """Re-key a provider-re-issued canonical player onto its history-bearing canonical id in EVERY increment relation
    (links: new_player_id -> hist_player_id, prod.cbbd_feed.reissued_athlete_links). Applied to the candidate before
    validation, so rows ingested by earlier syncs under the new id are re-keyed too. A link is skipped (never forced)
    when re-keying would collide with an existing row of the history id. The new id's increment core.players row is
    dropped (the history id's player row is the frozen history's); the portal record carries the resolved id."""
    rep = {"applied": [], "skipped_conflict": []}
    if links is None or not len(links):
        return rep
    rels = [(f"{s}.{t}", [c for c in cols]) for s, t, cols in con.execute(
        """SELECT schema_name, table_name, list(column_name) FROM duckdb_columns()
           WHERE schema_name IN ('flagship', 'core', 'metadata') GROUP BY ALL""").fetchall()]
    for rel, cols in rels:                      # an id / method column created all-NULL is INTEGER: re-type (lossless)
        for c in ("canonical_player_id", "identity_method"):
            if c in cols and con.execute(f"SELECT data_type FROM duckdb_columns() WHERE schema_name || '.' || table_name = ? "
                                         f"AND column_name = ?", [rel, c]).fetchone()[0] != "VARCHAR" \
                    and con.execute(f"SELECT count({c}) FROM {rel}").fetchone()[0] == 0:
                con.execute(f"ALTER TABLE {rel} ALTER COLUMN {c} SET DATA TYPE VARCHAR")
    for lk in links.to_dict(orient="records"):
        n, h = lk["new_player_id"], lk["hist_player_id"]
        clash = []
        for rel, cols in rels:
            if "canonical_player_id" not in cols or rel == "core.players":
                continue
            others = [k for k in KEYS.get(rel, ()) if k != "canonical_player_id"]
            if "canonical_player_id" not in KEYS.get(rel, ()):
                continue
            on = " AND ".join([f"a.{k} IS NOT DISTINCT FROM b.{k}" for k in others] or ["TRUE"])
            if con.execute(f"""SELECT count(*) FROM {rel} a JOIN {rel} b ON {on}
                               WHERE a.canonical_player_id = ? AND b.canonical_player_id = ?""", [n, h]).fetchone()[0]:
                clash.append(rel)
        if clash:
            rep["skipped_conflict"].append({**lk, "relations": clash})
            continue
        moved = {}
        for rel, cols in rels:
            if rel == "core.players" or "canonical_player_id" not in cols:
                continue
            k = con.execute(f"UPDATE {rel} SET canonical_player_id = ? WHERE canonical_player_id = ?", [h, n]).fetchone()[0]
            if k:
                moved[rel] = int(k)
        if "core.players" in {r for r, _ in rels}:
            moved["core.players_dropped"] = int(con.execute("DELETE FROM core.players WHERE canonical_player_id = ?",
                                                            [n]).fetchone()[0])
        tcols = dict(rels).get("core.transfers", [])
        if lk.get("source_transfer_id") is not None and {"source_transfer_id", "identity_method"} <= set(tcols):
            sets = ["canonical_player_id = ?", "identity_method = ?"] + \
                   (["needs_manual_review = FALSE"] if "needs_manual_review" in tcols else [])
            moved["core.transfers"] = int(con.execute(
                f"UPDATE core.transfers SET {', '.join(sets)} WHERE CAST(source_transfer_id AS VARCHAR) = ?",
                [h, method, str(lk["source_transfer_id"])]).fetchone()[0])
        rep["applied"].append({**lk, "rows": moved})
    return rep


def validate(db: Path, source_cutoff) -> dict:
    """Whole-increment validation. Returns coverage; raises StateUpdateFailed on any contract violation."""
    cut = _utc(source_cutoff)
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    errs = []
    have = {r[0] for r in con.execute("SELECT schema_name || '.' || table_name FROM duckdb_tables()").fetchall()}
    for rel in ("flagship.games",):
        if rel not in have:
            errs.append(f"{rel} missing")
    for rel, cols in REQUIRED.items():
        if rel not in have:
            continue
        got = {r[0] for r in con.execute(f"DESCRIBE {rel}").fetchall()}
        miss = [c for c in cols if c not in got]
        if miss:
            errs.append(f"{rel}: missing columns {miss}")
            continue
        dup = con.execute(f"SELECT count(*) FROM (SELECT {', '.join(key_exprs(rel, cols=got))} FROM {rel} GROUP BY ALL HAVING count(*) > 1)").fetchone()[0]
        if dup:
            errs.append(f"{rel}: {dup} duplicate keys {KEYS[rel]}")
        if rel in SEASONAL:
            bad = con.execute(f"SELECT count(*) FROM {rel} WHERE season IS DISTINCT FROM {PC.PRODUCTION_SEASON}").fetchone()[0]
            if bad:
                errs.append(f"{rel}: {bad} rows outside season {PC.PRODUCTION_SEASON} (frozen history is never modified)")
    if errs:
        con.close()
        raise StateUpdateFailed("; ".join(errs))
    q = lambda s: con.execute(s).fetchone()[0]
    if "core.transfers" in have and q(f"SELECT count(*) FROM core.transfers WHERE transfer_year <> {PC.PRODUCTION_SEASON - 1}"):
        errs.append("core.transfers: only portal year 2026 (feeds 2026-27) may enter the increment")
    if "core.recruits" in have and q(f"SELECT count(*) FROM core.recruits WHERE recruit_year <> {PC.PRODUCTION_SEASON - 1}"):
        errs.append("core.recruits: only recruiting class 2026 (feeds 2026-27) may enter the increment")
    if "core.recruits" in have and q("""SELECT count(*) FROM (SELECT canonical_player_id FROM core.recruits
                                        WHERE canonical_player_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1)"""):
        errs.append("IDENTITY_CONFLICT: one canonical player resolved from two recruiting records")
    g = "flagship.games"
    if q(f"SELECT count(*) FROM {g} WHERE canonical_game_id NOT LIKE 'G-%' OR home_team_id NOT LIKE 'T-%' OR away_team_id NOT LIKE 'T-%'"):
        errs.append("games: non-canonical ids")
    if q(f"SELECT count(*) FROM {g} WHERE tip_time_quality NOT IN {TIP_QUALITY} OR game_universe NOT IN {UNIVERSES}"):
        errs.append("games: invalid tip_time_quality / game_universe")
    if q(f"SELECT count(*) FROM {g} WHERE home_team_id = away_team_id"):
        errs.append("games: home == away")
    if q(f"SELECT count(*) FROM {g} WHERE (home_points IS NULL) <> (away_points IS NULL)"):
        errs.append("games: half-recorded result")
    # a result can only be recorded once it was knowable (canonical HR-1) and the source saw it
    if q(f"""SELECT count(*) FROM {g} WHERE home_points IS NOT NULL AND
             (CASE WHEN tip_time_quality = 'EXACT' THEN scheduled_tip_utc + INTERVAL 4 HOUR
                   ELSE timezone('America/New_York', CAST(game_date_et + INTERVAL 1 DAY AS TIMESTAMP) + INTERVAL 6 HOUR) END)
             > TIMESTAMPTZ '{cut.isoformat()}'"""):
        errs.append("FUTURE_LEAKAGE: a result is recorded before it was knowable at source_cutoff")
    for rel in ("flagship.team_game_outcomes", "flagship.player_game_outcomes", "flagship.substitutions"):
        if rel not in have:
            continue
        if q(f"SELECT count(*) FROM {rel} r LEFT JOIN {g} USING (canonical_game_id) WHERE {g}.canonical_game_id IS NULL"):
            errs.append(f"{rel}: rows for games not in the schedule")
        if q(f"SELECT count(*) FROM {rel} r JOIN {g} USING (canonical_game_id) WHERE {g}.home_points IS NULL"):
            errs.append(f"FUTURE_LEAKAGE: {rel} rows for a game with no recorded result")
        if q(f"SELECT count(*) FROM {rel} WHERE historical_knowable_at IS NULL OR historical_knowable_at > TIMESTAMPTZ '{cut.isoformat()}'"):
            errs.append(f"FUTURE_LEAKAGE: {rel} rows knowable after source_cutoff")
    if "flagship.player_game_outcomes" in have and q(
            "SELECT count(*) FROM flagship.player_game_outcomes WHERE canonical_player_id IS NOT NULL AND canonical_player_id NOT LIKE 'P-%'"):
        errs.append("player_game_outcomes: non-canonical player ids")
    if errs:
        con.close()
        raise StateUpdateFailed("; ".join(errs))
    cov = {
        "games": q(f"SELECT count(*) FROM {g}"),
        "completed_games": q(f"SELECT count(*) FROM {g} WHERE home_points IS NOT NULL"),
        "scheduled_games": q(f"SELECT count(*) FROM {g} WHERE home_points IS NULL"),
        "completed_with_team_box": q(f"""SELECT count(DISTINCT canonical_game_id) FROM flagship.team_game_outcomes""")
        if "flagship.team_game_outcomes" in have else 0,
        "completed_with_player_box": q("SELECT count(DISTINCT canonical_game_id) FROM flagship.player_game_outcomes")
        if "flagship.player_game_outcomes" in have else 0,
        "completed_with_substitutions": q("SELECT count(DISTINCT canonical_game_id) FROM flagship.substitutions")
        if "flagship.substitutions" in have else 0,
        "portal_rows_2026": q("SELECT count(*) FROM core.transfers") if "core.transfers" in have else 0,
        "recruit_rows_2026": q("SELECT count(*) FROM core.recruits") if "core.recruits" in have else 0,
    }
    last = con.execute(f"""SELECT canonical_game_id, scheduled_tip_utc FROM {g} WHERE home_points IS NOT NULL
                           ORDER BY scheduled_tip_utc DESC, canonical_game_id DESC LIMIT 1""").fetchone()
    cov["last_completed_game"] = {"canonical_game_id": last[0], "scheduled_tip_utc": str(last[1])} if last else None
    cov["relation_sha256"] = {rel: _sha_df(con.execute(f"SELECT * FROM {rel} ORDER BY ALL").df())
                              for rel in sorted(have) if rel in REQUIRED}
    con.close()
    return cov


def _write_pointer(root: Path, body: dict) -> None:
    tmp = root / f".CURRENT.{uuid.uuid4().hex}.json"
    tmp.write_text(json.dumps(body, default=str) + "\n")
    os.replace(tmp, root / "CURRENT.json")                          # the atomic last-known-good swap


def update(new_rows: dict, source_cutoff, provider: dict, preseason_evidence: dict, root: Path = PC.STATE_ROOT,
           created_at=None, replace=(), manifest_extra: dict | None = None, identity_links: pd.DataFrame | None = None) -> dict:
    """last-known-good increment + new legally available rows = candidate; validate the WHOLE candidate; only then
    promote it atomically. `replace`: snapshot relations whose previous contents are dropped first.
    preseason_evidence: {"portal_2026_complete": bool, "recruits_2026_complete": bool, "source": str, ...} declared by
    the feed. Idempotent: a candidate whose relations hash exactly like the current state's creates NO new version;
    the pointer only records the newer successful sync (last_successful_sync / last_sync_source_cutoff)."""
    root = Path(root)
    created_at = _utc(created_at) if created_at is not None else _now()
    cut = _utc(source_cutoff)
    if cut > created_at:
        raise StateUpdateFailed("source_cutoff is after the state creation time")
    unknown = sorted((set(new_rows) | set(replace)) - set(REQUIRED))
    if unknown:
        raise StateUpdateFailed(f"unknown relations {unknown}")
    prev = current(root)
    if prev is not None and cut < _utc(prev["pointer"].get("last_sync_source_cutoff") or prev["manifest"]["source_cutoff"]):
        raise StateUpdateFailed("source_cutoff moves backwards")
    (root / "versions").mkdir(parents=True, exist_ok=True)
    building = root / "versions" / f".building-{uuid.uuid4().hex}"
    building.mkdir()
    try:
        db = building / "increment.duckdb"
        if prev is not None:
            shutil.copyfile(prev["increment_db"], db)
        con = duckdb.connect(str(db))
        con.execute("SET TimeZone = 'UTC'")
        _empty_tables(con)
        for rel in sorted(set(new_rows) | set(replace)):
            _upsert(con, rel, new_rows.get(rel, pd.DataFrame()), replace=rel in replace)
        links_rep = apply_identity_links(con, identity_links)
        con.close()
        if identity_links is not None:
            manifest_extra = {**(manifest_extra or {}), "identity_p3": json.loads(json.dumps(links_rep, default=str))}
        cov = validate(db, cut)
        if prev is not None and cov["relation_sha256"] == prev["manifest"]["coverage"]["relation_sha256"] \
                and preseason_evidence == prev["manifest"].get("preseason_evidence"):
            shutil.rmtree(building, ignore_errors=True)
            ptr = {**prev["pointer"], "last_successful_sync": created_at.isoformat(), "last_sync_source_cutoff": cut.isoformat(),
                   "last_sync_extra": manifest_extra or {}}
            _write_pointer(root, ptr)
            return {**prev["manifest"], "idempotent_no_change": True, "pointer": ptr}
        inc_sha = hashlib.sha256(db.read_bytes()).hexdigest()
        version = "S" + created_at.strftime("%Y%m%dT%H%M%SZ") + "-" + inc_sha[:12]
        man = {"state_version": version, "created_at": created_at.isoformat(), "source_cutoff": cut.isoformat(),
               "parent_state_version": prev["manifest"]["state_version"] if prev else None,
               "production_season": PC.PRODUCTION_SEASON, "training_through": PC.TRAINING_THROUGH,
               "provider": provider, "preseason_evidence": preseason_evidence,
               "history_canonical": {"path": "data/lockbox_2025_26/canonical/cbb.duckdb", "role": "frozen, read-only, seasons <= 2025-26"},
               "coverage": cov, "last_completed_game": cov["last_completed_game"],
               "last_completed_game_included": cov["last_completed_game"],
               "input_hashes": {"new_rows": {k: _sha_df(v) for k, v in sorted(new_rows.items()) if len(v)},
                                "increment_relations": cov["relation_sha256"]},
               "replaced_relations": sorted(replace),
               "increment_sha256": inc_sha, "model_refit": False, "historical_rebuild": False,
               **(manifest_extra or {})}
        (building / "state_manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True, default=str) + "\n")
        final = root / "versions" / version
        if final.exists():
            raise StateUpdateFailed(f"state version {version} exists")
        os.replace(building, final)
        ptr = {"state_version": version, "promoted_at": _now().isoformat(), "last_successful_sync": created_at.isoformat(),
               "last_sync_source_cutoff": cut.isoformat()}
        _write_pointer(root, ptr)
        return {**man, "idempotent_no_change": False, "pointer": ptr}
    except Exception as e:
        shutil.rmtree(building, ignore_errors=True)
        log = root / "update_failures.jsonl"
        with open(log, "a") as f:
            f.write(json.dumps({"at": _now().isoformat(), "error": str(e)[:2000]}) + "\n")
        if isinstance(e, StateUpdateFailed):
            raise
        raise StateUpdateFailed(f"{type(e).__name__}: {e}") from e


def staleness(manifest: dict, prediction_as_of, pointer: dict | None = None) -> dict:
    """Age of the newest successfully synced source cutoff (an idempotent re-sync refreshes it without a new version).
    A state whose cutoff is AFTER prediction_as_of carries data not knowable at prediction time: POST_CUTOFF_DATA."""
    ref = (pointer or {}).get("last_sync_source_cutoff") or manifest["source_cutoff"]
    if (pointer or {}).get("last_sync_source_cutoff") and _utc(ref) > _utc(prediction_as_of):
        ref = manifest["source_cutoff"]
    age_h = (_utc(prediction_as_of) - _utc(ref)).total_seconds() / 3600.0
    return {"state_age_hours": age_h, "stale": bool(age_h > PC.STATE_MAX_AGE_HOURS),
            "post_cutoff_data": bool(_utc(manifest["source_cutoff"]) > _utc(prediction_as_of))}
