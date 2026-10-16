"""Fit-free lockbox scoring runner (docs/LOCKBOX_OPENING_PROTOCOL.md §2.4; plan §6). Loads a FROZEN artifact, scores one
explicitly supplied season from an explicitly supplied Stage 3 feature / label store, and evaluates the frozen
scorecard (docs/lockbox/lockbox_scorecard_v2.json) G1-G6. Nothing is fit, recalibrated, tuned or selected: the
module imports no fit function, and the development tests run it with every fit entry point disabled (FIT_CALLS = 0).

    inputs   --artifact   final_prelockbox_2024_25_artifact.json (or any artifact props_v1_refit.load_final accepts)
             --features-db / --labels-db   Stage 3 stores built by the frozen pipeline (stage3.build)
             --season     canonical end-year; refused by the CURRENT guard when it is 2025-26 (closed)
    scoring  props_v0.predict (candidate + rolling baseline means), props_v0.pmf_matrix / minutes_pmf (PMFs),
             props_v0_eval.score_distribution (log score, CRPS, coverage), props_v0_participation.predict / heuristic /
             metrics, props_v1_refit._coherence + props_v1_pricing.price (pricing integrity on the production
             pregame path props_v0_participation.pregame_outputs)
    output   deterministic JSON (sorted keys; no wall-clock content)

    .venv/bin/python -m cbb_platform.lockbox_runner --artifact A --features-db F --labels-db L --season S --out O
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform import config as C, lockbox, props_v0 as P, props_v0_eval as E, props_v0_participation as Q
from cbb_platform import props_v1_pricing as V, props_v1_refit as R
from cbb_platform.stage3 import spec

RUNNER_VERSION = "cbb-lockbox-runner-1.0.0"
SCORECARD_PATH = C.PROJECT_ROOT / "docs" / "lockbox" / "lockbox_scorecard_v2.json"
TARGETS = ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV")
HALF_LINES = {"PTS": (5.5, 9.5, 14.5), "REB": (2.5, 4.5, 6.5), **{t: (0.5, 1.5, 2.5) for t in ("AST", "FG3M", "STL", "BLK", "TOV")}}
GAP_FLAG = 0.02
CHUNK = 40000


@contextlib.contextmanager
def stores(features_db: Path, labels_db: Path):
    """Point the frozen loaders at the supplied Stage 3 stores (read-only), restoring the defaults afterwards."""
    old = spec.FEATURES_DB, spec.LABELS_DB
    spec.FEATURES_DB, spec.LABELS_DB = Path(features_db), Path(labels_db)
    try:
        yield
    finally:
        spec.FEATURES_DB, spec.LABELS_DB = old


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _history(season: int, cfg: dict) -> list[int]:
    return [s for s in range(season - cfg["recency"]["prior_seasons_used"], season + 1) if s in spec.MODEL_SEASONS]


# ============================================================================ per-row losses (for clustered intervals)
def _row_losses(pmf: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-row log score and discrete CRPS: the summands of props_v0_eval.score_distribution."""
    yi = np.minimum(y.astype(int), pmf.shape[1] - 1)
    cdf = np.cumsum(pmf, axis=1)
    step = (np.arange(pmf.shape[1])[None, :] >= yi[:, None]).astype(float)
    return -np.log(np.maximum(pmf[np.arange(len(y)), yi], 1e-300)), np.sum((cdf - step) ** 2, axis=1)


def cr1_interval(diff: np.ndarray, cluster: np.ndarray) -> dict:
    """Game-clustered (CR1) 95 % interval for the mean of a per-row difference (descriptive; gates use point estimates)."""
    n, d = len(diff), float(np.mean(diff))
    codes, inv = np.unique(cluster, return_inverse=True)
    g = len(codes)
    s = np.bincount(inv, weights=diff - d)
    se = float(np.sqrt(g / (g - 1) * np.sum(s ** 2)) / n) if g > 1 else float("nan")
    return {"mean_difference": d, "se": se, "ci95": [d - 1.96 * se, d + 1.96 * se], "clusters": int(g), "n": int(n)}


# ============================================================================ scoring
def _dist(mu, t, st, cfg):
    return np.concatenate([R._dist(mu[i:i + CHUNK], t, st, cfg) for i in range(0, len(mu), CHUNK)]) if len(mu) else \
        np.zeros((0, (cfg["minutes_support_max"] if t == "min" else cfg["pmf_support_max"]) + 1))


