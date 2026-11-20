"""Game-market exports (Release B, SHADOW): Boards `Game Lines`, frozen pre-cutoff evaluations, Grades `Game Markets` +
`Summary` block, Performance game sheets and game CLV. REPORTING ONLY: no network, no refit; every probability is the
persisted canonical GameJointPMF (game.forecast) priced by game.markets.

Board grain: one row per GAME x MARKET_TYPE (core) = the best executable offer (max EV over books x lines x sides) at
the latest quote state <= min(now, cutoff), on the latest pre-cutoff game PMF. Model-only games (no quote) are listed
with DECISION = NO_MARKET_AVAILABLE (never a model failure). Game markets: IS_ACTIONABLE = FALSE, tier RESEARCH.

Frozen evaluation: written once per game after its cutoff (ops/evaluations/<D>/game_market_evaluations.jsonl): one
row per core market type with a pre-cutoff offer; never rewritten. Grading uses the promoted state's final score
(OT-inclusive full-game settlement, the convention of the quoted full-game markets):
    SPREAD/ALT  WIN if side margin + point > 0, PUSH if = 0     MONEYLINE  WIN / LOSS (no ties after OT)
    TOTAL/ALT, TEAM TOTALS  OVER WIN if score > point, PUSH if =, UNDER mirrored
CLV (evaluation only): the closing state = the last pre-tip capture within game_quotes.CLOSE_WINDOW_MIN.
    EXACT_LINE (same book / line / side), SAME_LINE_NEAREST_CLOSE (another book at the line), LINE_MOVED (no close at
    the line: entry vs consensus close line + directional movement), NO_CLOSE_QUOTE, NO_DEVIG_PAIR.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform.game import forecast as GF, markets as GM
from cbb_platform.prod import game_quotes as GQ

VERSION = "cbb-game-exports-1.0.0"
TZ = "America/New_York"
POLICY = "GAME_MARKETS_SHADOW_RESEARCH_v1 (Release B: never actionable until Release C validation)"
NO_MARKET = "NO_MARKET_AVAILABLE"
LINE_COLS = ("GAME", "GAME_ID", "TIP_ET", "HOME_TEAM", "AWAY_TEAM", "MARKET_TYPE", "MODEL_HOME_SCORE",
             "MODEL_AWAY_SCORE", "MODEL_MARGIN", "MODEL_TOTAL", "LINE", "SIDE", "PICK", "BOOK", "ODDS", "MODEL%",
             "MARKET%", "PUSH%", "PROBABILITY_EDGE", "EV_GROSS", "EV_NET", "BOOKS_QUOTED", "IS_ACTIONABLE",
             "SELECTION_TIER", "DECISION", "NO_BET_REASON", "CAPTURE_UTC", "QUOTE_ID", "FORECAST_ID", "PLAYER_FIT_ID",
             "GAME_MODEL_VERSION", "TEAM_RATING_STATE", "PMF_SHA256", "POLICY")
GRADE_COLS = ("GAME", "GAME_ID", "MARKET_TYPE", "HOME_TEAM", "AWAY_TEAM", "LINE", "PICK", "SIDE", "MODEL%", "MARKET%",
              "PUSH%", "EDGE", "EV", "BOOK", "ODDS", "HOME_FINAL", "AWAY_FINAL", "REALIZED_MARGIN", "REALIZED_TOTAL",
              "RESULT", "PROFIT", "IS_ACTIONABLE", "SELECTION_TIER", "CLV_STATUS", "CLV_PROBABILITY", "LINE_MOVE",
              "FORECAST_ID", "QUOTE_ID", "GAME_MODEL_VERSION")
SUMMARY_COLS = ("POPULATION", "MARKET_TYPE", "GRADABLE", "WINS", "LOSSES", "PUSHES", "VOIDS", "PENDING", "HIT_RATE",
                "BRIER", "LOG_LOSS", "PROFIT", "RETURN")
EVAL_FILE = "game_market_evaluations.jsonl"


def _iso(t):
    return None if t is None or pd.isna(t) else pd.Timestamp(t).isoformat()


def _et(t):
    return None if t is None or pd.isna(t) else pd.Timestamp(t).tz_convert(TZ).strftime("%Y-%m-%d %H:%M")


def american(d: float) -> int | None:
    if d is None or not d > 1:
        return None
    return int(round((d - 1) * 100)) if d >= 2 else int(round(-100 / (d - 1)))


def pick_label(c: dict, home: str, away: str) -> str:
    mt, s, p = GM.base(c["market_type"]), c["side"], c["point"]
    if mt == "MONEYLINE":
        return f"{home if s == 'HOME' else away} ML"
    if mt == "SPREAD":
        return f"{home if s == 'HOME' else away} {p:+g}"
    who = {"GAME_TOTAL": "", "HOME_TEAM_TOTAL": f"{home} ", "AWAY_TEAM_TOTAL": f"{away} "}[mt]
    return f"{who}{s.title()} {p:g}"


# ============================================================================ inputs
def game_pmfs(ops_root: Path, day: str, at=None, cutoffs: dict | None = None) -> pd.DataFrame:
    """Latest game PMF row per game with prediction_as_of <= at (and < the game's cutoff when known)."""
    fs = sorted((Path(ops_root) / "forecasts" / day).glob(f"*/{GF.FILE}"))
    if not fs:
        return pd.DataFrame()
    g = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    g["_t"] = pd.to_datetime(g["prediction_as_of"], utc=True)
    if at is not None:
        g = g[g["_t"] <= pd.Timestamp(at)]
    cut = pd.to_datetime(g["cutoff_utc"], utc=True)
    g = g[cut.isna() | (g["_t"] < cut)]
    return g.sort_values("_t").drop_duplicates("game_id", keep="last").drop(columns="_t").reset_index(drop=True)


def _offers(q: pd.DataFrame, gid: str) -> pd.DataFrame:
    x = q[q["game_id"] == gid]
    return x.rename(columns={})[["game_id", "book", "market_type", "side", "point", "decimal", "american", "quote_id",
                                 "market_last_update", "collector_received_at"]] if len(x) else x


# ============================================================================ Board: Game Lines
def board_rows(pmfs: pd.DataFrame, quotes: pd.DataFrame, names: dict, now, alternates: bool = False) -> tuple[list, dict]:
    now = pd.Timestamp(now)
    rows, stats = [], {"games": 0, "games_with_market": 0, "rows_with_market": 0, "model_only_games": 0}
    for r in pmfs.to_dict(orient="records"):
        stats["games"] += 1
        g = GF.pmf_of(r)
        cut = pd.Timestamp(r["cutoff_utc"]) if r.get("cutoff_utc") else now
        q = GQ.as_of(quotes[quotes["game_id"] == r["game_id"]], min(now, cut)) if len(quotes) else quotes
        cands = GM.candidates(g, _offers(q, r["game_id"]) if len(q) else None)
        best = GM.best_by_market(cands, alternates)
        h, a = names.get(r["home_team_id"], r["home_team_id"]), names.get(r["away_team_id"], r["away_team_id"])
        base = {"GAME": f"{a} @ {h}", "GAME_ID": r["game_id"], "TIP_ET": _et(r["scheduled_tip_utc"]), "HOME_TEAM": h,
                "AWAY_TEAM": a, "MODEL_HOME_SCORE": r["model_home_score"], "MODEL_AWAY_SCORE": r["model_away_score"],
                "MODEL_MARGIN": r["model_margin"], "MODEL_TOTAL": r["model_total"], "FORECAST_ID": r["forecast_id"],
                "PLAYER_FIT_ID": r.get("player_model_version"), "GAME_MODEL_VERSION": r["model_version"],
                "TEAM_RATING_STATE": r["ratings_state_id"], "PMF_SHA256": r["pmf_sha256"], "POLICY": POLICY,
                "IS_ACTIONABLE": False, "SELECTION_TIER": GM.TIER_RESEARCH}
        if not best:
            stats["model_only_games"] += 1
            rows.append({**base, "MARKET_TYPE": "ALL", "DECISION": NO_MARKET, "NO_BET_REASON": NO_MARKET,
                         "PICK": None, "MODEL%": r["p_home_win"], "SIDE": "HOME_WIN_PROB"})
            continue
        stats["games_with_market"] += 1
        for mt in [m for m in GM.CORE + GM.ALTS if m in best]:
            c = best[mt]
            d = GM.decision(c)
            books = len({x["book"] for x in cands if x["market_type"] == mt})
            rows.append({**base, "MARKET_TYPE": mt, "LINE": c["point"], "SIDE": c["side"],
                         "PICK": pick_label(c, h, a) if d["PICK_SIDE"] != GM.NO_POSITIVE_EV else GM.NO_POSITIVE_EV,
                         "BOOK": c["book"], "ODDS": american(c["decimal"]), "MODEL%": c["model_p"],
                         "MARKET%": c["fair_p"], "PUSH%": c["p_push"], "PROBABILITY_EDGE": c["edge"],
                         "EV_GROSS": c["ev"], "EV_NET": c["ev"], "BOOKS_QUOTED": books, "DECISION": d["DECISION"],
                         "NO_BET_REASON": d["NO_BET_REASON"], "CAPTURE_UTC": _iso(c["collector_received_at"]),
                         "QUOTE_ID": c["quote_id"]})
            stats["rows_with_market"] += 1
    return [[row.get(k) for k in LINE_COLS] for row in rows], stats


# ============================================================================ frozen evaluations
def eval_path(ops_root: Path, day: str) -> Path:
    return Path(ops_root) / "evaluations" / day / EVAL_FILE


def _hash(d: dict) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


def freeze(ops_root: Path, day: str, quotes: pd.DataFrame, names: dict, now) -> dict:
    """Write the evaluation of every game whose cutoff has passed and is not frozen yet (write-once)."""
    now = pd.Timestamp(now)
    p = eval_path(ops_root, day)
    done = {json.loads(l)["game_id"] for l in p.read_text().splitlines() if l.strip()} if p.exists() else set()
    pm = game_pmfs(ops_root, day)
    new = []
    for r in pm.to_dict(orient="records"):
        cut = pd.Timestamp(r["cutoff_utc"]) if r.get("cutoff_utc") else None
        if r["game_id"] in done or cut is None or now < cut:
            continue
        g = GF.pmf_of(r)
        q = GQ.as_of(quotes[quotes["game_id"] == r["game_id"]], cut) if len(quotes) else quotes
        cands = GM.candidates(g, _offers(q, r["game_id"]) if len(q) else None)
        best = GM.best_by_market(cands)
        for mt, c in best.items():
            e = {"kind": "GAME_MARKET_EVALUATION", "game_id": r["game_id"], "slate_date": day, "market_type": mt,
                 "home_team_id": r["home_team_id"], "away_team_id": r["away_team_id"],
                 "scheduled_tip_utc": r["scheduled_tip_utc"], "cutoff_utc": r["cutoff_utc"],
                 **{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in c.items()
                    if k not in ("game_id",)}, **GM.decision(c),
                 "forecast_id": r["forecast_id"], "pmf_sha256": r["pmf_sha256"], "game_model_version": r["model_version"],
                 "ratings_state_id": r["ratings_state_id"], "player_model_version": r.get("player_model_version"),
                 "frozen_at": now.isoformat(), "version": VERSION}
            e["collector_received_at"] = _iso(e.get("collector_received_at"))
            e["content_hash"] = _hash(e)
            new.append(e)
        new.append({"kind": "GAME_FROZEN", "game_id": r["game_id"], "markets": len(best), "frozen_at": now.isoformat()})
    if new:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            for e in new:
                f.write(json.dumps(e, sort_keys=True, default=str) + "\n")
    return {"frozen_games": len({e["game_id"] for e in new}), "evaluations": sum(e["kind"] == "GAME_MARKET_EVALUATION"
                                                                                for e in new)}


def evaluations(ops_root: Path, day: str) -> list[dict]:
    p = eval_path(ops_root, day)
    return [e for e in (json.loads(l) for l in p.read_text().splitlines() if l.strip())
            if e["kind"] == "GAME_MARKET_EVALUATION"] if p.exists() else []


# ============================================================================ settlement + CLV
def settle(e: dict, hp, ap) -> str:
    if hp is None or ap is None or pd.isna(hp) or pd.isna(ap):
        return "PENDING"
    mt, s, p = GM.base(e["market_type"]), e["side"], e.get("point")
    m = float(hp) - float(ap)
    if mt == "MONEYLINE":
        return "WIN" if (m > 0) == (s == "HOME") else "LOSS"
    if mt == "SPREAD":
        v = (m if s == "HOME" else -m) + float(p)
    else:
        sc = {"GAME_TOTAL": hp + ap, "HOME_TEAM_TOTAL": hp, "AWAY_TEAM_TOTAL": ap}[mt]
        v = (float(sc) - float(p)) * (1 if s == "OVER" else -1)
    return "WIN" if v > 0 else "PUSH" if v == 0 else "LOSS"


def profit(result: str, decimal: float) -> float | None:
    return {"WIN": decimal - 1, "LOSS": -1.0, "PUSH": 0.0}.get(result)


def clv(e: dict, close: pd.DataFrame) -> dict:
    c = close[(close["game_id"] == e["game_id"]) & (close["market_type"] == e["market_type"])] if len(close) else close
    if not len(c):
        return {"CLV_STATUS": "NO_CLOSE_QUOTE"}
    pt = e.get("point")
    same_side = c[c["side"] == e["side"]]
    at = same_side[same_side["point"].fillna(-9999) == (-9999 if pt is None else pt)]
    if not len(at):
        cl = same_side["point"].median() if len(same_side) else None
        mv = None
        if cl is not None and pt is not None:      # positive = the entry got the better number
            mv = float(cl - pt) if e["side"] == "OVER" else float(pt - cl)   # UNDER and spread/handicap sides: pt - cl
        return {"CLV_STATUS": "LINE_MOVED", "CLOSE_LINE": cl, "LINE_MOVE": mv}
    ex = at[at["book"] == e["book"]]
    row = (ex if len(ex) else at).iloc[-1]
    status = "EXACT_LINE" if len(ex) else "SAME_LINE_NEAREST_CLOSE"
    os_, op = GM.opposite(e["market_type"], e["side"], pt)
    other = c[(c["book"] == row["book"]) & (c["side"] == os_) & (c["point"].fillna(-9999) == (-9999 if op is None else op))]
    fair_close = GM.no_vig(row["decimal"], other["decimal"].iloc[-1]) if len(other) else None
    if fair_close is None or e.get("fair_p") is None:
        return {"CLV_STATUS": "NO_DEVIG_PAIR", "CLOSE_LINE": pt}
    return {"CLV_STATUS": status, "CLOSE_LINE": pt, "CLV_PROBABILITY": fair_close - e["fair_p"], "LINE_MOVE": 0.0}


def graded(ops_root: Path, day: str, finals: dict, close: pd.DataFrame, names: dict) -> list[dict]:
    """finals = {game_id: (home_points, away_points)} from the promoted state (None while not final)."""
    out = []
    for e in evaluations(ops_root, day):
        hp, ap = finals.get(e["game_id"], (None, None))
        res = settle(e, hp, ap)
        h, a = names.get(e["home_team_id"], e["home_team_id"]), names.get(e["away_team_id"], e["away_team_id"])
        cv = clv(e, close)
        out.append({"GAME": f"{a} @ {h}", "GAME_ID": e["game_id"], "MARKET_TYPE": e["market_type"], "HOME_TEAM": h,
                    "AWAY_TEAM": a, "LINE": e.get("point"), "PICK": pick_label(e, h, a), "SIDE": e["side"],
                    "MODEL%": e["model_p"], "MARKET%": e.get("fair_p"), "PUSH%": e["p_push"], "EDGE": e.get("edge"),
                    "EV": e["ev"], "BOOK": e["book"], "ODDS": american(e["decimal"]), "HOME_FINAL": hp,
                    "AWAY_FINAL": ap, "REALIZED_MARGIN": None if hp is None else hp - ap,
                    "REALIZED_TOTAL": None if hp is None else hp + ap, "RESULT": res,
                    "PROFIT": profit(res, e["decimal"]), "IS_ACTIONABLE": False, "SELECTION_TIER": GM.TIER_RESEARCH,
                    "CLV_STATUS": cv["CLV_STATUS"], "CLV_PROBABILITY": cv.get("CLV_PROBABILITY"),
                    "LINE_MOVE": cv.get("LINE_MOVE"), "FORECAST_ID": e["forecast_id"], "QUOTE_ID": e.get("quote_id"),
                    "GAME_MODEL_VERSION": e["game_model_version"], "_p": e["model_p"], "_fav": e.get("point"),
                    "_slate": day})
    return out


def summary(rows: list[dict], population: str = "GAME MARKETS") -> list[list]:
    def block(label, rs):
        g = [r for r in rs if r["RESULT"] in ("WIN", "LOSS", "PUSH")]
        dec = [r for r in g if r["RESULT"] != "PUSH"]
        y = np.array([r["RESULT"] == "WIN" for r in dec], float)
        p = np.clip(np.array([r["_p"] for r in dec], float), 1e-6, 1 - 1e-6)
        pr = sum(r["PROFIT"] for r in g)
        return [population, label, len(g), sum(r["RESULT"] == "WIN" for r in g), sum(r["RESULT"] == "LOSS" for r in g),
                sum(r["RESULT"] == "PUSH" for r in g), sum(str(r["RESULT"]).startswith("VOID") for r in rs),
                sum(r["RESULT"] == "PENDING" for r in rs), float(y.mean()) if len(y) else None,
                float(((p - y) ** 2).mean()) if len(y) else None,
                float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()) if len(y) else None, pr,
                pr / len(g) if g else None]
    out = [block("ALL", rows)]
    for mt in GM.CORE:
        out.append(block(mt, [r for r in rows if r["MARKET_TYPE"] == mt]))
    return out


