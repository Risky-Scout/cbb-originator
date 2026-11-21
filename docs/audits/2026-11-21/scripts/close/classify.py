"""Section E: assign exactly one root cause to every game-market NO_CLOSE_QUOTE (deterministic precedence).
argv: <repo root> <forensics dir>"""
import json, sys, re
from pathlib import Path
import pandas as pd
sys.path.insert(0, sys.argv[1])
from cbb_platform.prod import game_quotes as GQ, game_exports as GX

F = Path(sys.argv[2]); D = F / "data"
B1 = pd.Timestamp("2026-11-20T12:57:00Z")          # Release B.1 (quote_slate until tip) deployed
skips = [pd.Timestamp(m.group(1)) for m in (re.match(r"(\S+) .*busy > 600 s", l) for l in open(F / "quotes_journal.txt")) if m]
runs, st = [], None
for l in open(F / "quotes_journal.txt"):
    t = pd.Timestamp(l.split()[0])
    if "Starting cbb-originator-quotes" in l:
        st = t
    elif "Finished cbb-originator-quotes" in l and st is not None:
        runs.append((st, t)); st = None
NOW = pd.Timestamp(sys.argv[3]) if len(sys.argv) > 3 else pd.Timestamp.now(tz="UTC")
rows = []
for day in ["2026-11-19", "2026-11-20", "2026-11-21"]:
    ev = [json.loads(l) for l in open(D / "evaluations" / day / "game_market_evaluations.jsonl") if l.strip()]
    ev = [e for e in ev if e.get("kind") == "GAME_MARKET_EVALUATION"]
    q = GQ.load(D / "game_quotes", day)
    recs = [json.loads(x.read_text()) for x in sorted((D / "game_quotes" / day).glob("GQ*.json"))]
    caps = pd.DataFrame([{"capture_id": r["capture_id"], "at": pd.Timestamp(r["at"]), "status": r["status"],
                          "event_calls": r.get("event_calls") or 0} for r in recs])
    tips = {e["game_id"]: e["scheduled_tip_utc"] for e in ev}
    close = GQ.closing(q, tips)
    for e in ev:
        cv = GX.clv(e, close)
        tip, cut = pd.Timestamp(e["scheduled_tip_utc"]), pd.Timestamp(e["cutoff_utc"])
        lo = tip - pd.Timedelta(minutes=GQ.CLOSE_WINDOW_MIN)
        r = {"slate_date": day, "game_id": e["game_id"], "market_type": e["market_type"], "side": e["side"],
             "cutoff_utc": cut, "tip_utc": tip, "clv_status": cv["CLV_STATUS"]}
        if tip > NOW:
            r["clv_status"] = "PENDING_TIP"
        if r["clv_status"] != "NO_CLOSE_QUOTE":
            rows.append(r); continue
        qg = q[q.game_id == e["game_id"]]; qgm = qg[qg.market_type == e["market_type"]]
        cw = caps[(caps["at"] >= lo - pd.Timedelta(minutes=5)) & (caps["at"] < tip)]
        cw_ok = cw[cw.status == "COMPLETE"]
        inwin = qg[(qg.collector_received_at >= lo) & (qg.collector_received_at < tip)]
        inwin_m = qgm[(qgm.collector_received_at >= lo) & (qgm.collector_received_at < tip)]
        last_g = qg.collector_received_at.max() if len(qg) else None
        last_m = qgm.collector_received_at.max() if len(qgm) else None
        ov = [(a, b) for a, b in runs if a < tip and b > lo - pd.Timedelta(minutes=15)]
        r.update(captures_in_window=len(cw_ok), failed_captures_in_window=int((cw.status != "COMPLETE").sum()),
                 game_rows_in_window=len(inwin), market_rows_in_window=len(inwin_m), last_game_row=last_g,
                 last_market_row=last_m,
                 min_before_tip_last_game_row=None if last_g is None else round((tip - last_g).total_seconds() / 60, 1),
                 lock_skip_near_window=any(lo - pd.Timedelta(minutes=10) <= s < tip for s in skips),
                 quote_runs_near_window=";".join(f"{a:%H:%M:%S}-{b:%H:%M:%S}" for a, b in ov))
        # deterministic precedence (first match wins)
        if tip <= B1 and last_g is not None and last_g <= cut + pd.Timedelta(minutes=2) and len(inwin) == 0:
            cause = "CUTOFF_ALREADY_PASSED"        # pre-B.1 capture slate dropped the game at its prediction cutoff
        elif len(cw_ok) == 0 and r["failed_captures_in_window"]:
            cause = "QUOTE_CALL_FAILED"
        elif len(cw_ok) == 0 and r["lock_skip_near_window"]:
            cause = "WRITER_LOCK_COLLISION"
        elif len(cw_ok) == 0:
            cause = "NO_DUE_TICK"
        elif len(inwin) == 0:
            cause = "NO_PROVIDER_EVENT"
        elif len(inwin_m) == 0:
            cause = ("OTHER_EXPLICIT_REASON" if e["market_type"] in ("HOME_TEAM_TOTAL", "AWAY_TEAM_TOTAL")
                     and cw_ok.event_calls.sum() == 0 else "NO_MARKET_POSTED")
        else:
            cause = "OTHER_EXPLICIT_REASON"
        r["cause"] = cause
        rows.append(r)
out = pd.DataFrame(rows)
out.to_csv(F / "close" / "game_no_close_rows.csv", index=False)
pd.set_option("display.width", 250)
print(out.groupby(["slate_date", "clv_status"]).size())
n = out[out.clv_status == "NO_CLOSE_QUOTE"]
print(n.groupby(["slate_date", "cause"]).size())
vc = n.cause.value_counts(); print(pd.DataFrame({"n": vc, "pct": (vc / vc.sum() * 100).round(1)}))
print(n.groupby(["cause", "market_type"]).size())
print(n[["slate_date", "game_id", "market_type", "cutoff_utc", "tip_utc", "captures_in_window", "game_rows_in_window",
         "min_before_tip_last_game_row", "quote_runs_near_window", "cause"]].to_string())
