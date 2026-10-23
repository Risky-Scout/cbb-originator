"""PROSPECTIVE 2026-27 SHADOW MONITORING AND REPORTS. DIAGNOSTICS ONLY (AUTOMATIC_MODEL_CORRECTION = False).

Reads only the evidence tree written by shadow_tick (ticks/*/projections, predictions_quoted, quote_sides, actuals,
side_settlements, settlements) and writes reports/cumulative.{json,md} and reports/daily/<ET date>.{json,md}. Nothing
here can change a forecast, a price or a threshold, and no warning reopens historical research or triggers a refit:
warnings are labels for the architect. No launch sample threshold is defined: every number carries its N, and means
carry a game-clustered 95% interval where one is defined.

Conventions (as the historical audits): signed error = forecast - actual (> 0 over-forecast); relative error = signed
error / mean actual. Quoted-line scores use the model's P(over | no push) at the quoted line against the realized
over / under (pushes and DNP excluded; a DNP is NO_ACTION, never a zero). The market benchmark uses, per sportsbook
quoting BOTH sides of the same (game, player, market, line) in the same snapshot, the normalized reciprocal decimal odds
p_over = (1/d_over) / (1/d_over + 1/d_under), averaged over those books; it is compared with the model only on exactly
those lines (same game, player, market, line and cutoff) and the same outcomes.
"""
from __future__ import annotations

import gzip
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform.prod import config as PC, side_monitor as SD

AUTOMATIC_MODEL_CORRECTION = False
DATA_INTEGRITY_REASONS = {"FUTURE_LEAKAGE", "STALE_STATE", "MISSING_PLAYER_STATE", "POST_CUTOFF_DATA", "MISSING_ROSTER",
                          "MISSING_SCHEDULE", "UNKNOWN_TEAM", "AMBIGUOUS_GAME", "SCORING_FAILURE", "NON_PSD_DEPENDENCE",
                          "PMF_NOT_NORMALIZED", "UNSUPPORTED_ROTATION_SHOCK", "PREDICTION_AFTER_CUTOFF",
                          "WRONG_PRODUCTION_SEASON"}
HIGH_EXPOSURE_MIN = 30.0
EARLY_MARKETS = ("PTS", "AST", "STL", "TOV", "PRA", "PR", "PA")
TRANSFER_MARKETS = ("PTS", "REB", "STL", "TOV", "STOCKS")
Z = 1.959963984540054


# ============================================================================ evidence
def _cat(ops_root: Path, name: str) -> pd.DataFrame:
    parts = [pd.read_parquet(p).assign(tick_id=p.parent.name) for p in sorted(Path(ops_root, "ticks").glob(f"*/{name}"))]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def load(ops_root: Path) -> dict:
    ops_root = Path(ops_root)
    ev = {k: _cat(ops_root, f"{k}.parquet") for k in ("projections", "predictions_quoted", "quote_sides", "actuals",
                                                       "side_settlements")}
    st = []
    for p in sorted(ops_root.glob("ticks/*/settlements.jsonl.gz")):
        with gzip.open(p, "rt") as f:
            st += [json.loads(l)["body"] for l in f if l.strip()]
    ev["settlements"] = pd.DataFrame(st)
    return ev


# ============================================================================ statistics
def cmean(v, g) -> dict:
    """Mean with a game-clustered SE and 95% interval (None when undetermined)."""
    v, g = np.asarray(v, dtype=float), np.asarray(g)
    ok = np.isfinite(v)
    v, g = v[ok], g[ok]
    n = int(len(v))
    if n == 0:
        return {"n": 0, "games": 0, "value": None, "se": None, "lo": None, "hi": None}
    m = float(v.mean())
    codes, _ = pd.factorize(g)
    G = int(codes.max()) + 1
    if G < 2:
        return {"n": n, "games": G, "value": m, "se": None, "lo": None, "hi": None}
    res = np.bincount(codes, weights=v - m, minlength=G)
    se = float(np.sqrt(G / (G - 1) * (res ** 2).sum()) / n)
    return {"n": n, "games": G, "value": m, "se": se, "lo": m - Z * se, "hi": m + Z * se}


