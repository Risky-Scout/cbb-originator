"""Stage 04 read-only: the complete unresolved non-active RotoWire identity population at an instant, with every
fact needed to classify each record (curated row + lapse, web map, CBBD roster by name, 2026-27 box participation,
portal/transfer records, upcoming games blocked). Writes OUT/population_<tag>.json. No writes elsewhere."""
import json, sys
from datetime import datetime, timezone
import duckdb, pandas as pd
from cbb_platform.prod import (config as PC, state as ST, history_extract as HX, matching as MT, rotowire_feed as RW,
                               rotowire_player_states as PS, roster_omissions as RO, rotowire_web_acquire as WA)
OUT = sys.argv[1]; tag = sys.argv[2] if len(sys.argv) > 2 else "now"
now = pd.Timestamp(sys.argv[3]) if len(sys.argv) > 3 else pd.Timestamp(datetime.now(timezone.utc))
ev = RW.EventStore(PC.ROTOWIRE_ROOT).frame()
stt = RW.player_state(ev, now)
st = ST.current(); con = duckdb.connect(str(st["increment_db"]), read_only=True)
ros = con.execute("select canonical_player_id, canonical_team_id, player_name, first_name, last_name, position, jersey, source_athlete_id from flagship.rosters where season=?", [PC.PRODUCTION_SEASON]).df()
pgo = con.execute("select canonical_game_id, canonical_team_id, canonical_player_id, identity_status, source_player_name, minutes, event_time from flagship.player_game_outcomes where season=?", [PC.PRODUCTION_SEASON]).df()
trn = con.execute("select * from core.transfers").df()
sch = con.execute("select canonical_game_id, start_time, home_team_id, away_team_id, home_team_name, away_team_name, status from metadata.prod_schedule where season=?", [PC.PRODUCTION_SEASON]).df()
con.close()
hcon = duckdb.connect(str(HX.resolve()), read_only=True)
teams = hcon.execute("select canonical_team_id, any_value(display_name) n from core.teams group by 1").df()
hcon.close()
tname = dict(zip(teams.canonical_team_id, teams.n)); tname.update(dict(zip(sch.home_team_id, sch.home_team_name))); tname.update(dict(zip(sch.away_team_id, sch.away_team_name)))
ros["_n"] = [MT.norm(f"{a or ''} {b or ''}".strip()) or MT.norm(c) for a, b, c in zip(ros.first_name, ros.last_name, ros.player_name)]
pgo["_n"] = pgo.source_player_name.map(lambda x: MT.norm(x) if isinstance(x, str) else None)
trn["_n"] = [MT.norm(f"{a or ''} {b or ''}") for a, b in zip(trn.first_name, trn.last_name)]
pm = pd.read_csv(PC.ROTOWIRE_MAP_ROOT / WA.PLAYER_MAP_FILE, dtype=str)
states, om = PS.load(), RO.load()
sch["start"] = pd.to_datetime(sch.start_time, utc=True)
up = sch[(sch.start > now) & (sch.start < now + pd.Timedelta(days=7))]
last = RW._order(RW.usable(ev, now)).groupby("rotowire_player_id").tail(1).set_index("rotowire_player_id")
bad = stt[(stt.identity_resolution_status != "RESOLVED") & ~stt.effective_status.isin(("ACTIVE", RW.NOT_LISTED))]
ids = set(bad.rotowire_player_id.astype(str)) | set(states.rotowire_player_id) | set(om.rotowire_player_id)
recs = []
for pid in sorted(ids):
    s = stt[stt.rotowire_player_id.astype(str) == pid]
    s = s.iloc[0].to_dict() if len(s) else None
    l = last.loc[pid].to_dict() if pid in last.index else {}
    name = f"{l.get('first_name') or ''} {l.get('last_name') or ''}".strip()
    cur = states[states.rotowire_player_id == pid]
    crow = cur.iloc[0].to_dict() if len(cur) else None
    if not name and crow: name = crow["rotowire_player_name"]
    k = MT.norm(name)
    tid = s["canonical_team_id"] if s else None
    cls = PS.classify(states, pid, tid, now) if (s and isinstance(tid, str)) else None
    blocked = []
    if s and s["identity_resolution_status"] != "RESOLVED" and s["effective_status"] not in ("ACTIVE", RW.NOT_LISTED):
        g = up if not isinstance(tid, str) else up[(up.home_team_id == tid) | (up.away_team_id == tid)]
        known = isinstance(tid, str) and (PS.non_blocking(states, pid, tid, now) or RO.recruit_anchored(RO.in_force(om, now), pid, tid))
        if not known:
            blocked = [f"{r.canonical_game_id} {r.start_time} {r.away_team_name}@{r.home_team_name}" for r in g.itertuples()]
    rr = ros[ros._n == k]
    pp = pgo[pgo._n == k]
    tt = trn[trn._n == k]
    recs.append({
        "rotowire_player_id": pid, "name": name, "rotowire_team_name": l.get("team_name") if "team_name" in l else None,
        "rw_canonical_team_id": tid, "rw_team": tname.get(tid), "effective_status": s and s["effective_status"],
        "gate": s and s["gate"], "identity_status": s and s["identity_resolution_status"],
        "identity_method": l.get("identity_method"), "last_knowable_at": s and str(s["knowable_at"]),
        "raw_status": s and s["raw_status"], "in_current_population": bool(s and pid in set(bad.rotowire_player_id.astype(str))),
        "curated_row": crow and {k2: crow[k2] for k2 in ("operational_class", "affiliated_team_id", "canonical_player_id", "verified_on", "review_by", "eligible_from")},
        "curated_in_force_class": cls,
        "curated_lapsed": bool(crow) and pd.Timestamp(crow["review_by"]) <= pd.Timestamp(PS._day(now)),
        "curated_team_matches_rw_team": bool(crow) and crow["affiliated_team_id"] == tid,
        "omission_row": bool((om.rotowire_player_id == pid).any()),
        "web_map": pm[pm.rotowire_player_id == pid][["canonical_player_id", "canonical_team_id", "rotowire_team_name", "method", "added_at"]].to_dict("records"),
        "cbbd_roster_by_name": [{"pid": r.canonical_player_id, "team": r.canonical_team_id, "team_name": tname.get(r.canonical_team_id), "pos": r.position, "jersey": r.jersey, "athlete": r.source_athlete_id} for r in rr.itertuples()],
        "box_2027_by_name": {f"{t}|{tname.get(t)}|{p}|{i}": int(n) for (t, p, i), n in pp.groupby(["canonical_team_id", pp.canonical_player_id.fillna("NULL"), pp.identity_status.fillna("")]).size().items()},
        "box_2027_minutes": float(pp.minutes.fillna(0).sum()) if len(pp) else 0.0,
        "box_last": str(pp.event_time.max()) if len(pp) else None,
        "transfers_by_name": tt[["transfer_year", "origin_team_name", "destination_team_name", "destination_team_id", "canonical_player_id", "eligibility"]].astype(str).to_dict("records"),
        "blocked_games_7d": blocked,
    })
