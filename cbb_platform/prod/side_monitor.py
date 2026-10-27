"""PROSPECTIVE OVER / UNDER SIDE MONITORING (architect ruling 2026-10-21, decision 3). DIAGNOSTICS ONLY.

Nothing here can change a forecast, a probability, a price, a threshold or a market's availability: the module reads
the run's priced distributions and matched quotes, writes its own append-only hash-chained log, and reports. Warnings
are labels for architect review, never corrections (AUTOMATIC_MODEL_CORRECTION = False).

Records (JSON Lines, hash-chained exactly like prod.ledger; the file is only opened in append mode):
    QUOTE_SIDE       one per matched VALID quote (game, player, market, line, side, book): what was known at prediction
                     time -- pregame exposure (min_mu, P(play), role, cold-start class, team games), model projection,
                     P(over) / P(under) / P(push), fair price, book odds, model edge, the opportunity's favored side.
                     Immutable; a duplicate record_id is refused.
    SIDE_SETTLEMENT  appended later for an existing record_id: actual, signed error, WIN / LOSS / PUSH, Brier and log
                     loss of the side, CLV only where a real later price was supplied (never fabricated). A DNP is
                     NO_ACTION (never a settled zero). May not carry any prediction-time field.

Opportunity = (run, game, player, market, line, book). Its FAVORED side is the side with the larger model edge among
the sides that book quotes; RECOMMENDED = favored side when that edge > 0, else NONE. (WAGERING_ENABLED stays False:
"recommended" is a monitoring label only.) One-sided recommendation frequency is reported, never treated as proof of bias.

Sign convention (the audit's): SIGNED_ERROR = projection - actual; > 0 OVER-PREDICTION, < 0 UNDER-PREDICTION.
Pregame exposure strata are the fixed ones of directional_audit (repository minutes_sigma_buckets on min_mu; HIGH
exposure = min_mu >= 30); opening-season and transfer strata carry forward the historical audit's RED subgroups.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform.prod import config as PC, directional_audit as DA, ledger as LG, pricing as PX

AUTOMATIC_MODEL_CORRECTION = False
SIDE_MONITOR_PATH = PC.LEDGER_PATH.parent / "side_monitor.jsonl"           # beside the shadow ledger
REPORT_PATH = PC.LEDGER_PATH.parent / "side_monitor_report.json"
COMBO_MARKETS = tuple(PC.COMBOS)
OPENING_TEAM_GAMES = DA.EARLY_TEAM_GAMES                       # team games so far <= 2 (historical RED subgroups)
# Over/Under side-monitor strata (quoted sides only; diagnostics). Distinct from prospective_monitor's report strata.
SIDE_MONITOR_OPENING_SEASON_MARKETS = ("PTS", "AST", "STL", "TOV")   # opening season: team games so far <= 2
TRANSFER_CLASS = "COLD_TRANSFER_IN"
SIDE_MONITOR_TRANSFER_IN_MARKETS = ("STL", "STOCKS", "TOV", "REB")      # cold_start_class == COLD_TRANSFER_IN
HIGH_EXPOSURE_FOCUS = "PTS"
Z = 1.959963984540054
STAT_COLS = {"PTS": "points", "REB": "reb", "AST": "ast", "FG3M": "three_pm", "STL": "stl", "BLK": "blk", "TOV": "tov"}

QUOTE_SIDE_FIELDS = (
    "record_id", "opportunity_id", "run_id", "model_version", "data_label", "prediction_as_of", "game_id", "game",
    "game_date", "week", "player_id", "player", "team_id", "market", "market_kind", "line", "side", "sportsbook",
    "book_decimal_odds", "book_american_odds", "quote_available_at", "collector_received_at",
    "predicted_minutes", "p_play", "role_pregame", "cold_start_class", "team_games_pregame", "exposure_band",
    "p_play_band", "high_exposure", "opening_season", "transfer_in",
    "projection", "p_over", "p_under", "p_push", "p_side", "p_side_nonpush", "fair_decimal", "fair_american",
    "model_edge", "favored_side", "recommended_side", "two_sided")
SETTLEMENT_FIELDS = ("record_id", "settled_at", "result_source", "participation", "actual", "signed_error", "result",
                     "side_win", "brier", "log_loss", "closing_decimal_odds", "clv")


class MonitorViolation(RuntimeError):
    pass


# ============================================================================ append-only log (prod.ledger chain)
def _records(path: Path):
    if not Path(path).exists():
        return
    with open(path) as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                yield n, json.loads(line)


def verify(path: Path = SIDE_MONITOR_PATH) -> dict:
    prev, n, ids, setts = LG.GENESIS, 0, set(), 0
    for n, rec in _records(path):
        body = {k: v for k, v in rec.items() if k != "row_hash"}
        if rec.get("prev_row_hash") != prev:
            raise MonitorViolation(f"line {n}: chain broken (prev_row_hash)")
        if hashlib.sha256(LG._canon(body).encode()).hexdigest() != rec.get("row_hash"):
            raise MonitorViolation(f"line {n}: row_hash mismatch (record edited)")
        t = rec.get("record_type")
        if t == "QUOTE_SIDE":
            if rec["body"]["record_id"] in ids:
                raise MonitorViolation(f"line {n}: duplicate record {rec['body']['record_id']}")
            ids.add(rec["body"]["record_id"])
        elif t == "SIDE_SETTLEMENT":
            if rec["body"]["record_id"] not in ids:
                raise MonitorViolation(f"line {n}: settlement of an unknown record")
            setts += 1
        else:
            raise MonitorViolation(f"line {n}: unknown record type {t}")
        prev = rec["row_hash"]
    return {"rows": n, "quote_sides": len(ids), "settlements": setts, "head": prev, "CHAIN_VALID": True}


def append(records: list[dict], path: Path = SIDE_MONITOR_PATH) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = verify(path)["head"]
    known, settled = set(), set()
    for _, r in _records(path):
        (known if r["record_type"] == "QUOTE_SIDE" else settled).add(r["body"]["record_id"])
    lines = []
    for r in records:
        t, body = r["record_type"], r["body"]
        if t == "QUOTE_SIDE":
            miss = [k for k in QUOTE_SIDE_FIELDS if k not in body]
            if miss:
                raise MonitorViolation(f"quote-side record missing fields {miss}")
            if body["record_id"] in known:
                raise MonitorViolation(f"record {body['record_id']} already recorded (immutable)")
            known.add(body["record_id"])
        elif t == "SIDE_SETTLEMENT":
            extra = sorted(set(body) - set(SETTLEMENT_FIELDS))
            if extra:
                raise MonitorViolation(f"settlement may not carry prediction-time fields: {extra}")
            if body.get("record_id") not in known:
                raise MonitorViolation("settlement of an unknown record")
            if body["record_id"] in settled:
                raise MonitorViolation(f"record {body['record_id']} already settled")
            settled.add(body["record_id"])
        else:
            raise MonitorViolation(f"unknown record type {t}")
        rec = {"record_type": t, "body": body, "prev_row_hash": prev}
        rec["row_hash"] = hashlib.sha256(LG._canon(rec).encode()).hexdigest()
        prev = rec["row_hash"]
        lines.append(LG._canon(rec) + "\n")
    with open(path, "a") as f:                                    # append only
        f.writelines(lines)
        f.flush()
        os.fsync(f.fileno())
    return {"appended": len(lines), "head": prev}


def load(path: Path = SIDE_MONITOR_PATH) -> pd.DataFrame:
    """QUOTE_SIDE records left-joined with their settlement (if any)."""
    q = [r["body"] for _, r in _records(path) if r["record_type"] == "QUOTE_SIDE"]
    s = [r["body"] for _, r in _records(path) if r["record_type"] == "SIDE_SETTLEMENT"]
    d = pd.DataFrame(q, columns=list(QUOTE_SIDE_FIELDS))
    st = pd.DataFrame(s, columns=list(SETTLEMENT_FIELDS))
    return d.merge(st, on="record_id", how="left")


# ============================================================================ quote-side records (prediction time)
def _iso(x):
    return None if x is None or (not isinstance(x, str) and pd.isna(x)) else pd.Timestamp(x).isoformat()


def _num(x):
    return None if x is None or pd.isna(x) else float(x)


def pregame_team_games(features_db: Path, game_ids) -> pd.DataFrame:
    """Pregame team games so far per (game, team): max(pp_team_games) of the ASOF feature rows -- the same quantity
    as joint_minutes_v2's team_games (the EARLY_SEASON stratum). Read-only; not a model input here."""
    import duckdb
    con = duckdb.connect(str(features_db), read_only=True)
    con.register("g", pd.DataFrame({"canonical_game_id": sorted(set(game_ids))}))
    df = con.execute("""SELECT canonical_game_id AS game_id, canonical_team_id AS team_id,
                               coalesce(max(pp_team_games), 0)::DOUBLE AS team_games
                        FROM feat.player_features WHERE canonical_game_id IN (SELECT canonical_game_id FROM g)
                        GROUP BY 1, 2""").df()
    con.close()
    return df


