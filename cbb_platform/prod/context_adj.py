"""Player corrections OT1 + OC2: stat-specific OWN-TEAM and DEFENDING-OPPONENT context (docs/audits/2026-11-25-defects/
DEFECT_PROTOCOL.json, amendment_1 ALLB). Promoted 2026-11-25.

Defect (current engine, chronological OOS): per-minute residuals persist by defending opponent (2025-26 split-half
correlation REB .67, TOV .65, AST .54, STL .53, FG3M .52, BLK .44) and by own team (PTS .52, AST .43, REB .38) after the
generic pace / efficiency environment terms. Root cause: no stat-specific team / opponent tendency in the player means.

Fix (stat means), from games on EARLIER dates of the current season only:
    mu_s <- mu_s * (Y_team + KT_s) / (CF_team + KT_s) * (Y_opp + KO_s) / (CF_opp + KO_s)      (KO_PTS = inf: no factor)
Y = realized stat, CF = incumbent PMF mean x realized minutes / forecast minutes (played rows) of every player of that
team (OT1) / every player who faced that opponent (OC2). Incumbent = the production configuration WITHOUT DV1 / OT1 / OC2
(RA2 + PTSC1 + TOVT1, opening marginal), scored point-in-time by the production simulator: exactly the evaluated
counterfactual. K = TRAIN 2024-25 grid choice (results/p1_train_fit.json). OC2 excludes PTS because PTS already receives
the opponent defensive-efficiency environment term (OC1 with PTS failed the combo / protected gates).

The ledger (one parquet per season) is updated by the morning walk-forward from its point-in-time training frames; the
daily run reads it. Missing or stale ledger -> factors 1 and receipt status LEDGER_UNAVAILABLE / LEDGER_STALE (fail-safe
to the DV1 engine's means; never blocks a run).

Single pre-declared evaluation (ALLB = DV1 x OT1 x OC2 vs the production configuration, common random numbers): stacked
log loss 2025-26 -0.00223 (ub -0.00205), live 2026-27 -0.00181 (ub -0.00147); live graded prop events log loss
.7186 -> .7080 (market .6884).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

ID = "CBB_PLAYER_CORRECTION_OT1_OC2_TEAM_OPPONENT_CONTEXT_V1"
STATS = ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV")
KAPPA_TEAM = {"PTS": 640.0, "REB": 640.0, "AST": 320.0, "FG3M": 640.0, "STL": 320.0, "BLK": 320.0, "TOV": 640.0}
KAPPA_OPP = {"PTS": float("inf"), "REB": 160.0, "AST": 160.0, "FG3M": 160.0, "STL": 80.0, "BLK": 80.0, "TOV": 80.0}
LEDGER_SIMS = 2000
MAX_LEDGER_AGE_DAYS = 2
ENABLED = True
COLS = ["game_id", "date", "team", "opp", "stat", "y", "cf"]
# Stage 07 bitemporal lineage per ledger row: when the row entered the ledger (= when its information was usable by the
# daily run), the immutable state version and training cutoff it came from, and the incumbent tag it was computed under.
# Rows written before Stage 07 have none (LEGACY): they are usable prospectively, never in an as-of replay.
LINEAGE_COLS = ["added_at", "state_version", "training_cutoff", "incumbent"]


def enabled() -> bool:
    return bool(ENABLED)


def _et_date(cutoff_utc) -> pd.Series:
    ts = pd.to_datetime(pd.Series(np.asarray(cutoff_utc)), utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
    return ts.dt.normalize()


def ledger_path(root: Path, season: int) -> Path:
    return Path(root) / "context" / f"ledger_{season}.parquet"


# ============================================================================ ledger (morning walk-forward)
def ledger_rows(oos: pd.DataFrame, frame: pd.DataFrame, team: pd.DataFrame, model, opening_v0: dict, games) -> pd.DataFrame:
    """Incumbent counterfactual rows for completed `games` (point-in-time OOS inputs of the walk-forward)."""
    from cbb_platform.prod import inputs as IN, rate_a2 as RA2, simulate as SM, tov_trend as TT
    from cbb_platform.v2 import phase3 as P3
    df = oos[oos["game_id"].isin(list(games))].copy()
    if df.empty:
        return pd.DataFrame(columns=COLS)
    df["_scored"] = P3.scored_mask(df)
    df = df.sort_values(["game_id", "team_id", "player_id"], kind="mergesort").reset_index(drop=True)
    cand = frame.set_index("player_game_example_id").reindex(df["player_game_example_id"].to_numpy()).reset_index()
    cand["prediction_cutoff_utc"] = df["prediction_cutoff_utc"].to_numpy()          # the row's own cutoff (T1 clock)
    df, _ = RA2.apply(df, cand, model.marginal_cfg, opening_v0)
    df, _ = TT.apply(df, cand)
    envd = IN.environment(team, model.jm1["struct"]["env"])
    out = []
    for gid, gp in df.groupby("game_id", sort=True):
        if not gp["_scored"].any() or gp["team_id"].nunique() != 2:
            continue
        bank = SM.simulate_game(model.jm1, gid, gp, envd, LEDGER_SIMS)
        rows, pmf = bank["rows"].reset_index(drop=True), bank["pmf"]
        mean = (pmf * np.arange(pmf.shape[2])[None, None, :]).sum(axis=2)          # n x 7
        ymin = rows["y_min"].to_numpy(float)
        played = ymin > 0
        t = rows["team_id"].to_numpy()
        teams = list(pd.unique(t))
        date = _et_date(rows["prediction_cutoff_utc"]).iloc[0]
        for j, s in enumerate(STATS):
            cf = mean[:, j] * ymin / np.maximum(rows["min_mu"].to_numpy(float), 1e-9)
            y = rows[f"y_{s}"].to_numpy(float)
            ok = played & np.isfinite(y) & np.isfinite(cf)
            for tm in teams:
                m = ok & (t == tm)
                out.append((gid, date, tm, teams[1] if tm == teams[0] else teams[0], s, float(y[m].sum()), float(cf[m].sum())))
    return pd.DataFrame(out, columns=COLS)


def update_ledger(root: Path, season: int, ts: dict, model, opening_v0: dict, engine_tag: str, log=print) -> dict:
    """Append the completed games the ledger does not hold yet (rebuild if the incumbent tag changed). Atomic write."""
    p = ledger_path(root, season)
    fr = ts.get("frames") or {}
    if not fr or fr.get("oos") is None or not len(fr["oos"]):
        return {"status": "NO_FRAMES"}
    meta_p = p.with_suffix(".json")
    old = pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=COLS)
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    if meta.get("incumbent") != engine_tag:
        old = pd.DataFrame(columns=COLS)
    oos = fr["oos"]
    have = set(old["game_id"])
    labelled = oos.loc[oos["y_min"].notna(), "game_id"].unique()
    todo = sorted(set(labelled) - have)
    new = ledger_rows(oos, fr["frame"], fr["team"], model, opening_v0, todo) if todo else pd.DataFrame(columns=COLS)
    if len(new):
        st = ts.get("state") or {}
        new = new.assign(added_at=pd.Timestamp.now(tz="UTC").isoformat(), state_version=st.get("state_version"),
                         training_cutoff=str(ts.get("training_cutoff")), incumbent=engine_tag)
    led = pd.concat([old, new], ignore_index=True) if len(new) else old
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".parquet.tmp")
    led.to_parquet(tmp, index=False)
    os.replace(tmp, p)
    rec = {"status": "UPDATED", "incumbent": engine_tag, "games": int(led["game_id"].nunique()), "added_games": int(len(todo)),
           "through_date": str(pd.to_datetime(led["date"]).max().date()) if len(led) else None,
           "updated_at": pd.Timestamp.now(tz="UTC").isoformat()}
    mt = meta_p.with_suffix(".json.tmp")
    mt.write_text(json.dumps(rec))
    os.replace(mt, meta_p)
    log(f"context ledger: {rec}")
    return rec


# ============================================================================ factors (daily run)
def factors(rows: pd.DataFrame, opp: np.ndarray, led: pd.DataFrame) -> dict:
    """stat -> multiplier per row; ledger entries strictly before the row's ET date."""
    date = _et_date(rows["prediction_cutoff_utc"]).to_numpy()
    team = rows["team_id"].to_numpy()
    out = {}
    L = led.assign(date=pd.to_datetime(led["date"]))
    for s in STATS:
        ls = L[L["stat"] == s]
        m = np.ones(len(rows))
        for key, vals, kap in (("team", team, KAPPA_TEAM[s]), ("opp", opp, KAPPA_OPP[s])):
            if not np.isfinite(kap):
                continue
            g = ls.groupby([key, "date"])[["y", "cf"]].sum().reset_index().sort_values("date")
            g[["y", "cf"]] = g.groupby(key)[["y", "cf"]].cumsum()
            q = pd.DataFrame({key: vals, "date": pd.to_datetime(date), "_i": np.arange(len(rows))}).sort_values("date")
            j = pd.merge_asof(q, g, on="date", by=key, allow_exact_matches=False).sort_values("_i")   # strictly earlier
            Y, CF = j["y"].to_numpy(float), j["cf"].to_numpy(float)
            f = np.where(np.isfinite(Y), (np.nan_to_num(Y) + kap) / (np.nan_to_num(CF) + kap), 1.0)
            m = m * f
        out[s] = m
    return out