res = {"as_of": str(now), "state_version": st["pointer"]["state_version"], "events": int(len(ev)),
       "players_with_state": int(len(stt)), "unresolved_nonactive": int(len(bad)),
       "unresolved_nonactive_by_status": bad.groupby(["identity_resolution_status"]).size().to_dict(),
       "blocking_records": sum(1 for r in recs if r["blocked_games_7d"]),
       "blocked_games_7d": sorted({g for r in recs for g in r["blocked_games_7d"]}),
       "upcoming_games_7d": int(len(up)), "records": recs}
p = f"{OUT}/population_{tag}.json"
open(p, "w").write(json.dumps(res, indent=1, default=str))
print(json.dumps({k: v for k, v in res.items() if k != "records"}, default=str, indent=1))
for r in recs:
    print(r["rotowire_player_id"], r["name"], "|", r["rw_team"], "|", r["effective_status"], r["identity_status"], r["identity_method"], "| cur:", (r["curated_row"] or {}).get("operational_class"), "lapsed" if r["curated_lapsed"] else "", "inforce=" + str(r["curated_in_force_class"]), "| roster:", [(x["team_name"]) for x in r["cbbd_roster_by_name"]], "| box:", r["box_2027_by_name"], "| blocks", len(r["blocked_games_7d"]))