def exposure_frame(rows: pd.DataFrame, team_games: pd.DataFrame | None = None) -> pd.DataFrame:
    """Pregame exposure per (game, player) from the run's model inputs (+ pregame team games per (game, team))."""
    e = rows[["game_id", "team_id", "player_id", "min_mu", "p_play", "role_diagnostic", "cold_start_class"]].copy()
    if team_games is not None and len(team_games):
        e = e.merge(team_games[["game_id", "team_id", "team_games"]], on=["game_id", "team_id"], how="left")
    else:
        e["team_games"] = np.nan
    return e.drop_duplicates(["game_id", "player_id"])


def quote_side_records(dist: pd.DataFrame, quotes: pd.DataFrame, exposure: pd.DataFrame, meta: dict) -> list[dict]:
    """One QUOTE_SIDE body per matched VALID quote of a priced (game, player, market). Reads the same pmf the board
    prices; never alters it."""
    if dist is None or not len(dist) or quotes is None or not len(quotes):
        return []
    q = quotes[quotes["quote_status"] == "VALID"]
    if not len(q):
        return []
    ex = exposure.set_index(["game_id", "player_id"]) if len(exposure) else pd.DataFrame()
    aso = pd.Timestamp(meta["prediction_as_of"])
    aso = aso.tz_localize("UTC") if aso.tzinfo is None else aso
    out = []
    for d in dist[dist["method"] != "NOT_PRICED"].itertuples(index=False):
        qq = q[(q["canonical_game_id"] == d.game_id) & (q["canonical_player_id"] == d.player_id) & (q["stat"] == d.market)]
        if not len(qq):
            continue
        x = ex.loc[(d.game_id, d.player_id)] if len(ex) and (d.game_id, d.player_id) in ex.index else None
        mu = _num(x["min_mu"]) if x is not None else None
        pp = _num(x["p_play"]) if x is not None else _num(d.p_play)
        tg = _num(x["team_games"]) if x is not None else None
        cold = None if x is None or pd.isna(x["cold_start_class"]) else str(x["cold_start_class"])
        gd = meta.get("game_dates", {}).get(d.game_id)
        gdate = pd.Timestamp(gd) if gd is not None else aso.tz_convert("America/New_York").tz_localize(None).normalize()
        iso = gdate.isocalendar()
        common = {
            "run_id": meta["run_id"], "model_version": meta["model_version"], "data_label": meta.get("data_label", "LIVE"),
            "prediction_as_of": aso.isoformat(), "game_id": d.game_id, "game": getattr(d, "game_label", None),
            "game_date": str(gdate.date()), "week": f"{iso[0]}-W{iso[1]:02d}", "player_id": d.player_id,
            "player": getattr(d, "player_label", None), "team_id": d.team_id, "market": d.market,
            "market_kind": "COMBO" if d.market in COMBO_MARKETS else "STAT",
            "predicted_minutes": mu, "p_play": pp,
            "role_pregame": None if x is None else str(x["role_diagnostic"]), "cold_start_class": cold,
            "team_games_pregame": tg,
            "exposure_band": str(DA.min_band([np.nan if mu is None else mu])[0]),
            "p_play_band": str(DA.pplay_band([np.nan if pp is None else pp])[0]),
            "high_exposure": bool(mu is not None and mu >= DA.HIGH_EXPOSURE_MIN),
            "opening_season": bool(tg is not None and tg <= OPENING_TEAM_GAMES),
            "transfer_in": cold == TRANSFER_CLASS, "projection": float(d.projection)}
        for (line, book), g in qq.groupby(["line", "sportsbook"], sort=True):
            try:
                pr = PX.price_line(d.pmf, float(line))
            except PX.InvalidProbabilities:
                continue                                          # unpriceable line: the board records its reason
            sides = {}
            for r in g.sort_values("decimal_odds", ascending=False).drop_duplicates("side").itertuples(index=False):
                sv = PX.side_view(pr, r.side)
                sides[r.side] = (r, sv, PX.expected_value(sv["win"], sv["lose"], float(r.decimal_odds)))
            fav = max(sides, key=lambda s: sides[s][2])
            rec_side = fav if sides[fav][2] > 0 else "NONE"
            opp = hashlib.sha256(f"{meta['run_id']}|{d.game_id}|{d.player_id}|{d.market}|{float(line)}|{book}".encode()).hexdigest()
            for side, (r, sv, edge) in sorted(sides.items()):
                nonpush = sv["win"] / pr["p_nonpush"] if pr["p_nonpush"] > 0 else None
                out.append({**common, "record_id": hashlib.sha256(f"{opp}|{side}".encode()).hexdigest(),
                            "opportunity_id": opp, "line": float(line), "side": side, "sportsbook": book,
                            "book_decimal_odds": float(r.decimal_odds), "book_american_odds": _num(r.american_odds),
                            "quote_available_at": _iso(r.quote_available_at),
                            "collector_received_at": _iso(r.collector_received_at),
                            "p_over": pr["p_over"], "p_under": pr["p_under"], "p_push": pr["p_push"],
                            "p_side": sv["win"], "p_side_nonpush": nonpush, "fair_decimal": sv["fair_decimal"],
                            "fair_american": sv["fair_american"], "model_edge": edge, "favored_side": fav,
                            "recommended_side": rec_side, "two_sided": len(sides) == 2})
    return out