def load_ledger(root: Path, season: int, as_of, expected_incumbent: str | None = None,
                replay: bool = False) -> tuple[pd.DataFrame | None, str]:
    """The ledger usable at as_of. Status (never silent, recorded in the run receipt):
    OK; LEDGER_UNAVAILABLE / LEDGER_STALE (no factors); LEDGER_FROM_FUTURE (updated after as_of: a replay reading a
    later ledger -- no factors); INCUMBENT_MISMATCH (applied, visibly: the tag differs from the scoring engine's
    pre-context tag until the next morning rebuild). replay=True additionally keeps only rows with added_at <= as_of and
    refuses LEGACY rows without lineage (LEDGER_REPLAY_UNVERIFIABLE)."""
    p = ledger_path(root, season)
    if not p.exists() or not p.with_suffix(".json").exists():
        return None, "LEDGER_UNAVAILABLE"
    meta = json.loads(p.with_suffix(".json").read_text())
    now = pd.Timestamp(as_of)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    age = (now - pd.Timestamp(meta["updated_at"])).total_seconds() / 86400
    led = pd.read_parquet(p)
    if replay:
        if "added_at" not in led or led["added_at"].isna().any():
            return None, "LEDGER_REPLAY_UNVERIFIABLE"
        led = led[pd.to_datetime(led["added_at"], utc=True) <= now].reset_index(drop=True)
    elif age < 0:
        return None, "LEDGER_FROM_FUTURE"
    if age > MAX_LEDGER_AGE_DAYS:
        return None, "LEDGER_STALE"
    if expected_incumbent is not None and meta.get("incumbent") != expected_incumbent:
        return led, "INCUMBENT_MISMATCH"
    return led, "OK"


