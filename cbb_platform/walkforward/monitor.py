"""PROSPECTIVE PROBABILITY-QUALITY MONITOR of the daily walk-forward models. DIAGNOSTICS ONLY:
AUTOMATIC_MODEL_CORRECTION = False; nothing here changes a forecast, a price, a model or a threshold.

Every settled number is attached to the MODEL VERSION THAT MADE THE PREDICTION (its provenance row), never to the
version active when it is scored. Settlement happens each morning BEFORE the refit (directive section 19).

Units
    binary (quoted lines)  one row per (run, game, player, market, line): p = P(over | no push) from the predicting
                           version's pmf; event = over. Pushes and DNP (no box row / 0 minutes: NO_ACTION, never a zero)
                           are excluded and counted. OVER / UNDER breakdowns use the side rows (p = P(side | no push)).
    distribution           one row per (run, game, player, market) priced: full-PMF log score -log p(y) and CRPS
                           sum_k (F(k) - 1{y <= k})^2 over the pmf support.
    market (matched)       per sportsbook quoting BOTH sides of the same line in the same snapshot: no-vig
                           p_over = (1/d_over) / (1/d_over + 1/d_under) (prospective_monitor convention, unchanged);
                           the line-level market probability averages the books (same convention); model and market are
                           scored on exactly the same (run, game, player, market, line, quote snapshot) and outcome.
                           DELTA = MODEL - MARKET (< 0: model better).
Uncertainty: game-clustered 95% intervals (prospective_monitor.cmean). Every block carries N, games, the observed
event rate and the mean predicted probability, so raw Brier is never compared across base rates without context.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cbb_platform.prod import config as PC, ledger as LG, monitoring as PMON, prospective_monitor as PM, side_monitor as SD
from cbb_platform.walkforward import config as WC, eligibility as EL, store as SO

AUTOMATIC_MODEL_CORRECTION = False
ROLES = ("TOP5_MINUTES", "ROTATION_6_8", "BENCH_9PLUS", "NO_ROLE_HISTORY")
WINDOWS = {"YESTERDAY": 1, "ROLLING_7_DAY": 7, "ROLLING_14_DAY": 14, "ROLLING_30_DAY": 30, "SEASON_TO_DATE": None}
LINE_RANGES = ((0.0, 2.5), (2.5, 5.5), (5.5, 10.5), (10.5, 20.5), (20.5, 1e9))     # descriptive bins (half-point edges)
STAT_COLS = SD.STAT_COLS
NO_VIG_METHOD = ("per-book normalized reciprocal decimal odds of the two sides of the same line in the same snapshot "
                 "(proportional / multiplicative de-vig); line-level market = mean over books (prospective_monitor)")
EPS = 1e-12
HIGH_COMBO_MARKETS = ("PRA", "PR", "PA")                 # frozen manifest warning B: q75-threshold combo overs
POLICY_CALIBRATION_SLOPE_BAND = (0.85, 1.15)             # PRODUCTION_SCORING_..._POLICY_v1_0.md section 8 (APPROVED)
PROPOSED_THRESHOLDS = Path(__file__).resolve().parents[2] / "docs" / "walkforward" / "PROPOSED_MONITORING_THRESHOLDS.json"


def _p(root: Path) -> dict:
    p = SO.paths(root)
    return {"provenance": p["predictions"] / "provenance.parquet", "pmfs": p["predictions"] / "pmfs.parquet",
            "sides": p["predictions"] / "side_monitor.jsonl", "ledger": p["predictions"] / "ledger.jsonl",
            "diagnostics": p["predictions"] / "game_diagnostics.jsonl",
            "settled": p["monitoring"] / "settlements.parquet", "dist_settled": p["monitoring"] / "distribution_settlements.parquet",
            "reports": p["monitoring"] / "reports"}


def _upsert(path: Path, new: pd.DataFrame, key) -> int:
    """Append rows whose key is not yet stored (idempotent); returns rows added."""
    if not len(new):
        return 0
    old = pd.read_parquet(path) if Path(path).exists() else None
    if old is not None:
        k_old = set(map(tuple, old[list(key)].astype(str).to_numpy()))
        new = new[[tuple(r) not in k_old for r in new[list(key)].astype(str).to_numpy()]]
        if not len(new):
            return 0
        new = pd.concat([old, new], ignore_index=True)
    tmp = Path(str(path) + ".tmp")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(tmp, index=False)
    tmp.replace(path)
    return int(len(new) - (0 if old is None else len(old)))


# ============================================================================ provenance (written after each scoring run)
def record_run(root: Path, summary: dict) -> dict:
    """Provenance rows of one prod.run.daily(model=ACTIVE) run: every PREDICTION record + role + PMFs, keyed to the
    predicting version (directive section 18)."""
    pp = _p(root)
    run_dir = Path(summary["run_dir"])
    preds = [r for r in LG.predictions(pp["ledger"]) if r["run_id"] == summary["run_id"]]
    if not preds:
        return {"rows": 0}
    df = pd.DataFrame(preds)
    mi = pd.read_parquet(run_dir / "model_inputs.parquet")[["game_id", "player_id", "role_diagnostic", "min_mu"]]
    df = df.merge(mi.rename(columns={"min_mu": "predicted_minutes"}), on=["game_id", "player_id"], how="left")
    sl = summary.get("state_as_of")
    df["data_cutoff"] = str(sl)
    df["checkpoint"] = WC.CHECKPOINT
    df["artifact_sha256"] = df["artifact_sha256"].map(lambda x: json.dumps(x, sort_keys=True))
    for c in ("line", "probability", "push_probability", "p_nonpush", "best_decimal_odds"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = ["prediction_id", "run_id", "prediction_as_of", "feature_cutoff", "data_cutoff", "checkpoint", "model_version",
            "training_cutoff", "artifact_sha256", "production_manifest", "state_version", "game_id", "game", "player_id",
            "player", "team_id", "market", "line", "side", "role_diagnostic", "predicted_minutes", "projection",
            "probability", "push_probability", "p_nonpush", "p_play", "sportsbook", "best_decimal_odds",
            "quote_available_at", "collector_received_at", "readiness_state", "distribution_method", "pmf_q75"]
    for c in keep:
        if c not in df:
            df[c] = None
    df = df[keep].astype({c: str for c in ("prediction_as_of", "feature_cutoff", "quote_available_at",
                                            "collector_received_at", "projection")})
    pm = pd.read_parquet(run_dir / "market_pmfs.parquet") if (run_dir / "market_pmfs.parquet").exists() else pd.DataFrame()
    if len(pm):
        pm = pm.assign(run_id=summary["run_id"], model_version=summary["model_identity"]["model_version"],
                       training_cutoff=summary["model_identity"].get("training_cutoff"))
        pm = pm.merge(mi[["game_id", "player_id", "role_diagnostic", "min_mu"]].rename(columns={"min_mu": "predicted_minutes"}),
                      on=["game_id", "player_id"], how="left")
        pm["game_date"] = pm["game_id"].map(df.drop_duplicates("game_id").set_index("game_id")["feature_cutoff"]
                                            .map(lambda x: str(pd.Timestamp(x).tz_convert(WC.TZ).date()))).fillna("")
    gd = run_dir / "game_diagnostics.json"
    known = {(r["run_id"], r["game_id"]) for r in SO.read_log(pp["diagnostics"])}
    for d in (json.loads(gd.read_text()) if gd.exists() else []):
        if (summary["run_id"], d["game_id"]) not in known:
            SO.append_log(pp["diagnostics"], {"run_id": summary["run_id"],
                                             "model_version": summary["model_identity"]["model_version"], **d})
    return {"rows": _upsert(pp["provenance"], df, ["prediction_id"]),
            "pmf_rows": _upsert(pp["pmfs"], pm, ["run_id", "game_id", "player_id", "market"]) if len(pm) else 0}


# ============================================================================ settlement (each morning, before the refit)
def actuals(increment_db: Path, game_ids) -> pd.DataFrame:
    con = duckdb.connect(str(increment_db), read_only=True)
    con.register("g", pd.DataFrame({"canonical_game_id": sorted(set(game_ids))}))
    cols = ", ".join(f"{c}::DOUBLE AS {s}" for s, c in STAT_COLS.items())
    df = con.execute(f"""SELECT canonical_game_id AS game_id, canonical_player_id AS player_id, minutes, {cols}
                         FROM flagship.player_game_outcomes WHERE canonical_game_id IN (SELECT canonical_game_id FROM g)
                           AND canonical_player_id IS NOT NULL""").df()
    con.close()
    df["participation"] = np.where(df["minutes"].fillna(0) > 0, "PLAYED", "DNP")
    return df


def _crps(pmf: np.ndarray, y: int) -> float:
    cdf = np.cumsum(pmf)
    k = np.arange(len(pmf))
    return float(np.sum((cdf - (k >= y)) ** 2))


def settle(root: Path, state_root: Path, now) -> dict:
    """Settle every stored prediction whose game is final with a complete, reconciled, identity-resolved box in the
    state as-of `now` (the training eligibility rule: a game is never settled on a partial box)."""
    pp = _p(root)
    if not pp["provenance"].exists():
        return {"settled": 0, "distribution_settled": 0, "pending": 0}
    st = EL.state_asof(state_root, now)
    if st is None:
        return {"settled": 0, "distribution_settled": 0, "pending": 0, "note": "no state"}
    prov = pd.read_parquet(pp["provenance"])
    done = set(pd.read_parquet(pp["settled"])["prediction_id"]) if pp["settled"].exists() else set()
    todo = prov[~prov["prediction_id"].isin(done) & prov["line"].notna()]
    el = EL.evaluate(st, now, EL.outcome_available_at(state_root, now))
    final = set(el.loc[el["decision"].eq("INCLUDED"), "game_id"])
    a = actuals(st["increment_db"], final).drop_duplicates(["game_id", "player_id"]).set_index(["game_id", "player_id"])
    rows = []
    for r in todo[todo["game_id"].isin(final)].itertuples(index=False):
        key = (r.game_id, r.player_id)
        row = a.loc[key] if key in a.index else None
        y = None if row is None or row["participation"] != "PLAYED" else SD._actual(row, r.market)
        base = {"prediction_id": r.prediction_id, "settled_at": str(pd.Timestamp(now)), "settlement_state_version": st["state_version"]}
        if y is None:
            rows.append({**base, "actual": np.nan, "result": "NO_ACTION", "side_win": np.nan, "brier": np.nan, "log_loss": np.nan})
            continue
        res = "PUSH" if y == r.line else ("WIN" if (y > r.line) == (r.side == "OVER") else "LOSS")
        win = np.nan if res == "PUSH" else float(res == "WIN")
        p = r.probability / r.p_nonpush if r.p_nonpush and r.p_nonpush > 0 else np.nan
        rows.append({**base, "actual": float(y), "result": res, "side_win": win,
                     "brier": float((p - win) ** 2) if res != "PUSH" else np.nan,
                     "log_loss": float(-np.log(np.clip(p if win else 1 - p, EPS, 1))) if res != "PUSH" else np.nan})
    n = _upsert(pp["settled"], pd.DataFrame(rows), ["prediction_id"])
    nd = 0
    if pp["pmfs"].exists():
        pm = pd.read_parquet(pp["pmfs"])
        dd = set(map(tuple, pd.read_parquet(pp["dist_settled"])[["run_id", "game_id", "player_id", "market"]].to_numpy())) \
            if pp["dist_settled"].exists() else set()
        out = []
        for r in pm[pm["game_id"].isin(final)].itertuples(index=False):
            if (r.run_id, r.game_id, r.player_id, r.market) in dd:
                continue
            key = (r.game_id, r.player_id)
            row = a.loc[key] if key in a.index else None
            y = None if row is None or row["participation"] != "PLAYED" else SD._actual(row, r.market)
            pmf = np.asarray(r.pmf, dtype=float)
            ok = y is not None
            out.append({"run_id": r.run_id, "game_id": r.game_id, "player_id": r.player_id, "market": r.market,
                        "actual": float(y) if ok else np.nan, "result": "SCORED" if ok else "NO_ACTION",
                        "actual_minutes": float(row["minutes"]) if ok else np.nan,
                        "pmf_log_score": float(-np.log(max(pmf[int(y)] if int(y) < len(pmf) else 0.0, EPS))) if ok else np.nan,
                        "crps": _crps(pmf, int(y)) if ok else np.nan, "settled_at": str(pd.Timestamp(now))})
        nd = _upsert(pp["dist_settled"], pd.DataFrame(out), ["run_id", "game_id", "player_id", "market"])
    return {"settled": n, "distribution_settled": nd, "pending": int(len(todo) - len(rows)),
            "state_version": st["state_version"]}


# ============================================================================ aggregation
def line_range(x) -> str:
    for lo, hi in LINE_RANGES:
        if lo <= x < hi:
            return f"[{lo:g},{hi:g})" if hi < 1e8 else f"[{lo:g},inf)"
    return "NA"


def block(d: pd.DataFrame, p: str = "p", y: str = "y") -> dict:
    if not len(d):
        return {"n": 0, "games": 0}
    g = d["game_id"].to_numpy()
    out = {"n": int(len(d)), "games": int(d["game_id"].nunique())}
    pv, yv = d[p].to_numpy(dtype=float), d[y].to_numpy(dtype=float)
    out.update({"observed_event_rate": float(yv.mean()), "mean_predicted_probability": float(pv.mean()),
                "calibration_gap": PM.cmean(pv - yv, g), "brier": PM.cmean((pv - yv) ** 2, g),
                "log_loss": PM.cmean(-(yv * np.log(np.clip(pv, EPS, 1)) + (1 - yv) * np.log(np.clip(1 - pv, EPS, 1))), g)})
    return out


def calibration_slope(p: np.ndarray, y: np.ndarray) -> dict:
    """Logistic recalibration slope b of y ~ a + b logit(p) (1 = calibrated spread), IRLS, model-based SE."""
    x = np.log(np.clip(p, EPS, 1 - EPS) / np.clip(1 - p, EPS, 1 - EPS))
    X = np.column_stack([np.ones(len(x)), x])
    b = np.zeros(2)
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return {"n": int(len(x)), "slope": None}
    for _ in range(50):
        m = 1 / (1 + np.exp(-(X @ b)))
        H = (X * (m * (1 - m))[:, None]).T @ X
        try:
            step = np.linalg.solve(H, X.T @ (y - m))
        except np.linalg.LinAlgError:
            return {"n": int(len(x)), "slope": None}
        b += step
        if np.abs(step).max() < 1e-10:
            break
    se = float(np.sqrt(np.linalg.inv(H)[1, 1]))
    lo, hi = POLICY_CALIBRATION_SLOPE_BAND
    return {"n": int(len(x)), "slope": float(b[1]), "se": se, "policy_band": [lo, hi],
            "OUTSIDE_POLICY_BAND": bool(not lo <= b[1] <= hi)}


def drift(b: pd.DataFrame, d: pd.DataFrame, diags: list[dict]) -> dict:
    """Directive section 26 drift diagnostics (labels for the architect; nothing is triggered automatically)."""
    out = {"sample": {"binary_n": int(len(b)), "distribution_n": int(len(d))}}
    out["calibration_slope"] = calibration_slope(b["p"].to_numpy(dtype=float), b["y"].to_numpy(dtype=float)) if len(b) else {"n": 0}
    if len(d):
        pts = d[d["market"] == "PTS"]
        out["PTS_BIAS"] = PM.cmean((pts["projection"] - pts["actual"]).to_numpy(dtype=float), pts["game_id"].to_numpy())
        mn = d.drop_duplicates(["run_id", "game_id", "player_id"])
        out["MINUTES_BIAS"] = PM.cmean((mn["predicted_minutes"] - mn["actual_minutes"]).to_numpy(dtype=float),
                                       mn["game_id"].to_numpy())
        act = {(r.game_id, r.player_id): r.actual for r in pts.itertuples(index=False)}
        games = set(pts["game_id"])
        out["SAME_TEAM_DEPENDENCE"] = PMON.same_team_dependence([x for x in diags if x["game_id"] in games], act)
    hc = (b[b["market"].isin(HIGH_COMBO_MARKETS) & (b["line"] >= pd.to_numeric(b["pmf_q75"], errors="coerce"))]
          if len(b) and "pmf_q75" in b else b.iloc[0:0])
    out["HIGH_COMBO_OVERS"] = {m: block(g) for m, g in hc.groupby("market")} if len(hc) else {"n": 0}
    out["thresholds"] = {"status": "PROPOSED_NOT_ACTIVE (architect review)", "file": str(PROPOSED_THRESHOLDS.name),
                         "approved_alert": "calibration slope outside [0.85, 1.15] (policy v1.0 section 8): ALERT only"}
    return out


def market_block(d: pd.DataFrame) -> dict:
    if not len(d):
        return {"n": 0, "status": "BLOCKED_NO_QUOTES"}
    g = d["game_id"].to_numpy()
    out = {"n": int(len(d)), "games": int(d["game_id"].nunique()), "observed_event_rate": float(d["y"].mean()),
           "mean_model_probability": float(d["p_model"].mean()), "mean_market_probability": float(d["p_market"].mean())}
    for k, f in (("BRIER", lambda p, y: (p - y) ** 2),
                 ("LOG_LOSS", lambda p, y: -(y * np.log(np.clip(p, EPS, 1)) + (1 - y) * np.log(np.clip(1 - p, EPS, 1))))):
        m, k_ = f(d["p_model"].to_numpy(), d["y"].to_numpy()), f(d["p_market"].to_numpy(), d["y"].to_numpy())
        out[f"MODEL_{k}"], out[f"MARKET_{k}"], out[f"DELTA_{k}"] = PM.cmean(m, g), PM.cmean(k_, g), PM.cmean(m - k_, g)
    out["no_vig_method"] = NO_VIG_METHOD
    return out


def _frames(root: Path) -> dict:
    pp = _p(root)
    prov = pd.read_parquet(pp["provenance"]) if pp["provenance"].exists() else pd.DataFrame()
    st = pd.read_parquet(pp["settled"]) if pp["settled"].exists() else pd.DataFrame()
    side = binary = pd.DataFrame()
    if len(prov) and len(st):
        s = prov.merge(st, on="prediction_id", how="inner")
        s = s[s["result"].isin(["WIN", "LOSS"])].copy()
        s["game_date"] = s["feature_cutoff"].map(lambda x: pd.Timestamp(x).tz_convert(WC.TZ).date())
        s["p"] = s["probability"] / s["p_nonpush"]
        s["y"] = s["side_win"]
        s["role_diagnostic"] = s["role_diagnostic"].fillna("NO_ROLE_HISTORY")
        s["line_range"] = s["line"].map(line_range)
        side = s
        ov = s[s["side"] == "OVER"].copy()
        un = s[s["side"] == "UNDER"].copy()
        un["p"], un["y"], un["side"] = 1 - un["p"], 1 - un["y"], "OVER"
        k = ["run_id", "game_id", "player_id", "market", "line"]
        binary = pd.concat([ov, un[~un.set_index(k).index.isin(ov.set_index(k).index)]], ignore_index=True)
    dist = pd.DataFrame()
    if pp["dist_settled"].exists() and pp["pmfs"].exists():
        dist = pd.read_parquet(pp["dist_settled"]).merge(
            pd.read_parquet(pp["pmfs"]).drop(columns=["pmf"]), on=["run_id", "game_id", "player_id", "market"], how="left")
        dist = dist[dist["result"] == "SCORED"].copy()
        dist["game_date"] = pd.to_datetime(dist["game_date"]).dt.date
        dist["role_diagnostic"] = dist["role_diagnostic"].fillna("NO_ROLE_HISTORY")
    return {"side": side, "binary": binary, "dist": dist, "market": market_frame(root, st),
            "diagnostics": SO.read_log(pp["diagnostics"])}


def market_frame(root: Path, settled: pd.DataFrame) -> pd.DataFrame:
    """Matched two-sided quotes (side-monitor QUOTE_SIDE records of the walk-forward runs) with their outcomes."""
    pp = _p(root)
    if not pp["sides"].exists() or not len(settled):
        return pd.DataFrame()
    q = SD.load(pp["sides"])
    q = q[q["two_sided"].astype(bool)] if len(q) else q
    if not len(q):
        return pd.DataFrame()
    prov = pd.read_parquet(pp["provenance"])[["run_id", "game_id", "player_id", "market", "line", "side", "prediction_id",
                                              "role_diagnostic", "feature_cutoff", "training_cutoff"]]
    out = settled.merge(prov, on="prediction_id")
    out = out[out["result"].isin(["WIN", "LOSS"])][["run_id", "game_id", "player_id", "market", "line", "side", "actual",
                                                    "role_diagnostic", "feature_cutoff", "training_cutoff"]]
    act = out.drop_duplicates(["run_id", "game_id", "player_id", "market", "line"])
    k = ["run_id", "game_id", "player_id", "market", "line", "sportsbook", "quote_available_at", "model_version"]
    q = q[k + ["side", "book_decimal_odds", "p_over", "p_under", "collector_received_at"]]
    o = q[q["side"] == "OVER"].set_index(k)
    u = q[q["side"] == "UNDER"].set_index(k)
    both = o.join(u[["book_decimal_odds"]], rsuffix="_under", how="inner").reset_index()
    both["p_market"] = (1 / both["book_decimal_odds"]) / (1 / both["book_decimal_odds"] + 1 / both["book_decimal_odds_under"])
    both["p_model"] = both["p_over"] / (both["p_over"] + both["p_under"])
    m = both.merge(act.drop(columns=["side"]), on=["run_id", "game_id", "player_id", "market", "line"], how="inner")
    m = m[m["actual"] != m["line"]].copy()
    m["y"] = (m["actual"] > m["line"]).astype(float)
    m["game_date"] = m["feature_cutoff"].map(lambda x: pd.Timestamp(x).tz_convert(WC.TZ).date())
    m["role_diagnostic"] = m["role_diagnostic"].fillna("NO_ROLE_HISTORY")
    m["line_range"] = m["line"].map(line_range)
    m["checkpoint"] = WC.CHECKPOINT
    return m


def _window(df: pd.DataFrame, day: date, n: int | None) -> pd.DataFrame:
    if not len(df):
        return df
    hi = day - timedelta(days=1)
    lo = WC.SEASON_FIRST_DATE if n is None else day - timedelta(days=n)
    return df[(df["game_date"] >= lo) & (df["game_date"] <= hi)]


def _by(df: pd.DataFrame, cols, fn) -> dict:
    if not len(df):
        return {}
    return {"|".join(map(str, k if isinstance(k, tuple) else (k,))): fn(g) for k, g in df.groupby(list(cols), sort=True)}


def report(root: Path, day: date) -> dict:
    """The day-D report (settled outcomes of ET dates before D). Deterministic: same inputs -> same bytes."""
    fr = _frames(root)
    out = {"report_date": day.isoformat(), "automatic_model_correction": AUTOMATIC_MODEL_CORRECTION,
           "checkpoint": WC.CHECKPOINT, "no_vig_method": NO_VIG_METHOD, "windows": {}}
    for w, n in WINDOWS.items():
        b, s, d, m = (_window(fr[k], day, n) for k in ("binary", "side", "dist", "market"))
        dist_fn = lambda g: {"n": int(len(g)), "games": int(g["game_id"].nunique()),
                             "pmf_log_score": PM.cmean(g["pmf_log_score"].to_numpy(), g["game_id"].to_numpy()),
                             "crps": PM.cmean(g["crps"].to_numpy(), g["game_id"].to_numpy())}
        out["windows"][w] = {
            "binary": {"ALL": block(b), "STAT": _by(b, ["market"], block), "ROLE": _by(b, ["role_diagnostic"], block),
                       "STAT_X_ROLE": _by(b, ["market", "role_diagnostic"], block), "LINE": _by(b, ["market", "line"], block),
                       "LINE_RANGE": _by(b, ["market", "line_range"], block), "CHECKPOINT": _by(b, ["checkpoint"], block),
                       "MODEL_VERSION": _by(b, ["model_version"], block)},
            "side": {"OVER_UNDER": _by(s, ["side"], block), "STAT_X_SIDE": _by(s, ["market", "side"], block)},
            "distribution": {"ALL": dist_fn(d) if len(d) else {"n": 0}, "STAT": _by(d, ["market"], dist_fn),
                             "STAT_X_ROLE": _by(d, ["market", "role_diagnostic"], dist_fn),
                             "MODEL_VERSION": _by(d, ["model_version"], dist_fn)},
            "market_relative": _market_section(m),
            "drift": drift(b, d, [x for x in fr["diagnostics"]])}
    out["roi"] = {"status": "NOT_AUTHORIZED (no actionable-betting policy; WAGERING_ENABLED = False)"}
    return out


def _market_section(m: pd.DataFrame) -> dict:
    if not len(m):
        return {"ALL": {"n": 0, "status": "BLOCKED_NO_QUOTES"}}
    # line level (books averaged, the existing convention) and per sportsbook
    k = ["run_id", "game_id", "player_id", "market", "line", "quote_available_at"]
    line = m.groupby(k, as_index=False).agg(p_market=("p_market", "mean"), p_model=("p_model", "first"), y=("y", "first"),
                                            role_diagnostic=("role_diagnostic", "first"), line_range=("line_range", "first"),
                                            checkpoint=("checkpoint", "first"))
    return {"ALL": market_block(line), "STAT": _by(line, ["market"], market_block),
            "ROLE": _by(line, ["role_diagnostic"], market_block), "STAT_X_ROLE": _by(line, ["market", "role_diagnostic"], market_block),
            "SPORTSBOOK": _by(m, ["sportsbook"], market_block), "CHECKPOINT": _by(line, ["checkpoint"], market_block),
            "LINE_RANGE": _by(line, ["market", "line_range"], market_block),
            "MODEL_VERSION": _by(m, ["model_version"], market_block)}


def clv(sides: pd.DataFrame, closing: pd.DataFrame | None) -> dict:
    """CLV of recorded quote sides against REAL later prices (same book / market / player / line / side, last quote
    received at or before tip). closing: (game_id, player_id, market, line, side, sportsbook, closing_decimal_odds).
    CLV = book_decimal / closing_decimal - 1 (side_monitor convention). Separate from proper scoring and from ROI."""
    if closing is None or not len(closing) or not len(sides):
        return {"status": "BLOCKED_NO_CLOSE_QUOTES", "n": 0}
    k = ["game_id", "player_id", "market", "line", "side", "sportsbook"]
    m = sides.merge(closing[k + ["closing_decimal_odds"]], on=k, how="inner")
    if not len(m):
        return {"status": "BLOCKED_NO_CLOSE_QUOTES", "n": 0}
    v = (m["book_decimal_odds"] / m["closing_decimal_odds"] - 1.0).to_numpy()
    return {"status": "OK", "n": int(len(m)), "clv": PM.cmean(v, m["game_id"].to_numpy()),
            "by_sportsbook": {b: PM.cmean((g["book_decimal_odds"] / g["closing_decimal_odds"] - 1).to_numpy(),
                                          g["game_id"].to_numpy()) for b, g in m.groupby("sportsbook")}}


def write_report(root: Path, day: date) -> dict:
    rep = report(root, day)
    p = _p(root)["reports"]
    p.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rep, indent=1, sort_keys=True, default=str) + "\n"
    f = p / f"{day.isoformat()}.json"
    if not f.exists() or f.read_text() != body:
        tmp = f.with_suffix(".tmp")
        tmp.write_text(body)
        tmp.replace(f)
    return {"path": str(f), "season_to_date_binary_n": rep["windows"]["SEASON_TO_DATE"]["binary"]["ALL"]["n"]}
