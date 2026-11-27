"""CBB daily Excel exports: Boards, Distributions, Grades, Performance. REPORTING ONLY.

    PYTHONPATH=. .venv/bin/python -B -m cbb_platform.prod.exports daily --date YYYY-MM-DD

writes, for the America/New_York slate date D, into <export root>/D/:

    CBB_Boards_MM_DD_YYYY.xlsx         REQUIRED. Full Board | 60 Percent Plus | 65 Percent Plus | Model Coverage
                                       (prod.market_exports: ONE row per game x player x stat, asserted; every book x
                                       line x side priced at its exact line from the persisted PMF by prod.market_eval;
                                       Full Board = rows with a real pre-cutoff market, Model Coverage = model-only rows)
                                       + boards_receipt.json (row / side / tier counts, quote-ingestion and quota totals)
    CBB_Distributions_MM_DD_YYYY.xlsx  supporting. ONE row per (game, player, stat) -- mean, median, sd, percentiles,
                                       P(play), expected minutes, availability, model version, forecast id, PMF hash
    CBB_Distributions_MM_DD_YYYY_pmf_refs.parquet   REFERENCES, not vectors (the full PMF is stored ONCE, in
                                       ticks/<tick>/pmfs.parquet or forecasts/<D>/<snapshot>/pmfs.parquet)
    (on request only) `exports expand-distributions --date D` writes the legacy one-row-per-outcome workbook
    CBB_Grades_<D-1>.xlsx              REQUIRED. Grades the D-1 ET slate, and its file name carries D-1 (folder D, e.g.
                                       exports/2026-11-21/CBB_Grades_11_20_2026.xlsx; never a blank same-day workbook): every frozen
                                       pre-cutoff market evaluation (PASS_NO_EDGE included) against the exact persisted
                                       line. Graded Predictions | By Stat | By Player Role | Stat x Role | By Side |
                                       By Reporting Tier | Status
    CBB_Performance_MM_DD_YYYY.xlsx    REQUIRED, cumulative (prod.performance): Overall | By Stat | By Player Role |
                                       Stat x Role | By Side | By Reporting Tier | Daily | Calibration | Selections

Read-only over evidence production has ALREADY persisted (no refit, no scoring, no provider / API / GitHub call: the
module imports no network or acquisition code):

    durable PMFs        ticks/<tick>/pmfs.parquet (the official prediction's PMFs, prod.pmf_evidence) and
                        forecasts/<D>/<snapshot>/pmfs.parquet (forecast snapshots, prod.lifecycle); per game the LATEST
                        prediction made before the game's production cutoff
    quotes              quotes/<D>/observations.jsonl (+ parquet): every normalized, matched, freshness-checked quote
                        row of each collection (prod.quote_obs); the Board uses the latest observation before the cutoff
    evaluations         evaluations/<D>/market_evaluations.jsonl (write-once frozen market evaluations, written here
                        once a game's cutoff has passed)
    outcomes            ticks/*/actuals.parquet (final box settlement by prod.shadow_tick)
    names               core.teams of the history store + the current 2026-27 state increment (read-only)
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from cbb_platform.prod import config as PC, engine as ENG, pmf_compact as PCX, pmf_evidence as PE

EXPORT_VERSION = "cbb-prod-daily-exports-2.0.0"
TZ = "America/New_York"
EXPORT_ROOT = Path(os.environ.get("CBB_EXPORT_ROOT") or (PC.PROD_ROOT / "exports"))   # <root>/<YYYY-MM-DD>/CBB_*.xlsx
RUNS_ROOT = PC.RUNS_ROOT
PMF_TOL = 1e-9                                   # = prod.pricing.SUM_TOL = prod.simulate.PMF_TOL (production PMF tolerance)
LIVE_LABELS = ("LIVE",)
DECIDED = ("PREDICTED", "EXCLUDED", "MISSED_PREDICTION_CUTOFF", "NOT_SCORED_AT_CUTOFF")   # = shadow_tick.DECIDED (tested)
SIDES = ("OVER", "UNDER")

BOARD_RECEIPT = "boards_receipt.json"
BOARD_AUDIT = "board_quote_audit.jsonl.gz"
GRADE_RECEIPT = "grades_receipt.json"
DIST_SHEET = "Distributions"            # further sheets "Distributions 2", "Distributions 3", ... (EXCEL_MAX_ROWS)
EXCEL_MAX_ROWS = 1_048_576               # SpreadsheetML worksheet limits (ECMA-376 / Excel): a row or column ref
EXCEL_MAX_COLS = 16_384                  # beyond them is invalid content (Excel "repairs" by deleting the cells)
DIST_COLS = ("GAME", "TEAM", "PLAYER", "PROP", "MEAN", "OUTCOME", "PROBABILITY")   # long derivative (on request)
DIST_SUMMARY_COLS = ("GAME", "TEAM", "OPPONENT", "PLAYER", "PROP", "MEAN", "MEDIAN", "SD", "P10", "P25", "P75", "P90",
                     "MAX_OUTCOME", "P_PLAY", "EXP_MINUTES", "AVAILABILITY", "MODEL_VERSION", "ENGINE_ID", "SOURCE", "FORECAST_ID",
                     "PMF_SHA256")
DIST_PERCENTILES = (0.10, 0.25, 0.50, 0.75, 0.90)   # smallest outcome k with CDF(k) >= q
ROLES = ("CORE_30_PLUS", "ROTATION_20_TO_30", "LIMITED_UNDER_20", "NO_EXPECTED_MINUTES", "UNKNOWN_ROLE")
BANDS = (("<50%", None, 0.50), ("50-55%", 0.50, 0.55), ("55-60%", 0.55, 0.60), ("60-65%", 0.60, 0.65),
         ("65-70%", 0.65, 0.70), ("70%+", 0.70, None))
FORMATS = {"MEAN": "0.000", "PROBABILITY": "0.0000%", "OUTCOME": "0", "SD": "0.000", "P_PLAY": "0.0%",
           "EXP_MINUTES": "0.0"}                                  # Distributions; Boards / Grades / Performance carry their own


class ExportIntegrityError(RuntimeError):
    """An invariant of the export contract failed (immutable field changed, negative / non-normalized PMF, ...)."""


# ============================================================================ small helpers
def _f(x):
    """Finite float or None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _utc(x) -> pd.Timestamp | None:
    if x is None or (not isinstance(x, (str, pd.Timestamp)) and pd.isna(x)):
        return None
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def slate_date_of(tip) -> str | None:
    t = _utc(tip)
    return None if t is None else str(t.tz_convert(TZ).date())


def _tag(d: pd.Timestamp) -> str:
    return f"{d.month:02d}_{d.day:02d}_{d.year:04d}"


def file_names(date: str) -> dict:
    """Names of the files in the export folder of production date D. Every file carries D, EXCEPT Grades: the Grades
    workbook of folder D grades the D-1 ET slate, so its file name carries D-1 (CBB_Grades_<D-1>.xlsx)."""
    d = pd.Timestamp(date)
    tag = _tag(d)
    return {"boards": f"CBB_Boards_{tag}.xlsx", "distributions": f"CBB_Distributions_{tag}.xlsx",
            "grades": f"CBB_Grades_{_tag(d - pd.Timedelta(days=1))}.xlsx", "performance": f"CBB_Performance_{tag}.xlsx", "distribution_pmfs": f"CBB_Distributions_{tag}_pmf_refs.parquet",
            "distributions_long": f"CBB_Distributions_{tag}_LONG.xlsx"}


def player_role(expected_minutes) -> str:
    """Fixed role from prediction-time expected minutes (never realized minutes)."""
    if isinstance(expected_minutes, bool):
        return "UNKNOWN_ROLE"
    m = _f(expected_minutes)
    if m is None:
        return "UNKNOWN_ROLE"
    if m >= 30:
        return "CORE_30_PLUS"
    if m >= 20:
        return "ROTATION_20_TO_30"
    if m > 0:
        return "LIMITED_UNDER_20"
    return "NO_EXPECTED_MINUTES"


def model_band(p) -> str | None:
    p = _f(p)
    if p is None:
        return None
    for name, lo, hi in BANDS:
        if (lo is None or p >= lo) and (hi is None or p < hi):
            return name
    return None


def valid_american(odds) -> bool:
    o = _f(odds)
    return o is not None and abs(o) >= 100


def profit(result: str, odds):
    """One unit risked per grade row."""
    if result == "WIN":
        o = float(odds)
        return o / 100.0 if o > 0 else 100.0 / abs(o)
    if result == "LOSS":
        return -1.0
    if result in ("PUSH", "VOID"):
        return 0.0
    return None                                   # PENDING / UNSUPPORTED: blank


def grade_result(side, odds, settlement: str | None) -> str:
    if side not in SIDES or not valid_american(odds):
        return "UNSUPPORTED"
    if settlement is None or (not isinstance(settlement, str) and pd.isna(settlement)):
        return "PENDING"
    if settlement in ("WIN", "LOSS", "PUSH"):
        return settlement
    if settlement == "NO_ACTION":
        return "VOID"
    return "UNSUPPORTED"


def no_vig(side: str, d_side, d_other):
    """(1/d_side) / (1/d_side + 1/d_other): prospective_monitor.line_table's same-book normalization."""
    a, b = _f(d_side), _f(d_other)
    if a is None or b is None or a <= 1 or b <= 1:
        return None
    ia, ib = 1.0 / a, 1.0 / b
    return ia / (ia + ib)