def share(k: int, n: int) -> dict:
    return SD._wilson(int(k), int(n))


# ============================================================================ analysis tables
def player_table(ev: dict) -> pd.DataFrame:
    """One row per (game, player) projection with actuals (settled games only carry actuals)."""
    pj = ev["projections"]
    if not len(pj):
        return pd.DataFrame()
    pj = pj.drop_duplicates(["game_id", "player_id"], keep="first")
    act = ev["actuals"]
    if len(act):
        pj = pj.merge(act.drop_duplicates(["game_id", "player_id"]).drop(columns=["tick_id"], errors="ignore"),
                      on=["game_id", "player_id"], how="left")
    else:
        pj = pj.assign(participation=None)
    return pj


def line_table(ev: dict, players: pd.DataFrame) -> pd.DataFrame:
    """One row per quoted (game, player, market, line): model probabilities at the line, the no-vig market probability
    where both sides were quoted by the same book, and the realized outcome once settled."""
    qs = ev["quote_sides"]
    if not len(qs):
        return pd.DataFrame()
    key = ["game_id", "player_id", "market", "line"]
    first = qs.sort_values("record_id").drop_duplicates(key)
    keep = key + [c for c in ("p_over", "p_under", "p_push", "projection", "predicted_minutes", "p_play", "game_date",
                              "position_group", "cur_min", "evidence_bucket", "cold_start_class", "transfer_in",
                              "opening_season", "first_30_days", "high_exposure", "center", "cold_start_new", "clean",
                              "prediction_as_of", "target_cutoff") if c in first]
    lt = first[keep].copy()
    if "clean" not in lt:
        lt["clean"] = True
    two = qs.pivot_table(index=key + ["sportsbook"], columns="side", values="book_decimal_odds", aggfunc="max")
    if {"OVER", "UNDER"} <= set(two.columns):
        two = two.dropna(subset=["OVER", "UNDER"])
        io, iu = 1.0 / two["OVER"], 1.0 / two["UNDER"]
        two["nv_over"] = io / (io + iu)
        nv = two.groupby(level=key).agg(market_p_over=("nv_over", "mean"), market_books=("nv_over", "size")).reset_index()
        lt = lt.merge(nv, on=key, how="left")
    else:
        lt["market_p_over"], lt["market_books"] = np.nan, 0
    lt["model_p_over_nonpush"] = lt["p_over"] / (1.0 - lt["p_push"])
    if len(players) and "participation" in players:
        a = players.set_index(["game_id", "player_id"])
        part = [a["participation"].get((g, p)) if (g, p) in a.index else None for g, p in zip(lt["game_id"], lt["player_id"])]
        lt["participation"] = part
        lt["actual"] = [a[f"act_{m}"].get((g, p)) if (g, p) in a.index and f"act_{m}" in a else None
                        for g, p, m in zip(lt["game_id"], lt["player_id"], lt["market"])]
    else:
        lt["participation"], lt["actual"] = None, None
    y = pd.to_numeric(lt["actual"], errors="coerce")
    lt["settled"] = (lt["participation"] == "PLAYED") & y.notna()
    lt["push"] = lt["settled"] & (y == lt["line"])
    lt["over"] = np.where(lt["settled"] & ~lt["push"], (y > lt["line"]).astype(float), np.nan)
    p = lt["model_p_over_nonpush"].clip(1e-12, 1 - 1e-12)
    q = lt["market_p_over"].clip(1e-12, 1 - 1e-12)
    lt["model_brier"] = (lt["model_p_over_nonpush"] - lt["over"]) ** 2
    lt["model_log_loss"] = -(lt["over"] * np.log(p) + (1 - lt["over"]) * np.log(1 - p))
    lt["market_brier"] = (lt["market_p_over"] - lt["over"]) ** 2
    lt["market_log_loss"] = -(lt["over"] * np.log(q) + (1 - lt["over"]) * np.log(1 - q))
    return lt


