"""Player-market NO_CLOSE_QUOTE root causes (same vocabulary, deterministic precedence). argv: <repo> <forensics dir>"""
import re, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, sys.argv[1])
from cbb_platform.prod import market_exports as MX

F = Path(sys.argv[2]); D = F / "data"
skips = [pd.Timestamp(m.group(1)) for m in (re.match(r"(\S+) .*busy > 600 s", l) for l in open(F / "quotes_journal.txt")) if m]
perf = pd.read_parquet(D / "performance" / "cbb_market_performance.parquet")
nc = perf[(perf.clv_status == "NO_CLOSE_QUOTE") & perf.result.isin(["WIN", "LOSS"])]
U = lambda t: pd.Timestamp(t).tz_convert("UTC") if pd.Timestamp(t).tzinfo else pd.Timestamp(t).tz_localize("UTC")
rows, cache = [], {}
for e in nc.to_dict("records"):
    day = e["slate_date"]
    obs = cache.setdefault(day, MX.Observations(D, day))
    end = min(U(e["tip_utc"]), U(e["cutoff_utc"])); lo = end - pd.Timedelta(minutes=MX.CLOSE_MAX_STALE_MINUTES)
    entry = U(e["entry_received_at"]) if isinstance(e["entry_received_at"], str) else None
    g = obs.of_game(e["game_id"])
    after = [o for o in g if (entry is None or U(o["received_at"]) >= entry) and U(o["received_at"]) <= end]
    inwin = [o for o in after if U(o["received_at"]) >= lo]
    with_c = [o for o in inwin if any(k[0] == e["player_id"] and k[1] == e["stat"] for k in obs.contracts(o))]
    failed = [o for o in g if lo <= U(o["received_at"]) <= end and str(o.get("discovery_status", "")).startswith(("FAIL", "ERROR"))]
    if entry is None:
        cause = "OTHER_EXPLICIT_REASON"                    # no entry observation (model-only evaluation)
    elif entry < lo and not inwin and failed:
        cause = "QUOTE_CALL_FAILED"
    elif entry < lo and not inwin and any(lo - pd.Timedelta(minutes=10) <= s <= end for s in skips):
        cause = "WRITER_LOCK_COLLISION"
    elif entry < lo and not inwin:
        cause = "NO_DUE_TICK"                              # no refresh of this game in the last 60 min before cutoff
    elif inwin and not with_c:
        cause = "NO_MARKET_POSTED"                         # game re-quoted in-window, the player/stat prop was gone
    else:
        cause = "OTHER_EXPLICIT_REASON"
    rows.append({"slate_date": day, "eval_key": e["eval_key"], "stat": e["stat"], "side": e["side"],
                 "cutoff_utc": e["cutoff_utc"], "entry_received_at": e["entry_received_at"],
                 "entry_min_before_cutoff": None if entry is None else round((end - entry).total_seconds() / 60, 1),
                 "game_obs_after_entry": len(after), "game_obs_in_window": len(inwin),
                 "in_window_with_contract": len(with_c),
                 "in_window_statuses": ";".join(sorted({str(o.get("discovery_status")) for o in inwin})), "cause": cause})
out = pd.DataFrame(rows); out.to_csv(F / "close" / "player_no_close_rows.csv", index=False)
tot = perf.result.isin(["WIN", "LOSS"]).sum()
print("graded", tot, "no_close", len(out))
vc = out.cause.value_counts(); print(pd.DataFrame({"n": vc, "pct": (vc / vc.sum() * 100).round(1)}))
print(out.groupby(["slate_date", "cause"]).size())
print(out.entry_min_before_cutoff.describe())
print(out.in_window_statuses.value_counts().head(10))