# ============================================================================ evidence
def _jsonl(p: Path) -> list[dict]:
    p = Path(p)
    if not p.exists():
        return []
    op = gzip.open if p.suffix == ".gz" else open
    with op(p, "rt") as f:
        return [json.loads(l) for l in f if l.strip()]


def _verify_chain(recs: list[dict], path: Path) -> None:
    """Hash chain of a prod.ledger / prod.side_monitor log (any edit / reorder -> ExportIntegrityError)."""
    prev = "0" * 64
    for n, rec in enumerate(recs, 1):
        body = {k: v for k, v in rec.items() if k != "row_hash"}
        if rec.get("prev_row_hash") != prev or hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest() != rec.get("row_hash"):
            raise ExportIntegrityError(f"{path}: hash chain broken at line {n}")
        prev = rec["row_hash"]


def slate_games(ops_root: Path, date: str) -> pd.DataFrame:
    """Decided PREDICTED registry rows (first decision per game, as shadow_tick.decided) whose scheduled tip falls on
    the America/New_York date `date`."""
    rows = _jsonl(Path(ops_root) / "registry" / "games.jsonl")
    cols = ["game_id", "status", "tick_id", "run_id", "scheduled_tip", "cutoff_rule", "prediction_as_of"]
    if not rows:
        return pd.DataFrame(columns=cols + ["tip_utc", "cutoff_utc"])
    r = pd.DataFrame(rows)
    for c in cols:
        if c not in r:
            r[c] = None
    r = r[r["status"].isin(DECIDED)].drop_duplicates("game_id", keep="first")
    r = r[r["status"] == "PREDICTED"].copy()
    r["slate_date"] = r["scheduled_tip"].map(slate_date_of)
    r = r[r["slate_date"] == date].copy()
    r["tip_utc"] = r["scheduled_tip"].map(_utc)
    tc = r["target_cutoff"] if "target_cutoff" in r else pd.Series([None] * len(r), index=r.index)
    r["cutoff_utc"] = [_utc(c) if _utc(c) is not None else (None if t is None else t - pd.Timedelta(hours=1))
                       for c, t in zip(tc, r["tip_utc"])]     # registry target_cutoff, else TIP_MINUS_1H
    return r[cols + ["tip_utc", "cutoff_utc"]].reset_index(drop=True)


REFRESH_RECEIPTS = "reprice_side_monitor.jsonl"        # = prod.quote_refresh.REFRESH_FILE (tested)


def team_names(history_db: Path | None, state_root: Path | None) -> dict:
    """canonical_team_id -> display_name: frozen history core.teams + the 2026-27 increment (read-only)."""
    import duckdb
    names: dict = {}
    dbs = []
    if state_root is not None:
        try:
            from cbb_platform.prod import state as ST
            st = ST.current(Path(state_root))
            if st and Path(st["increment_db"]).exists():
                dbs.append(Path(st["increment_db"]))
        except Exception:                                         # noqa: BLE001 -- names only; fall back to ids
            pass
    if history_db is not None and Path(history_db).exists():
        dbs.append(Path(history_db))
    for db in reversed(dbs):                                      # history first, increment adds new teams
        try:
            con = duckdb.connect(str(db), read_only=True)
            has = con.execute("SELECT count(*) FROM duckdb_tables() WHERE schema_name='core' AND table_name='teams'").fetchone()[0]
            if has:
                for tid, dn in con.execute("SELECT canonical_team_id, any_value(display_name) FROM core.teams GROUP BY 1").fetchall():
                    if dn is not None:
                        names.setdefault(tid, dn)
            con.close()
        except Exception:                                         # noqa: BLE001
            pass
    return names


# ============================================================================ offers (Boards + Grades population)
def decimal_odds(american) -> float | None:
    """Exact bettor-facing decimal payout of American odds (None when not a valid American price)."""
    if not valid_american(american):
        return None
    o = float(american)
    return 1.0 + o / 100.0 if o > 0 else 1.0 + 100.0 / abs(o)


def norm_book(b) -> str:
    return "" if b is None or (not isinstance(b, str) and pd.isna(b)) else str(b).strip().lower()


def _check_pmf(pmf: np.ndarray, what: str) -> None:
    if not np.isfinite(pmf).all() or (pmf < 0).any():
        raise ExportIntegrityError(f"negative / non-finite PMF {what}")
    if abs(pmf.sum() - 1.0) > PMF_TOL:
        raise ExportIntegrityError(f"PMF mass {pmf.sum()} {what}")


def _dists_of(path: Path, gid: str, cache: dict | None) -> list[dict]:
    """PE.distributions of one durable PMF file restricted to one game. cache (one export run): path -> {game_id: [...]}
    so each evidence file is read and parsed once, not once per game it covers (same rows, same order)."""
    if cache is None:
        return [x for x in PE.distributions(PE.read(path)) if x["game_id"] == gid]
    k = str(path)
    if k not in cache:
        by: dict = {}
        for x in PE.distributions(PE.read(path)):
            by.setdefault(x["game_id"], []).append(x)
        cache[k] = by
    return list(cache[k].get(gid, []))


def _official_pmfs(ops_root: Path, runs_root: Path, g, cache: dict | None = None) -> tuple[pd.DataFrame | None, str | None, list]:
    """The PMFs of one official (shadow-tick) prediction: the durable tick evidence ticks/<tick>/pmfs.parquet
    (prod.pmf_evidence), else the legacy run-directory market_pmfs.parquet. Returns (frame with game_label / player_label
    / team_id / stat / mean / pmf, source path, paths looked in)."""
    tick = Path(ops_root) / "ticks" / str(g.tick_id)
    durable = tick / PE.FILE
    cand = [durable, tick / "market_pmfs.parquet", Path(runs_root) / str(g.run_id) / "market_pmfs.parquet"]
    if durable.exists():
        return pd.DataFrame(_dists_of(durable, g.game_id, cache)), str(durable), cand
    src = next((p for p in cand[1:] if p.exists()), None)
    if src is None:
        return None, None, cand
    mp = pd.read_parquet(src)
    mp = mp[mp["game_id"] == g.game_id]
    lab = tick / "projections.parquet"
    lb = pd.read_parquet(lab).drop_duplicates(["game_id", "player_id"]).set_index(["game_id", "player_id"]) \
        if lab.exists() else pd.DataFrame()
    rows = []
    for r in mp.itertuples(index=False):
        k = (g.game_id, r.player_id)
        rows.append({"game_id": g.game_id, "player_id": r.player_id, "team_id": r.team_id, "stat": r.market,
                     "mean": _f(r.projection), "pmf": np.asarray(r.pmf, dtype=float),
                     "game_label": lb.loc[k, "game"] if len(lb) and k in lb.index else g.game_id,
                     "player_label": lb.loc[k, "player"] if len(lb) and k in lb.index else r.player_id})
    return pd.DataFrame(rows), str(src), cand


def forecast_sources(ops_root: Path, date: str) -> dict:
    """game_id -> [(prediction_as_of, cutoff, pmfs path)] of every line-independent forecast snapshot of ET date
    `date` (prod.lifecycle) that forecast the game BEFORE its production cutoff."""
    out: dict = {}
    for r in _jsonl(Path(ops_root) / "forecasts" / "index.jsonl"):
        if r.get("slate_date") != date or r.get("status") != "FORECAST" or not r.get("prediction_as_of"):
            continue
        p = Path(ops_root) / "forecasts" / date / r["snapshot_id"] / PE.FILE
        if not p.exists():
            continue
        aso = _utc(r["prediction_as_of"])
        for gid in r.get("games") or []:
            cut = _utc((r.get("cutoffs") or {}).get(gid))
            if cut is not None and aso <= cut:                    # at or before the cutoff (directive §5)
                out.setdefault(gid, []).append((aso, cut, p))
    return out


def distribution_rows(ops_root: Path, runs_root: Path, games: pd.DataFrame, names: dict,
                      date: str | None = None, audit: dict | None = None, pmf_out: list | None = None) -> tuple[list, list]:
    """(rows, missing): the legacy one-row-per-outcome view [GAME, TEAM, PLAYER, PROP, MEAN, OUTCOME, PROBABILITY] of
    distribution_records (the long derivative; the daily workbook is the summary)."""
    recs, missing = distribution_records(ops_root, runs_root, games, names, date, audit, pmf_out)
    return long_rows(recs), missing


def long_rows(recs: list) -> list:
    rows = []
    for x in recs:
        rows += [[x["game"], x["team"], x["player"], x["market"], x["mean"], int(o), float(p)]
                 for o, p in enumerate(x["pmf_trimmed"])]
    return rows