def grade_table(rows: list[dict]) -> list[list]:
    return [[r.get(k) for k in GRADE_COLS] for r in rows]


# ============================================================================ lineage (hard validation)
LINEAGE_FIELDS = (("player_run_id", "run_id"), ("player_model_version", "model_version"),
                  ("state_version", "state_version"), ("forecast_id", "snapshot_id"))


def lineage(ops_root: Path, day: str) -> dict:
    """Every game PMF row must carry exactly its player snapshot's run, fit, state and forecast id, one ratings state
    and training cutoff per snapshot, and the ratings cutoff must be D's training cutoff (no intraday refit)."""
    from cbb_platform.walkforward import config as WC
    want_cut = pd.Timestamp(WC.refit_cutoff(pd.Timestamp(day).date())).isoformat()
    bad, n = [], 0
    for f in sorted((Path(ops_root) / "forecasts" / day).glob(f"*/{GF.FILE}")):
        sp = f.parent / "snapshot.json"
        snap = json.loads(sp.read_text()) if sp.exists() else {}
        g = pd.read_parquet(f, columns=[a for a, _ in LINEAGE_FIELDS] + ["ratings_state_id", "training_cutoff_utc"])
        n += len(g)
        for a, b in LINEAGE_FIELDS:
            if snap and set(g[a].astype(str)) != {str(snap.get(b))}:
                bad.append({"snapshot": f.parent.name, "field": a, "game": sorted(set(g[a].astype(str))),
                            "player": snap.get(b)})
        if g["ratings_state_id"].nunique() != 1 or set(g["training_cutoff_utc"]) != {want_cut}:
            bad.append({"snapshot": f.parent.name, "field": "ratings_state/training_cutoff",
                        "game": sorted(set(g["training_cutoff_utc"])), "expected": want_cut})
    days_states = set()
    for f in sorted((Path(ops_root) / "forecasts" / day).glob(f"*/{GF.FILE}")):
        days_states |= set(pd.read_parquet(f, columns=["ratings_state_id"])["ratings_state_id"])
    return {"status": "PASS" if not bad else "FAIL", "rows": n, "mismatches": bad[:20],
            "ratings_states_today": len(days_states)}


