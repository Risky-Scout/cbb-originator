"""Stage 2 profile (read-only on production state): re-run ONE cached warm-up build verbatim with per-statement timing.
argv: OUT STATE_VERSION [max_games]"""
import json, os, re, resource, sys, time
from pathlib import Path
REL = os.path.realpath("/opt/sports-models/cbb-originator/current"); sys.path.insert(0, REL)
import pandas as pd
from cbb_platform.prod import features as PF, config as PC
from cbb_platform.stage3 import build as S3B
from cbb_platform.walkforward import training_rows as TR, config as WC
from cbb_platform.v2 import marginal as M

OUT, SV = Path(sys.argv[1]), sys.argv[2]
S = Path("/var/lib/sports-models/cbb-originator")
CACHE = S / "walkforward/cbb-originator/training/features"
HIST = S / "history/cbb_production_history_minimal.duckdb"
INC = S / f"prod/state/versions/{SV}/increment.duckdb"
LOG = []


def rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def io():
    d = dict(l.split(": ") for l in open("/proc/self/io").read().strip().split("\n"))
    return {k: int(v) for k, v in d.items() if k in ("read_bytes", "write_bytes", "rchar")}


class Proxy:
    def __init__(self, con, tag): self._c, self._t = con, tag
    def execute(self, sql, *a, **k):
        t = time.perf_counter(); r = self._c.execute(sql, *a, **k); dt = time.perf_counter() - t
        m = re.search(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP\s+)?(?:TABLE|VIEW|SCHEMA)\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.]+)", sql, re.I)
        LOG.append({"phase": self._t, "s": round(dt, 3), "rel": m.group(1) if m else sql.strip()[:60].replace("\n", " "),
                    "rss_mb": round(rss(), 1)})
        return r
    def __getattr__(self, n): return getattr(self._c, n)


# the cached group to replay: games of this state version built together (same build.as_of)
metas = [json.loads((d / "meta.json").read_text()) for d in CACHE.iterdir() if (d / "meta.json").exists()]
mine = [m for m in metas if m.get("state_version") == SV and m.get("status") == "BUILT"]
groups = {}
for m in mine:
    groups.setdefault(m["build"]["as_of"], []).append(m)
asof, grp = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0]
gids = sorted(m["game_id"] for m in grp)
from cbb_platform import props_v0 as PV0
CFG = PV0.load_config()
print("state", SV, "groups", {k: len(v) for k, v in groups.items()}, "replay as_of", asof, "games", len(gids), flush=True)

from cbb_platform import duckdb_limits as DLM
print("DLM", DLM.install(), flush=True)
orig_connect = S3B.connect
S3B.connect = lambda p: Proxy(orig_connect(p), "stage3")
work = OUT / "work"; work.mkdir(parents=True, exist_ok=True)
t0 = time.perf_counter(); io0 = io()
# instant scan cost (the 46e07f3 grouping proof)
ti = time.perf_counter(); inst = PF.as_of_mask_instants(HIST, INC, WC.PRODUCTION_SEASON, pd.Timestamp(asof) - pd.Timedelta(hours=12), pd.Timestamp(asof)); t_inst = time.perf_counter() - ti
tc = time.perf_counter(); canon = PF.compact_canonical(work / "canonical_asof.duckdb", gids, asof, WC.PRODUCTION_SEASON, HIST, INC); t_comp = time.perf_counter() - tc
io1 = io()
tf = time.perf_counter(); feats = PF.build_features(work / "canonical_asof.duckdb", work / "features_asof.duckdb", gids, WC.PRODUCTION_SEASON); t_s3 = time.perf_counter() - tf
io2 = io()
tm = time.perf_counter(); fr = PF.model_frame(work / "features_asof.duckdb", CFG, gids, WC.PRODUCTION_SEASON); t_mf = time.perf_counter() - tm
cand, team = fr["cand"], fr["team"]
eq = {}
for m in grp:
    g = m["game_id"]
    c = cand[cand["canonical_game_id"] == g].sort_values("player_game_example_id", kind="mergesort")
    t = team[team["game_id"] == g].sort_values("team_id", kind="mergesort")
    eq[g] = {"cand": TR._sha_df(c) == m["cand_sha256"], "team": TR._sha_df(t) == m["team_sha256"]}
sizes = {p.name: p.stat().st_size for p in work.iterdir()}
import duckdb
con = duckdb.connect(str(work / "canonical_asof.duckdb"), read_only=True)
rows = {r: con.execute(f"SELECT count(*) FROM {r}").fetchone()[0] for r in
        ("flagship.games", "flagship.player_game_outcomes", "flagship.team_game_outcomes", "flagship.substitutions", "flagship.rosters", "core.players")}
by_season = con.execute("SELECT season, count(*) FROM flagship.player_game_outcomes GROUP BY 1 ORDER BY 1").fetchall()
con.close()
con = duckdb.connect(str(work / "features_asof.duckdb"), read_only=True)
frows = {r: con.execute(f"SELECT count(*) FROM {r}").fetchone()[0] for r in
         ("feat.player_box_state", "feat.player_participation_state", "feat.player_features", "feat.team_features", "ex.player_game_examples")}
con.close()
for p in work.iterdir(): p.unlink()
work.rmdir()
agg = {}
for e in LOG:
    agg[e["rel"]] = agg.get(e["rel"], 0) + e["s"]
out = {"state_version": SV, "replay_as_of": asof, "games": gids, "group_sizes": {k: len(v) for k, v in groups.items()},
       "seconds": {"as_of_mask_instants_12h": round(t_inst, 2), "compact_canonical": round(t_comp, 2), "stage3": round(t_s3, 2),
                   "model_frame": round(t_mf, 2), "total": round(time.perf_counter() - t0, 2)},
       "instants_in_12h_window": len(inst), "io": {"compact": {k: io1[k] - io0[k] for k in io0}, "stage3": {k: io2[k] - io1[k] for k in io1}},
       "peak_rss_mb": round(rss(), 1), "scratch_bytes": sizes, "canonical_rows": rows, "player_rows_by_season": by_season,
       "feature_rows": frows, "incumbent_reproduces_cache": eq,
       "top_statements": sorted(agg.items(), key=lambda kv: -kv[1])[:25], "statements": LOG}
(OUT / f"profile_{SV}.json").write_text(json.dumps(out, indent=1, default=str))
print(json.dumps({k: out[k] for k in ("seconds", "instants_in_12h_window", "peak_rss_mb", "feature_rows", "incumbent_reproduces_cache")}, default=str))
print("TOP", json.dumps(out["top_statements"][:15]))
