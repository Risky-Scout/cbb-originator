"""CBB_PUBLIC_DATA_CONTRACT_V1 (frozen 2026-11-25): the only CBB fields that may leave the Droplet for a public page.

Two tabs: "Game Lines & Totals" (default) and "Player Props". Built ONLY from the approved internal export rows
(prod.game_exports.LINE_COLS board rows, prod.market_exports.BOARD_COLS board rows); every public field is a pure function
of one internal field (row-for-row QA: qa_rows). Never exposed: environment / credentials, raw model state, fit artifacts,
PMFs and hashes, quote ids, private research, internal QA, raw provider data.

Sign convention: MODEL_MARGIN = HOME - AWAY (points). Sportsbook spread display for a side = -(that side's expected
margin): HOME line = -MODEL_MARGIN, AWAY line = +MODEL_MARGIN. Game total = MODEL_TOTAL; home / away team total =
MODEL_HOME_SCORE / MODEL_AWAY_SCORE; moneyline model line = fair American odds of the model probability.

No row is labeled a validated wager: actionability is not validated (ACTIONABILITY_VALIDATED = False); every row carries
the model-output disclaimer and the pick is shown as "Model side".
"""
from __future__ import annotations

import html
import json
import math

CONTRACT_ID = "CBB_PUBLIC_DATA_CONTRACT_V1"
ACTIONABILITY_VALIDATED = False
DISCLAIMER = ("Model output for information only. Probabilities are model estimates compared with de-vigged market "
              "prices; they are not validated wagering recommendations.")
TABS = ("Game Lines & Totals", "Player Props")
DEFAULT_TAB = TABS[0]
GAME_FIELDS = ("game", "tip_et", "away_team", "home_team", "projected_away_score", "projected_home_score", "market",
               "market_line", "model_line", "model_side", "best_odds", "sportsbook", "model_probability",
               "devigged_probability", "edge", "ev")
PROP_FIELDS = ("game", "tip_et", "player", "team", "opponent", "stat", "projection", "market_line", "model_side",
               "best_odds", "sportsbook", "model_probability", "devigged_probability", "edge", "ev")
FORBIDDEN_TOKENS = ("sha", "quote_id", "forecast_id", "fit", "pmf", "state", "actionable", "tier", "validated", "policy",
                    "engine", "env", "secret", "key")
MARKET_LABEL = {"SPREAD": "Spread", "MONEYLINE": "Moneyline", "GAME_TOTAL": "Game Total",
                "HOME_TEAM_TOTAL": "Home Team Total", "AWAY_TEAM_TOTAL": "Away Team Total"}
NO_EDGE = "NO_POSITIVE_EV"


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _r(v, n):
    f = _num(v)
    return None if f is None else round(f, n)


def fair_american(p) -> int | None:
    p = _num(p)
    if p is None or not 0 < p < 1:
        return None
    return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))


def base_market(mt: str) -> str:
    for k in MARKET_LABEL:
        if str(mt).startswith(k):
            return k
    return str(mt)


def model_line(mt: str, side: str, margin, total, home, away):
    """The model's number in the sportsbook's own display convention for this market and side."""
    b = base_market(mt)
    m = _num(margin)
    if b == "SPREAD":
        return None if m is None else round(-m if side == "HOME" else m, 1)
    if b == "GAME_TOTAL":
        return _r(total, 1)
    if b == "HOME_TEAM_TOTAL":
        return _r(home, 1)
    if b == "AWAY_TEAM_TOTAL":
        return _r(away, 1)
    return None                                                         # moneyline: see model fair odds (model_line_odds)


