"""Challenger M2 (cold-player rate uncertainty) evaluation, written before any M2 metric was read
(PHASE4_PROTOCOL.json challenger_M2_cold_rate_uncertainty). The M1 evaluator's functions and gates, arms PRODXM2 vs
PRODXT1 (production A2 + P2 + T1; identical seeds).   p13_m2_eval.py <F2026 m2> <F2026 t1> <LIVE m2> <LIVE t1>"""
import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("p9", Path(__file__).with_name("p9_m1_eval.py"))
p9 = importlib.util.module_from_spec(spec); spec.loader.exec_module(p9)

if __name__ == "__main__":
    f26m, f26b, livm, livb = sys.argv[1:5]
    F = p9.report("F2026 PRODXM2 vs PRODXT1", p9.pair(f26m, "PRODXM2", f26b, "PRODXT1"))
    L = p9.report("LIVE PRODXM2 vs PRODXT1", p9.pair(livm, "PRODXM2", livb, "PRODXT1"))
    g = {"G1": F["stacked"][1] < 0 and L["stacked"][1] < 0 and all(v[1] <= 0.0005 for r in (F, L) for v in r["per_stat"].values()),
         "G2": all(v[1] <= 0.002 for v in F["cells"].values()),
         "G3": L.get("early_stacked", (0, 1))[1] <= 0.0005,
         "TARGET": F["target_stacked"][1] < 0 and L["target_stacked"][1] < 0,
         "COMBOS": all(v[1] <= 0 for r in (F, L) for v in r["combos_rps"].values())}
    print("\nGATES", g, "\nDECISION:", "PROMOTE" if all(g.values()) else "REJECT")
    json.dump({"gates": g, "F2026": F, "LIVE": L}, open(p9.C.PROJECT_ROOT / "docs/audits/2026-11-22-causal/results/m2_eval.json", "w"), indent=1, default=float)