def side_frame(ev: dict) -> pd.DataFrame:
    """QUOTE_SIDE records + their settlement in side_monitor.load() form (so the existing side counters apply)."""
    qs = ev["quote_sides"]
    if not len(qs):
        return pd.DataFrame(columns=list(SD.QUOTE_SIDE_FIELDS) + list(SD.SETTLEMENT_FIELDS))
    ss = ev["side_settlements"]
    d = qs.drop_duplicates("record_id")
    if len(ss):
        d = d.merge(ss.drop_duplicates("record_id")[list(SD.SETTLEMENT_FIELDS)], on="record_id", how="left")
    else:
        for c in SD.SETTLEMENT_FIELDS:
            if c != "record_id":
                d[c] = None
    for c in ("high_exposure", "opening_season", "transfer_in", "two_sided"):
        if c in d:
            d[c] = d[c].fillna(False).astype(bool)
    return d


# ============================================================================ blocks
def projection_block(players: pd.DataFrame, market: str) -> dict:
    """All scored players (quoted or not) with a settled PLAYED outcome: model mean vs actual, signed / relative error."""
    need = {f"proj_{market}", f"act_{market}", "participation", "priced", "clean"}
    if not len(players) or not need <= set(players.columns):
        return {"n": 0}
    x = players[(players["participation"] == "PLAYED") & players["priced"].fillna(False).astype(bool)
                & players["clean"].fillna(False).astype(bool)]
    f = pd.to_numeric(x[f"proj_{market}"], errors="coerce")
    y = pd.to_numeric(x[f"act_{market}"], errors="coerce")
    ok = f.notna() & y.notna()
    f, y, g = f[ok], y[ok], x.loc[ok, "game_id"]
    if not len(f):
        return {"n": 0}
    se = cmean((f - y).to_numpy(), g.to_numpy())
    my = float(y.mean())
    return {"n": int(len(f)), "games": se["games"], "mean_model": float(f.mean()), "mean_actual": my,
            "signed_error": se, "relative_error": None if my <= 0 or se["value"] is None else se["value"] / my}


def line_block(lt: pd.DataFrame) -> dict:
    """Quoted-line proper scores (one per line), model lean and P(over) / P(under)."""
    if not len(lt):
        return {"quoted_lines": 0}
    s = lt[lt["settled"] & ~lt["push"] & lt["clean"].fillna(True).astype(bool)]
    out = {"quoted_lines": int(len(lt)), "settled_lines": int(len(s)),
           "mean_model_p_over": float(lt["p_over"].mean()), "mean_model_p_under": float(lt["p_under"].mean()),
           "model_over_lean_lines": int((lt["model_p_over_nonpush"] > 0.5).sum()),
           "model_under_lean_lines": int((lt["model_p_over_nonpush"] < 0.5).sum()),
           "realized_over_rate": cmean(s["over"].to_numpy(), s["game_id"].to_numpy()),
           "model_brier": cmean(s["model_brier"].to_numpy(), s["game_id"].to_numpy()),
           "model_log_loss": cmean(s["model_log_loss"].to_numpy(), s["game_id"].to_numpy())}
    m = s[s["market_p_over"].notna()]
    out["market_benchmark"] = market_benchmark(m)
    return out