def game_records(line_rows: list[list], cols: tuple) -> list[dict]:
    out = []
    for row in line_rows:
        r = dict(zip(cols, row))
        mt = r.get("MARKET_TYPE")
        has_market = mt in MARKET_LABEL or base_market(mt) in MARKET_LABEL
        b = base_market(mt) if has_market else None
        rec = {"game": r["GAME"], "tip_et": r["TIP_ET"], "away_team": r["AWAY_TEAM"], "home_team": r["HOME_TEAM"],
               "projected_away_score": _r(r["MODEL_AWAY_SCORE"], 1), "projected_home_score": _r(r["MODEL_HOME_SCORE"], 1),
               "market": MARKET_LABEL.get(b, "No market") if has_market else "No market",
               "market_line": _r(r.get("LINE"), 1) if has_market else None,
               "model_line": (fair_american(r.get("MODEL%")) if b == "MONEYLINE" else
                              model_line(mt, r.get("SIDE"), r["MODEL_MARGIN"], r["MODEL_TOTAL"], r["MODEL_HOME_SCORE"],
                                         r["MODEL_AWAY_SCORE"])) if has_market else None,
               "model_side": (None if r.get("PICK") in (None, NO_EDGE) else r.get("PICK")) if has_market else None,
               "best_odds": r.get("ODDS") if has_market else None, "sportsbook": r.get("BOOK") if has_market else None,
               "model_probability": _r(r.get("MODEL%"), 4) if has_market else None,
               "devigged_probability": _r(r.get("MARKET%"), 4) if has_market else None,
               "edge": _r(r.get("PROBABILITY_EDGE"), 4) if has_market else None,
               "ev": _r(r.get("EV_NET"), 4) if has_market else None}
        if not has_market and any(o["game"] == rec["game"] and o["market"] == "No market" for o in out):
            continue
        out.append(rec)
    return out


def prop_records(board_rows: list[list], cols: tuple) -> list[dict]:
    out = []
    for row in board_rows:
        r = dict(zip(cols, row))
        side = r.get("Preferred Side")
        out.append({"game": r["Game"], "tip_et": r["Tip Time ET"], "player": r["Player"], "team": r["Team"],
                    "opponent": r["Opponent"], "stat": r["Stat"], "projection": _r(r.get("Model Projection"), 2),
                    "market_line": _r(r.get("Best Market Line"), 1),
                    "model_side": None if side in (None, NO_EDGE, "") else str(side).title(),
                    "best_odds": r.get("Best American Odds"), "sportsbook": r.get("Best Book"),
                    "model_probability": _r(r.get("Model Probability"), 4),
                    "devigged_probability": _r(r.get("Fair Market Probability"), 4),
                    "edge": _r(r.get("Probability Edge"), 4), "ev": _r(r.get("Preferred EV"), 4)})
    return out


def payload(games: list[dict], props: list[dict], generated_at: str, slate_date: str) -> dict:
    p = {"contract": CONTRACT_ID, "slate_date": slate_date, "generated_at": generated_at, "disclaimer": DISCLAIMER,
         "actionability_validated": ACTIONABILITY_VALIDATED, "default_tab": DEFAULT_TAB,
         "tabs": {TABS[0]: games, TABS[1]: props}}
    validate(p)
    return p


def validate(p: dict) -> None:
    """Allow-list schema: exactly the frozen fields, no forbidden token in any key, no validated-wager wording."""
    if p.get("contract") != CONTRACT_ID or p.get("actionability_validated") is not False:
        raise ValueError("not a CBB_PUBLIC_DATA_CONTRACT_V1 payload")
    for tab, fields in zip(TABS, (GAME_FIELDS, PROP_FIELDS)):
        for rec in p["tabs"][tab]:
            if tuple(rec) != fields:
                raise ValueError(f"{tab}: fields {tuple(rec)} != contract")
    keys = {k.lower() for k in p} | {k.lower() for t in p["tabs"].values() for r in t for k in r}
    bad = [k for k in keys if any(t in k for t in FORBIDDEN_TOKENS) and k != "actionability_validated"]
    if bad:
        raise ValueError(f"forbidden keys {bad}")
    txt = json.dumps(p["tabs"]).lower()
    if "validated wager" in txt or "actionable" in txt:
        raise ValueError("validated-wager wording in public rows")


def qa_rows(p: dict, line_rows: list[list], line_cols: tuple, board_rows: list[list], board_cols: tuple) -> dict:
    """Row-for-row QA: rebuild every public record from its internal source row and require equality."""
    g, pr = game_records(line_rows, line_cols), prop_records(board_rows, board_cols)
    fails = [i for i, (a, b) in enumerate(zip(p["tabs"][TABS[0]], g)) if a != b]
    fails += [f"P{i}" for i, (a, b) in enumerate(zip(p["tabs"][TABS[1]], pr)) if a != b]
    ok = not fails and len(g) == len(p["tabs"][TABS[0]]) and len(pr) == len(p["tabs"][TABS[1]])
    spread = [r for r in g if r["market"] == "Spread" and r["model_line"] is not None]
    return {"WIZARDOFODDS_ROW_QA": "PASS" if ok else "FAIL", "game_rows": len(g), "prop_rows": len(pr),
            "mismatches": fails[:20], "spread_rows_checked": len(spread)}