# ============================================================================ cumulative game Performance (separate workbook)
PERF_SHEETS = ("Game Overall", "Game Daily", "By Market Type", "By Side", "Market Type x Side", "Game Calibration",
               "Game Market Comparison", "Conference Game", "Favorite-Size Bucket", "Total-Range Bucket", "Game CLV",
               "Limitations")
PERF_COLS = ("N", "GRADED", "WINS", "LOSSES", "PUSHES", "PENDING", "HIT_RATE", "MEAN_MODEL_P", "BRIER", "LOG_LOSS",
             "MARKET_BRIER", "MEAN_EDGE", "MEAN_EV", "UNITS", "ROI", "MEAN_CLV", "POSITIVE_CLV_PCT", "EXACT_LINE_CLV_N",
             "LINE_MOVED_N", "NO_CLOSE_N")


def perf_name(date: str) -> str:
    return "CBB_Game_Performance_" + pd.Timestamp(date).strftime("%m_%d_%Y") + ".xlsx"


def _pm(rows: list[dict]) -> list:
    g = [r for r in rows if r["RESULT"] in ("WIN", "LOSS", "PUSH")]
    d = [r for r in g if r["RESULT"] != "PUSH"]
    y = np.array([r["RESULT"] == "WIN" for r in d], float)
    p = np.clip(np.array([r["_p"] for r in d], float), 1e-6, 1 - 1e-6)
    mk = [(r["MARKET%"], r["RESULT"] == "WIN") for r in d if r.get("MARKET%") is not None]
    cl = [r["CLV_PROBABILITY"] for r in rows if r.get("CLV_PROBABILITY") is not None]
    mean = lambda v: float(np.mean(v)) if len(v) else None                       # noqa: E731
    units = float(sum(r["PROFIT"] for r in g)) if g else 0.0
    return [len(rows), len(g), sum(r["RESULT"] == "WIN" for r in g), sum(r["RESULT"] == "LOSS" for r in g),
            sum(r["RESULT"] == "PUSH" for r in g), sum(r["RESULT"] == "PENDING" for r in rows), mean(y),
            mean(p), mean((p - y) ** 2) if len(y) else None,
            mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(y) else None,
            mean([(m - w) ** 2 for m, w in mk]), mean([r["EDGE"] for r in rows if r.get("EDGE") is not None]),
            mean([r["EV"] for r in rows]), units, units / len(g) if g else None, mean(cl),
            mean([c > 0 for c in cl]), sum(r["CLV_STATUS"] == "EXACT_LINE" for r in rows),
            sum(r["CLV_STATUS"] == "LINE_MOVED" for r in rows), sum(r["CLV_STATUS"] == "NO_CLOSE_QUOTE" for r in rows)]