def distribution_records(ops_root: Path, runs_root: Path, games: pd.DataFrame, names: dict,
                         date: str | None = None, audit: dict | None = None, pmf_out: list | None = None) -> tuple[list, list]:
    """(records, missing): the complete PMFs of every eligible (game, player, stat) prediction of the slate. Per game the
    LATEST valid pre-cutoff prediction is used (directive §8): the official shadow-tick prediction when it exists (it
    is made inside [cutoff - lead, cutoff)), else the latest forecast snapshot made before the game's cutoff. PMFs are
    read from the durable evidence exactly as persisted (never recomputed). games: the PREDICTED registry rows.
    One record per (game, player, stat), in export order (tip, game, player, prop)."""
    missing, recs = [], []
    cache: dict = {}
    fsrc = forecast_sources(ops_root, date) if date else {}
    official = {g.game_id: g for g in games.itertuples(index=False)}
    order = {g.game_id: g.tip_utc for g in games.itertuples(index=False)}
    for gid in sorted(set(official) | set(fsrc)):
        best = None
        best_src = None
        if gid in official:
            g = official[gid]
            df, src, looked = _official_pmfs(ops_root, runs_root, g, cache)
            aso = _utc(getattr(g, "prediction_as_of", None))
            cut = _utc(getattr(g, "cutoff_utc", None))
            if df is not None and aso is not None and cut is not None and aso > cut:
                df = None                                         # post-cutoff official evidence is never used
                if gid not in fsrc:
                    missing.append({"game_id": gid, "run_id": g.run_id, "looked_in": ["post-cutoff prediction"]})
                    continue
            if df is not None:
                best = (aso or pd.Timestamp.max.tz_localize("UTC"), df, src)
                best_src = ("OFFICIAL_TICK", str(g.tick_id), cut, src)
            elif gid not in fsrc:
                missing.append({"game_id": gid, "run_id": g.run_id, "looked_in": [str(p) for p in looked]})
                continue
        for aso, cut, p in fsrc.get(gid, []):
            if best is None or (aso > best[0] and aso <= cut):
                best = (aso, pd.DataFrame(_dists_of(p, gid, cache)), str(p))
                best_src = ("FORECAST_SNAPSHOT", Path(p).parent.name, cut, str(p))
        if best is None or not len(best[1]):
            continue
        df = best[1]
        tip = order.get(gid)
        if tip is None:
            tip = min((c for _, c, _ in fsrc.get(gid, [])), default=None)
        b_aso = None if best[0] == pd.Timestamp.max.tz_localize("UTC") else best[0]
        for r in df.itertuples(index=False):
            eid = ENG.attribute(getattr(r, "engine_id", None), getattr(r, "prediction_as_of", None) or b_aso)
            pmf = np.asarray(r.pmf, dtype=float)
            _check_pmf(pmf, f"{gid} {r.player_id} {r.stat}")
            nz = np.flatnonzero(pmf)
            last = int(nz[-1]) if len(nz) else 0                 # trailing zeros only; the folded tail point stays
            recs.append({"tip": tip, "game": r.game_label, "player": r.player_label, "market": r.stat,
                         "team": names.get(r.team_id, r.team_id), "mean": _f(r.mean), "pmf_trimmed": pmf[: last + 1],
                         "key": (gid, r.player_id, r.stat), "pmf": pmf, "game_id": gid, "player_id": r.player_id,
                         "team_id": r.team_id, "model_version": getattr(r, "model_version", None), "engine_id": eid,
                         "source_kind": best_src[0], "forecast_id": best_src[1], "method": getattr(r, "method", None),
                         "game_label": r.game_label, "player_label": r.player_label, "pmf_source": best_src[3]})
            if pmf_out is not None and getattr(r, "method", None) != "NOT_PRICED":
                pmf_out.append({"game_id": gid, "player_id": r.player_id, "team_id": r.team_id, "stat": r.stat,
                                "mean": _f(r.mean), "pmf": pmf, "game_label": r.game_label, "player_label": r.player_label,
                                "tip": tip, "cutoff": best_src[2], "source_kind": best_src[0], "forecast_id": best_src[1],
                                "model_version": getattr(r, "model_version", None), "engine_id": eid,
                                "prediction_as_of": None if best[0] == pd.Timestamp.max.tz_localize("UTC") else best[0]})
    if audit is not None:                                        # identity keys are canonical ids, never display labels
        canon: dict = {}
        label: dict = {}
        for x in recs:
            canon[x["key"]] = canon.get(x["key"], 0) + 1
            label.setdefault((x["game"], x["player"], x["market"]), set()).add(x["key"])
        audit["canonical_duplicate_keys"] = sorted(list(k) for k, n in canon.items() if n > 1)
        audit["display_label_collisions"] = sorted([list(k), sorted(list(x) for x in v)] for k, v in label.items() if len(v) > 1)
    recs.sort(key=lambda x: (x["tip"] or pd.Timestamp.max.tz_localize("UTC"), x["game"], x["player"], x["market"]))
    return recs, missing


def record_mass_check(recs: list) -> dict:
    """pmf_mass_check over records (the exported vectors, the same values the long rows carry)."""
    worst = max((abs(float(np.sum(x["pmf_trimmed"])) - 1) for x in recs), default=0.0)
    neg = int(sum(int((x["pmf_trimmed"] < 0).sum()) for x in recs))
    return {"distributions": len(recs), "max_abs_mass_error": worst, "negative": neg,
            "PMF_RECONCILIATION": "PASS" if worst <= PMF_TOL and neg == 0 else "FAIL"}


def pmf_summary(pmf: np.ndarray) -> dict:
    """Summary of one PMF on support 0..len-1: mean, sd, quantiles (smallest k with CDF(k) >= q), max outcome with mass."""
    x = np.arange(len(pmf), dtype=float)
    mean = float((pmf * x).sum())
    sd = float(np.sqrt(max(float((pmf * (x - mean) ** 2).sum()), 0.0)))
    cdf = np.cumsum(pmf)
    q = {f"P{int(round(v * 100))}": int(min(np.searchsorted(cdf, v - 1e-12, side="left"), len(pmf) - 1))
         for v in DIST_PERCENTILES}
    nz = np.flatnonzero(pmf)
    return {"mean": mean, "sd": sd, **q, "max_outcome": int(nz[-1]) if len(nz) else 0}


def player_context(ops_root: Path, date: str | None, recs: list) -> dict:
    """(forecast_id, player_id) -> {p_play, min_mu, availability_status} from the forecast snapshot (or official tick)
    the exported PMF came from; absent fields stay blank (never estimated). An official tick persists its per-player
    context in forecast_players.parquet (ticks since 2026-11-18) or, before that, in its own projections.parquet (the
    same run's model_inputs min_mu / p_play) -- the context of THAT prediction, never another snapshot's."""
    out: dict = {}
    for fid in {(x["source_kind"], x["forecast_id"]) for x in recs}:
        kind, f = fid
        base = (Path(ops_root) / "forecasts" / str(date) / str(f)) if kind == "FORECAST_SNAPSHOT" else Path(ops_root) / "ticks" / str(f)
        p = base / "forecast_players.parquet"
        if not p.exists() and kind != "FORECAST_SNAPSHOT":
            p = base / "projections.parquet"
        if not p.exists():
            continue
        have = set(pq.read_schema(p).names)
        fp = pd.read_parquet(p, columns=[c for c in ("player_id", "p_play", "min_mu", "availability_status") if c in have])
        for r in fp.drop_duplicates("player_id").itertuples(index=False):
            out[(f, r.player_id)] = r._asdict()
    return out


def summary_rows(recs: list, ctx: dict) -> list:
    """One workbook row per (game, player, stat): DIST_SUMMARY_COLS."""
    teams: dict = {}
    for x in recs:
        teams.setdefault(x["game_id"], {})[x["team_id"]] = x["team"]
    rows = []
    for x in recs:
        sm = pmf_summary(np.asarray(x["pmf"], dtype=float))
        opp = [n for t, n in teams.get(x["game_id"], {}).items() if t != x["team_id"]]
        c = ctx.get((x["forecast_id"], x["player_id"]), {})
        rows.append([x["game"], x["team"], opp[0] if len(opp) == 1 else None, x["player"], x["market"], x["mean"],
                     sm["P50"], _f(sm["sd"]), sm["P10"], sm["P25"], sm["P75"], sm["P90"], sm["max_outcome"],
                     _f(c.get("p_play")), _f(c.get("min_mu")), c.get("availability_status"), x["model_version"],
                     x.get("engine_id"), x["source_kind"], x["forecast_id"], PCX.content_sha(0, np.asarray(x["pmf"], dtype=np.float64))])
    return rows


def pmf_frame(recs: list, ops_root: Path | None = None) -> pd.DataFrame:
    """The slate's exported PMFs as REFERENCES (no probability vectors): the canonical durable file each PMF lives in
    (absolute, and relative to ops_root when under it) and its content sha256 (prod.pmf_compact.content_sha). The PMF
    payload is never copied into the export tree (footprint directive section 5/6: one canonical payload)."""
    if not recs:
        return pd.DataFrame(columns=PMF_EXPORT_COLS)
    root = Path(ops_root).resolve() if ops_root is not None else None

    def rel(src):
        if root is None or not src:
            return None
        try:
            return str(Path(src).resolve().relative_to(root))
        except ValueError:
            return None
    return pd.DataFrame([{"game_id": x["game_id"], "player_id": x["player_id"], "team_id": x["team_id"],
                          "stat": x["market"], "game_label": x["game"], "player_label": x["player"], "team": x["team"],
                          "mean": x["mean"], "model_version": x["model_version"], "engine_id": x.get("engine_id"),
                          "source_kind": x["source_kind"],
                          "forecast_id": x["forecast_id"], "method": x["method"], "support_min": 0,
                          "support_max": len(x["pmf"]) - 1, "pmf_source": str(x.get("pmf_source") or ""),
                          "pmf_source_rel": rel(x.get("pmf_source")),
                          "pmf_sha256": PCX.content_sha(0, np.asarray(x["pmf"], dtype=np.float64))} for x in recs])[list(PMF_EXPORT_COLS)]


PMF_EXPORT_COLS = ("game_id", "player_id", "team_id", "stat", "game_label", "player_label", "team", "mean",
                   "model_version", "engine_id", "source_kind", "forecast_id", "method", "support_min", "support_max",
                   "pmf_source", "pmf_source_rel", "pmf_sha256")