def market_benchmark(m: pd.DataFrame) -> dict:
    """Model vs no-vig market on identical lines and outcomes (difference < 0: the model scores better)."""
    if not len(m):
        return {"lines": 0, "status": "WAITING_FOR_TWO_SIDED_SETTLED_QUOTES"}
    g = m["game_id"].to_numpy()
    return {"lines": int(len(m)), "games": int(m["game_id"].nunique()),
            "model_brier": cmean(m["model_brier"].to_numpy(), g), "market_brier": cmean(m["market_brier"].to_numpy(), g),
            "brier_model_minus_market": cmean((m["model_brier"] - m["market_brier"]).to_numpy(), g),
            "model_log_loss": cmean(m["model_log_loss"].to_numpy(), g),
            "market_log_loss": cmean(m["market_log_loss"].to_numpy(), g),
            "log_loss_model_minus_market": cmean((m["model_log_loss"] - m["market_log_loss"]).to_numpy(), g),
            "convention": "per-book normalized reciprocal decimal odds (both sides, same book, same snapshot), "
                          "averaged over books; same game / player / market / line / cutoff only"}


def side_block(d: pd.DataFrame) -> dict:
    if not len(d):
        return {"opportunities": 0}
    c = SD.side_counters(d)
    out = {"counters": c, "over": SD.side_calibration(d, "OVER", recommended_only=False),
           "under": SD.side_calibration(d, "UNDER", recommended_only=False),
           "over_recommended": SD.side_calibration(d, "OVER"), "under_recommended": SD.side_calibration(d, "UNDER")}
    if c.get("opportunities"):
        out["over_share_of_favored"] = share(c["over_favored"], c["over_favored"] + c["under_favored"])
    return out


def stratum(players, lt, d, market, pmask=None, lmask=None, dmask=None) -> dict:
    P = players if pmask is None else players[pmask]
    L = lt[lt["market"] == market] if len(lt) else lt
    if lmask is not None and len(L):
        L = L[lmask.reindex(L.index, fill_value=False)]
    D = d[d["market"] == market] if len(d) else d
    if dmask is not None and len(D):
        D = D[dmask.reindex(D.index, fill_value=False)]
    return {"projection": projection_block(P, market), "quoted_lines": line_block(L), "sides": side_block(D)}


def _mask(df: pd.DataFrame, col: str, val=True) -> pd.Series:
    if not len(df) or col not in df:
        return pd.Series(False, index=df.index)
    return df[col] == val if not isinstance(val, bool) else df[col].fillna(False).astype(bool) == val


