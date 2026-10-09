"""Challenger v0 evaluation: two chronological development evaluations (A: train <= 2022-23 / validate 2023-24;
B: train <= 2023-24 / validate 2024-25), challenger vs rolling baseline, written to
modeling/player_props_v0/metrics.json.

    .venv/bin/python -m cbb_platform.props_v0_eval
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from cbb_platform import props_v0 as P

METRICS_PATH = P.CONFIG_PATH.parent / "metrics.json"


def _intervals(cdf: np.ndarray, y: np.ndarray, level: float) -> float:
    lo = np.argmax(cdf >= (1 - level) / 2, axis=1)
    hi = np.argmax(cdf >= 1 - (1 - level) / 2 - 1e-12, axis=1)
    return float(np.mean((y >= lo) & (y <= hi)))


def score_distribution(pmf: np.ndarray, y: np.ndarray, levels) -> dict:
    """Log score (mean negative log likelihood), discrete CRPS and central-interval coverage."""
    yi = np.minimum(y.astype(int), pmf.shape[1] - 1)
    cdf = np.cumsum(pmf, axis=1)
    p = pmf[np.arange(len(y)), yi]
    step = (np.arange(pmf.shape[1])[None, :] >= yi[:, None]).astype(float)
    return {"log_score": float(np.mean(-np.log(np.maximum(p, 1e-300)))),
            "crps": float(np.mean(np.sum((cdf - step) ** 2, axis=1))),
            **{f"coverage_{int(l * 100)}": _intervals(cdf, yi, l) for l in levels},
            "max_pmf_row_sum_error": float(np.max(np.abs(pmf.sum(axis=1) - 1)))}


def _calibration(mu, y, edges):
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (mu >= a) & (mu < b)
        if m.any():
            out.append({"bucket": f"[{a},{b})", "n": int(m.sum()), "mean_pred": round(float(mu[m].mean()), 4),
                        "mean_obs": round(float(y[m].mean()), 4)})
    return out


def _dist(kind, mu, cfg, model_state, t):
    if t == "min":
        sig = P._band_sigma(mu, cfg["minutes_sigma_buckets"], model_state["minutes_sigma"])
        return P.minutes_pmf(mu, sig, cfg["minutes_support_max"])
    return P.pmf_matrix(mu, model_state["dispersion"][t]["phi"], cfg["pmf_support_max"])


def evaluate(frame, finals, cfg, ev) -> tuple[dict, str]:
    st = P.fit(frame, finals, cfg, ev["train_through"])
    va = frame[frame["season"] == ev["validate"]]
    assert va["prediction_cutoff_utc"].min() > pd.Timestamp(st["train_max_cutoff"]), "validation must start after training"
    pr = P.predict(va, cfg, st)
    levels = cfg["interval_levels"]
    res = {"train_through": ev["train_through"], "validate": ev["validate"], "n_train_rows": st["n_train_rows"],
           "state_hash": st["state_hash"], "targets": {}}
    digest = hashlib.sha256()
    for t in ["min"] + list(cfg["count_targets"].values()):
        y = va["y_min" if t == "min" else f"y_{t}"].to_numpy(dtype=float)
        ok = ~np.isnan(y)
        r = {"n": int(ok.sum()), "n_excluded_label_unavailable": int((~ok).sum())}
        for kind, means, mstate in (("baseline", pr["baseline"], st["baseline"]), ("challenger", pr["challenger"], st)):
            mu = means[t][ok]
            digest.update(np.ascontiguousarray(mu).tobytes())
            d = {"mae": float(np.mean(np.abs(mu - y[ok]))), "rmse": float(np.sqrt(np.mean((mu - y[ok]) ** 2))),
                 "mean_pred": float(mu.mean()), "mean_obs": float(y[ok].mean())}
            pmf = np.concatenate([_dist(kind, mu[i:i + 50000], cfg, mstate, t) for i in range(0, len(mu), 50000)])
            d.update(score_distribution(pmf, y[ok], levels))
            d["family"] = "DISCRETIZED_NORMAL" if t == "min" else mstate["dispersion"][t]["family"]
            d["calibration"] = _calibration(mu, y[ok], cfg["minutes_sigma_buckets"] if t == "min" else cfg["count_mean_buckets"])
            r[kind] = d
        res["targets"][{"min": "MIN", **{v: k for k, v in cfg["count_targets"].items()}}[t]] = r
    res["prediction_hash"] = digest.hexdigest()
    res["model_state"] = {k: st[k] for k in ("coef", "prior_rate", "prior_k_minutes", "dispersion", "minutes_sigma")}
    res["baseline_state"] = st["baseline"]
    return res, res["prediction_hash"]


def run(write: bool = True) -> dict:
    cfg = P.load_config()
    frame, finals, excl = P.load_frames(cfg)
    out = {"version": cfg["version"], "upstream_dataset": cfg["upstream_dataset"], "lockbox_touched": False,
           "seasons_read": sorted(int(s) for s in frame["season"].unique()), "recency": cfg["recency"],
           "scoring_population": cfg["scoring_population"],
           "exclusions_by_season": {str(s): excl[s] for s in sorted(excl)}, "evaluations": {}}
    for ev in cfg["evaluations"]:
        res, h1 = evaluate(frame, finals, cfg, ev)
        _, h2 = evaluate(frame, finals, cfg, ev)                      # deterministic rerun
        res["deterministic_rerun"] = h1 == h2
        out["evaluations"][ev["name"]] = res
    if write:
        METRICS_PATH.write_text(json.dumps(out, indent=1, default=str) + "\n")
    return out


if __name__ == "__main__":
    r = run()
    for name, ev in r["evaluations"].items():
        print(f"== {name}: validate {ev['validate']} (rerun equal: {ev['deterministic_rerun']})")
        for t, d in ev["targets"].items():
            b, c = d["baseline"], d["challenger"]
            print(f"{t:5s} n={d['n']:6d} mae {b['mae']:.3f}->{c['mae']:.3f}  log {b['log_score']:.4f}->{c['log_score']:.4f}"
                  f"  crps {b['crps']:.4f}->{c['crps']:.4f}  cov50/80/95 {c['coverage_50']:.3f}/{c['coverage_80']:.3f}/{c['coverage_95']:.3f} {c['family']}")