# ============================================================================ settlement (after outcomes are final)
def actuals_from_canonical(db: Path, game_ids) -> pd.DataFrame:
    """Final player box rows of finished games -> (game_id, player_id, participation, base stats, received_at).
    A player with no box row or zero / null minutes is DNP (no action), never a settled zero."""
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    con.register("g", pd.DataFrame({"canonical_game_id": sorted(set(game_ids))}))
    cols = ", ".join(f"{c} AS {s}" for s, c in STAT_COLS.items())
    df = con.execute(f"""SELECT canonical_game_id AS game_id, canonical_player_id AS player_id, minutes, {cols},
                                received_at FROM fact.player_games WHERE canonical_game_id IN (SELECT canonical_game_id FROM g)""").df()
    con.close()
    df["participation"] = np.where(df["minutes"].fillna(0) > 0, "PLAYED", "DNP")
    return df


def _actual(row: pd.Series, market: str):
    if market in PC.COMBOS:
        v = [row.get(s) for s in PC.COMBOS[market]]
        return None if any(x is None or pd.isna(x) for x in v) else float(sum(v))
    v = row.get(market)
    return None if v is None or pd.isna(v) else float(v)


def settlement_records(path: Path, actuals: pd.DataFrame, settled_at, result_source: str,
                       closing: pd.DataFrame | None = None) -> list[dict]:
    """SIDE_SETTLEMENT bodies for every unsettled QUOTE_SIDE record whose game appears in `actuals` (games the caller
    has confirmed final). closing: optional real later prices (record_id, closing_decimal_odds); otherwise CLV = None."""
    d = load(path)
    d = d[d["settled_at"].isna() & d["game_id"].isin(set(actuals["game_id"]))]
    a = actuals.drop_duplicates(["game_id", "player_id"]).set_index(["game_id", "player_id"])
    close = {} if closing is None or not len(closing) else closing.set_index("record_id")["closing_decimal_odds"].to_dict()
    out = []
    for r in d.itertuples(index=False):
        key = (r.game_id, r.player_id)
        row = a.loc[key] if key in a.index else None
        base = {"record_id": r.record_id, "settled_at": pd.Timestamp(settled_at).isoformat(), "result_source": result_source,
                "closing_decimal_odds": None, "clv": None}
        y = None if row is None or row["participation"] != "PLAYED" else _actual(row, r.market)
        if y is None:
            out.append({**base, "participation": "DNP" if row is None or row["participation"] != "PLAYED" else "PLAYED",
                        "actual": None, "signed_error": None, "result": "NO_ACTION", "side_win": None, "brier": None,
                        "log_loss": None})
            continue
        res = "PUSH" if y == r.line else ("WIN" if (y > r.line) == (r.side == "OVER") else "LOSS")
        win = None if res == "PUSH" else float(res == "WIN")
        p = r.p_side_nonpush
        cd = close.get(r.record_id)
        out.append({**base, "participation": "PLAYED", "actual": y, "signed_error": float(r.projection) - y, "result": res,
                    "side_win": win,
                    "brier": None if win is None or p is None else float((p - win) ** 2),
                    "log_loss": None if win is None or p is None else float(-np.log(np.clip(p if win else 1 - p, 1e-12, 1))),
                    "closing_decimal_odds": None if cd is None else float(cd),
                    "clv": None if cd is None else float(r.book_decimal_odds / float(cd) - 1.0)})
    return out


