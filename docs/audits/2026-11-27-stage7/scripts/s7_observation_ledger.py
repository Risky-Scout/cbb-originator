"""Stage 07, read-only: first-observed / revision ledger of the 2026-27 season from ALL retained immutable state versions.

For every player box line (player_game_key), team box line (game, team), game result (canonical_game_id) and roster row
(player, team, season) it records the first retained version that contains it (version created_at = actual acquisition into
canonical state), the row's own received_at / historical_knowable_at, and every later content change (a provider revision),
with the version instant at which each revised content first became observable. No per-row timestamp is invented: version
instants come from state_manifest.created_at, rows from their own columns.

    s7_observation_ledger.py OUT
"""
import glob, json, os, sys, hashlib
import duckdb, pandas as pd
OUT = sys.argv[1]
V = "/var/lib/sports-models/cbb-originator/prod/state/versions"
SKIP = {"source_file", "ingestion_version", "received_at", "processed_at", "source_record_key"}
SPECS = {
    "player_box": ("flagship.player_game_outcomes", ["player_game_key"]),
    "team_box": ("flagship.team_game_outcomes", ["canonical_game_id", "canonical_team_id"]),
    "game_result": ("flagship.games", ["canonical_game_id"]),
    "roster": ("flagship.rosters", ["canonical_player_id", "canonical_team_id", "season"]),
}
EXTRA = {"player_box": ["canonical_game_id", "canonical_player_id", "historical_knowable_at", "received_at"],
         "team_box": ["historical_knowable_at", "received_at"],
         "game_result": ["scheduled_tip_utc", "home_points", "away_points"],
         "roster": ["valid_from", "valid_to", "received_at"]}
WHERE = {"game_result": "season = 2027 AND home_points IS NOT NULL", "roster": "season = 2027"}
state = {k: {} for k in SPECS}          # key -> dict
versions = []
for d in sorted(glob.glob(f"{V}/S2026*")):
    v = os.path.basename(d)
    try:
        man = json.load(open(f"{d}/state_manifest.json"))
        at = pd.Timestamp(man["created_at"])
        con = duckdb.connect(f"{d}/increment.duckdb", read_only=True, config={"threads": 1, "memory_limit": "400MB"})
    except Exception as e:
        versions.append({"version": v, "error": repr(e)[:200]}); continue
    versions.append({"version": v, "created_at": str(at), "source_cutoff": man.get("source_cutoff"), "sync_id": man.get("sync_id")})
    for kind, (tab, key) in SPECS.items():
        cols = [c[0] for c in con.execute(f"select column_name from information_schema.columns where table_schema||'.'||table_name='{tab}' order by ordinal_position").fetchall()]
        content = [c for c in cols if c not in SKIP]
        w = WHERE.get(kind, "season = 2027")
        ex = [c for c in EXTRA[kind] if c in cols]
        q = (f"select {', '.join(key)}, md5(concat_ws('|', {', '.join('coalesce(cast(%s as varchar),chr(0))' % c for c in content)})) h"
             + (", " + ", ".join(ex) if ex else "") + f" from {tab} where {w}")
        df = con.execute(q).df()
        S = state[kind]
        for r in df.itertuples(index=False):
            rd = r._asdict()
            k = tuple(rd[c] for c in key)
            cur = S.get(k)
            if cur is None:
                S[k] = {"first_version": v, "first_at": at, "h": rd["h"], "n_rev": 0, "last_rev_at": None,
                        "first_extra": {c: rd[c] for c in ex}, "last_seen_version": v, "absent_since": None, "rev_extra": []}
            else:
                if cur["h"] != rd["h"]:
                    cur["n_rev"] += 1; cur["last_rev_at"] = at; cur["h"] = rd["h"]
                    if len(cur["rev_extra"]) < 5:
                        cur["rev_extra"].append({"at": str(at), **{c: str(rd[c]) for c in ex}})
                cur["last_seen_version"] = v; cur["absent_since"] = None
        present = set(tuple(x) for x in df[key].itertuples(index=False, name=None))
        for k, cur in S.items():
            if k not in present and cur["absent_since"] is None and cur["last_seen_version"] != v:
                cur["absent_since"] = at
    con.close()
summary = {"versions_read": sum(1 for x in versions if "created_at" in x), "version_errors": [x for x in versions if "error" in x],
           "first_version": versions[0], "last_version": versions[-1]}
for kind, (tab, key) in SPECS.items():
    rows = []
    for k, c in state[kind].items():
        r = dict(zip(key, k)); r.update({"first_version": c["first_version"], "first_at": c["first_at"], "n_rev": c["n_rev"],
                                       "last_rev_at": c["last_rev_at"], "absent_since": c["absent_since"],
                                       "rev_extra": json.dumps(c["rev_extra"], default=str)})
        r.update({f"first_{x}": y for x, y in c["first_extra"].items()})
        rows.append(r)
    df = pd.DataFrame(rows)
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str)
    df.to_parquet(f"{OUT}/obs_{kind}.parquet", index=False)
    summary[kind] = {"keys": len(df), "revised_keys": int((df.n_rev > 0).sum()) if len(df) else 0,
                     "removed_keys": int((df.absent_since != "None").sum()) if len(df) else 0}
pd.DataFrame(versions).to_csv(f"{OUT}/obs_versions.csv", index=False)
json.dump(summary, open(f"{OUT}/obs_summary.json", "w"), indent=1, default=str)
print(json.dumps(summary, indent=1, default=str))