def _thresholds(pmf: np.ndarray, y: np.ndarray, lines) -> dict:
    out = {}
    for L in lines:
        p, o = V.price(pmf, L)["over_probability"], (y > L).astype(float)
        pc, gap = np.clip(p, 1e-15, 1 - 1e-15), float(p.mean() - o.mean())
        out[str(L)] = {"n": int(len(o)), "pred": float(p.mean()), "observed": float(o.mean()), "gap": gap,
                       "flagged": abs(gap) > GAP_FLAG, "brier": float(np.mean((p - o) ** 2)),
                       "log_loss": float(-np.mean(o * np.log(pc) + (1 - o) * np.log(1 - pc)))}
    return out


def score_counts(frame: pd.DataFrame, cfg: dict, st: dict) -> dict:
    """Minutes + the 7 counts: candidate vs rolling baseline on the scored population (PLAYED, box row, label)."""
    pr = P.predict(frame, cfg, st)
    games = frame["canonical_game_id"].to_numpy() if "canonical_game_id" in frame else frame["player_game_example_id"].to_numpy()
    out = {"n_rows": int(len(frame)), "targets": {}}
    names = {"min": "MIN", **{v: k for k, v in cfg["count_targets"].items()}}
    for t in ["min"] + list(cfg["count_targets"].values()):
        y = frame["y_min" if t == "min" else f"y_{t}"].to_numpy(dtype=float)
        ok = ~np.isnan(y)
        r = {"n": int(ok.sum()), "n_excluded_label_unavailable": int((~ok).sum())}
        losses = {}
        for kind, means, mstate in (("candidate", pr["challenger"], st), ("rolling_baseline", pr["baseline"], st["baseline"])):
            mu = means[t][ok]
            pmf = _dist(mu, t, mstate, cfg)
            d = {"mae": float(np.mean(np.abs(mu - y[ok]))) if ok.any() else None,
                 "rmse": float(np.sqrt(np.mean((mu - y[ok]) ** 2))) if ok.any() else None,
                 "mean_pred": float(mu.mean()) if ok.any() else None, "observed_mean": float(y[ok].mean()) if ok.any() else None}
            if ok.any():
                d.update(E.score_distribution(pmf, y[ok], cfg["interval_levels"]))
                d["mean_bias"] = d["mean_pred"] - d["observed_mean"]
                d["min_pmf_entry"] = float(pmf.min())
                d["family"] = "DISCRETIZED_NORMAL" if t == "min" else mstate["dispersion"][t]["family"]
                d["phi"] = None if t == "min" else mstate["dispersion"][t]["phi"]
                if t != "min":
                    d["prop_thresholds"] = _thresholds(pmf, y[ok], HALF_LINES[names[t]])
                ls, cr = _row_losses(pmf, y[ok]) if t != "min" else (None, None)
                losses[kind] = {"log": ls, "crps": cr, "abs": np.abs(mu - y[ok]), "sq": (mu - y[ok]) ** 2}
            r[kind] = d
        if ok.any():
            g = games[ok]
            keys = ("abs", "sq") if t == "min" else ("log", "crps")
            r["clustered_difference_candidate_minus_baseline"] = {
                k: cr1_interval(losses["candidate"][k] - losses["rolling_baseline"][k], g) for k in keys}
            if t != "min":
                # consistency: the per-row summands reproduce the frozen score_distribution means exactly
                for kind in ("candidate", "rolling_baseline"):
                    assert abs(float(np.mean(losses[kind]["log"])) - r[kind]["log_score"]) <= 1e-12
                    assert abs(float(np.mean(losses[kind]["crps"])) - r[kind]["crps"]) <= 1e-12
        out["targets"][names[t]] = r
    return out