# ============================================================================ aggregation
def _cluster_mean(v: np.ndarray, g: np.ndarray) -> dict:
    """Mean with a game-clustered standard error and 95% interval (None when undetermined)."""
    ok = np.isfinite(v)
    v, g = v[ok], g[ok]
    n = len(v)
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


def _wilson(k: int, n: int) -> dict:
    if n == 0:
        return {"k": 0, "n": 0, "share": None, "lo": None, "hi": None}
    p = k / n
    den = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / den
    h = Z * np.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / den
    return {"k": int(k), "n": int(n), "share": p, "lo": float(c - h), "hi": float(c + h)}


def side_counters(d: pd.DataFrame) -> dict:
    """Quoted-opportunity counters (prediction time). Shares are of opportunities; edges / probabilities of records."""
    if not len(d):
        return {"opportunities": 0}
    opp = d.drop_duplicates("opportunity_id")
    n = len(opp)
    fo, fu = int((opp["favored_side"] == "OVER").sum()), int((opp["favored_side"] == "UNDER").sum())
    ro, ru = int((opp["recommended_side"] == "OVER").sum()), int((opp["recommended_side"] == "UNDER").sum())
    eo = d.loc[d["side"] == "OVER", "model_edge"].astype(float)
    eu = d.loc[d["side"] == "UNDER", "model_edge"].astype(float)
    return {"opportunities": n, "quote_side_records": int(len(d)), "two_sided_opportunities": int(opp["two_sided"].sum()),
            "over_favored": fo, "under_favored": fu, "over_share": fo / n, "under_share": fu / n,
            "over_recommended": ro, "under_recommended": ru, "no_recommendation": n - ro - ru,
            "under_recommended_share": _wilson(ru, ro + ru),
            "mean_over_edge": float(eo.mean()) if len(eo) else None, "mean_under_edge": float(eu.mean()) if len(eu) else None,
            "mean_p_over": float(opp["p_over"].astype(float).mean()), "mean_p_under": float(opp["p_under"].astype(float).mean()),
            "mean_p_push": float(opp["p_push"].astype(float).mean())}