def _by(rows, key, label):
    ks = sorted({key(r) for r in rows}, key=str)
    return {"columns": (label,) + PERF_COLS, "rows": [[k] + _pm([r for r in rows if key(r) == k]) for k in ks]}


def performance_tables(rows: list[dict]) -> dict:
    fav = lambda r: ("NA" if r.get("_margin") is None else "<3" if abs(r["_margin"]) < 3 else "3-7"   # noqa: E731
                     if abs(r["_margin"]) < 7 else "7-12" if abs(r["_margin"]) < 12 else "12+")
    tot = lambda r: ("NA" if r.get("_total") is None else "<135" if r["_total"] < 135 else "135-145"   # noqa: E731
                     if r["_total"] < 145 else "145-155" if r["_total"] < 155 else "155+")
    cal = lambda r: f"{min(int(r['_p'] * 10), 9) * 10}-{min(int(r['_p'] * 10), 9) * 10 + 10}%"           # noqa: E731
    t = {"Game Overall": {"columns": ("POPULATION",) + PERF_COLS, "rows": [["GAME MARKETS (SHADOW)"] + _pm(rows)]},
         "Game Daily": _by(rows, lambda r: r["_slate"], "SLATE_DATE"),
         "By Market Type": _by(rows, lambda r: r["MARKET_TYPE"], "MARKET_TYPE"),
         "By Side": _by(rows, lambda r: r["SIDE"], "SIDE"),
         "Market Type x Side": _by(rows, lambda r: f"{r['MARKET_TYPE']} {r['SIDE']}", "MARKET_TYPE x SIDE"),
         "Game Calibration": _by(rows, cal, "MODEL% BUCKET"),
         "Game Market Comparison": _by(rows, lambda r: "MODEL_VS_SAME_BOOK_NO_VIG", "COMPARISON"),
         "Conference Game": _by(rows, lambda r: str(r.get("_conf")), "CONFERENCE_GAME"),
         "Favorite-Size Bucket": _by(rows, fav, "|MODEL MARGIN|"),
         "Total-Range Bucket": _by(rows, tot, "MODEL TOTAL"),
         "Game CLV": _by(rows, lambda r: r["CLV_STATUS"], "CLV_STATUS")}
    t["Limitations"] = {"columns": ("NOTE",), "rows": [[x] for x in (
        "Game markets are SHADOW (Release B): never actionable, tier RESEARCH, until Release C validation.",
        "Historical CBBD validation (2023-24 dev, 2024-25 selection): calibrated and coherent, but no edge vs the "
        "close (ATS 50.8% / 48.6%, market margin RMSE 11.12 vs model 11.53).",
        "Conference tier (high/mid/low-major) is not yet mapped; Conference Game splits conference vs non-conference.",
        "Settlement is OT-inclusive full game (the quoted full-game markets' convention).")]}
    return t


