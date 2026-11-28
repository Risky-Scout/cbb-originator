"""Stage 08: reconcile the 55 Stage 06 parameter groups (P1-P38, G1-G17) against the consolidated Part III requirements.

The Stage 06 inventory (../../2026-11-27-stage6/PARAMETER_PROVENANCE.md) is NOT repeated: its value / estimator / T->P->R trace is
cited by id. This table adds what Part III requires and Stage 06 did not record: the Part III class name, materiality, the Part III §5
responsible stage, the Part III §4 disposition still required, and the Stage 08 benchmark evidence that bears on it.

    s8_parameters.py OUT
"""
import csv, sys

OUT = sys.argv[1]
CLASS = {"FIT-D": "EMPIRICALLY_FITTED", "FIT-1": "EMPIRICALLY_FITTED", "REG": "REGULARIZED_OR_PRIOR_DRIVEN",
         "VAL": "VALIDATED_FIXED_PARAMETER", "HEUR": "UNFITTED_HEURISTIC", "UNK": "UNKNOWN_PROVENANCE",
         "MATH": "MATHEMATICAL_CONSTANT", "OPS": "OPERATIONAL_CONTROL", "SAFE": "SAFETY_CONTROL", "FALLBACK": "SAFETY_CONTROL"}
# id, stage06 class(es), material predictive?, Part III §5 stage, disposition required (A-D) or n/a, Stage 08 evidence, priority
R = [
 ("P1", "FIT-D", True, "09", "re-validate (Hook A)", "participation reliability: p<0.10 bins realize 8-19% vs 1-7% forecast; 0.5-0.7 bins realize 44-50% vs 55-65% (22,997 player-games, LAST_PREGAME)", 1),
 ("P2", "HEUR/SAFE", True, "09", "A or D", "L2 1e-4*n and logit clip: no selection evidence; interacts with the tail miscalibration of P1", 2),
 ("P3", "FIT-D", True, "09", "re-validate (Hook C)", "projected minutes unbiased overall (+0.01, CI -0.10..0.10); realized starters -1.03 / bench +0.95 (diagnostic)", 2),
 ("P4", "FIT-D", True, "09", "re-validate", "minutes z sd 1.03, normal-reference 90% coverage 0.90; COLD classes z sd 1.10-1.23", 3),
 ("P5", "SAFE/MATH", True, "09", "A or D (minutes clipping)", "clip [1,45] + KMAX 60; S4 Jensen inflation at low minutes", 2),
 ("P6", "FIT-1", True, "09", "re-validate vs daily sigma (S6)", "-", 3),
 ("P7", "HEUR", True, "09", "A or D", "PS2 band edges / tail rule unfitted (S1)", 3),
 ("P8", "FIT-D/MATH", True, "17", "validate shared OT state", "-", 4),
 ("P9", "VAL", True, "10", "n/a (validated)", "-", 5),
 ("P10", "HEUR", True, "10/12", "A or D", "prior_seasons 3, rate_prior_min 100, NB threshold 1.1, sigma buckets: no selection evidence", 3),
 ("P11", "FIT-D", True, "10", "n/a", "-", 5),
 ("P12", "REG/FIT-D", True, "10", "re-validate (recency, S8)", "cold-start PTS bias: COLD_EARLY_SEASON +0.56, COLD_NO_HISTORY +0.53 vs WARM +0.14", 2),
 ("P13", "SAFE", False, "-", "n/a", "-", 9),
 ("P14", "FIT-1", True, "10", "n/a (holdout PASS)", "-", 5),
 ("P15", "VAL", True, "10", "n/a (joint holdout)", "-", 5),
 ("P16", "FIT-1", True, "10/11", "re-validate under daily GLM (S3)", "TOV bias -0.069 overall; DAILY era -0.030 (CI -0.070..0.006); FROZEN era -0.083", 4),
 ("P17", "FIT-1", True, "10", "n/a", "-", 5),
 ("P18", "HEUR", True, "10", "A, B or D (DEV1 K=31)", "no grid/fit; one value for 7 stats", 2),
 ("P19", "REG/FIT-1", True, "10", "n/a", "-", 5),
 ("P20", "HEUR", True, "10", "A, B or D (PI2 clip [0.7,1.3], K_NO_PRIOR 31, standardization)", "clip binds for every first-appearance PTS player; final-engine window too short for a stratified test", 2),
 ("P21", "FIT-1", True, "10/16", "n/a (sealed)", "-", 5),
 ("P22", "FIT-1", True, "11", "re-validate under PS2 (C3)", "kappa selected against the pre-PS2 incumbent", 3),
 ("P23", "FIT-D", True, "11", "n/a (C3 corrected)", "-", 6),
 ("P24", "OPS/SAFE", False, "-", "n/a", "-", 9),
 ("P25", "FIT-D", True, "15/13", "re-validate", "-", 4),
 ("P26", "HEUR", True, "11/15", "A or D (environment shrinkage 4/6)", "declared, not tuned", 3),
 ("P27", "FIT-D", True, "11", "re-validate (double correction S3, beta_PTS ~1.30 Jensen S4)", "PTS bias by PROJECTED minutes: <20 min +0.65..+0.71 while projected minutes are unbiased in those bands; 20+ min -0.35..-0.43", 1),
 ("P28", "REG/SAFE/VAL", True, "11", "n/a", "-", 6),
 ("P29", "FIT-D", True, "12", "re-validate vs corrected mean", "PIT tails: PTS lower 5% tail 9.7%, PA 8.9%, PR 8.3%, PRA 8.0%; other stats 4.8-5.7%", 1),
 ("P30", "FIT-D (unread)", False, "12", "C (eliminate as model state) or wire", "fitted but not read by the scorer (E2p)", 6),
 ("P31", "FIT-1/SAFE", True, "12", "re-estimate PTS zero / low-count mass", "forecast P(PTS=0 | play) 0.224 vs realized 0.412 (BENCH_9PLUS, n=5,961); 0.082 vs 0.178 (ROTATION); 0.016 vs 0.052 (TOP5)", 1),
 ("P32", "SAFE/OPS", False, "12", "n/a", "0 outcomes beyond support; 55 of 727,236 played PMF rows (all vintages) hit the 1e-12 log floor", 9),
 ("P33", "REG/FIT-D", True, "13", "re-estimate on compound-PTS PIT (S2)", "-", 4),
 ("P34", "FIT-D", False, "13", "n/a (estimation only)", "-", 9),
 ("P35", "VAL/SAFE", True, "13", "n/a", "-", 7),
 ("P36", "MATH/SAFE", False, "13", "n/a", "-", 9),
 ("P37", "OPS", False, "-", "n/a", "-", 9),
 ("P38", "SAFE/OPS/VAL", False, "07", "n/a", "-", 9),
 ("G1", "FIT-D", True, "15", "re-validate", "spread (Holm p 0.009) and away team total markets inferior to the matched market", 2),
 ("G2", "MATH", False, "15", "n/a (C4 corrected)", "all historical game PMFs are pre-C4 joint 1.0.0", 9),
 ("G3", "MATH/SAFE/OPS", False, "15", "n/a", "-", 9),
 ("G4", "UNK", True, "14", "A or D (tau 150, lambdas, rho_carry)", "no search code; spread inferiority is consistent with team-strength error (not isolated)", 2),
 ("G5", "FIT-1", True, "14", "n/a", "-", 6),
 ("G6", "VAL", True, "14", "n/a", "-", 6),
 ("G7", "FIT-1/VAL", True, "14", "n/a (ridge 1.0 and clip hand-set: D)", "-", 5),
 ("G8", "FALLBACK", True, "14", "n/a (visible)", "-", 8),
 ("G9", "UNK (unused)", True, "16", "C (eliminate) or A", "loaded, never applied; guarded", 4),
 ("G10", "HEUR", True, "16", "A or D (gamma 0.3)", "not separable in Stage 08 (availability_delta recorded; no shock subsample large enough)", 3),
 ("G11", "UNK", True, "17", "A (refit on corrected means)", "margin under-dispersed: 90% coverage 0.858 (n=331), model sd 11.70 vs residual sd 12.83; ML overconfident 0.5-0.9", 1),
 ("G12", "FIT-1", True, "17", "n/a", "-", 6),
 ("G13", "HEUR", True, "17", "A or D (T*, max lead, reach, MIN_N)", "-", 5),
 ("G14", "FIT-1", True, "17", "re-validate (S3g)", "-", 5),
 ("G15", "FIT-1", True, "17", "n/a", "-", 6),
 ("G16", "SAFE/MATH/OPS", False, "17", "n/a", "-", 9),
 ("G17", "SAFE", False, "17", "n/a", "-", 9),
]
assert len(R) == 55 and len({r[0] for r in R}) == 55
with open(f"{OUT}/parameter_reconciliation.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "stage06_class", "part3_class", "material_predictive", "part3_stage", "disposition_required",
                "stage08_evidence", "priority_rank", "stage06_trace"])
    for i, c, m, st, disp, ev, pr in R:
        primary = c.split("/")[0].split(" ")[0]
        w.writerow([i, c, CLASS.get(primary, "UNKNOWN_PROVENANCE"), m, st, disp, ev, pr,
                    "../../2026-11-27-stage6/PARAMETER_PROVENANCE.md#" + i])
print("55 groups written")