def ledger_meta(root: Path, season: int) -> dict:
    p = ledger_path(root, season).with_suffix(".json")
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def apply(rows: pd.DataFrame, opp: np.ndarray, led: pd.DataFrame | None, status: str) -> tuple[pd.DataFrame, dict]:
    """rows = model input rows (after prod.dev_trend); opp = the defending team per row."""
    if not enabled() or rows.empty:
        return rows, {"id": ID, "enabled": enabled(), "rows": 0}
    if led is None or not len(led):
        return rows, {"id": ID, "enabled": True, "rows": int(len(rows)), "status": status, "applied": False}
    f = factors(rows, np.asarray(opp), led)
    out = rows.copy()
    for s, v in f.items():
        out[f"{s}_mu"] = out[f"{s}_mu"].to_numpy(dtype=float) * v
    neutral = {s: int((v == 1.0).sum()) for s, v in f.items()}        # F2: rows with no team / opp history -> factor 1
    return out, {"id": ID, "enabled": True, "rows": int(len(rows)), "status": status, "applied": True,
                 "ledger_games": int(led["game_id"].nunique()),
                 "ledger_rows_with_lineage": int(led["added_at"].notna().sum()) if "added_at" in led else 0,
                 "rows_factor_exactly_1": neutral,
                 "multiplier_range": {s: [float(v.min()), float(v.max())] for s, v in f.items()}}