# ============================================================================ report
def build(ops_root: Path, date: str | None = None) -> dict:
    ev = load(ops_root)
    players = player_table(ev)
    if date is not None:
        for k in ("projections", "quote_sides"):
            if len(ev[k]) and "game_date" in ev[k]:
                ev[k] = ev[k][ev[k]["game_date"] == date]
        players = players[players["game_date"] == date] if len(players) else players
    lt = line_table(ev, players)
    d = side_frame(ev)
    rep = {"policy": "diagnostic only; AUTOMATIC_MODEL_CORRECTION = False; no launch sample threshold; architect decides",
           "sign_convention": "signed_error = forecast - actual (> 0 over-forecast)", "scope": date or "SEASON_TO_DATE",
           "evidence": {"player_projections": int(len(players)),
                        "settled_players_played": int((players["participation"] == "PLAYED").sum())
                        if len(players) and "participation" in players else 0,
                        "excluded_not_clean": int((~players["clean"].astype(bool)).sum()) if len(players) else 0,
                        "quoted_lines": int(len(lt)), "quote_side_records": int(len(d)),
                        "settled_quote_sides": int(d["result"].notna().sum()) if len(d) and "result" in d else 0}}
    # 14. high-exposure PTS
    he_p = _mask(players, "high_exposure")
    he_l = _mask(lt, "high_exposure")
    he_d = _mask(d, "high_exposure")
    he = stratum(players, lt, d, "PTS", he_p, he_l, he_d)
    pe = he["projection"].get("signed_error") or {}
    he["PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING"] = bool(pe.get("hi") is not None and pe["hi"] < 0)
    he["definition"] = f"PTS, pregame min_mu >= {HIGH_EXPOSURE_MIN}"
    rep["high_exposure_pts"] = he
    # 15. early season
    rep["early_season"] = {
        "team_games_le_2": {m: stratum(players, lt, d, m, _mask(players, "opening_season"), _mask(lt, "opening_season"),
                                       _mask(d, "opening_season")) for m in EARLY_MARKETS},
        "first_30_days": {m: stratum(players, lt, d, m, _mask(players, "first_30_days"), _mask(lt, "first_30_days"),
                                     _mask(d, "first_30_days")) for m in EARLY_MARKETS}}
    # 16. current-season evidence buckets (PTS)
    rep["evidence_buckets_pts"] = {b: stratum(players, lt, d, "PTS", _mask(players, "evidence_bucket", b),
                                              _mask(lt, "evidence_bucket", b), _mask(d, "evidence_bucket", b))
                                   for _, _, b in PC.EVIDENCE_BUCKETS}
    # 17. transfers / cold starts (existing cold_start_class only)
    rep["transfer_cold_start"] = {
        "COLD_TRANSFER_IN": {m: stratum(players, lt, d, m, _mask(players, "transfer_in"), _mask(lt, "transfer_in"),
                                        _mask(d, "transfer_in")) for m in TRANSFER_MARKETS},
        "COLD_NO_HISTORY": {m: stratum(players, lt, d, m, _mask(players, "cold_start_new"), _mask(lt, "cold_start_new"),
                                       _mask(d, "cold_start_new")) for m in TRANSFER_MARKETS}}
    # 18. centers (PTS), with the non-center reference
    rep["center_pts"] = {"C": stratum(players, lt, d, "PTS", _mask(players, "center"), _mask(lt, "center"), _mask(d, "center")),
                         "non_C": stratum(players, lt, d, "PTS", ~_mask(players, "center"), ~_mask(lt, "center"),
                                          ~_mask(d, "center"))}
    # 19. Over / Under side monitor (existing counters + warnings)
    sides = {"overall": side_block(d), "by_market": {m: side_block(g) for m, g in d.groupby("market")} if len(d) else {}}
    if len(d):
        srep = {"high_exposure_pts": SD.high_exposure_pts(d),
                "opening_season": {m: SD._group(d[d["opening_season"] & (d["market"] == m)]) for m in SD.OPENING_MARKETS},
                "transfer": {m: SD._group(d[d["transfer_in"] & (d["market"] == m)]) for m in SD.TRANSFER_MARKETS},
                "by": {"market": {m: SD._group(g) for m, g in d.groupby("market")}}}
        sides["warnings"] = SD.warnings(srep, d)
        sides["stop_for_architect_review"] = any(w["code"] == "STOP_HIGH_EXPOSURE_UNDER_PROJECTION_WITH_POOR_UNDER_CALIBRATION"
                                                 for w in sides["warnings"])
    else:
        sides["warnings"], sides["stop_for_architect_review"] = [], False
    rep["over_under_sides"] = sides
    # 22. market benchmark (all markets, then per market)
    lt_s = lt[lt["settled"] & ~lt["push"] & lt["market_p_over"].notna() & lt["clean"].fillna(True).astype(bool)] if len(lt) else lt
    rep["market_benchmark"] = {"all": market_benchmark(lt_s),
                               "by_market": {m: market_benchmark(g) for m, g in lt_s.groupby("market")} if len(lt_s) else {}}
    # all markets: projection error + quoted-line scores
    rep["by_market"] = {m: {"projection": projection_block(players[players["clean"].astype(bool)] if len(players) else players, m),
                            "quoted_lines": line_block(lt[lt["market"] == m] if len(lt) else lt)} for m in PC.MARKETS}
    w = [x["code"] for x in sides["warnings"]]
    if he["PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING"] and "PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING" not in w:
        w.append("PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING")
    rep["warnings"] = sorted(set(w))
    rep["warning_action"] = "NONE: diagnostic labels for architect review; no automatic correction, no research reopened"
    return rep