def resolve_pmf_refs(df: pd.DataFrame, ops_root: Path | None = None) -> pd.DataFrame:
    """References -> the same frame plus `probabilities`, read from the canonical durable files (each file read once)
    and re-hashed against pmf_sha256. A missing file, a missing PMF or a hash mismatch is an ExportIntegrityError."""
    if "probabilities" in df.columns:                             # a pre-reference export (vectors stored inline)
        return df
    files: dict = {}
    vecs = []
    for r in df.itertuples(index=False):
        cand = ([Path(ops_root) / r.pmf_source_rel] if ops_root is not None and r.pmf_source_rel else []) + \
               ([Path(r.pmf_source)] if r.pmf_source else [])
        src = next((p for p in cand if p.exists()), None)
        if src is None:
            raise ExportIntegrityError(f"canonical PMF file absent for {r.game_id} {r.player_id} {r.stat}: {cand}")
        k = str(src)
        if k not in files:
            if src.name == PE.FILE:
                files[k] = {(x["game_id"], x["player_id"], x["stat"]): x["pmf"] for x in PE.distributions(PE.read(src))}
            else:                                                 # legacy run market_pmfs.parquet
                mp = pd.read_parquet(src)
                files[k] = {(m.game_id, m.player_id, m.market): np.asarray(m.pmf, dtype=float) for m in mp.itertuples()}
        pmf = files[k].get((r.game_id, r.player_id, r.stat))
        if pmf is None:
            raise ExportIntegrityError(f"{src}: no PMF for {r.game_id} {r.player_id} {r.stat}")
        pmf = np.asarray(pmf, dtype=np.float64)
        if PCX.content_sha(0, pmf) != r.pmf_sha256:
            raise ExportIntegrityError(f"{src}: PMF content hash mismatch for {r.game_id} {r.player_id} {r.stat}")
        vecs.append(pmf)
    return df.assign(probabilities=vecs)


def records_from_pmf_frame(df: pd.DataFrame, ops_root: Path | None = None) -> list:
    """Inverse of pmf_frame for the long derivative (same order, same trimmed vectors as the daily long rows)."""
    out = []
    for r in resolve_pmf_refs(df, ops_root).itertuples(index=False):
        pmf = np.asarray(r.probabilities, dtype=np.float64)
        nz = np.flatnonzero(pmf)
        out.append({"game": r.game_label, "team": r.team, "player": r.player_label, "market": r.stat, "mean": r.mean,
                    "pmf_trimmed": pmf[: (int(nz[-1]) if len(nz) else 0) + 1]})
    return out


def pmf_mass_check(rows: list) -> dict:
    """Mass of every exported distribution. A distribution is the run of rows opened by OUTCOME 0 (distribution_rows
    writes each canonical (game, player_id, stat) PMF as outcomes 0..k): two players sharing a display name are two
    distributions, never one summed key."""
    sums: list = []
    neg = 0
    for _, _, _, _, _, outcome, p in rows:
        if outcome == 0 or not sums:
            sums.append(0.0)
        sums[-1] += p
        neg += p < 0
    worst = max((abs(v - 1) for v in sums), default=0.0)
    return {"distributions": len(sums), "max_abs_mass_error": worst, "negative": int(neg),
            "PMF_RECONCILIATION": "PASS" if worst <= PMF_TOL and neg == 0 else "FAIL"}


# ============================================================================ consensus Board (forecast universe)
def snapshot_tips(ops_root: Path, date: str) -> dict:
    """game_id -> scheduled tip of every forecast snapshot of ET date `date` (snapshot.json scheduled_tips)."""
    out = {}
    for f in sorted((Path(ops_root) / "forecasts" / date).glob("*/snapshot.json")):
        try:
            out.update({g: t for g, t in (json.loads(f.read_text()).get("scheduled_tips") or {}).items() if t})
        except (OSError, ValueError):
            continue
    return out


def _writers_sha(*mods) -> str:
    h = hashlib.sha256(Path(__file__).read_bytes())
    for m in mods:
        h.update(Path(m.__file__).read_bytes())
    return h.hexdigest()


def write_if_changed(path: Path, receipt_name: str, content, build, writer_sha: str, extra: dict | None = None) -> tuple[Path, bool]:
    """Write `path` (build(path)) unless its receipt records exactly this content hash, this writer and the file's
    current sha256. Returns (path, written)."""
    path = Path(path)
    rec_p = path.with_name(receipt_name)
    want = {"file": path.name, "content_sha256": hashlib.sha256(json.dumps(content, default=str).encode()).hexdigest(),
            "writer_sha256": writer_sha}
    try:
        rec = json.loads(rec_p.read_text())
    except (OSError, ValueError):
        rec = None
    if rec and all(rec.get(k) == v for k, v in want.items()) and path.exists() and _file_sha(path) == rec.get("xlsx_sha256"):
        return path, False
    p = build(path)
    tmp = rec_p.with_name(rec_p.name + ".tmp")
    tmp.write_text(json.dumps({**want, **(extra or {}), "xlsx_sha256": _file_sha(p)}, sort_keys=True, indent=1) + "\n")
    os.replace(tmp, rec_p)
    return p, True


def _retire_legacy_grades(out_dir: Path, date: str, current: Path) -> str | None:
    """Before 2026-11-21 the Grades workbook of folder D was misnamed CBB_Grades_<D>.xlsx although it grades D-1. When
    folder D is re-exported under the corrected name, the misnamed copy (same content contract, same folder) is removed
    so the folder holds exactly one Grades workbook. Only that exact legacy name in this folder is ever touched, and
    only after the correctly named workbook exists and its receipt names it."""
    legacy = Path(out_dir) / f"CBB_Grades_{_tag(pd.Timestamp(date))}.xlsx"
    if legacy == Path(current) or not legacy.exists() or not Path(current).exists():
        return None
    try:
        if json.loads((Path(out_dir) / GRADE_RECEIPT).read_text()).get("file") != Path(current).name:
            return None
    except (OSError, ValueError):
        return None
    legacy.unlink()
    return legacy.name


DIST_RECEIPT = "distributions_receipt.json"


def _rows_sha(rows: list) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(r).encode())
        h.update(b"\n")
    return h.hexdigest()


