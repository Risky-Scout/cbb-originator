"""Builds BEFORE_AFTER.md tables + summary numbers from the measured evidence files in this folder (no new runs)."""
import json, statistics as st
from pathlib import Path
D = Path(__file__).parent
eq = [json.loads(l) for l in (D / "equivalence_results.jsonl").open()]
bench = [json.loads(l) for l in (D / "bench_results.jsonl").open()]
val = json.loads((D / "validation_overhead.json").read_text())
cen = json.loads((D / "cache_census.json").read_text())
prof = json.loads((D / "profile_S20261125T152533Z-fbba09a7a326.json").read_text())
out = {"equivalence": {"records": len(eq), "closure_builds": sum(r["closure_on"] for r in eq), "incumbent_builds": sum(not r["closure_on"] for r in eq),
                       "games": sum(r["games"] for r in eq if r["closure_on"]),
                       "player_rows_compared": sum(sum(v[2] for v in r["per_game"].values()) for r in eq),
                       "all_equal": all(r["all_equal"] for r in eq), "pit_violations": sum(r["pit_violations"] for r in eq),
                       "target_outcome_rows": sum(r["target_outcome_rows"] for r in eq), "post_as_of_rows": sum(r["post_as_of_rows"] for r in eq)},
       "bench": bench, "validation_overhead": val, "cache_census": cen}
L = ["| as_of (state) | games | path | box rows read | subs rows | wall s | CPU s (u+s) | peak RSS MB | disk write MB | read (rchar) MB | scratch MB | equal |",
     "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for r in sorted(bench, key=lambda r: (r["as_of"], not r["closure"])):
    L.append(f"| {r['as_of'][:16]} | {r['games']} | {'closure' if r['closure'] else 'incumbent'} | {r['player_box_rows']:,} | {r['substitution_rows']:,} | "
             f"{r['wall_s']} | {round(r['cpu_user_s'] + r['cpu_sys_s'], 1)} | {r['peak_rss_mb']} | {r['write_mb']} | {r['rchar_mb']} | {r['scratch_mb']} | {r['all_equal']} |")
pairs = {}
for r in bench:
    pairs.setdefault(r["as_of"], {})["closure" if r["closure"] else "incumbent"] = r
R = []
for a, p in sorted(pairs.items()):
    if len(p) == 2:
        R.append({"as_of": a[:16], "games": p["closure"]["games"], "wall_ratio": round(p["incumbent"]["wall_s"] / p["closure"]["wall_s"], 2),
                  "cpu_ratio": round((p["incumbent"]["cpu_user_s"] + p["incumbent"]["cpu_sys_s"]) / (p["closure"]["cpu_user_s"] + p["closure"]["cpu_sys_s"]), 2),
                  "write_ratio": round(p["incumbent"]["write_mb"] / p["closure"]["write_mb"], 1),
                  "rows_ratio": round(p["incumbent"]["player_box_rows"] / p["closure"]["player_box_rows"], 1)})
out["paired_ratios"] = R
cl = [r["wall_s"] for r in eq if r["closure_on"]]
out["closure_wall_s"] = {"min": min(cl), "median": st.median(cl), "max": max(cl)}
inc = [r["wall_s"] for r in eq if not r["closure_on"]] + [r["wall_s"] for r in bench if not r["closure"]]
out["incumbent_wall_s"] = {"min": min(inc), "median": st.median(inc), "max": max(inc)}
(D / "summary.json").write_text(json.dumps(out, indent=1, default=str))
(D / "bench_table.md").write_text("\n".join(L) + "\n")
print("\n".join(L)); print(json.dumps({k: out[k] for k in ("equivalence", "paired_ratios", "closure_wall_s", "incumbent_wall_s")}, indent=1))