def operations(ops_root: Path, date: str | None = None) -> dict:
    """Operational counters (from the registry and ledgers), optionally for one ET date."""
    from cbb_platform.prod import ops_store as OS
    ops_root = Path(ops_root)

    def et(x):
        return None if not x else str(pd.Timestamp(x).tz_convert("America/New_York").date())
    reg = pd.DataFrame(OS.read_jsonl(ops_root / "registry" / "games.jsonl"))
    if len(reg) and date:
        reg = reg[reg["scheduled_tip"].map(et) == date]
    snaps = pd.DataFrame(OS.read_jsonl(ops_root / "registry" / "quote_snapshots.jsonl"))
    settl = pd.DataFrame(OS.read_jsonl(ops_root / "registry" / "settlements.jsonl"))
    odds = OS.read_jsonl(ops_root / "ledgers" / "odds_production_requests.jsonl")
    cbbd = OS.read_jsonl(ops_root / "ledgers" / "cbbd_production_requests.jsonl")
    if date:
        odds = [e for e in odds if et(e.get("requested_at")) == date]
        cbbd = [e for e in cbbd if et(e.get("requested_at")) == date]
        if len(snaps):
            gids = set(reg["game_id"]) if len(reg) else set()
            snaps = snaps[snaps["game_id"].isin(gids)]
        if len(settl):
            settl = settl[settl["game_id"].isin(set(reg["game_id"]) if len(reg) else set())]
    dec = reg.drop_duplicates("game_id", keep="last") if len(reg) else reg
    pred = dec[dec["status"] == "PREDICTED"] if len(dec) else dec
    blocked = int(sum(1 for r in (pred["game_reasons"] if len(pred) and "game_reasons" in pred else [])
                      if isinstance(r, list) and r)) if len(pred) else 0
    deviation = pd.to_numeric(pred["timing_deviation_seconds"], errors="coerce") if len(pred) and "timing_deviation_seconds" in pred else pd.Series(dtype=float)
    live = ("FETCHED", "FETCHED_UNCHANGED", "FAILED", "ATTEMPT_FAILED")
    return {"games_registered": int(len(dec)),
            "games_by_status": dec["status"].value_counts().to_dict() if len(dec) else {},
            "games_predicted": int(len(pred)), "games_predicted_with_game_blockers": blocked,
            "timing_deviation_minutes": {"n": int(deviation.notna().sum()),
                                         "min": None if deviation.dropna().empty else float(deviation.min() / 60),
                                         "median": None if deviation.dropna().empty else float(deviation.median() / 60),
                                         "max": None if deviation.dropna().empty else float(deviation.max() / 60)},
            "quote_snapshots": int(len(snaps)),
            "quote_snapshots_with_bookmakers": int((pd.to_numeric(snaps.get("bookmakers"), errors="coerce") > 0).sum()) if len(snaps) else 0,
            "games_settled": int(len(settl)),
            "odds_api": {"live_requests": sum(1 for e in odds if e.get("status") in live),
                         "credits_used": int(sum(int(e.get("cost") or 0) for e in odds)),
                         "quota_remaining_last": next((e.get("quota_remaining") for e in reversed(odds)
                                                       if e.get("quota_remaining") is not None), None)},
            "cbbd": {"live_requests": sum(1 for e in cbbd if e.get("status") in live),
                     "cache_hits": sum(1 for e in cbbd if e.get("status") == "CACHE_HIT"),
                     "refused": sum(1 for e in cbbd if str(e.get("status", "")).startswith("REFUSED"))}}


def daily_report_due(ops_root: Path, now, started) -> bool:
    return bool(_missing_daily(ops_root, now, started))


