"""Row-by-row HOME/AWAY/team-total mapping proof against the RAW Odds API responses (independent of prod parse)."""
import glob, gzip, json, re
from pathlib import Path
import numpy as np, pandas as pd

OUT = Path(__file__).resolve().parent
R = pd.read_parquet(OUT / "rows.parquet")
import sys; sys.path.insert(0, str(OUT.parent / "pylib"))
names = {}
for d in ("2026-11-20", "2026-11-21"):
    g = pd.read_excel(OUT.parent / "data" / "exports" / d / f"CBB_Grades_{d[5:7]}_{d[8:]}_2026.xlsx", sheet_name="Game Markets")
    for x in g.itertuples():
        names[x.GAME_ID] = (x.HOME_TEAM, x.AWAY_TEAM)
b = pd.read_excel(OUT.parent / "data" / "exports" / "2026-11-21" / "CBB_Boards_11_21_2026.xlsx", sheet_name="Game Lines")
for x in b.itertuples():
    names.setdefault(x.GAME_ID, (x.HOME_TEAM, x.AWAY_TEAM))
TEAMNAME = {}
for r in R.itertuples():
    if r.game_id in names:
        TEAMNAME[r.home_team_id], TEAMNAME[r.away_team_id] = names[r.game_id]

raw = []
for kind in ("game_odds", "game_event_odds"):
    for f in sorted(glob.glob(str(OUT / "raw" / kind / "*.json.gz"))):
        ts = pd.to_datetime(Path(f).name.split("__")[0], format="%Y%m%dT%H%M%S%fZ", utc=True)
        body = json.loads(gzip.open(f).read())
        evs = body if isinstance(body, list) else [body]
        for e in evs:
            for b in e.get("bookmakers") or []:
                for m in b.get("markets") or []:
                    for o in m.get("outcomes") or []:
                        raw.append((str(e["id"]), e.get("home_team"), e.get("away_team"), e.get("commence_time"),
                                    b["key"], m["key"], o.get("name"), o.get("description"), o.get("point"),
                                    o.get("price"), ts))
raw = pd.DataFrame(raw, columns=["eid", "p_home", "p_away", "commence", "book", "key", "name", "desc", "point",
                                 "price", "ts"])
print("raw outcomes", len(raw))


def toks(s):
    return set(re.sub(r"[^a-z0-9 ]", " ", str(s).lower().replace("state", "st").replace("saint", "st")).split())


def sim(provider, tid):
    sc = TEAMNAME.get(tid, "")
    t = toks(provider)
    best = 0
    for c in (sc,):
        c = toks(c)
        if c:
            best = max(best, len(t & c) / len(c))
    return best


out = []
for r in R.to_dict(orient="records"):
    eid, book, key, side, point, recv = r["quote_id"].split("|")
    key, who = (key.split(":") + [None])[:2]
    rts = pd.Timestamp(recv)
    c = raw[(raw.eid == eid) & (raw.book == book) & (raw.key == key)]
    if len(c):
        dt = (c.ts - rts).abs()
        c = c[dt == dt.min()]
        rec_dt = dt.min().total_seconds()
    else:
        rec_dt = None
    pt = None if point in ("None", "nan") else float(point)
    if key in ("h2h", "spreads"):
        cand = c if pt is None else c[c.point.astype(float) == pt]
        team_field = "name"
    elif key == "totals":
        cand = c[(c.name.str.upper() == side) & (c.point.astype(float) == pt)]
        team_field = None
    else:
        cand = c[(c.name.str.upper() == side) & (c.point.astype(float) == pt)]
        team_field = "desc"
    rec = dict(slate=r["slate"], game_id=r["game_id"], market_type=r["market_type"], side=side, point=pt, book=book,
               key=key, raw_found=len(cand) > 0, dt_s=rec_dt, price_match=None, provider_home=None, provider_away=None,
               raw_team=None, eval_team_id=None, sim_eval=None, sim_other=None, ok=None)
    if len(c):
        rec["provider_home"], rec["provider_away"] = c.p_home.iloc[0], c.p_away.iloc[0]
        # orientation of the event itself (home of provider vs canonical home)
        rec["event_home_sim"] = sim(c.p_home.iloc[0], r["home_team_id"])
        rec["event_away_sim"] = sim(c.p_away.iloc[0], r["away_team_id"])
        rec["event_swapped_sim"] = sim(c.p_home.iloc[0], r["away_team_id"])
    dec = r["decimal"]
    if len(cand):
        if team_field is None:
            rec["ok"] = True
            x = cand.iloc[0]
        else:
            eval_tid = None
            mt = r["market_type"].replace("ALT_", "")
            if mt in ("MONEYLINE", "SPREAD"):
                eval_tid = r["home_team_id"] if side == "HOME" else r["away_team_id"]
            else:
                eval_tid = r["home_team_id"] if mt == "HOME_TEAM_TOTAL" else r["away_team_id"]
            other = r["away_team_id"] if eval_tid == r["home_team_id"] else r["home_team_id"]
            # pick the outcome whose price equals the frozen price (identifies the outcome uniquely)
            cand = cand.assign(dec=cand.price.map(lambda a: 1 + (a / 100 if a > 0 else 100 / -a)))
            x = cand.iloc[(cand.dec - dec).abs().argsort()].iloc[0]
            rec["raw_team"] = x[team_field]
            rec["eval_team_id"] = eval_tid
            rec["sim_eval"], rec["sim_other"] = sim(x[team_field], eval_tid), sim(x[team_field], other)
            rec["ok"] = rec["sim_eval"] > rec["sim_other"]
        rec["price_match"] = abs((1 + (x.price / 100 if x.price > 0 else 100 / -x.price)) - dec) < 1e-6
    out.append(rec)
T = pd.DataFrame(out)
T.to_csv(OUT / "team_mapping_rows.csv", index=False)
print("rows", len(T), "raw found", T.raw_found.sum(), "price match", T.price_match.sum(), "ok", T.ok.sum())
print(T.groupby("market_type").agg(n=("ok", "size"), found=("raw_found", "sum"), ok=("ok", "sum"),
                                   price=("price_match", "sum")).to_string())
bad = T[(T.ok == False) | (T.raw_found == False) | (T.price_match == False)]
print("problem rows", len(bad))
print(bad[["game_id", "market_type", "side", "point", "book", "raw_team", "provider_home", "provider_away", "sim_eval",
           "sim_other", "raw_found", "price_match"]].to_string())
ev = T.dropna(subset=["event_home_sim"]).drop_duplicates("game_id")
print("event orientation: home sim mean", ev.event_home_sim.mean(), "min", ev.event_home_sim.min(),
      " provider home looks like canonical away (sim>=.5):", (ev.event_swapped_sim >= .5).sum())
print(ev[ev.event_home_sim < .5][["game_id", "provider_home", "provider_away", "event_home_sim", "event_swapped_sim"]].to_string())
