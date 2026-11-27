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


def information_state_groups(groups: dict, history_db: Path, season: int = WC.PRODUCTION_SEASON) -> list:
    """Coalesce the per-(cutoff, state version) pregame builds into one build per IDENTICAL information state.

    Root cause (2026-11-26, warmup receipts 11-19 .. 11-26): one frozen Stage 3 build costs ~220 s whatever the number
    of target games (Droplet profile: compact 11 s, Stage 3 218 s, frames 8 s for 5 games), and the warmup ran one build
    per distinct cutoff -- 27..39 builds, 51 -> 135 min. A pregame build is a pure function of (history DB, the state
    version's increment DB, the target games, the season, the as_of mask). Within one state version, consecutive cutoffs
    c_1 < ... < c_k with NO as_of-mask instant in (c_1, c_k] (prod.features.as_of_mask_instants) see a byte-identical
    masked canonical DB, so ONE build at as_of = c_1 (the earliest: the strictest mask) yields exactly their rows.
    Each row keeps its own prediction_cutoff_utc (Stage 3 derives it from the schedule, never from as_of) and the frozen
    per-row PIT audit still runs. The proof is recorded per game in meta build.information_state.

    groups: {(cut, state_version): (st, [game ids])}. Returns [((as_of, sv), (st, gids, {gid: own cutoff}, info))];
    info is None for a single-cutoff build (the incumbent meta, byte-for-byte)."""
    out, cur = [], None
    items = sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    inst: dict = {}
    for sv in {sv for (_, sv) in groups}:                    # one instant scan per state version over its cutoff span
        cuts = sorted(c for (c, s) in groups if s == sv)
        if len(cuts) > 1:
            inc = next(st for (c, s), (st, _) in groups.items() if s == sv)["increment_db"]
            inst[sv] = [pd.Timestamp(t) for t in PF.as_of_mask_instants(history_db, inc, season, cuts[0], cuts[-1])]
    for (cut, sv), (st, gids) in items:
        if cur is not None and cur["sv"] == sv and not any(cur["cut"] < t <= cut for t in inst.get(sv, ())):
            cur["cuts"].append(cut)
            cur["gcut"].update({g: cut for g in gids})
            cur["gids"] += gids
            continue
        cur = {"cut": cut, "sv": sv, "st": st, "gids": list(gids), "gcut": {g: cut for g in gids}, "cuts": [cut]}
        out.append(cur)
    res = []
    for g in out:
        info = None if len(g["cuts"]) == 1 else {
            "rule": "IDENTICAL_AS_OF_MASKED_CANONICAL", "as_of": g["cut"].isoformat(), "state_version": g["sv"],
            "equivalent_through": g["cuts"][-1].isoformat(), "group_cutoffs": len(g["cuts"]),
            "as_of_mask_instants_in_window": 0}
        res.append(((g["cut"], g["sv"]), (g["st"], g["gids"], g["gcut"], info)))
    return res


# ============================================================================ causal identity of a cached pregame row
CAUSAL_KEY_VERSION = "PREGAME_CAUSAL_KEY_V1"
_FILE_SHA: dict = {}


def file_sha256(p: Path) -> str:
    """Content sha256 of an immutable input file, memoized per (path, size, mtime_ns)."""
    p = Path(p)
    st = p.stat()
    k = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    if k not in _FILE_SHA:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(1 << 22), b""):
                h.update(b)
        _FILE_SHA[k] = h.hexdigest()
    return _FILE_SHA[k]


def feature_semantics_sha(cfg: dict) -> str:
    """Identity of everything that defines a pregame feature row's VALUES: the frozen Stage 3 SQL modules, the
    production mask / universe adaptation and frame SELECTs of prod.features, and the frozen marginal config. The
    dependency closure is deliberately excluded: it is an exact optimization (proven equivalent), not a semantic input."""
    import inspect
    from cbb_platform.stage3 import participation, pitlib, player_state, sources as S3S, spec as S3P, team_state, universe
    parts = [inspect.getsource(m) for m in (participation, pitlib, player_state, S3S, S3P, team_state, universe)]
    parts += [PF.KNOWABLE, repr(PF.RESULT_COLS), repr(PF.EMPTY_RELATIONS), repr(PF.UNION_RELATIONS), PF.TS_ORIGINAL,
              PF.TS_PRODUCTION, inspect.getsource(PF._universe), inspect.getsource(PF._frame_sql),
              inspect.getsource(PF.model_frame), json.dumps(cfg, sort_keys=True, default=str)]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def causal_key(*, semantics_sha: str, history_sha: str, state_version: str, increment_sha: str, cutoff, as_of,
               season: int, group_targets) -> dict:
    """Typed causal identity of one cached pregame row. Every component that can change the row's values is a content
    hash or an exact instant; game ids alone are never the key."""
    k = {"version": CAUSAL_KEY_VERSION, "feature_semantics_sha256": semantics_sha, "history_db_sha256": history_sha,
         "state_version": state_version, "increment_db_sha256": increment_sha,
         "prediction_cutoff_utc": _utc(cutoff).isoformat(), "build_as_of_utc": _utc(as_of).isoformat(),
         "season": int(season),
         "group_targets_sha256": hashlib.sha256("|".join(sorted(group_targets)).encode()).hexdigest()}
    k["key_sha256"] = hashlib.sha256(json.dumps(k, sort_keys=True).encode()).hexdigest()
    return k