def _missing_daily(ops_root: Path, now, started) -> list[str]:
    now = pd.Timestamp(now)
    last = (now - pd.Timedelta(hours=PC.DAILY_REPORT_HOUR_UTC)).tz_convert("UTC").date() - timedelta(days=1)
    first = pd.Timestamp(started).tz_convert("America/New_York").date()
    out = []
    d = first
    while d <= last:
        if not (Path(ops_root) / "reports" / "daily" / f"{d}.json").exists():
            out.append(str(d))
        d = d + timedelta(days=1)
    return out


def next_daily_report_at(now) -> pd.Timestamp:
    now = pd.Timestamp(now).tz_convert("UTC")
    t = now.normalize() + pd.Timedelta(hours=PC.DAILY_REPORT_HOUR_UTC)
    return t if t > now else t + pd.Timedelta(days=1)


def _fmt(x, nd=3):
    if isinstance(x, dict):
        if "value" in x:
            if x["value"] is None:
                return f"n={x.get('n', 0)}"
            ci = f" [{x['lo']:.{nd}f}, {x['hi']:.{nd}f}]" if x.get("lo") is not None else ""
            return f"{x['value']:.{nd}f}{ci} (n={x['n']}, games={x.get('games')})"
        if "share" in x:
            return "n=0" if not x.get("n") else f"{x['share']:.3f} [{x['lo']:.3f}, {x['hi']:.3f}] ({x['k']}/{x['n']})"
    if x is None:
        return "-"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def _row(name: str, s: dict) -> str:
    p, l, sd = s["projection"], s["quoted_lines"], s["sides"]
    c = sd.get("counters", {})
    return (f"| {name} | {p.get('n', 0)} | {_fmt(p.get('mean_model'))} | {_fmt(p.get('mean_actual'))} | "
            f"{_fmt(p.get('signed_error'))} | {_fmt(p.get('relative_error'))} | {l.get('settled_lines', 0)}/{l.get('quoted_lines', 0)} | "
            f"{_fmt(l.get('model_brier'))} | {_fmt(l.get('model_log_loss'))} | "
            f"{c.get('over_favored', 0)}/{c.get('under_favored', 0)} |")


HDR = ("| stratum | N played | mean model | mean actual | signed error (95% CI) | rel. error | settled/quoted lines | "
       "quoted-line Brier | quoted-line log loss | Over/Under favored |\n|---|---|---|---|---|---|---|---|---|---|")