def side_calibration(d: pd.DataFrame, side: str, recommended_only: bool = True) -> dict:
    """Settled calibration of one side (default: opportunities where that side was RECOMMENDED)."""
    x = d[(d["side"] == side) & d["result"].isin(["WIN", "LOSS", "PUSH"])]
    if recommended_only:
        x = x[x["recommended_side"] == side]
    if not len(x):
        return {"side": side, "settled": 0}
    dec = x[x["result"] != "PUSH"]
    g = dec["game_id"].to_numpy()
    out = {"side": side, "settled": int(len(x)), "wins": int((x["result"] == "WIN").sum()),
           "losses": int((x["result"] == "LOSS").sum()), "pushes": int((x["result"] == "PUSH").sum()),
           "expected_win_rate": _cluster_mean(dec["p_side_nonpush"].astype(float).to_numpy(), g),
           "realized_win_rate": _cluster_mean(dec["side_win"].astype(float).to_numpy(), g),
           "win_rate_gap_realized_minus_expected": _cluster_mean(
               (dec["side_win"].astype(float) - dec["p_side_nonpush"].astype(float)).to_numpy(), g),
           "brier": _cluster_mean(dec["brier"].astype(float).to_numpy(), g),
           "log_loss": _cluster_mean(dec["log_loss"].astype(float).to_numpy(), g)}
    clv = x["clv"].dropna().astype(float)
    out["clv"] = {"n": int(len(clv)), "mean": float(clv.mean())} if len(clv) else {"n": 0, "mean": None,
                                                                                    "note": "no valid later price"}
    return out