class StateIndex:
    """state_versions(state_root) read ONCE; asof(t) == eligibility.state_asof(state_root, t) without re-reading the
    ~300 manifests per call."""
    def __init__(self, state_root: Path):
        self.v = EL.state_versions(state_root)
        self.names = {x["state_version"] for x in self.v}

    def asof(self, t):
        t = _utc(t)
        ok = [x for x in self.v if x["created_at"] <= t and x["source_cutoff"] <= t]
        return ok[-1] if ok else None


def cache_validity(meta: dict, cutoff, states, semantics_sha: str | None, history_sha: str | None) -> str:
    """VALID | LEGACY_VALID | STALE_<reason>. A cached row is reusable only while its causal identity still holds:
    the game's CURRENT cutoff equals the cached one (a rescheduled tip invalidates), the state version as-of that
    cutoff is still the one it was built on (a re-created / replaced state invalidates), and -- when the row carries a
    causal key -- the feature semantics and the history DB content are unchanged. Corrections that arrive AFTER the
    cutoff never invalidate: a training row holds only what was knowable at its cutoff (no backdating). Legacy rows
    (pre-key) are checked on cutoff + state version; their semantics are those of stage3 SQL frozen 2026-10-16."""
    if meta.get("status") not in ("BUILT", "NO_PREGAME_EXAMPLES"):
        return "STALE_STATUS"
    if _utc(meta["prediction_cutoff_utc"]) != _utc(cutoff):
        return "STALE_CUTOFF"
    idx = states if isinstance(states, StateIndex) else StateIndex(states)
    if meta.get("state_version") not in idx.names:
        return "LEGACY_VALID" if meta.get("causal_key") is None else "VALID_STATE_RETIRED"   # cannot rebuild better
    st = idx.asof(cutoff)
    if st is None or st["state_version"] != meta.get("state_version"):
        return "STALE_STATE_VERSION"
    k = meta.get("causal_key")
    if k is None:
        return "LEGACY_VALID"
    if semantics_sha is not None and k.get("feature_semantics_sha256") != semantics_sha:
        return "STALE_FEATURE_SEMANTICS"
    if history_sha is not None and k.get("history_db_sha256") != history_sha:
        return "STALE_HISTORY_DB"
    return "VALID"


REUSABLE = ("VALID", "LEGACY_VALID", "VALID_STATE_RETIRED")


def supersede(root: Path, gid: str, reason: str) -> Path:
    """Move an invalidated cache entry aside (kept as evidence, never deleted); atomic within the cache root."""
    dst = Path(root) / ".superseded" / f"{gid}.{pd.Timestamp.now(tz='UTC').strftime('%Y%m%dT%H%M%SZ')}.{reason}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(Path(root) / gid, dst)
    return dst