def score_participation(rows: pd.DataFrame, pst: dict) -> dict:
    lab = rows[rows["y"].notna()]
    y = lab["y"].to_numpy()
    p, h = Q.predict(lab, pst), Q.heuristic(lab, pst)
    out = {"n": int(len(lab)), "label_counts": {k: int(v) for k, v in rows["participation_status"].value_counts().sort_index().items()},
           "excluded_unknown_or_not_on_roster": int(rows["y"].isna().sum())}
    if len(lab):
        out["candidate"], out["heuristic"] = Q.metrics(y, p), Q.metrics(y, h)
        pc, hc = np.clip(p, 1e-15, 1 - 1e-15), np.clip(h, 1e-15, 1 - 1e-15)
        g = lab["canonical_game_id"].to_numpy()
        out["clustered_difference_candidate_minus_heuristic"] = {
            "brier": cr1_interval((p - y) ** 2 - (h - y) ** 2, g),
            "log_loss": cr1_interval(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)) + (y * np.log(hc) + (1 - y) * np.log(1 - hc)), g)}
    return out


def pricing_integrity(cand: pd.DataFrame, part_rows: pd.DataFrame, art: dict) -> dict:
    """G6 on the production pregame path (every pregame candidate of the season, all 7 count PMFs + MIN rows)."""
    acc, mins = {}, {"max_row_sum_error": 0.0, "min_entry": 1.0}
    for i in range(0, len(cand), CHUNK):
        res = Q.pregame_outputs(cand.iloc[i:i + CHUNK], part_rows, art["config"], art["v0_state"], art["participation_state"])
        for name, pmf in res["conditional_stat_distribution"].items():
            if name == "MIN":
                mins["max_row_sum_error"] = max(mins["max_row_sum_error"], float(np.max(np.abs(pmf.sum(axis=1) - 1))))
                mins["min_entry"] = min(mins["min_entry"], float(pmf.min()))
            else:
                acc[name] = R._merge(acc.get(name), R._coherence(pmf))
    return {"candidates": int(len(cand)), "counts": acc, "MIN": mins}


# ============================================================================ gates (scorecard pass_criteria, verbatim semantics)
def _num(x) -> bool:
    return x is not None and np.isfinite(x)


def gates(counts: dict, part: dict, integ: dict) -> dict:
    """G1-G6 of docs/lockbox/lockbox_scorecard_v2.json. A gated value that is missing or not finite FAILS its gate."""
    tg = counts["targets"]
    g = {}
    n = {t: tg[t]["n"] for t in TARGETS}
    agg = {}
    for kind in ("candidate", "rolling_baseline"):
        vals = {m: [tg[t][kind].get(m) for t in TARGETS] for m in ("log_score", "crps")}
        agg[kind] = {m: (float(sum(n[t] * v for t, v in zip(TARGETS, vs)) / sum(n.values()))
                         if sum(n.values()) and all(_num(v) for v in vs) else None) for m, vs in vals.items()}
    c, b = agg["candidate"], agg["rolling_baseline"]
    g["G1_aggregate"] = {"pass": all(_num(v) for v in (*c.values(), *b.values())) and c["log_score"] < b["log_score"]
                         and c["crps"] < b["crps"], "aggregate": agg}
    wins = {t: _num(tg[t]["candidate"].get("log_score")) and _num(tg[t]["rolling_baseline"].get("log_score"))
            and tg[t]["candidate"]["log_score"] < tg[t]["rolling_baseline"]["log_score"]
            and tg[t]["candidate"]["crps"] < tg[t]["rolling_baseline"]["crps"] for t in TARGETS}
    g["G2_per_target"] = {"pass": sum(wins.values()) >= 6, "wins": wins, "n_wins": int(sum(wins.values()))}
    mc, mb = tg["MIN"]["candidate"], tg["MIN"]["rolling_baseline"]
    g["G3_minutes"] = {"pass": all(_num(x) for x in (mc["mae"], mb["mae"], mc["rmse"], mb["rmse"]))
                       and mc["mae"] < mb["mae"] and mc["rmse"] < mb["rmse"]}
    pc, ph = part.get("candidate", {}), part.get("heuristic", {})
    g["G4_participation"] = {"pass": all(_num(x) for x in (pc.get("brier"), ph.get("brier"), pc.get("log_loss"), ph.get("log_loss")))
                             and pc["brier"] < ph["brier"] and pc["log_loss"] < ph["log_loss"]}
    g["G5_participation_calibration"] = {"pass": _num(pc.get("calibration_slope")) and _num(pc.get("calibration_in_the_large"))
                                         and 0.80 <= pc["calibration_slope"] <= 1.25 and abs(pc["calibration_in_the_large"]) <= 0.25}
    cnt = integ["counts"]
    g6 = {"row_sums_within_1e-9": bool(cnt) and all(r["max_row_sum_error"] <= 1e-9 for r in cnt.values())
          and integ["MIN"]["max_row_sum_error"] <= 1e-9,
          "no_negative_entry": bool(cnt) and all(r["min_entry"] >= 0 for r in cnt.values()) and integ["MIN"]["min_entry"] >= 0,
          "monotonicity_violations_zero": bool(cnt) and all(r["p_over_monotonicity_violations"] == 0 for r in cnt.values()),
          "push_semantics_exact": bool(cnt) and all(r["integer_push_max_error"] <= 1e-12 and r["half_line_max_push"] == 0.0
                                                   for r in cnt.values()),
          "fair_prices_finite": bool(cnt) and all(r["invalid_fair_prices"] == 0 for r in cnt.values())}
    g["G6_pricing_integrity"] = {"pass": all(g6.values()), "checks": g6}
    for k in ("G1_aggregate", "G2_per_target", "G3_minutes", "G4_participation", "G5_participation_calibration",
              "G6_pricing_integrity"):
        g[k]["pass"] = bool(g[k]["pass"])
    g["verdict"] = "GO" if all(g[k]["pass"] for k in g) else "NO-GO"
    return g


