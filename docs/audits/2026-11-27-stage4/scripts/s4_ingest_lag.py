"""Read-only: first retained state version that contains each 2026-27 game's player box lines vs its HR-1
historical_knowable_at. Version instant = state_manifest created/source cutoff (falls back to the S<ts> name)."""
import json, sys, os, glob
import duckdb, pandas as pd
OUT = sys.argv[1]
V = "/var/lib/sports-models/cbb-originator/prod/state/versions"
first, keys_seen = {}, None
for d in sorted(glob.glob(f"{V}/S2026*")):
    v = os.path.basename(d)
    try:
        man = json.load(open(f"{d}/state_manifest.json"))
    except Exception:
        man = {}
    if keys_seen is None:
        keys_seen = sorted(man.keys())
    built = pd.Timestamp(v[1:16].replace("T", ""), tz="UTC") if True else None
    built = pd.to_datetime(v[1:16], format="%Y%m%dT%H%M%S", utc=True)
    try:
        con = duckdb.connect(f"{d}/increment.duckdb", read_only=True, config={"threads": 1, "memory_limit": "300MB"})
        df = con.execute("SELECT canonical_game_id, min(historical_knowable_at) k, count(*) n FROM flagship.player_game_outcomes "
                         "WHERE season = 2027 GROUP BY 1").df()
        con.close()
    except Exception as e:
        continue
    for r in df.itertuples(index=False):
        if r.canonical_game_id not in first:
            first[r.canonical_game_id] = (v, built, pd.Timestamp(r.k))
rows = pd.DataFrame([{"game": g, "first_version": v, "first_built": b, "knowable": k} for g, (v, b, k) in first.items()])
rows["lag_h"] = (rows.first_built - pd.to_datetime(rows.knowable, utc=True)).dt.total_seconds() / 3600
first_v = sorted(glob.glob(f"{V}/S2026*"))[0]
rows["in_first_retained"] = rows.first_version == os.path.basename(first_v)
x = rows[~rows.in_first_retained]
rep = {"versions": len(glob.glob(f"{V}/S2026*")), "manifest_keys": keys_seen, "games": int(len(rows)),
       "games_first_seen_after_first_retained": int(len(x)),
       "ingested_before_knowable": int((x.lag_h < 0).sum()),
       "lag_h_quantiles": x.lag_h.quantile([0, .05, .25, .5, .75, .95, 1]).round(2).to_dict() if len(x) else {},
       "lag_gt_6h": int((x.lag_h > 6).sum()), "lag_gt_24h": int((x.lag_h > 24).sum())}
rows.to_csv(f"{OUT}/box_ingest_lag.csv", index=False)
json.dump(rep, open(f"{OUT}/box_ingest_lag.json", "w"), indent=1, default=str)
print(json.dumps(rep, indent=1, default=str))