def _cell(v):
    if v is None:
        return "&mdash;"
    if isinstance(v, float):
        return f"{v:.1%}" if abs(v) <= 1 and v != round(v) else f"{v:g}"
    return html.escape(str(v))


def render_html(p: dict) -> str:
    """Self-contained mobile card page: two tabs, Game Lines & Totals default; data embedded (no external requests)."""
    def card(rec, title, sub, rows):
        items = "".join(f"<div class='kv'><span>{html.escape(lab)}</span><b>{_cell(rec[k])}</b></div>" for lab, k in rows)
        return f"<article class='card'><h3>{html.escape(title)}</h3><p class='sub'>{html.escape(sub)}</p>{items}</article>"
    gl = [("Projected", "projected_away_score"), ("", "projected_home_score"), ("Market line", "market_line"),
          ("Model line", "model_line"), ("Model side", "model_side"), ("Best odds", "best_odds"), ("Book", "sportsbook"),
          ("Model %", "model_probability"), ("Market % (no-vig)", "devigged_probability"), ("Edge", "edge"), ("EV", "ev")]
    gl[1] = ("Projected home", "projected_home_score"); gl[0] = ("Projected away", "projected_away_score")
    pl = [("Projection", "projection"), ("Line", "market_line"), ("Model side", "model_side"), ("Best odds", "best_odds"),
          ("Book", "sportsbook"), ("Model %", "model_probability"), ("Market % (no-vig)", "devigged_probability"),
          ("Edge", "edge"), ("EV", "ev")]
    g = "".join(card(r, r["game"], f"{r['market']} · {r['tip_et'] or ''}", gl) for r in p["tabs"][TABS[0]])
    q = "".join(card(r, f"{r['player']} — {r['stat']}", f"{r['team']} vs {r['opponent']} · {r['tip_et'] or ''}", pl)
                for r in p["tabs"][TABS[1]])
    css = (":root{--bg:#fff;--fg:#111;--mut:#666;--card:#f5f6f8;--acc:#0b5}"
           "@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#aaa;--card:#1c1e22;--acc:#3d8}}"
           "body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}"
           "header{padding:16px}h1{font-size:20px;margin:0 0 4px}.note{color:var(--mut);font-size:13px}"
           ".tabs{display:flex;gap:8px;padding:0 16px}.tabs button{flex:1;padding:10px;border:0;border-radius:8px;"
           "background:var(--card);color:var(--fg);font-weight:600}.tabs button[aria-selected=true]{background:var(--acc);color:#fff}"
           ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;padding:16px}"
           ".card{background:var(--card);border-radius:12px;padding:12px}.card h3{margin:0;font-size:16px}"
           ".sub{margin:2px 0 8px;color:var(--mut);font-size:13px}.kv{display:flex;justify-content:space-between;"
           "padding:3px 0;border-top:1px solid rgba(127,127,127,.15)}.kv span{color:var(--mut)}[hidden]{display:none}")
    js = ("for(const b of document.querySelectorAll('.tabs button'))b.onclick=()=>{for(const x of document.querySelectorAll"
          "('.tabs button'))x.setAttribute('aria-selected',x===b);for(const s of document.querySelectorAll('section'))"
          "s.hidden=s.id!==b.dataset.t};")
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
            f"initial-scale=1'><title>CBB Model Board</title><style>{css}</style></head><body><header><h1>CBB Model Board "
            f"{html.escape(p['slate_date'])}</h1><p class='note'>{html.escape(p['disclaimer'])} Updated "
            f"{html.escape(p['generated_at'])}.</p></header><nav class='tabs'>"
            f"<button data-t='g' aria-selected='true'>{TABS[0]}</button><button data-t='p' aria-selected='false'>{TABS[1]}"
            f"</button></nav><section id='g' class='grid'>{g}</section><section id='p' class='grid' hidden>{q}</section>"
            f"<script>{js}</script></body></html>")