# ============================================================================ entry point
def run(artifact: Path, features_db: Path, labels_db: Path, season: int, *, scorecard: Path = SCORECARD_PATH,
        expected_artifact_sha256: str | None = None) -> dict:
    lockbox.check_season(season)                       # the CURRENT guard decides; closed => 2025-26 refused here
    art_sha = _sha(artifact)
    if expected_artifact_sha256 and art_sha != expected_artifact_sha256:
        raise ValueError("artifact sha256 differs from the frozen candidate")
    art = R.load_final(Path(artifact))                 # fingerprint, spec and override checks; never refits
    cfg = art["config"]
    with stores(features_db, labels_db):
        frame, _, excl = P.load_frames(cfg, seasons=_history(season, cfg))
        frame = frame[frame["season"] == season].reset_index(drop=True)
        cand = V.load_candidates(season)
        part_rows, _ = Q.load(seasons=(season,))
        games = cand[["player_game_example_id", "canonical_game_id"]]
        frame = frame.merge(games, on="player_game_example_id", how="left", validate="one_to_one")
        counts = score_counts(frame, cfg, art["v0_state"])
        part = score_participation(part_rows, art["participation_state"])
        integ = pricing_integrity(cand, part_rows, art)
    out = {"runner_version": RUNNER_VERSION, "season": int(season), "season_label": lockbox.season_label(int(season)),
           "artifact": {"path": str(Path(artifact).name), "sha256": art_sha, "artifact_fingerprint": art["artifact_fingerprint"],
                        "model_version": art["model_version"], "training_through_season": art["training_through_season"],
                        "dataset_version": art["dataset_version"]},
           "scorecard": {"path": str(Path(scorecard).relative_to(C.PROJECT_ROOT)) if Path(scorecard).is_relative_to(C.PROJECT_ROOT)
                         else str(scorecard), "sha256": _sha(scorecard)},
           "exclusions": {str(k): v for k, v in excl.items() if int(k) == season},
           "row_identity_sha256": hashlib.sha256("\n".join(frame["player_game_example_id"].astype(str)).encode()).hexdigest(),
           "counts_and_minutes": counts, "participation": part, "pricing_integrity": integ,
           "fit_free": True, "refit_recalibrated_or_selected": False}
    out["gates"] = gates(counts, part, integ)
    out["result_sha256"] = hashlib.sha256(json.dumps(out, sort_keys=True, default=str).encode()).hexdigest()
    return out


def dumps(result: dict) -> str:
    return json.dumps(result, indent=1, sort_keys=True, default=str) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--features-db", required=True)
    ap.add_argument("--labels-db", required=True)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expected-artifact-sha256")
    a = ap.parse_args()
    r = run(Path(a.artifact), Path(a.features_db), Path(a.labels_db), a.season,
            expected_artifact_sha256=a.expected_artifact_sha256)
    Path(a.out).write_text(dumps(r))
    print(json.dumps({"season": r["season"], "verdict": r["gates"]["verdict"], "result_sha256": r["result_sha256"]}))