def markdown(rep: dict, ops: dict, title: str) -> str:
    L = [f"# {title}", "", "SHADOW_ONLY. Diagnostic only: no automatic model correction, no wagering, no public "
         "deployment. Every figure carries its N; no launch threshold is implied.", "",
         "## Operations", "", "```", json.dumps(ops, indent=1, default=str), "```", "",
         f"Evidence: {json.dumps(rep['evidence'])}", "", f"Warnings: {', '.join(rep['warnings']) or 'none'}", "",
         "## High-exposure PTS (min_mu >= 30)", "", HDR, _row("PTS min_mu>=30", rep["high_exposure_pts"]), "",
         f"PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING: {rep['high_exposure_pts']['PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING']}", "",
         "Market benchmark (same lines): " + json.dumps(rep["high_exposure_pts"]["quoted_lines"].get("market_benchmark", {}),
                                                         default=str)[:600], "",
         "## Early season", "", HDR]
    for k, v in rep["early_season"].items():
        L += [_row(f"{m} {k}", s) for m, s in v.items()]
    L += ["", "## Current-season evidence buckets (PTS)", "", HDR] + [_row(b, s) for b, s in rep["evidence_buckets_pts"].items()]
    L += ["", "## Transfers / cold starts", "", HDR]
    for k, v in rep["transfer_cold_start"].items():
        L += [_row(f"{m} {k}", s) for m, s in v.items()]
    L += ["", "## Centers (PTS)", "", HDR] + [_row(k, s) for k, s in rep["center_pts"].items()]
    sd = rep["over_under_sides"]["overall"]
    c = sd.get("counters", {})
    L += ["", "## Over / Under sides", "",
          f"Opportunities {c.get('opportunities', 0)}; OVER favored {c.get('over_favored', 0)}, UNDER favored "
          f"{c.get('under_favored', 0)}; OVER share of favored {_fmt(sd.get('over_share_of_favored'))}; "
          f"mean edge OVER {_fmt(c.get('mean_over_edge'))}, UNDER {_fmt(c.get('mean_under_edge'))}.", "",
          "| side | settled | W/L/P | Brier | log loss |", "|---|---|---|---|---|"]
    for side in ("over", "under"):
        x = sd.get(side, {})
        L.append(f"| {side.upper()} | {x.get('settled', 0)} | {x.get('wins', 0)}/{x.get('losses', 0)}/{x.get('pushes', 0)} | "
                 f"{_fmt(x.get('brier'))} | {_fmt(x.get('log_loss'))} |")
    mb = rep["market_benchmark"]["all"]
    L += ["", "## Model vs no-vig market (identical lines)", "",
          f"Lines {mb.get('lines', 0)}. Brier model {_fmt(mb.get('model_brier'))}, market {_fmt(mb.get('market_brier'))}, "
          f"difference {_fmt(mb.get('brier_model_minus_market'))}. Log loss model {_fmt(mb.get('model_log_loss'))}, "
          f"market {_fmt(mb.get('market_log_loss'))}, difference {_fmt(mb.get('log_loss_model_minus_market'))}.", "",
          "## All markets", "", "| market | N played | signed error | rel. error | settled lines | Brier | log loss |",
          "|---|---|---|---|---|---|---|"]
    for m, s in rep["by_market"].items():
        p, l = s["projection"], s["quoted_lines"]
        L.append(f"| {m} | {p.get('n', 0)} | {_fmt(p.get('signed_error'))} | {_fmt(p.get('relative_error'))} | "
                 f"{l.get('settled_lines', 0)} | {_fmt(l.get('model_brier'))} | {_fmt(l.get('model_log_loss'))} |")
    return "\n".join(L) + "\n"


def write_reports(ops_root: Path, now, started) -> dict:
    """Cumulative report always; daily reports for every missing completed ET date and the last 3 dates."""
    from cbb_platform.prod import ops_store as OS
    ops_root = Path(ops_root)
    head = (OS.read_jsonl(ops_root / "CHAIN.jsonl") or [{"hash": OS.GENESIS}])[-1]["hash"]
    cum = {**build(ops_root), "operations": operations(ops_root), "evidence_chain_head": head}
    OS.write_json(ops_root / "reports" / "cumulative.json", cum)
    md = markdown(cum, cum["operations"], "CBB 2026-27 prospective shadow: season to date")
    p = ops_root / "reports" / "cumulative.md"
    if not p.exists() or p.read_text() != md:
        p.write_text(md)
    dates = set(_missing_daily(ops_root, now, started))
    last = (pd.Timestamp(now) - pd.Timedelta(hours=PC.DAILY_REPORT_HOUR_UTC)).tz_convert("UTC").date() - timedelta(days=1)
    first = pd.Timestamp(started).tz_convert("America/New_York").date()
    for k in range(3):
        d = last - timedelta(days=k)
        if d >= first:
            dates.add(str(d))
    for d in sorted(dates):
        rep = {**build(ops_root, d), "operations": operations(ops_root, d), "evidence_chain_head": head, "date_et": d}
        OS.write_json(ops_root / "reports" / "daily" / f"{d}.json", rep)
        md = markdown(rep, rep["operations"], f"CBB 2026-27 shadow daily report: {d} (ET)")
        p = ops_root / "reports" / "daily" / f"{d}.md"
        if not p.exists() or p.read_text() != md:
            p.write_text(md)
    return {"cumulative": True, "daily": sorted(dates), "warnings": cum["warnings"]}
