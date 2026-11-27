"""Stage 04 read-only before/after on production stores with the CANDIDATE tree (CBB_APP_ROOT/PYTHONPATH).
Nothing is written outside OUT: map extension is computed with write=False and the would-be re-resolution events are
held in memory only. Outputs OUT/after_<tag>.json."""
import json, sys
from datetime import datetime, timezone
import duckdb, pandas as pd
from cbb_platform.prod import (config as PC, state as ST, history_extract as HX, rotowire_feed as RW, roster_omissions as RO,
                               rotowire_player_states as PS, rotowire_web_acquire as WA, universe as UV, identity_conflicts as IC)
OUT = sys.argv[1]; tag = sys.argv[2]
now = pd.Timestamp(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else pd.Timestamp(datetime.now(timezone.utc))
hist = HX.resolve(); st = ST.current()
res = {"as_of": now.isoformat(), "state_version": st["pointer"]["state_version"]}
# ---------------------------------------------------------------- 1. identity roster with BOX_PARTICIPATION
con = duckdb.connect(str(st["increment_db"]), read_only=True)
cbbd = con.execute("SELECT canonical_player_id, canonical_team_id, first_name, last_name, player_name, position FROM flagship.rosters WHERE season=?", [PC.PRODUCTION_SEASON]).df()
box = RW.box_lines(con)
sch = con.execute("SELECT canonical_game_id, start_time, home_team_id, away_team_id, home_team_name, away_team_name FROM metadata.prod_schedule WHERE season=?", [PC.PRODUCTION_SEASON]).df()
con.close()
part, dec = RO.participation_rows(box, cbbd, now, PS.load(), {e["suppressed_canonical_player_id"] for e in IC.load()})
res["participation_decisions"] = pd.Series([d["decision"] for d in dec]).value_counts().to_dict()
res["participation_non_moot"] = [d for d in dec if d["decision"] != "MOOT_CBBD_LISTS_PLAYER"]
ctx = RW.load_identity_context(PC.STATE_ROOT, hist)
r = ctx.roster
res["identity_roster_sources"] = r["roster_source"].value_counts().to_dict()
res["max_teams_per_player_in_identity_roster"] = int(r.groupby("canonical_player_id")["canonical_team_id"].nunique().max())
res["participation_temporal_ok"] = bool((pd.to_datetime(part["box_last_knowable_at"], utc=True) <= now).all())
# temporal check: at 2026-11-20T00:00Z (before Avila's first TCU tip 11-20 01:00Z) the TCU row must not exist
p_early, _ = RO.participation_rows(box, cbbd, pd.Timestamp("2026-11-20T00:00Z"), PS.load())
res["avila_tcu_row_at_1120T0000Z"] = bool(((p_early.canonical_player_id == "P-5ce042f648e4fb1d")).any())
res["avila_tcu_row_now"] = bool(((part.canonical_player_id == "P-5ce042f648e4fb1d") & (part.canonical_team_id == "T-cf2771d63aeecf02")).any())
# ---------------------------------------------------------------- 2. map extension + re-resolution (in memory)
store = RW.EventStore(PC.ROTOWIRE_ROOT); ev = store.frame()
ident = WA.WebIdentity(ctx, PC.ROTOWIRE_MAP_ROOT)
stale = WA.stale_unresolved_fields(store, ident, set())
would = WA.extend_maps(ctx, stale, PC.ROTOWIRE_MAP_ROOT, write=False)
res["stale_unresolved_fields"] = len(stale)
res["would_add_player_rows"] = [{k: x[k] for k in ("rotowire_player_id", "rotowire_player_name", "canonical_player_id", "canonical_team_id", "method")} for x in would["player_rows"]]
for row in would["player_rows"]:
    ident.player.setdefault(str(row["rotowire_player_id"]), set()).add(row["canonical_player_id"])
last = RW._order(ev).groupby("rotowire_player_id").tail(1)
new = []
for prev in last.to_dict("records"):
    if prev.get("identity_resolution_status") == "RESOLVED":
        continue
    n = ident.resolve_stored(prev)
    if n is not None and not RW._same_identity(prev, n):
        e = {**prev, **n, "reconciliation": "IDENTITY_RERESOLVED_OFF_LIST", "collected_at_utc": now.isoformat(), "knowable_at": now.isoformat(),
             "seq": int(ev["seq"].max()) + 1 + len(new)}
        new.append(e)
res["reresolved"] = [{"rotowire_player_id": e["rotowire_player_id"], "name": f"{e['first_name']} {e['last_name']}", "canonical_player_id": e["canonical_player_id"], "team": e["canonical_team_id"]} for e in new]
ev2 = pd.concat([ev, pd.DataFrame(new)], ignore_index=True) if new else ev
T1 = now + pd.Timedelta(seconds=1)
# ---------------------------------------------------------------- 3. game blocking before/after over the next 7 days
sch["start"] = pd.to_datetime(sch.start_time, utc=True)
up = sch[(sch.start > now) & (sch.start < now + pd.Timedelta(days=7))].copy()
g = pd.DataFrame({"canonical_game_id": up.canonical_game_id, "home_team_id": up.home_team_id, "away_team_id": up.away_team_id,
                  "feature_cutoff": up.start - pd.Timedelta(minutes=30)})
def blocked(events, states, omissions, at):
    _, rs = RW.availability_frame(events, g, at, states=states, omissions=omissions)
    return sorted(k for k, v in rs.items() if "AVAILABILITY_IDENTITY_UNRESOLVED" in v)
import subprocess
old_states = PS.load(sys.argv[4]) if len(sys.argv) > 4 else None
old_om = RO.load(sys.argv[5]) if len(sys.argv) > 5 else None
b0 = blocked(ev, old_states, old_om, T1) if old_states is not None else None
b1 = blocked(ev2, PS.load(), RO.load(), T1)
nm = dict(zip(up.canonical_game_id, up.away_team_name + "@" + up.home_team_name))
res["games_7d"] = int(len(g))
res["blocked_before"] = None if b0 is None else [f"{x} {nm[x]}" for x in b0]
res["blocked_after"] = [f"{x} {nm[x]}" for x in b1]
st2 = RW.player_state(ev2, T1)
bad = st2[(st2.identity_resolution_status != "RESOLVED") & ~st2.effective_status.isin(("ACTIVE", RW.NOT_LISTED))]
res["unresolved_nonactive_after"] = int(len(bad))
res["unresolved_nonactive_after_ids"] = sorted(bad.rotowire_player_id.astype(str))
# ---------------------------------------------------------------- 4. reconciliation report (new curated tables)
rep = PS.reconcile(PS.load(), RO.load(), st2, ctx.roster, box, T1, names=PS.event_names(ev2))
res["reconcile"] = {k: rep[k] for k in ("curated_states", "unresolved_nonactive", "action_required", "counts", "actions")}
json.dump(res, open(f"{OUT}/after_{tag}.json", "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if k not in ("reconcile",)}, indent=1, default=str))
print(json.dumps(res["reconcile"], indent=1, default=str)[:4000])
