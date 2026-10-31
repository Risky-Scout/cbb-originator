"""DAILY PROMOTION HARD GATES (directive section 15). A candidate becomes ACTIVE only if EVERY gate passes; otherwise
DAILY_MODEL_PROMOTION = REFUSED and the last known-good version stays active. No gate has a tunable threshold: each is an
identity, a legality rule, a frozen tolerance or an equality with the approved architecture.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform import config as C
from cbb_platform.prod import config as PC, fixtures as FX, run as RN, state as ST
from cbb_platform.v2 import jm1_final as F, jm1_final_production as JP, joint_minutes_v2 as JM, latent as L
from cbb_platform.walkforward import config as WC, fit as WF, store as SO

GOLDEN = C.PROJECT_ROOT / "tests" / "fixtures" / "golden" / "prod_equivalence_v1.json"
ARCH_KEYS = ("terms", "alpha", "families", "kmax_minutes", "kmax_counts", "exposure", "seeds", "architecture")


def _all(xs) -> bool:
    return bool(all(xs))


def architecture_unchanged(art: dict, marg: dict, opening: dict) -> dict:
    """Nothing but parameters may differ from the opening artifacts."""
    oa, om = opening["art"], opening["marg"]
    out = {k: art.get(k) == oa.get(k) for k in ARCH_KEYS}
    out["glm_terms"] = art["structure"]["terms"] == oa["structure"]["terms"] == list(JM.JM_TERMS)
    out["count_families"] = {s: d["family"] for s, d in art["conditional_dispersion"].items()} == \
                            {s: d["family"] for s, d in oa["conditional_dispersion"].items()}
    out["marginal_config"] = marg["config"] == om["config"]
    out["marginal_coef_order"] = marg["coef_order"] == om["coef_order"]
    out["marginal_transforms"] = (marg["v0_state"]["transform"]["ind"] == om["v0_state"]["transform"]["ind"]
                                  and marg["participation_state"]["transform"]["ind"] == om["participation_state"]["transform"]["ind"])
    out["marginal_dispersion_families"] = ({t: d["family"] for t, d in marg["v0_state"]["dispersion"].items()}
                                           == {t: d["family"] for t, d in om["v0_state"]["dispersion"].items()})
    out["weighting_policy"] = (marg["weighting_policy"], marg["half_life"]) == (om["weighting_policy"], om["half_life"])
    out["stl_policy"] = marg["stl_policy"] == om["stl_policy"]
    return out


def dispersion_valid(art: dict) -> dict:
    out = {}
    for s, d in art["conditional_dispersion"].items():
        phi = d.get("phi")
        out[s] = phi is not None and np.isfinite(phi) and phi >= 0 and (d["family"] != "POISSON" or phi == 0)
    return out


def golden_smoke(model, work: Path) -> dict:
    """The CI golden fixture slate (labeled synthetic production-format data) scored end-to-end by the candidate:
    same scored games, same priced key set, same distribution methods and readiness as the golden file, every market
    pmf normalized and non-negative. Values are NOT compared (parameters legitimately differ)."""
    gold = json.loads(GOLDEN.read_text())
    work.mkdir(parents=True, exist_ok=True)
    hist = FX.synthetic_history(work / "history.duckdb")
    rows, meta = FX.increment_rows(hist)
    ST.update(rows, FX.SOURCE_CUTOFF, FX.FIXTURE_PROVIDER, meta["preseason_evidence"], work / "state",
              created_at=FX.SOURCE_CUTOFF + pd.Timedelta(minutes=5))
    q = FX.quotes_files(hist, meta, work / "quotes")
    r = RN.daily(FX.AS_OF, q, availability=FX.availability(meta), state_root=work / "state", history_db=hist,
                 ledger_path=work / "ledger.jsonl", run_root=work / "runs", sims=PC.SIMS, sync_log=work / "sync.jsonl",
                 odds_cache_root=work / "odds_cache", odds_collections=None, odds_ledger=work / "odds.jsonl",
                 odds_record=work / "odds_record.json", model=model)
    run_dir = next((work / "runs").iterdir())
    preds = [json.loads(l)["body"] for l in open(work / "ledger.jsonl") if json.loads(l)["record_type"] == "PREDICTION"]
    keys = sorted(f"{b['game_id']}|{b['player_id']}|{b['market']}|{b['line']}|{b['side']}|{b['sportsbook']}" for b in preds)
    mp = pd.read_parquet(run_dir / "market_pmfs.parquet")
    pm = [np.asarray(x, dtype=float) for x in mp["pmf"]]
    out = {"scored_games": sorted(r["banks"]) == sorted(gold["banks"]),
           "readiness_state": r["readiness_state"] == gold["readiness_state"],
           "prediction_keys": keys == [p["key"] for p in gold["predictions"]],
           "methods": {b["distribution_method"] for b in preds} == {p["method"] for p in gold["predictions"]},
           "pmf_normalized": bool(all(abs(x.sum() - 1) < 1e-9 for x in pm)),
           "pmf_non_negative": bool(all((x >= -1e-15).all() for x in pm)),
           "model_version_recorded": all(b["model_version"] == model.identity["model_version"]
                                         and b.get("training_cutoff") == model.identity["training_cutoff"] for b in preds),
           "draws_per_game": all(v["priced_players"] > 0 for v in r["banks"].values())}
    return {"checks": out, "predictions": len(preds), "PASS": _all(out.values())}


def evaluate(cand_dir: Path, version: str, ctx: dict, log=print) -> dict:
    """ctx: training (pit / leakage evidence), fits (first, repeat), opening artifacts, previous active version."""
    g = {}
    tr = ctx["training"]
    metas = tr["feature_meta"]
    inc = tr["included"]
    cut = pd.Timestamp(ctx["training_cutoff"])
    # ---- input integrity
    fr = tr.get("frames")
    dup = 0 if fr is None else (int(fr["frame"]["player_game_example_id"].duplicated().sum())
                                + int(fr["part"]["player_game_example_id"].duplicated().sum())
                                + int(fr["team"].duplicated(["game_id", "team_id"]).sum())
                                + int(fr["oos"]["player_game_example_id"].duplicated().sum()))
    built = all(metas.get(gid, {}).get("status") == "BUILT" for gid in tr["trained_games"])
    g["INPUT_DATA_INTEGRITY"] = {"PASS": bool(tr["state_validation_ok"] and dup == 0 and built),
                                 "state_validation_ok": tr["state_validation_ok"], "duplicate_keys": dup,
                                 "all_trained_games_built": built}
    # ---- point in time
    pit_bad = [gid for gid in tr["trained_games"] if metas[gid]["pit_audit"]["violations"]
               or metas[gid]["build"]["post_as_of_rows"] or pd.Timestamp(metas[gid]["state_created_at"]) > pd.Timestamp(metas[gid]["prediction_cutoff_utc"])
               or pd.Timestamp(metas[gid]["build"]["as_of"]) != pd.Timestamp(metas[gid]["prediction_cutoff_utc"])]
    late_rows = 0 if fr is None else int((pd.to_datetime(fr["frame"]["prediction_cutoff_utc"], utc=True) >= cut).sum())
    el = tr["eligibility"]
    ei = el[el["game_id"].isin(tr["trained_games"])]
    late_out = int(((ei["outcome_available_at"] > cut) | (ei["knowable_at"] > cut) | (ei["scheduled_tip_utc"] >= cut)).sum())
    label_state_ok = tr["label_state_created_at"] is None or pd.Timestamp(tr["label_state_created_at"]) <= cut
    future = bool(late_rows or late_out or not label_state_ok or tr["label_post_as_of_rows"]
                  or (fr is not None and (fr["frame"]["season"] > WC.PRODUCTION_SEASON).any()))
    tgt_feat = bool(any(metas[gid]["build"]["target_outcome_rows"] or metas[gid]["pit_audit"]["player_state_from_target_game"]
                        for gid in tr["trained_games"]))
    oos_ok = fr is None or bool((fr["oos"]["training_through_season"] < fr["oos"]["forecast_season"]).all())
    label_games_ok = fr is None or set(fr["oos"]["game_id"]) <= set(inc)
    tgt_out = bool(not oos_ok or not label_games_ok or not tr["oos_state_is_opening_marginal"])
    g["FUTURE_DATA_LEAKAGE"] = {"PASS": not future, "FUTURE_DATA_LEAKAGE_DETECTED": future, "rows_at_or_after_cutoff": late_rows,
                                "outcomes_after_cutoff": late_out, "label_state_created_before_cutoff": label_state_ok}
    g["TARGET_GAME_FEATURE_LEAKAGE"] = {"PASS": not tgt_feat, "TARGET_GAME_FEATURE_LEAKAGE_DETECTED": tgt_feat}
    g["TARGET_GAME_OUTCOME_LEAKAGE"] = {"PASS": not tgt_out, "TARGET_GAME_OUTCOME_LEAKAGE_DETECTED": tgt_out,
                                        "oos_base_forecasts_out_of_sample": oos_ok, "labels_only_from_included_games": label_games_ok,
                                        "oos_base_state_is_opening_marginal": tr["oos_state_is_opening_marginal"]}
    g["POINT_IN_TIME_LEGALITY"] = {"PASS": not pit_bad and not future and not tgt_feat and not tgt_out,
                                   "games_with_pit_violation": pit_bad}
    # ---- identity
    unres = int(ei["unresolved_rows"].sum()) if len(ei) else 0
    bad_ids = 0 if fr is None else int((~fr["frame"]["canonical_player_id"].astype(str).str.startswith("P-")).sum())
    g["EXACT_IDENTITY"] = {"PASS": unres == 0 and bad_ids == 0, "unresolved_rows": unres, "non_canonical_player_ids": bad_ids}
    # ---- artifacts
    try:
        model = SO.load_dir(cand_dir, version)
        readable = True
    except Exception as e:                                      # noqa: BLE001
        model, readable = None, False
        g["ARTIFACT_READABILITY_ERROR"] = {"PASS": False, "error": str(e)[:500]}
    g["ARTIFACT_READABILITY"] = {"PASS": readable}
    marg, art, dep = (json.loads((Path(cand_dir) / f).read_text()) for f in SO.FILES.values())
    g["FINITE_COEFFICIENTS"] = {"PASS": WF.finite_tree(marg) and WF.finite_tree(art) and WF.finite_tree(dep)}
    dv = dispersion_valid(art)
    g["VALID_DISPERSION"] = {"PASS": _all(dv.values()), **dv}
    au = architecture_unchanged(art, marg, ctx["opening"])
    g["ARCHITECTURE_UNCHANGED"] = {"PASS": _all(au.values()), **au}
    man = json.loads((Path(cand_dir) / SO.MANIFEST).read_text())
    g["SIMS_20000_PRESERVED"] = {"PASS": man["random_seed_policy"]["draws_per_game"] == PC.SIMS == F.SIMS == 20_000}
    g["SUPPORTED_MARKETS_UNCHANGED"] = {"PASS": tuple(man["supported_stats"]) == PC.BASE_STATS == tuple(L.STATS)
                                        and {k: tuple(v) for k, v in man["supported_combos"].items()} == PC.COMBOS}
    # ---- determinism of the fit
    a, b = ctx["fit_content_sha"]
    g["DETERMINISTIC_REPEAT_FIT"] = {"PASS": a == b, "content_sha256": [a, b], "rule": "bit-identical artifacts"}
    # ---- mechanical scorer integrity (frozen jm1_final_production.integrity_checks on 3 x 4,000-draw games)
    try:
        if model is None:
            raise RuntimeError("artifact unreadable")
        integ = JP.integrity_checks(model.jm1, lambda s: None)
        ck = integ["checks"]
        g["PMF_NORMALIZED_NONNEGATIVE"] = {"PASS": ck["pmf_normalized"]}
        g["PSD_DEPENDENCE"] = {"PASS": ck["psd_game_correlation"], "per_game": integ["per_game"]}
        g["PRICING_PUSH_MATH"] = {"PASS": ck["pricing_push_exact"]}
        g["DETERMINISTIC_REPEAT_SCORING"] = {"PASS": ck["deterministic"]}
        g["SCORER_INVARIANTS"] = {"PASS": integ["ALL_PASS"], **ck}
        with tempfile.TemporaryDirectory(prefix="cbb-wf-smoke-") as tmp:
            sm = golden_smoke(model, Path(tmp))
        g["GOLDEN_PRODUCTION_SMOKE"] = sm
        g["REPRESENTATIVE_SLATE_SCORED"] = {"PASS": sm["checks"]["scored_games"] and sm["predictions"] > 0,
                                            "predictions": sm["predictions"]}
    except Exception as e:                                      # noqa: BLE001 -- any scorer failure refuses promotion
        for k in ("PMF_NORMALIZED_NONNEGATIVE", "PSD_DEPENDENCE", "PRICING_PUSH_MATH", "DETERMINISTIC_REPEAT_SCORING",
                  "SCORER_INVARIANTS", "GOLDEN_PRODUCTION_SMOKE", "REPRESENTATIVE_SLATE_SCORED"):
            g.setdefault(k, {"PASS": False, "reason": f"{type(e).__name__}: {str(e)[:300]}"})
    # ---- rollback
    prev = ctx.get("previous_active")
    try:
        ok = prev is not None and SO.verify_version(ctx["root"], prev)["VERIFIED"]
    except Exception:                                           # noqa: BLE001
        ok = False
    g["ROLLBACK_AVAILABLE"] = {"PASS": bool(ok), "previous_active": prev}
    return {"gates": g, "ALL_PASS": _all(v["PASS"] for v in g.values()),
            "POINT_IN_TIME_LEGALITY": "PASS" if g["POINT_IN_TIME_LEGALITY"]["PASS"] else "FAIL",
            "TARGET_GAME_FEATURE_LEAKAGE_DETECTED": tgt_feat, "TARGET_GAME_OUTCOME_LEAKAGE_DETECTED": tgt_out,
            "FUTURE_DATA_LEAKAGE_DETECTED": future, "failed": sorted(k for k, v in g.items() if not v["PASS"])}