def _writer_sha() -> str:
    """The workbook bytes are a pure function of the rows and this module (write_xlsx is deterministic)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def distributions_if_changed(rows: list, path: Path, frame: pd.DataFrame | None = None,
                             pmf_path: Path | None = None) -> tuple[Path, bool]:
    """Write the Distributions summary workbook (and the compact PMF parquet beside it) unless the existing files were
    written from exactly these rows / PMFs by this writer (the receipt records rows / writer / file sha256s and the files
    still hash to them). Returns (path, written)."""
    path = Path(path)
    rec_p = path.with_name(DIST_RECEIPT)
    want = {"file": path.name, "rows_sha256": _rows_sha(rows), "writer_sha256": _writer_sha(), "rows": len(rows)}
    if frame is not None:
        want["pmf_content_sha256"] = hashlib.sha256("".join(frame["pmf_sha256"] + frame["pmf_source"].astype(str)).encode()).hexdigest()
    try:
        rec = json.loads(rec_p.read_text())
    except (OSError, ValueError):
        rec = None
    same = rec and all(rec.get(k) == v for k, v in want.items()) and path.exists() and _file_sha(path) == rec.get("xlsx_sha256")
    if same and pmf_path is not None:
        same = Path(pmf_path).exists() and _file_sha(Path(pmf_path)) == rec.get("pmf_parquet_sha256")
    if same:
        return path, False
    extra = {}
    if frame is not None and pmf_path is not None:
        tmp = Path(pmf_path).with_name(Path(pmf_path).name + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, pmf_path)
        extra = {"pmf_file": Path(pmf_path).name, "pmf_parquet_sha256": _file_sha(Path(pmf_path))}
    p = distributions_summary_workbook(rows, path) if frame is not None else distributions_workbook(rows, path)
    tmp = rec_p.with_name(rec_p.name + ".tmp")
    tmp.write_text(json.dumps({**want, **extra, "xlsx_sha256": _file_sha(p)}, sort_keys=True, indent=1) + "\n")
    os.replace(tmp, rec_p)
    return p, True


# ============================================================================ minimal deterministic XLSX (stdlib)
def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


class _Styles:
    BUILTIN = {"General": 0, "0": 1}

    def __init__(self):
        self.fmts: dict = {}
        self.xfs = [(0, 0)]                                       # (font, numFmtId); 0 = default

    def xf(self, fmt: str | None, bold: bool = False) -> int:
        fmt = fmt or "General"
        fid = self.BUILTIN.get(fmt)
        if fid is None:
            fid = self.fmts.setdefault(fmt, 164 + len(self.fmts))
        key = (1 if bold else 0, fid)
        if key not in self.xfs:
            self.xfs.append(key)
        return self.xfs.index(key)

    def xml(self) -> str:
        nf = "".join(f'<numFmt numFmtId="{i}" formatCode="{escape(f, {chr(34): "&quot;"})}"/>' for f, i in self.fmts.items())
        xfs = "".join(f'<xf numFmtId="{n}" fontId="{f}" fillId="0" borderId="0" xfId="0"'
                      f'{" applyNumberFormat=" + chr(34) + "1" + chr(34) if n else ""}{" applyFont=" + chr(34) + "1" + chr(34) if f else ""}/>'
                      for f, n in self.xfs)
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<numFmts count="{len(self.fmts)}">{nf}</numFmts>'
                '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
                '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                f'<cellXfs count="{len(self.xfs)}">{xfs}</cellXfs>'
                '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                '</styleSheet>')


def _cell(ref: str, v, style: int) -> str:
    s = f' s="{style}"' if style else ""
    if v is None or (isinstance(v, (float, np.floating)) and not math.isfinite(v)):
        return f'<c r="{ref}"{s}/>' if style else ""            # export contract: non-finite -> blank cell
    if isinstance(v, (bool, np.bool_)):
        v = str(bool(v)).upper()
    if isinstance(v, (int, np.integer)):
        return f'<c r="{ref}"{s}><v>{int(v)}</v></c>'
    if isinstance(v, (float, np.floating)):
        return f'<c r="{ref}"{s}><v>{repr(float(v))}</v></c>'
    t = escape(str(v))
    sp = ' xml:space="preserve"' if t != t.strip() else ""
    return f'<c r="{ref}"{s} t="inlineStr"><is><t{sp}>{t}</t></is></c>'


def _width(v) -> int:
    if v is None:
        return 0
    if isinstance(v, float):
        return len(f"{v:.4f}")
    return len(str(v))


def write_xlsx(path: Path, sheets: list[dict]) -> Path:
    """sheets: [{"name", "blocks": [{"title"|None, "columns", "rows"}], "table_filters": bool}]. Bold headers, frozen
    pane below the first header, AutoFilter (sheet filter for one-table sheets, a filtered table per section
    otherwise), widths capped at 40, numeric cells numeric. Deterministic bytes (fixed zip timestamps)."""
    styles = _Styles()
    files, wb_sheets, rels, ctypes, defined = {}, [], [], [], []
    table_id = 0
    for si, sh in enumerate(sheets, 1):
        rows_xml, widths, tables = [], {}, []
        r = 0
        freeze = None
        sheet_filter = None
        for bi, b in enumerate(sh["blocks"]):
            if bi:
                r += 2                                            # exactly two blank rows between sections
            cols = list(b["columns"])
            if b.get("title"):
                r += 1
                rows_xml.append(f'<row r="{r}">{_cell(f"A{r}", b["title"], styles.xf(None, True))}</row>')
            r += 1
            hdr = r
            rows_xml.append(f'<row r="{r}">' + "".join(_cell(f"{_col(i)}{r}", c, styles.xf(None, True))
                                                       for i, c in enumerate(cols)) + "</row>")
            for i, c in enumerate(cols):
                widths[i] = max(widths.get(i, 0), len(c))
            fm = {**FORMATS, **(b.get("formats") or {})}                   # block-level formats (Performance)
            xf = [styles.xf(fm.get(c)) for c in cols]
            for row in b["rows"]:
                r += 1
                rows_xml.append(f'<row r="{r}">' + "".join(_cell(f"{_col(i)}{r}", v, xf[i] if v is not None else 0)
                                                           for i, v in enumerate(row)) + "</row>")
                for i, v in enumerate(row):
                    widths[i] = max(widths.get(i, 0), _width(v))
            if r > EXCEL_MAX_ROWS or len(cols) > EXCEL_MAX_COLS:
                raise ExportIntegrityError(f"sheet {sh['name']!r}: {r} rows x {len(cols)} columns exceeds the Excel "
                                           f"worksheet limit {EXCEL_MAX_ROWS} x {EXCEL_MAX_COLS}")
            last = f"{_col(len(cols) - 1)}{r}"
            if freeze is None:
                freeze = hdr
            if sh.get("table_filters"):
                if b["rows"]:
                    table_id += 1
                    tname = f"T{table_id}_" + "".join(ch for ch in (b.get("title") or sh["name"]) if ch.isalnum())
                    tables.append((table_id, tname, f"A{hdr}:{last}", cols))
            elif sheet_filter is None:
                sheet_filter = f"A{hdr}:{last}"
        cols_xml = "<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{min(40, w + 2)}" customWidth="1"/>'
                                      for i, w in sorted(widths.items())) + "</cols>" if widths else ""
        pane = (f'<pane ySplit="{freeze}" topLeftCell="A{freeze + 1}" activePane="bottomLeft" state="frozen"/>'
                f'<selection pane="bottomLeft" activeCell="A{freeze + 1}" sqref="A{freeze + 1}"/>') if freeze else ""
        xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
               f'<sheetViews><sheetView workbookViewId="0"{" tabSelected=" + chr(34) + "1" + chr(34) if si == 1 else ""}>{pane}</sheetView></sheetViews>'
               '<sheetFormatPr defaultRowHeight="15"/>' + cols_xml +
               "<sheetData>" + "".join(rows_xml) + "</sheetData>" +
               (f'<autoFilter ref="{sheet_filter}"/>' if sheet_filter else ""))
        srels = []
        if tables:
            xml += f'<tableParts count="{len(tables)}">' + "".join(
                f'<tablePart r:id="rId{k}"/>' for k in range(1, len(tables) + 1)) + "</tableParts>"
            for k, (tid, tname, ref, cols) in enumerate(tables, 1):
                srels.append(f'<Relationship Id="rId{k}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                             f'relationships/table" Target="../tables/table{tid}.xml"/>')
                tc = "".join(f'<tableColumn id="{j}" name="{escape(c, {chr(34): "&quot;"})}"/>' for j, c in enumerate(cols, 1))
                files[f"xl/tables/table{tid}.xml"] = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    f'<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" id="{tid}" name="{tname}" '
                    f'displayName="{tname}" ref="{ref}" totalsRowShown="0"><autoFilter ref="{ref}"/>'
                    f'<tableColumns count="{len(cols)}">{tc}</tableColumns>'
                    '<tableStyleInfo showFirstColumn="0" showLastColumn="0" showRowStripes="0" showColumnStripes="0"/></table>')
                ctypes.append(f'<Override PartName="/xl/tables/table{tid}.xml" ContentType="application/'
                              'vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>')
        xml += "</worksheet>"
        files[f"xl/worksheets/sheet{si}.xml"] = xml
        if srels:
            files[f"xl/worksheets/_rels/sheet{si}.xml.rels"] = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(srels) + "</Relationships>")
        if sheet_filter:
            nm = escape(sh["name"]).replace("'", "''")
            absref = ":".join("$" + "".join(ch for ch in x if ch.isalpha()) + "$" + "".join(ch for ch in x if ch.isdigit())
                              for x in sheet_filter.split(":"))
            defined.append(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{si - 1}" hidden="1">'
                           f"'{nm}'!{absref}</definedName>")
        wb_sheets.append(f'<sheet name="{escape(sh["name"])}" sheetId="{si}" r:id="rId{si}"/>')
        rels.append(f'<Relationship Id="rId{si}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                    f'relationships/worksheet" Target="worksheets/sheet{si}.xml"/>')
        ctypes.append(f'<Override PartName="/xl/worksheets/sheet{si}.xml" ContentType="application/'
                      'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    n = len(sheets)
    rels.append(f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/styles" Target="styles.xml"/>')
    files["xl/workbook.xml"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                                "<sheets>" + "".join(wb_sheets) + "</sheets>"
                                + (f"<definedNames>{''.join(defined)}</definedNames>" if defined else "") + "</workbook>")
    files["xl/_rels/workbook.xml.rels"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                                           '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                           + "".join(rels) + "</Relationships>")
    files["xl/styles.xml"] = styles.xml()
    files["_rels/.rels"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    files["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'spreadsheetml.styles+xml"/>' + "".join(ctypes) + "</Types>")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    order = ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"]
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in order + sorted(k for k in files if k not in order):
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, files[name])
    os.replace(tmp, path)
    return path


def read_xlsx(path: Path) -> dict:
    """{sheet name: {"rows": [[values]], "styles": {ref: numFmt}, "bold": set(refs), "freeze": ref, "filters": [refs],
    "widths": {col: width}}} -- semantic reader for tests / idempotence (inline strings + numbers)."""
    import re
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    out = {}
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        st = ET.fromstring(z.read("xl/styles.xml"))
        fm = {int(n.get("numFmtId")): n.get("formatCode") for n in (st.find("m:numFmts", ns) if st.find("m:numFmts", ns) is not None else [])}
        fm.update({0: "General", 1: "0"})
        xfs = [(int(x.get("fontId")), fm[int(x.get("numFmtId"))]) for x in st.find("m:cellXfs", ns)]
        for i, s in enumerate(wb.find("m:sheets", ns), 1):
            ws = ET.fromstring(z.read(f"xl/worksheets/sheet{i}.xml"))
            grid: dict = {}
            fmts, bold = {}, set()
            for c in ws.iter(f"{{{ns['m']}}}c"):
                ref = c.get("r")
                m = re.match(r"([A-Z]+)(\d+)", ref)
                col = sum((ord(ch) - 64) * 26 ** k for k, ch in enumerate(reversed(m.group(1)))) - 1
                row = int(m.group(2)) - 1
                sidx = int(c.get("s", 0))
                font, f = xfs[sidx]
                if font:
                    bold.add(ref)
                fmts[ref] = f
                if c.get("t") == "inlineStr":
                    v = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
                else:
                    vv = c.find("m:v", ns)
                    if vv is None:
                        v = None
                    else:
                        v = float(vv.text)
                        v = int(v) if f in ("0",) and v.is_integer() else v
                grid.setdefault(row, {})[col] = v
            nrow = max(grid) + 1 if grid else 0
            rows = []
            for rr in range(nrow):
                d = grid.get(rr, {})
                rows.append([d.get(cc) for cc in range(max(d) + 1)] if d else [])
            pane = ws.find(".//m:pane", ns)
            filters = [a.get("ref") for a in ws.findall("m:autoFilter", ns)]
            rels = f"xl/worksheets/_rels/sheet{i}.xml.rels"
            if rels in z.namelist():
                for rel in ET.fromstring(z.read(rels)):
                    t = ET.fromstring(z.read("xl/" + rel.get("Target").replace("../", "")))
                    filters.append(t.find("m:autoFilter", ns).get("ref"))
            widths = {int(c.get("min")) - 1: float(c.get("width")) for c in ws.iter(f"{{{ns['m']}}}col")}
            out[s.get("name")] = {"rows": rows, "styles": fmts, "bold": bold,
                                  "freeze": None if pane is None else pane.get("topLeftCell"), "filters": filters,
                                  "widths": widths}
    return out


def semantic(path: Path) -> list:
    """Sheet names, order and every cell value: the idempotence comparison (bytes are not compared)."""
    return [(name, s["rows"]) for name, s in read_xlsx(path).items()]


# ============================================================================ workbooks
def distribution_sheets(rows: list, max_rows: int | None = None) -> list[list]:
    """Split the Distributions rows into consecutive sheets of at most `max_rows` data rows (plus the header) without
    ever splitting one (GAME, PLAYER, PROP) distribution; row order and every value are unchanged."""
    max_rows = EXCEL_MAX_ROWS - 1 if max_rows is None else max_rows      # header row + data rows <= EXCEL_MAX_ROWS
    sheets, cur, start = [], [], 0
    for i, row in enumerate(rows):
        if i and (row[0], row[2], row[3]) != (rows[i - 1][0], rows[i - 1][2], rows[i - 1][3]):
            start = len(cur)                                     # index of the current distribution's first row
        cur.append(row)
        if len(cur) > max_rows:
            if start == 0:
                raise ExportIntegrityError(f"one distribution has more than {max_rows} outcomes")
            sheets.append(cur[:start])
            cur, start = cur[start:], 0
    return sheets + [cur] if cur or not sheets else sheets


def distributions_summary_workbook(rows: list, path: Path) -> Path:
    """One row per (game, player, stat); the full PMFs are in the compact parquet beside it."""
    return write_xlsx(path, [{"name": DIST_SHEET, "blocks": [{"title": None, "columns": DIST_SUMMARY_COLS, "rows": rows}]}])


def expand_distributions(date: str, out_root: Path = None, ops_root: Path = None) -> Path:
    """The explicitly requested long derivative: CBB_Distributions_<date>_LONG.xlsx (one row per outcome, the legacy
    layout), DERIVED ON DEMAND from the slate's PMF references resolved against the canonical durable PMF files (each
    vector verified against its sha256; nothing recomputed)."""
    date = str(pd.Timestamp(date).date())
    out_dir = Path(out_root if out_root is not None else EXPORT_ROOT) / date
    n = file_names(date)
    df = pd.read_parquet(out_dir / n["distribution_pmfs"])
    return distributions_workbook(long_rows(records_from_pmf_frame(df, ops_root)), out_dir / n["distributions_long"])


def migrate_distributions(date: str, *, ops_root: Path = PC.OPS_ROOT, runs_root: Path = RUNS_ROOT,
                          out_root: Path = EXPORT_ROOT, history_db: Path = PC.HISTORY_CANONICAL,
                          state_root: Path = PC.STATE_ROOT) -> dict:
    """Replace a past date's exploded (one-row-per-outcome) Distributions workbook by the summary workbook + PMF
    references, ONLY when the long form derived from the references reproduces the existing workbook exactly (every
    sheet, every cell value, in order). Built in a scratch directory first; the old workbook is replaced atomically;
    on any difference nothing changes and MISMATCH is returned. Boards / Grades are not touched."""
    import shutil
    date = str(pd.Timestamp(date).date())
    out_dir, n = Path(out_root) / date, file_names(date)
    old = out_dir / n["distributions"]
    if not old.exists():
        return {"date": date, "status": "NO_WORKBOOK"}
    first = read_xlsx(old)
    if not first or next(iter(first.values()))["rows"][:1] != [list(DIST_COLS)]:
        return {"date": date, "status": "ALREADY_SUMMARY"}
    old_rows = [r for s_ in first.values() for r in s_["rows"][1:]]
    old_bytes = old.stat().st_size
    del first
    games = slate_games(ops_root, date)
    names = team_names(history_db, state_root)
    audit: dict = {}
    recs, missing = distribution_records(ops_root, runs_root, games, names, date, audit=audit)
    if missing or not recs or audit["canonical_duplicate_keys"]:
        return {"date": date, "status": "MISSING_EVIDENCE", "missing": missing[:5]}
    tmp_root = out_dir.parent / f".migrate-{date}"
    shutil.rmtree(tmp_root, ignore_errors=True)
    try:
        tdir = tmp_root / date
        tdir.mkdir(parents=True)
        rows = summary_rows(recs, player_context(ops_root, date, recs))
        frame = pmf_frame(recs, ops_root)
        distributions_if_changed(rows, tdir / n["distributions"], frame, tdir / n["distribution_pmfs"])
        derived = read_xlsx(expand_distributions(date, tmp_root, ops_root))
        new_rows = [r for s_ in derived.values() for r in s_["rows"][1:]]
        if new_rows != old_rows:
            return {"date": date, "status": "MISMATCH", "old_rows": len(old_rows), "derived_rows": len(new_rows)}
        for f in (n["distribution_pmfs"], DIST_RECEIPT, n["distributions"]):     # the workbook last
            os.replace(tdir / f, out_dir / f)
        return {"date": date, "status": "MIGRATED", "long_rows_verified": len(old_rows), "summary_rows": len(rows),
                "old_bytes": old_bytes, "new_bytes": (out_dir / n["distributions"]).stat().st_size
                + (out_dir / n["distribution_pmfs"]).stat().st_size}
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def distributions_workbook(rows: list, path: Path) -> Path:
    parts = distribution_sheets(rows)
    return write_xlsx(path, [{"name": DIST_SHEET if k == 0 else f"{DIST_SHEET} {k + 1}",
                              "blocks": [{"title": None, "columns": DIST_COLS, "rows": part}]}
                             for k, part in enumerate(parts)])


def daily(date: str, *, ops_root: Path = PC.OPS_ROOT, runs_root: Path = RUNS_ROOT, out_root: Path = EXPORT_ROOT,
          history_db: Path | None = PC.HISTORY_CANONICAL, state_root: Path | None = PC.STATE_ROOT,
          labels=LIVE_LABELS, now=None) -> dict:
    """Generate the three workbooks of slate date `date` (America/New_York) from persisted evidence only; nothing is
    fetched, fitted or simulated. Each workbook is rewritten only when its effective content changed (receipts).

    Boards        the forecast universe: one row per (game, player, stat) persisted PMF (the same PMF Distributions
                  exports), market consensus from the latest pre-cutoff quote observation (blank when none is valid)
    Distributions the persisted PMFs
    Grades        the official (frozen) best-offer selections and results; GRADE_STATUS NOTHING_SETTLED_YET until one
                  settles"""
    from cbb_platform.prod import consensus as CN, quote_obs as QO
    date = str(pd.Timestamp(date).date())
    out_dir = Path(out_root) / date
    names_ = file_names(date)
    at = _utc(now) or pd.Timestamp.now(tz="UTC")
    rep = {"version": EXPORT_VERSION, "date": date, "timezone": TZ, "out_dir": str(out_dir), "network_calls": 0,
           "refits_triggered": 0}
    games = slate_games(ops_root, date)
    rep["predicted_games"] = int(len(games))
    names = team_names(history_db, state_root)
    audit: dict = {}
    pmfs: list = []
    recs, missing = distribution_records(ops_root, runs_root, games, names, date, audit=audit, pmf_out=pmfs)
    pctx = player_context(ops_root, date, recs) if recs else {}
    rows = summary_rows(recs, pctx) if recs else []
    if rows and not missing:
        pq_path = out_dir / names_["distribution_pmfs"]
        frame = pmf_frame(recs, ops_root)
        p, written = distributions_if_changed(rows, out_dir / names_["distributions"], frame, pq_path)
        mass = record_mass_check(recs)
        if audit["canonical_duplicate_keys"]:
            mass["PMF_RECONCILIATION"] = "FAIL"                  # one canonical prediction exported twice
        rep["distributions"] = {"status": "WRITTEN", "path": str(p), "rows": len(rows), "rewritten": written, **mass,
                                "pmf_refs_path": str(pq_path), "pmf_refs": int(len(frame)),
                                "outcome_rows_represented": int(sum(len(x["pmf_trimmed"]) for x in recs)),
                                "canonical_duplicate_keys": len(audit["canonical_duplicate_keys"]),
                                "display_label_collisions": audit["display_label_collisions"][:20]}
    elif rows:
        rep["distributions"] = {"status": "MISSING_EVIDENCE",
                                "missing": f"durable PMFs absent for {len(missing)} predicted game(s)", "games": missing[:20]}
    else:
        rep["distributions"] = {"status": "MISSING_EVIDENCE",
                                "missing": f"no durable PMF evidence (official tick pmfs.parquet or pre-cutoff forecast "
                                           f"snapshot) for ET slate date {date}", "games": missing[:20]}
    # ---------------------------------------------------------------- Boards: forecast universe x exact-line market
    from cbb_platform.prod import market_eval as ME, market_exports as MX
    cutoffs_all = _cutoffs(pmfs, games, ops_root, date)
    obs = QO.latest(ops_root, date, cutoffs_all, at)
    rep["quote_observations"] = {"games": len(obs), "rows": int(sum(len(q) for _, q in obs.values()))}
    rep["raw_quote_rows"] = rep["quote_observations"]["rows"]
    if pmfs:
        brows, evals, baudit, cnt = MX.board(pmfs, obs, names, pctx, at)
        rep["board_keys"] = cnt
        try:
            MX.assert_unique(brows)
        except MX.MarketExportIntegrityError:
            cnt["duplicate_keys"] = max(1, cnt["duplicate_keys"])
        if cnt["duplicate_keys"]:
            rep["boards"] = {"status": "ABORTED_DUPLICATE_BOARD_KEYS", "duplicate_keys": cnt["duplicate_keys"],
                             "duplicates": cnt["duplicates"],
                             "missing": "DUPLICATE_BOARD_KEYS > 0: export aborted (defect; never silently deduplicated)"}
        else:
            receipt = {**{k: v for k, v in cnt.items() if k != "duplicates"}, **MX.ingestion_totals(ops_root, date),
                       "board_as_of": at.isoformat(), "thresholds": {
                           "min_ev": ME.ACTION_MIN_EV, "min_edge": ME.ACTION_MIN_EDGE,
                           "min_model_probability": ME.ACTION_MIN_MODEL_PROBABILITY,
                           "reliable_min_books": ME.RELIABLE_MIN_BOOKS}, "best_offer_order": list(ME.ORDER)}
            glines = game_lines(date, ops_root, state_root, names, at)    # Release B (SHADOW); None when off
            rep["game_lines"] = {k: v for k, v in (glines or {"status": "NOT_ENABLED"}).items() if k != "rows"}
            if glines is not None:
                receipt["game_lines"] = rep["game_lines"]
            content = brows if glines is None else [brows, glines["rows"]]
            p, written = write_if_changed(out_dir / names_["boards"], BOARD_RECEIPT, content,
                                          lambda path: MX.boards_workbook(brows, path) if glines is None else
                                          boards_shadow_workbook(brows, glines["rows"], path),
                                          _writers_sha(CN, QO, MX, ME), receipt)
            if written:
                tmp = out_dir / (BOARD_AUDIT + ".tmp")
                with gzip.open(tmp, "wt") as f:
                    for x in baudit:
                        f.write(json.dumps(x, sort_keys=True, default=str) + "\n")
                os.replace(tmp, out_dir / BOARD_AUDIT)
            sh = MX.board_sheets(brows)
            rep["boards"] = {"status": "WRITTEN", "path": str(p), "rows": len(brows), "rewritten": written,
                             "unique_player_stat_keys": cnt["unique_keys"], "duplicate_keys": 0,
                             "rows_with_valid_market": cnt["valid_market"],
                             "rows_with_no_valid_market": cnt["model_only_rows"],
                             "audit": str(out_dir / BOARD_AUDIT), "audit_rows": cnt["audit_rows"],
                             "receipt": receipt, **{sname: len(sh[sname]) for sname in MX.BOARD_SHEETS}}
            rep["evaluations"] = MX.freeze(ops_root, date, pmfs, names, pctx, cutoffs_all, at)
            rep["public_contract"] = public_contract(out_dir, date, at, brows, glines)
    else:
        rep["boards"] = {"status": "MISSING_EVIDENCE",
                         "missing": f"no persisted PMF (forecast snapshot / official tick) for ET slate date {date}"}
    # ---------------------------------------------------------------- Grades: D grades the D-1 ET slate
    prior = MX.prior_day(date)
    rep["prior_day_evaluations"] = _freeze_day(prior, ops_root, runs_root, names, at)
    try:
        grows, ginfo = MX.graded_rows(ops_root, prior, at)
        sheets = MX.grades_tables(grows, prior, ginfo)
        gg = game_grades(prior, ops_root, state_root, names)
        rep["game_grades"] = {k: v for k, v in (gg or {"status": "NOT_ENABLED"}).items() if k not in ("rows",)}
        if gg is not None:
            sheets = grades_shadow_sheets(sheets, grows, gg["rows"])
        p, written = write_if_changed(out_dir / names_["grades"], GRADE_RECEIPT,
                                      [[b["rows"] for b in sh_["blocks"]] for sh_ in sheets],
                                      lambda path: write_xlsx(path, sheets), _writers_sha(MX, ME),
                                      extra={"export_date": date, "graded_slate_date": prior,
                                             "EXPORT_DATE": date, "GRADED_SLATE_DATE": prior})
        legacy = _retire_legacy_grades(out_dir, date, p)
        m = MX.metrics(grows)
        rep["grades"] = {"status": "WRITTEN", "path": str(p), "rows": len(grows), "rewritten": written,
                         "graded_slate_date": prior, "GRADE_STATUS": MX.grade_status(grows),
                         "results": pd.Series([r["result"] for r in grows]).value_counts().to_dict() if grows else {},
                         "graded": m["Graded"], "void_dnp": m["VOID_DNP"], "pending": m["Pending"],
                         "games_frozen": ginfo.get("games_frozen", 0), "export_date": date,
                         "file": p.name, **({"retired_legacy_file": legacy} if legacy else {})}
    except MX.MarketExportIntegrityError as e:
        rep["grades"] = {"status": "ABORTED_INTEGRITY", "missing": str(e)[:400]}
    rep["performance"] = _performance(date, ops_root, out_dir, at)        # after Grades (same graded rows)
    if _game_mode():                                                       # Release B: separate game population
        from cbb_platform.prod import game_exports as GX
        try:
            rep["game_performance"] = GX.game_performance(date, ops_root, out_dir,
                                                          lambda ids: _finals(state_root, ids), names,
                                                          conf_of=lambda ids: _conference_flags(state_root, ids))
        except Exception as e:                                             # noqa: BLE001 - never breaks player exports
            rep["game_performance"] = {"status": "FAILED", "missing": f"{type(e).__name__}: {str(e)[:300]}"}
    return rep


# ============================================================================ Release B game markets (SHADOW)
def _game_mode() -> bool:
    from cbb_platform.game import forecast as GF
    return GF.mode() == "SHADOW"


def _finals(state_root: Path | None, ids) -> dict:
    """{game_id: (home_points, away_points)} of completed games in the promoted state (OT-inclusive final score)."""
    from cbb_platform.prod import state as ST
    st = ST.current(state_root) if state_root else None
    if st is None or not ids:
        return {}
    import duckdb
    con = duckdb.connect(str(st["increment_db"]), read_only=True)
    try:
        df = con.execute("select canonical_game_id, home_points, away_points from flagship.games where "
                         "canonical_game_id in (select unnest(?)) and home_points is not null", [list(ids)]).df()
    finally:
        con.close()
    return {g: (int(h), int(a)) for g, h, a in zip(df["canonical_game_id"], df["home_points"], df["away_points"])}


def _conference_flags(state_root: Path | None, ids) -> dict:
    """{game_id: True | False} from flagship.games.conference_game (None-valued games are left out)."""
    from cbb_platform.prod import state as ST
    st = ST.current(state_root) if state_root else None
    if st is None or not ids:
        return {}
    import duckdb
    con = duckdb.connect(str(st["increment_db"]), read_only=True)
    try:
        df = con.execute("select canonical_game_id, conference_game from flagship.games where "
                         "canonical_game_id in (select unnest(?)) and conference_game is not null", [list(ids)]).df()
    finally:
        con.close()
    return {g: bool(c) for g, c in zip(df["canonical_game_id"], df["conference_game"])}


def game_lines(date: str, ops_root: Path, state_root, names: dict, at) -> dict | None:
    """Boards `Game Lines` rows (best executable offer per game x core market) + the write-once freeze of every game
    whose cutoff passed. None when CBB_GAME_MODEL is not SHADOW."""
    if not _game_mode():
        return None
    from cbb_platform.prod import game_exports as GX, game_quotes as GQ
    pm = GX.game_pmfs(ops_root, date, at)
    q = GQ.load(Path(ops_root) / "game_quotes", date)
    rows, st = GX.board_rows(pm, q, names, at) if len(pm) else ([], {"games": 0})
    fr = GX.freeze(ops_root, date, q, names, at)
    lin = GX.lineage(ops_root, date)
    return {"status": "WRITTEN" if len(pm) else "NO_GAME_PMFS", "rows": rows, **st, "quote_rows": int(len(q)),
            "frozen": fr, "lineage": lin}


def game_grades(day: str, ops_root: Path, state_root, names: dict) -> dict | None:
    if not _game_mode():
        return None
    from cbb_platform.prod import game_exports as GX, game_quotes as GQ
    ev = GX.evaluations(ops_root, day)
    q = GQ.load(Path(ops_root) / "game_quotes", day)
    tips = {e["game_id"]: e["scheduled_tip_utc"] for e in ev}
    rows = GX.graded(ops_root, day, _finals(state_root, sorted(tips)), GQ.closing(q, tips), names)
    return {"status": "WRITTEN", "rows": rows, "evaluations": len(ev),
            "results": pd.Series([r["RESULT"] for r in rows]).value_counts().to_dict() if rows else {}}


def public_contract(out_dir: Path, date: str, at, brows: list, glines: dict | None) -> dict:
    """CBB_PUBLIC_DATA_CONTRACT_V1 (prod.public_contract): the publishable payload + mobile page built from the SAME
    internal rows as the boards workbook, with row-for-row QA. Written to <export day>/public/; never raises."""
    try:
        from cbb_platform.prod import game_exports as GXP, market_exports as MXP, public_contract as PUB
        lines = (glines or {}).get("rows") or []
        pay = PUB.payload(PUB.game_records(lines, GXP.LINE_COLS), PUB.prop_records(brows, MXP.BOARD_COLS),
                          pd.Timestamp(at).isoformat(), date)
        qa = PUB.qa_rows(pay, lines, GXP.LINE_COLS, brows, MXP.BOARD_COLS)
        d = out_dir / "public"
        d.mkdir(parents=True, exist_ok=True)
        for name, body in (("cbb_public_v1.json", json.dumps(pay, sort_keys=True, default=str)), ("index.html", PUB.render_html(pay)),
                           ("qa_receipt.json", json.dumps({**qa, "generated_at": pay["generated_at"]}, default=str))):
            tmp = d / (name + ".tmp")
            tmp.write_text(body)
            os.replace(tmp, d / name)
        return {"status": "WRITTEN", "contract": PUB.CONTRACT_ID, "dir": str(d), **qa}
    except Exception as e:                                       # noqa: BLE001 -- publication artifact only
        return {"status": "FAILED", "error": f"{type(e).__name__}: {str(e)[:300]}", "WIZARDOFODDS_ROW_QA": "FAIL"}


def boards_shadow_workbook(brows: list, glines: list, path: Path) -> Path:
    """Release B layout: `Player Props` (the market-backed player board: one row per game x player x stat best offer),
    `Game Lines` (game x core market best offer, SHADOW), then the PLAYER-ONLY supporting tabs."""
    from cbb_platform.prod import game_exports as GX, market_exports as MX
    sh = MX.board_sheets(brows)
    blk = lambda rows: [{"title": None, "columns": MX.BOARD_COLS, "rows": rows, "formats": MX.BOARD_FORMATS}]  # noqa: E731
    gfmt = {c: MX.PCT for c in ("MODEL%", "MARKET%", "PUSH%", "PROBABILITY_EDGE", "EV_GROSS", "EV_NET")}
    sheets = [{"name": "Player Props", "blocks": blk(sh["Full Board"])},
              {"name": "Game Lines", "blocks": [{"title": None, "columns": GX.LINE_COLS, "rows": glines,
                                                 "formats": gfmt}]}]
    sheets += [{"name": f"Player {s}" if "Percent" in s else s, "blocks": blk(sh[s])}
               for s in MX.BOARD_SHEETS if s != "Full Board"]
    return write_xlsx(path, sheets)


def grades_shadow_sheets(sheets: list, grows: list, game_rows: list) -> list:
    """Release B Grades layout: `Player Props` (every market-backed player prediction; VOID_DNP shown, excluded from
    the metrics), `Game Markets`, `Summary` (separate PLAYER PROPS and GAME MARKETS blocks), then the supporting tabs."""
    from cbb_platform.prod import game_exports as GX, market_exports as MX
    m = MX.metrics(grows)
    first = sheets[0]
    gfmt = {c: MX.PCT for c in ("MODEL%", "MARKET%", "PUSH%", "EDGE", "EV", "CLV_PROBABILITY")}
    player_summary = ["PLAYER PROPS", "ALL", m["Graded"], m.get("Wins"), m.get("Losses"), m.get("Pushes"),
                      m["VOID_DNP"], m["Pending"], m.get("Hit Rate"), m.get("Brier Score"), m.get("Log Loss"),
                      m.get("Units Won"), m.get("ROI")]
    sfmt = {"HIT_RATE": MX.PCT, "RETURN": MX.PCT}
    out = [{**first, "name": "Player Props"},
           sheets[1],                                    # MX.SUCCESS_SHEET: the second worksheet (Stage 06 contract)
           {"name": "Game Markets", "blocks": [{"title": None, "columns": GX.GRADE_COLS,
                                                "rows": GX.grade_table(game_rows), "formats": gfmt}]},
           {"name": "Summary", "blocks": [
               {"title": "PLAYER PROPS", "columns": GX.SUMMARY_COLS, "rows": [player_summary], "formats": sfmt},
               {"title": "GAME MARKETS (SHADOW: research only, never actionable)", "columns": GX.SUMMARY_COLS,
                "rows": GX.summary(game_rows), "formats": sfmt}]}]
    return out + sheets[2:]


def _cutoffs(pmfs: list, games: pd.DataFrame, ops_root: Path, date: str) -> dict:
    tips = {**snapshot_tips(ops_root, date), **(dict(zip(games["game_id"], games["tip_utc"])) if len(games) else {})}
    out: dict = {}
    for r in pmfs:
        r["tip"] = _utc(tips.get(r["game_id"])) or r["tip"]
        if r["cutoff"] is not None:
            out[r["game_id"]] = r["cutoff"]
    if len(games):
        out.update({g: c for g, c in zip(games["game_id"], games["cutoff_utc"]) if c is not None})
    return out


def _freeze_day(day: str, ops_root: Path, runs_root: Path, names: dict, at) -> dict:
    """Freeze any game of ET date `day` whose cutoff passed without a frozen evaluation (normally every game froze on
    its own day's quote ticks; this covers a game whose cutoff fell after the last export of that day)."""
    from cbb_platform.prod import market_exports as MX
    games = slate_games(ops_root, day)
    fs = forecast_sources(ops_root, day)
    cut = {g: max(c for _, c, _ in v) for g, v in fs.items()}
    if len(games):
        cut.update({g: c for g, c in zip(games["game_id"], games["cutoff_utc"]) if c is not None})
    _, frozen = MX.load_evaluations(MX.eval_path(ops_root, day))
    todo = sorted(g for g, c in cut.items() if g not in frozen and _utc(c) is not None and _utc(at) >= _utc(c))
    if not todo:
        return {"day": day, "games_frozen_now": 0}
    pmfs: list = []
    recs, _ = distribution_records(ops_root, runs_root, games, names, day, audit={}, pmf_out=pmfs)
    pmfs = [r for r in pmfs if r["game_id"] in set(todo)]
    cut = {**{g: cut[g] for g in todo}, **_cutoffs(pmfs, games, ops_root, day)}
    return {"day": day, **MX.freeze(ops_root, day, pmfs, names, player_context(ops_root, day, recs) if recs else {},
                                    {g: cut[g] for g in todo}, at)}


def _performance(date: str, ops_root: Path, out_dir: Path, at) -> dict:
    """CBB_Performance (prod.performance): cumulative, from the canonical dataset; an explicit status, never omitted."""
    from cbb_platform.prod import performance as PF
    return PF.export(date, ops_root, out_dir, at)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cbb_platform.prod.exports")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daily", help="write the Boards, Distributions, Grades and Performance workbooks of one slate date")
    d.add_argument("--date", required=True, help="slate date YYYY-MM-DD (America/New_York)")
    d.add_argument("--ops-root", default=str(PC.OPS_ROOT))
    d.add_argument("--runs-root", default=str(RUNS_ROOT))
    d.add_argument("--out-root", default=str(EXPORT_ROOT))
    d.add_argument("--history-db", default=None, help="default: the production history DB (history_extract.resolve)")
    d.add_argument("--state-root", default=str(PC.STATE_ROOT))
    x = sub.add_parser("expand-distributions", help="write the long one-row-per-outcome Distributions derivative")
    x.add_argument("--date", required=True)
    x.add_argument("--out-root", default=str(EXPORT_ROOT))
    x.add_argument("--ops-root", default=str(PC.OPS_ROOT))
    m = sub.add_parser("migrate-distributions", help="replace a past exploded Distributions workbook by summary + refs "
                                                     "(only when the derived long form equals it exactly)")
    m.add_argument("--date", required=True)
    for k, v in (("--ops-root", PC.OPS_ROOT), ("--runs-root", RUNS_ROOT), ("--out-root", EXPORT_ROOT),
                 ("--history-db", PC.HISTORY_CANONICAL), ("--state-root", PC.STATE_ROOT)):
        m.add_argument(k, default=str(v))
    a = ap.parse_args(argv)
    if a.cmd == "migrate-distributions":
        r = migrate_distributions(a.date, ops_root=Path(a.ops_root), runs_root=Path(a.runs_root),
                                  out_root=Path(a.out_root), history_db=Path(a.history_db), state_root=Path(a.state_root))
        print(json.dumps(r, default=str))
        return 0 if r["status"] in ("MIGRATED", "ALREADY_SUMMARY", "NO_WORKBOOK") else 2
    if a.cmd == "expand-distributions":
        print(expand_distributions(a.date, Path(a.out_root), Path(a.ops_root)))
        return 0
    if a.history_db is None:
        from cbb_platform.prod import history_extract as HX
        a.history_db = str(HX.resolve())                   # the DB the scheduled lifecycle uses (never a stale default)
    rep = daily(a.date, ops_root=Path(a.ops_root), runs_root=Path(a.runs_root), out_root=Path(a.out_root),
                history_db=Path(a.history_db), state_root=Path(a.state_root))
    print(json.dumps(rep, indent=1, default=str))
    return 0 if all(rep[k]["status"] == "WRITTEN" for k in ("boards", "distributions", "grades", "performance")) else 2


if __name__ == "__main__":
    sys.exit(main())