def game_performance(date: str, ops_root: Path, out_dir: Path, finals_of, names: dict, conf_of=None) -> dict:
    """Cumulative game-market Performance through `date` from every frozen game evaluation (ops/evaluations/*)."""
    from cbb_platform.prod import exports as EX
    days = sorted(p.parent.name for p in (Path(ops_root) / "evaluations").glob(f"*/{EVAL_FILE}") if p.parent.name <= date)
    rows = []
    for d in days:
        ev = evaluations(ops_root, d)
        q = GQ.load(Path(ops_root) / "game_quotes", d)
        tips = {e["game_id"]: e["scheduled_tip_utc"] for e in ev}
        rs = graded(ops_root, d, finals_of(sorted(tips)), GQ.closing(q, tips), names)
        pm = game_pmfs(ops_root, d)
        mm = dict(zip(pm.get("game_id", []), zip(pm.get("model_margin", []), pm.get("model_total", []))))
        cf = conf_of(sorted(tips)) if conf_of else {}
        for r in rs:
            r["_margin"], r["_total"] = mm.get(r["GAME_ID"], (None, None))
            c = cf.get(r["GAME_ID"])
            r["_conf"] = "UNKNOWN" if c is None else ("CONFERENCE" if c else "NON_CONFERENCE")
        rows += rs
    t = performance_tables(rows)
    path = Path(out_dir) / perf_name(date)
    fm = {c: "0.0%" for c in ("HIT_RATE", "MEAN_MODEL_P", "MEAN_EDGE", "MEAN_EV", "ROI", "MEAN_CLV", "POSITIVE_CLV_PCT")}
    EX.write_xlsx(path, [{"name": s, "blocks": [{"title": None, "columns": t[s]["columns"], "rows": t[s]["rows"],
                                                 "formats": fm}]} for s in PERF_SHEETS])
    return {"status": "WRITTEN", "path": str(path), "days": len(days), "evaluations": len(rows),
            "graded": sum(r["RESULT"] in ("WIN", "LOSS", "PUSH") for r in rows),
            "start_date": days[0] if days else None}