def feature_rows(cache_root: Path, state_root: Path, games: pd.DataFrame, history_db: Path, cfg: dict,
                 season: int = WC.PRODUCTION_SEASON, log=print, closure: bool | None = None) -> dict:
    """games: canonical_game_id + prediction_cutoff_utc. Returns {"cand", "team", "meta"} for every game (cached rows
    are reused byte-for-byte while their causal identity holds -- cache_validity; a stale entry is superseded and
    rebuilt; a game whose cutoff has no state version is reported, never built from later data).

    closure: build with the exact dependency closure (prod.features.dependency_closure). None = env
    CBB_FEATURE_CLOSURE (default ON); OFF restores the incumbent full build (the comparator)."""
    if closure is None:
        closure = os.environ.get("CBB_FEATURE_CLOSURE", "ON").upper() != "OFF"
    root = Path(cache_root) / "features"
    root.mkdir(parents=True, exist_ok=True)
    sem, hsha = feature_semantics_sha(cfg), file_sha256(history_db)
    sidx = StateIndex(state_root)
    todo, metas = [], {}
    for r in games.itertuples(index=False):
        mp = root / r.canonical_game_id / "meta.json"
        if mp.exists():
            m = json.loads(mp.read_text())
            v = cache_validity(m, r.prediction_cutoff_utc, sidx, sem, hsha)
            if v in REUSABLE:
                metas[r.canonical_game_id] = m
                continue
            log(f"pregame features {r.canonical_game_id}: cached row {v} -> superseded, rebuilt")
            supersede(root, r.canonical_game_id, v)
        elif (root / r.canonical_game_id).exists():              # a directory without meta.json: never complete
            log(f"pregame features {r.canonical_game_id}: incomplete cache entry -> superseded, rebuilt")
            supersede(root, r.canonical_game_id, "STALE_INCOMPLETE")
        todo.append(r)
    groups: dict = {}
    for r in todo:
        st = sidx.asof(r.prediction_cutoff_utc)
        if st is None:
            metas[r.canonical_game_id] = {"game_id": r.canonical_game_id, "status": "NO_STATE_AT_CUTOFF",
                                          "prediction_cutoff_utc": _utc(r.prediction_cutoff_utc).isoformat()}
            continue
        groups.setdefault((_utc(r.prediction_cutoff_utc), st["state_version"]), (st, []))[1].append(r.canonical_game_id)
    for (cut, sv), (st, gids, gcut, info) in information_state_groups(groups, history_db, season):
        work = root / f".work-{uuid.uuid4().hex}"
        try:
            fr = PF.build(work, gids, season, cut, cfg, history_db, st["increment_db"], closure=closure)
            isha = file_sha256(st["increment_db"])
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
                        "prediction_cutoff_utc": gcut[gid].isoformat(), "state_version": sv,
                        "state_created_at": st["created_at"].isoformat(), "candidate_rows": int(len(c)),
                        "team_rows": int(len(t)), "cand_sha256": _sha_df(c), "team_sha256": _sha_df(t),
                        "build": {"target_outcome_rows": prov["target_outcome_rows"], "post_as_of_rows": prov["post_as_of_rows"],
                                  "as_of": prov["as_of"], "group_games": len(gids),
                                  **({"information_state": info} if info else {}),
                                  **({"closure": prov["closure"]} if "closure" in prov else {})}, "pit_audit": pit,
                        "causal_key": causal_key(semantics_sha=sem, history_sha=hsha, state_version=sv,
                                                 increment_sha=isha, cutoff=gcut[gid], as_of=cut, season=season,
                                                 group_targets=gids)}
                (tmp / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
                if (gdir / "meta.json").exists():                   # concurrent builder won: keep the first (immutable)
                    shutil.rmtree(tmp)
                else:
                    if gdir.exists():                               # incomplete leftover (no meta.json): keep as evidence
                        supersede(root, gid, "STALE_INCOMPLETE")
                    os.replace(tmp, gdir)
                metas[gid] = json.loads((gdir / "meta.json").read_text())
            log(f"pregame features {cut.isoformat()} ({len(gids)} games, state {sv}"
                + (f", {info['group_cutoffs']} cutoffs through {info['equivalent_through']})" if info else ")"))
        finally:
            shutil.rmtree(work, ignore_errors=True)
    built = [g for g, m in metas.items() if m.get("status") == "BUILT"]
    cand = pd.concat([pd.read_parquet(root / g / "cand.parquet") for g in sorted(built)], ignore_index=True) if built else pd.DataFrame()
    team = pd.concat([pd.read_parquet(root / g / "team.parquet") for g in sorted(built)], ignore_index=True) if built else pd.DataFrame()
    return {"cand": cand, "team": team, "meta": metas}


def cached_valid_games(cache_root: Path, state_root: Path, games: pd.DataFrame, history_db: Path, cfg: dict) -> set:
    """Game ids whose cached pregame row is reusable now (cache_validity), for the warm-up's to-do list. Read-only."""
    root = Path(cache_root) / "features"
    if not root.exists():
        return set()
    sem, hsha, sidx = feature_semantics_sha(cfg), file_sha256(history_db), StateIndex(state_root)
    out = set()
    for r in games.itertuples(index=False):
        mp = root / r.canonical_game_id / "meta.json"
        if mp.exists() and cache_validity(json.loads(mp.read_text()), r.prediction_cutoff_utc, sidx, sem, hsha) in REUSABLE:
            out.add(r.canonical_game_id)
    return out


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
