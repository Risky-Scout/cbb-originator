"""Stage 07, read-only: historical F1 (frozen-model substitution) activation scan over every persisted production
forecast snapshot (ops/forecasts/*/index.jsonl + snapshot.json), official tick (ops/ticks/*/tick.json + pmfs.parquet)
and run summary (runs/*/run_summary.json). Frozen string = cbb-v2-jm1-final-shadow-1.0.0."""
import glob, json, os, sys
import pandas as pd
OUT = sys.argv[1]
P = "/var/lib/sports-models/cbb-originator/prod"
FROZEN = "cbb-v2-jm1-final-shadow-1.0.0"
rows = []
for f in sorted(glob.glob(f"{P}/ops/forecasts/*/*/snapshot.json")):
    try:
        s = json.load(open(f))
    except Exception as e:
        rows.append({"kind": "snapshot", "path": f, "error": repr(e)[:100]}); continue
    rows.append({"kind": "snapshot", "path": os.path.relpath(f, P), "day": f.split("/")[-3], "window": s.get("window") or s.get("snapshot_kind"),
                 "as_of": s.get("prediction_as_of") or s.get("as_of"), "model_version": s.get("model_version"),
                 "model_kind": s.get("model_kind"), "code_commit": s.get("code_commit"), "engine_id": s.get("engine_id")})
for d in sorted(glob.glob(f"{P}/ops/ticks/*")):
    mv = None
    pm = f"{d}/pmfs.parquet"
    if os.path.exists(pm):
        try:
            mv = sorted(set(map(str, pd.read_parquet(pm, columns=["model_version"])["model_version"].dropna())))
        except Exception as e:
            mv = ["ERR " + repr(e)[:60]]
    t = {}
    try:
        t = json.load(open(f"{d}/tick.json"))
    except Exception:
        pass
    rows.append({"kind": "tick", "path": os.path.relpath(d, P), "day": None, "as_of": t.get("started_at") or t.get("tick_at"),
                 "model_version": ",".join(mv) if mv else None, "code_commit": t.get("source_commit")})
for f in sorted(glob.glob(f"{P}/runs/*/run_summary.json")):
    try:
        s = json.load(open(f))
    except Exception:
        continue
    mi = s.get("model_identity") or {}
    rows.append({"kind": "run", "path": os.path.relpath(f, P), "as_of": s.get("prediction_as_of"), "model_version": mi.get("model_version"),
                 "model_kind": mi.get("model_kind"), "code_commit": mi.get("code_commit"), "engine_id": mi.get("engine_id"),
                 "readiness": s.get("readiness_state"), "predictions": s.get("predictions")})
df = pd.DataFrame(rows)
df.to_csv(f"{OUT}/fallback_scan.csv", index=False)
df["frozen"] = df["model_version"].astype(str).str.contains(FROZEN, regex=False)
rep = {k: {"n": int(len(g)), "frozen": int(g.frozen.sum()),
           "frozen_as_of_range": [str(g.loc[g.frozen, "as_of"].min()), str(g.loc[g.frozen, "as_of"].max())] if g.frozen.any() else None,
           "model_versions": g["model_version"].astype(str).value_counts().head(12).to_dict()} for k, g in df.groupby("kind")}
json.dump(rep, open(f"{OUT}/fallback_scan.json", "w"), indent=1, default=str)
print(json.dumps(rep, indent=1, default=str))