def projection_error(d: pd.DataFrame) -> dict:
    """Signed projection error per settled (game, player, market) -- one per player-game, latest prediction."""
    x = d[d["result"].isin(["WIN", "LOSS", "PUSH"])].sort_values("prediction_as_of")
    x = x.drop_duplicates(["game_id", "player_id", "market"], keep="last")
    r = _cluster_mean(x["signed_error"].astype(float).to_numpy(), x["game_id"].to_numpy())
    if r["value"] is not None and len(x):
        r["rel"] = r["value"] / max(float(x["actual"].astype(float).mean()), 1e-9)
    return r


def _group(d: pd.DataFrame) -> dict:
    return {"counters": side_counters(d), "projection_error": projection_error(d),
            "over": side_calibration(d, "OVER"), "under": side_calibration(d, "UNDER")}


def high_exposure_pts(d: pd.DataFrame) -> dict:
    """THE key prospective check: high-pregame-exposure PTS (min_mu >= 30)."""
    x = d[(d["market"] == HIGH_EXPOSURE_FOCUS) & d["high_exposure"].astype(bool)]
    c = side_counters(x)
    u = side_calibration(x, "UNDER")
    return {"definition": f"PTS, pregame min_mu >= {DA.HIGH_EXPOSURE_MIN}", "opportunities": c.get("opportunities", 0),
            "under_recommendation_share": c.get("under_recommended_share"),
            "under_expected_win_rate": u.get("expected_win_rate"), "under_realized_win_rate": u.get("realized_win_rate"),
            "under_win_rate_gap": u.get("win_rate_gap_realized_minus_expected"),
            "under_brier": u.get("brier"), "under_log_loss": u.get("log_loss"), "under_settled": u.get("settled", 0),
            "signed_projection_error": projection_error(x), "counters": c}


def report(path: Path = SIDE_MONITOR_PATH) -> dict:
    d = load(path)
    out = {"policy": "diagnostic only; AUTOMATIC_MODEL_CORRECTION = False; one-sided frequency is not proof of bias",
           "sign_convention": "signed_error = projection - actual; > 0 OVER-PREDICTION, < 0 UNDER-PREDICTION",
           "records": int(len(d)), "settled_records": int(d["settled_at"].notna().sum()) if len(d) else 0}
    if not len(d):
        out.update({"status": "NO_QUOTED_OPPORTUNITIES_YET", "warnings": [], "stop_for_architect_review": False})
        return out
    out["overall"] = _group(d)
    out["by"] = {k: {str(v): _group(g) for v, g in d.groupby(col, sort=True)}
                 for k, col in (("market", "market"), ("market_kind", "market_kind"), ("exposure_band", "exposure_band"),
                                ("role_pregame", "role_pregame"), ("sportsbook", "sportsbook"), ("day", "game_date"),
                                ("week", "week"))}
    out["high_exposure_pts"] = high_exposure_pts(d)
    out["opening_season"] = {m: _group(d[d["opening_season"].astype(bool) & (d["market"] == m)]) for m in SIDE_MONITOR_OPENING_SEASON_MARKETS}
    out["transfer"] = {m: _group(d[d["transfer_in"].astype(bool) & (d["market"] == m)]) for m in SIDE_MONITOR_TRANSFER_IN_MARKETS}
    out["warnings"] = warnings(out, d)
    out["stop_for_architect_review"] = any(w["code"] == "STOP_HIGH_EXPOSURE_UNDER_PROJECTION_WITH_POOR_UNDER_CALIBRATION"
                                           for w in out["warnings"])
    out["status"] = "TRACKING"
    return out


# ============================================================================ warnings (labels only)
def _below(ci: dict | None, ref: float = 0.0) -> bool:
    return bool(ci and ci.get("hi") is not None and ci["hi"] < ref)


def _above(ci: dict | None, ref: float = 0.0) -> bool:
    return bool(ci and ci.get("lo") is not None and ci["lo"] > ref)


def _w(code: str, scope: str, evidence: dict) -> dict:
    return {"code": code, "scope": scope, "evidence": evidence, "action": "NONE (diagnostic; architect review)"}


def warnings(rep: dict, d: pd.DataFrame) -> list[dict]:
    """Diagnostic warnings. A warning fires only when the 95% game-clustered interval excludes the calibrated value in
    the stated direction -- no sample-size threshold is invented; N and intervals are always reported."""
    out = []
    he = rep["high_exposure_pts"]
    pe = he["signed_projection_error"]
    if _below(pe):
        out.append(_w("PTS_HIGH_EXPOSURE_UNDER_BIAS_WARNING", "PTS min_mu>=30", pe))
    low = d[d["exposure_band"].isin(["MIN_LT10", "MIN_10_20"])]
    for m, g in low.groupby("market"):
        e = projection_error(g)
        if _above(e):
            out.append(_w("LOW_EXPOSURE_OVER_BIAS_WARNING", f"{m} min_mu<20", e))
    for m, g in rep["opening_season"].items():
        e = g["projection_error"]
        if _below(e) or _above(e):
            out.append(_w("EARLY_SEASON_DIRECTIONAL_WARNING", f"{m} team_games<={OPENING_TEAM_GAMES}", e))
    for m, g in rep["transfer"].items():
        e = g["projection_error"]
        if _below(e) or _above(e):
            out.append(_w("TRANSFER_DIRECTIONAL_WARNING", f"{m} {TRANSFER_CLASS}", e))
    for m, g in rep["by"]["market"].items():
        for side in ("over", "under"):
            gap = g[side].get("win_rate_gap_realized_minus_expected")
            if _below(gap):
                out.append(_w("SIDE_CALIBRATION_WARNING", f"{m} {side.upper()} recommended", gap))
    for m, g in rep["by"]["market"].items():
        s = g["counters"].get("under_recommended_share")
        if s and s.get("n") and (_above(s, 0.75) or _below(s, 0.25)):
            out.append(_w("OVER_UNDER_CONCENTRATION_WARNING", f"{m} recommended sides",
                          {**s, "note": "concentration alone is not evidence of bias"}))
    gap = he.get("under_win_rate_gap")
    if _below(pe) and _below(gap):                            # ruling item 7: report and STOP, never repair
        out.append(_w("STOP_HIGH_EXPOSURE_UNDER_PROJECTION_WITH_POOR_UNDER_CALIBRATION", "PTS min_mu>=30",
                      {"signed_projection_error": pe, "under_win_rate_gap": gap}))
    return out


# ============================================================================ CLI
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.side_monitor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--path", type=Path, default=SIDE_MONITOR_PATH)
    r.add_argument("--out", type=Path, default=REPORT_PATH)
    s = sub.add_parser("settle", help="append settlements for FINAL games from a canonical box-score database")
    s.add_argument("--path", type=Path, default=SIDE_MONITOR_PATH)
    s.add_argument("--canonical-db", type=Path, required=True)
    s.add_argument("--final-game-ids", nargs="+", required=True)
    s.add_argument("--result-source", default="CBBD_FINAL_BOX")
    v = sub.add_parser("verify")
    v.add_argument("--path", type=Path, default=SIDE_MONITOR_PATH)
    a = ap.parse_args(argv)
    if a.cmd == "verify":
        print(json.dumps(verify(a.path), indent=1))
    elif a.cmd == "settle":
        act = actuals_from_canonical(a.canonical_db, a.final_game_ids)
        recs = settlement_records(a.path, act, pd.Timestamp.now(tz="UTC"), a.result_source)
        print(json.dumps(append([{"record_type": "SIDE_SETTLEMENT", "body": b} for b in recs], a.path), indent=1))
    else:
        rep = report(a.path)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rep, indent=1, default=str))
        print(json.dumps({"records": rep["records"], "status": rep["status"], "warnings": [w["code"] for w in rep["warnings"]],
                          "stop_for_architect_review": rep["stop_for_architect_review"]}, indent=1))


if __name__ == "__main__":
    main()
