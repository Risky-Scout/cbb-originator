"""PRE-LAUNCH DIRECTIONAL-BIAS AUDIT of JM1_FINAL on EXISTING true-OOS evidence. Diagnostic only.

Never refits, recalibrates, shifts, re-simulates or calls any API. It reads the saved per-(player, game, stat) cells
of the two JM1_FINAL true-OOS evaluations (read-only parquet, gitignored scratch evidence):

    2024-25  data/qa/large/v2_jm2/develop_2025_rows.parquet   arm "JM1"        (the JM1 development arm, reproduced
             bit-exactly by jm1_final.py as JM1_FINAL; parameters through 2023-24; used for development)
    2025-26  data/qa/large/v2_jm1/confirm_2026_rows.parquet   arm "JM1_FINAL"  (one-shot confirmation; parameters
             through 2024-25)
No JM1_FINAL OOS evidence exists for 2023-24 or earlier (the architecture was built on 2024-25); those seasons are
reported as NOT_AVAILABLE, never substituted with another architecture or with in-sample fits.

SIGN CONVENTION (the repository's own: phase3 MIN_BIAS = min_mean - y; JM1_FINAL_REPORT "PTS bias +0.212 = mean
over-forecast"):
    SIGNED_ERROR = forecast_mean - actual
    > 0  OVER-PREDICTION  (forecast too high; realized outcomes fall BELOW the forecast -> pressure toward OVER bets)
    < 0  UNDER-PREDICTION (forecast too low;  realized outcomes fall ABOVE the forecast -> pressure toward UNDER bets)
PIT (the repository's discrete PIT interval [pit_lo, pit_hi] = [F(y-1), F(y)]; phase3.pit_calibration conventions):
    mid-PIT = (pit_lo + pit_hi) / 2       E[mid-PIT] = 0.5 exactly under calibration
    P(PIT > u) = mass of the uniform-within-interval PIT above u (the coverage convention of phase3.pit_calibration);
                 expected 1 - u under calibration
    mean mid-PIT > 0.5  -> outcomes land ABOVE the forecast distribution (UNDER-PREDICTION)
    mean mid-PIT < 0.5  -> outcomes land BELOW the forecast distribution (OVER-PREDICTION)
Tails: UPPER_TAIL_MISS(q) = P(PIT > q) (outcome above the forecast q-quantile), expected 1 - q;
       LOWER_TAIL_MISS(q) = P(PIT < 1 - q), expected 1 - q.  TAIL_ASYMMETRY(q) = UPPER - LOWER:
       > 0 the model puts too much probability BELOW realized outcomes (the Under-bias warning sign).
Uncertainty: the repository's game-clustered bootstrap (phase3: multinomial game weights, 2000 draws, seed 20366),
95% percentile intervals.

Combos (PRA PR PA RA STOCKS): the combo forecast mean is EXACTLY the sum of the stat means (linearity; JM1_FINAL combo
draws are sums of the same-draw stat counts). Per-row combo PMFs / quantiles were not saved (only per-game q25/q50/q75
Brier SUMS, which carry no direction), so combo quantile calibration is NOT_AVAILABLE without re-simulation, which this
audit does not do.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from cbb_platform import config as C

STATS = ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV")
COMBOS = {"PRA": ("PTS", "REB", "AST"), "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"),
          "STOCKS": ("STL", "BLK")}
HALF_LINES = {"PTS": (5.5, 9.5, 14.5), "REB": (2.5, 4.5, 6.5), **{t: (0.5, 1.5, 2.5) for t in ("AST", "FG3M", "STL", "BLK", "TOV")}}
SOURCES = {
    "2024-25": {"path": "data/qa/large/v2_jm2/develop_2025_rows.parquet", "arm": "JM1", "forecast_season": 2025,
                "games": "data/qa/large/v2_jm2/develop_2025_games.parquet",
                "role": "development evaluation (true OOS: parameters through 2023-24; used for JM1 development)"},
    "2025-26": {"path": "data/qa/large/v2_jm1/confirm_2026_rows.parquet", "arm": "JM1_FINAL", "forecast_season": 2026,
                "games": "data/qa/large/v2_jm1/confirm_2026_games.parquet",
                "role": "one-shot confirmation (true OOS: parameters through 2024-25)"},
}
NOT_AVAILABLE_SEASONS = {"2023-24": "no JM1_FINAL true-OOS evaluation exists (architecture developed on 2024-25)"}
ROLES = ("TOP5_MINUTES", "ROTATION_6_8", "BENCH_9PLUS", "NO_ROLE_HISTORY")
COLD = ("WARM", "COLD_EARLY_SEASON", "COLD_TRANSFER_IN", "COLD_NO_HISTORY")
EARLY_DAYS = 30
EARLY_TEAM_GAMES = 2                     # = joint_minutes_v2.EARLY_TEAM_GAMES (the established EARLY_SEASON stratum)
BOOT, SEED = 2000, 20366                 # = phase3.EVAL_BOOT / EVAL_SEED
OUT_JSON = C.PROJECT_ROOT / "modeling" / "player_props_v2" / "jm1_final_directional_bias_audit.json"
OUT_MD = C.PROJECT_ROOT / "modeling" / "player_props_v2" / "JM1_FINAL_DIRECTIONAL_BIAS_AUDIT.md"

# Classification thresholds: fixed BEFORE any result was computed. "sig" = the 95% game-clustered interval excludes 0.
WATCH = {"rel_mean": 0.03, "pit": 0.010, "tail": 0.020}   # |SME| / mean actual; |mean mid-PIT - 0.5|; |tail asymmetry|
RED = {"rel_mean": 0.06, "pit": 0.025, "tail": 0.050}
MIN_GROUP_N = 2000                                        # subgroups smaller than this are reported, never classified

# PREGAME-EXPOSURE strata (architect ruling 2026-10-21, item 2), fixed BEFORE their bias results were examined.
# min_mu = the Phase 1B pregame CONDITIONAL minutes mean that drives JM1_FINAL's minutes draws (jm1_final.py: M_ib =
# clip(min_mu + min_sigma eps) x D / E[D]); p_play = the pregame Phase 1B P(play). Neither uses target-game outcomes.
# Bands: the repository's own minutes_sigma_buckets [0, 10, 20, 30, inf) of the frozen marginal artifact; HIGH exposure
# = the top band (min_mu >= 30); secondary cut = prod.inputs.ROTATION_SHOCK_MINUTES (min_mu >= 20). P(play) bands are
# simple fixed cuts (< 0.5, 0.5-0.9, >= 0.9).
MIN_BANDS = (0.0, 10.0, 20.0, 30.0)
HIGH_EXPOSURE_MIN, ROTATION_MIN = 30.0, 20.0
PPLAY_BANDS = (0.5, 0.9)
EXPOSURE_FOCUS = ("PTS", "AST", "FG3M", "TOV", "PRA", "PR", "PA")
OUT_EXPOSURE_JSON = C.PROJECT_ROOT / "modeling" / "player_props_v2" / "jm1_final_pregame_exposure_audit.json"


# ============================================================================ loading
def load(season: str, root: Path | None = None) -> pd.DataFrame:
    src = SOURCES[season]
    path = Path(root or C.PROJECT_ROOT) / src["path"]
    a = src["arm"]
    want = ["forecast_season", "game_id", "team_id", "player_id", "role_diagnostic", "cold_start_class",
            "prediction_cutoff_utc", "team_games", "min_mu", "p_play"] + [f"y_{s}" for s in STATS]
    want += [f"{a}|{s}|{k}" for s in STATS for k in ("mean", "pit_lo", "pit_hi", "pover0", "pover1", "pover2")]
    df = pq.read_table(path, columns=want).to_pandas()
    df = df.rename(columns={c: c.split("|", 1)[1] for c in df.columns if c.startswith(a + "|")})
    if set(df["forecast_season"].unique()) != {src["forecast_season"]}:
        raise RuntimeError(f"{path}: unexpected forecast season")
    t = pd.to_datetime(df["prediction_cutoff_utc"], utc=True).dt.tz_convert("America/New_York")
    df["season_day"] = (t.dt.normalize() - t.dt.normalize().min()).dt.days
    return df


# ============================================================================ game-clustered bootstrap
class Boot:
    def __init__(self, game_ids: np.ndarray, n_boot: int = BOOT, seed: int = SEED):
        self.codes, self.games = pd.factorize(game_ids)
        G = len(self.games)
        self.W = np.random.default_rng(seed).multinomial(G, np.full(G, 1.0 / G), size=n_boot).astype(float)
        self.G = G

    def mean(self, v: np.ndarray, mask: np.ndarray | None = None) -> dict:
        """Row mean with a game-clustered 95% interval (ratio of game sums, as phase3._ratio_ci)."""
        m = np.ones(len(v), bool) if mask is None else mask
        num = np.bincount(self.codes[m], weights=v[m], minlength=self.G)
        den = np.bincount(self.codes[m], minlength=self.G).astype(float)
        if den.sum() == 0:
            return {"value": None, "lo": None, "hi": None, "n": 0}
        bs = (self.W @ num) / np.maximum(self.W @ den, 1e-12)
        lo, hi = np.quantile(bs, (0.025, 0.975))
        return {"value": float(num.sum() / den.sum()), "lo": float(lo), "hi": float(hi), "n": int(den.sum())}


def _sig(ci: dict, ref: float = 0.0) -> int:
    """+1 / -1 when the interval lies entirely above / below ref, else 0."""
    if ci["value"] is None:
        return 0
    return 1 if ci["lo"] > ref else -1 if ci["hi"] < ref else 0


def _p_above(lo: np.ndarray, hi: np.ndarray, u: float) -> np.ndarray:
    """Mass of the uniform-within-PIT-interval above u (phase3.pit_calibration convention)."""
    w = hi - lo
    return np.where(w > 1e-12, np.clip((hi - u) / np.maximum(w, 1e-12), 0, 1), (lo > u).astype(float))


# ============================================================================ metrics
def stat_metrics(df: pd.DataFrame, s: str, bt: Boot, mask=None) -> dict:
    m = np.ones(len(df), bool) if mask is None else np.asarray(mask, bool)
    f = df[f"{s}|mean"].to_numpy(float)
    y = df[f"y_{s}"].to_numpy(float)
    e = f - y
    lo, hi = df[f"{s}|pit_lo"].to_numpy(float), df[f"{s}|pit_hi"].to_numpy(float)
    mid = 0.5 * (lo + hi)
    out = {"N": int(m.sum()), "mean_forecast": float(f[m].mean()), "mean_actual": float(y[m].mean()),
           "signed_mean_error": bt.mean(e, m), "median_signed_error": float(np.median(e[m])),
           "MAE": float(np.abs(e[m]).mean()), "RMSE": float(np.sqrt((e[m] ** 2).mean())),
           "P_actual_gt_forecast_mean": float((y[m] > f[m]).mean()), "P_actual_lt_forecast_mean": float((y[m] < f[m]).mean()),
           "P_actual_eq_forecast_mean": float((y[m] == f[m]).mean()),
           "mean_mid_pit": bt.mean(mid, m), "median_mid_pit": float(np.median(mid[m])),
           "P_pit_gt_half": bt.mean(_p_above(lo, hi, 0.5), m), "P_pit_lt_half": float(1 - _p_above(lo, hi, 0.5)[m].mean()),
           "mid_pit_deciles": (np.histogram(mid[m], bins=np.linspace(0, 1, 11))[0] / max(m.sum(), 1)).tolist()}
    out["rel_signed_mean_error"] = out["signed_mean_error"]["value"] / max(out["mean_actual"], 1e-9)
    tails = {}
    for q in (0.75, 0.90):
        up = _p_above(lo, hi, q)                       # outcome above the forecast q-quantile
        dn = 1 - _p_above(lo, hi, 1 - q)               # outcome below the forecast (1-q)-quantile
        tails[f"q{int(q * 100)}"] = {"upper_tail_miss": float(up[m].mean()), "lower_tail_miss": float(dn[m].mean()),
                                    "expected_each": round(1 - q, 4), "asymmetry": bt.mean(up - dn, m)}
    tails["q50"] = {"P_above_median": out["P_pit_gt_half"]["value"], "P_below_median": out["P_pit_lt_half"], "expected_each": 0.5}
    out["tails"] = tails
    lines = {}
    for li, L in enumerate(HALF_LINES[s]):
        p = df[f"{s}|pover{li}"].to_numpy(float)
        o = (y > L).astype(float)
        lines[str(L)] = {"mean_p_over": float(p[m].mean()), "observed_over_rate": float(o[m].mean()),
                         "p_over_minus_observed": bt.mean(p - o, m)}
    out["fixed_line_over_calibration"] = lines          # > 0: model P(over) too high (over-prediction direction)
    return out


def combo_metrics(df: pd.DataFrame, c: str, bt: Boot, mask=None) -> dict:
    m = np.ones(len(df), bool) if mask is None else np.asarray(mask, bool)
    parts = COMBOS[c]
    f = sum(df[f"{s}|mean"].to_numpy(float) for s in parts)
    y = sum(df[f"y_{s}"].to_numpy(float) for s in parts)
    e = f - y
    out = {"N": int(m.sum()), "mean_projection": float(f[m].mean()), "mean_actual": float(y[m].mean()),
           "signed_mean_error": bt.mean(e, m), "median_signed_error": float(np.median(e[m])),
           "MAE": float(np.abs(e[m]).mean()), "RMSE": float(np.sqrt((e[m] ** 2).mean())),
           "P_actual_gt_projection": float((y[m] > f[m]).mean()), "P_actual_lt_projection": float((y[m] < f[m]).mean()),
           "quantile_calibration": "NOT_AVAILABLE (per-row combo quantiles not saved; re-simulation not performed)"}
    out["rel_signed_mean_error"] = out["signed_mean_error"]["value"] / max(out["mean_actual"], 1e-9)
    return out


def combo_brier(season: str, root: Path | None = None) -> dict:
    """Stored per-game combo q-line Brier sums (non-directional): JM1_FINAL vs S0, per line."""
    src = SOURCES[season]
    g = pd.read_parquet(Path(root or C.PROJECT_ROOT) / src["games"])
    out = {}
    for c in COMBOS:
        out[c] = {f"q{q}": {"JM1_FINAL": float(g[f"{src['arm']}|{c}_BRIER_q{q}"].sum() / g["n_players"].sum()),
                            "S0": float(g[f"S0|{c}_BRIER_q{q}"].sum() / g["n_players"].sum())} for q in (25, 50, 75)}
    return out


# ============================================================================ classification
def _flags(r: dict, kind: str) -> dict:
    """Per-season direction (+1 OVER-PREDICTION, -1 UNDER-PREDICTION, 0 none) and materiality of each family."""
    sme = r["signed_mean_error"]
    fam = {"mean": (_sig(sme), abs(r["rel_signed_mean_error"]), "rel_mean")}
    if kind == "stat":
        pit = r["mean_mid_pit"]
        fam["pit"] = (-_sig(pit, 0.5), abs(pit["value"] - 0.5), "pit")      # PIT > .5 = under-prediction (-1)
        asym = r["tails"]["q75"]["asymmetry"]
        fam["tail"] = (-_sig(asym), abs(asym["value"]), "tail")             # upper > lower = under-prediction
    return fam


def classify(by_season: dict, kind: str) -> dict:
    """GREEN / WATCH / RED from the per-season results of one stat or combo (aggregate rows).
    RED   : some metric family significant in EVERY available season, in the SAME direction, at RED materiality.
    WATCH : some family significant at WATCH materiality in any season (or RED materiality in only one season).
    GREEN : otherwise."""
    seasons = list(by_season)
    fams = {s: _flags(by_season[s], kind) for s in seasons}
    level, why = "GREEN", []
    for f in fams[seasons[0]]:
        d = [fams[s][f][0] for s in seasons]
        mag = [fams[s][f][1] for s in seasons]
        thr = fams[seasons[0]][f][2]
        if all(x != 0 for x in d) and len(set(d)) == 1 and all(x >= RED[thr] for x in mag):
            level = "RED"
            why.append(f"{f}: same-sign in every season ({'OVER' if d[0] > 0 else 'UNDER'}-PREDICTION), "
                       f"magnitudes {[round(x, 4) for x in mag]} >= {RED[thr]}")
        elif any(x != 0 and v >= WATCH[thr] for x, v in zip(d, mag)):
            if level != "RED":
                level = "WATCH"
            dirs = {s: ("OVER" if fams[s][f][0] > 0 else "UNDER" if fams[s][f][0] < 0 else "none") for s in seasons}
            why.append(f"{f}: significant at >= {WATCH[thr]} in {[s for s in seasons if fams[s][f][0] != 0 and fams[s][f][1] >= WATCH[thr]]}; "
                       f"direction {dirs}; magnitudes {[round(x, 4) for x in mag]}")
    return {"level": level, "evidence": why}


def subgroup_level(by_season: dict, kind: str) -> str:
    if any(r["N"] < MIN_GROUP_N for r in by_season.values()):
        return "NOT_CLASSIFIED_SMALL_N"
    return classify(by_season, kind)["level"]


# ============================================================================ audit
def strata(df: pd.DataFrame) -> dict:
    s = {f"ROLE:{r}": (df["role_diagnostic"] == r).to_numpy() for r in ROLES}
    cold = df["cold_start_class"].fillna("UNKNOWN")
    s.update({f"STATE:{c}": (cold == c).to_numpy() for c in COLD})
    s[f"EARLY:first_{EARLY_DAYS}_days"] = (df["season_day"] < EARLY_DAYS).to_numpy()
    s[f"EARLY:after_{EARLY_DAYS}_days"] = (df["season_day"] >= EARLY_DAYS).to_numpy()
    s[f"EARLY:team_games<={EARLY_TEAM_GAMES}"] = (df["team_games"].fillna(0) <= EARLY_TEAM_GAMES).to_numpy()
    return {k: v for k, v in s.items() if v.any()}


def min_band(min_mu) -> np.ndarray:
    m = np.asarray(min_mu, float)
    lab = np.array(["MIN_LT10", "MIN_10_20", "MIN_20_30", "MIN_30PLUS"])
    out = lab[np.clip(np.searchsorted(MIN_BANDS, m, side="right") - 1, 0, 3)].astype(object)
    out[~np.isfinite(m)] = "MIN_UNKNOWN"
    return out


def pplay_band(p) -> np.ndarray:
    p = np.asarray(p, float)
    out = np.where(p < PPLAY_BANDS[0], "PPLAY_LT_0.5", np.where(p < PPLAY_BANDS[1], "PPLAY_0.5_0.9", "PPLAY_GE_0.9")).astype(object)
    out[~np.isfinite(p)] = "PPLAY_UNKNOWN"
    return out


def exposure_strata(df: pd.DataFrame) -> dict:
    """Pregame-exposure strata (pregame model quantities only)."""
    mb, pb = min_band(df["min_mu"]), pplay_band(df["p_play"])
    mu = df["min_mu"].to_numpy(float)
    s = {f"MIN:{b}": mb == b for b in ("MIN_LT10", "MIN_10_20", "MIN_20_30", "MIN_30PLUS")}
    s.update({f"PPLAY:{b}": pb == b for b in ("PPLAY_LT_0.5", "PPLAY_0.5_0.9", "PPLAY_GE_0.9")})
    s["HIGH_EXPOSURE:min_mu>=30"] = mu >= HIGH_EXPOSURE_MIN
    s["HIGH_EXPOSURE:min_mu>=30&p_play>=0.9"] = (mu >= HIGH_EXPOSURE_MIN) & (df["p_play"].to_numpy(float) >= PPLAY_BANDS[1])
    s["ROTATION_CUT:min_mu>=20"] = mu >= ROTATION_MIN
    s["LOW_EXPOSURE:min_mu<20"] = mu < ROTATION_MIN
    for r in ROLES[:3]:                                    # pregame role x the high-exposure band (overlap diagnostic)
        role = (df["role_diagnostic"] == r).to_numpy()
        s[f"ROLE:{r}&min_mu>=30"] = role & (mu >= HIGH_EXPOSURE_MIN)
        s[f"ROLE:{r}&min_mu<30"] = role & (mu < HIGH_EXPOSURE_MIN)
    return {k: v for k, v in s.items() if v.any()}


def exposure_run(root: Path | None = None, write: bool = True) -> dict:
    """Item 2 of the 2026-10-21 ruling: the role question repeated with PREGAME exposure (stored OOS cells only)."""
    res, sizes, cross = {}, {}, {}
    for season in SOURCES:
        df = load(season, root)
        bt = Boot(df["game_id"].to_numpy())
        sizes[season] = {"rows": int(len(df)), "games": int(bt.G),
                         "min_mu_missing": int((~np.isfinite(df["min_mu"].to_numpy(float))).sum())}
        cross[season] = pd.crosstab(df["role_diagnostic"], min_band(df["min_mu"])).to_dict()
        for g, m in exposure_strata(df).items():
            for name in STATS + tuple(COMBOS):
                fn = stat_metrics if name in STATS else combo_metrics
                r = fn(df, name, bt, m)
                r["mean_min_mu"] = float(df["min_mu"].to_numpy(float)[m].mean())
                res.setdefault(name, {}).setdefault(g, {})[season] = r
        del df
    levels = {n: {g: subgroup_level(v, "stat" if n in STATS else "combo") for g, v in gs.items()} for n, gs in res.items()}
    out = {"audit": "JM1_FINAL pregame-exposure directional audit (diagnostic only; stored true-OOS cells; no model change)",
           "sign_convention": "SIGNED_ERROR = forecast_mean - actual; > 0 OVER-PREDICTION, < 0 UNDER-PREDICTION",
           "exposure_definition": {"min_mu": "Phase 1B pregame conditional minutes mean (JM1_FINAL minutes-draw input)",
                                   "p_play": "Phase 1B pregame P(play)", "min_bands": "minutes_sigma_buckets [0,10,20,30,inf)",
                                   "high_exposure": f"min_mu >= {HIGH_EXPOSURE_MIN}", "secondary_cut": f"min_mu >= {ROTATION_MIN}",
                                   "pplay_bands": "< 0.5, 0.5-0.9, >= 0.9",
                                   "fixed_before_results": True, "target_game_information_used": False},
           "role_definition": "role_diagnostic = marginal.role_of(role_l5_minutes_rank): team rank of pregame L5 minutes "
                              "(pr_l5_min_per_app x pr_l5_appearances, ASOF available_at <= prediction cutoff) <= 5 TOP5, "
                              "<= 8 ROTATION_6_8, else BENCH_9PLUS; NaN NO_ROLE_HISTORY. Pregame-known.",
           "sources": SOURCES, "sizes": sizes, "role_x_min_band_counts": cross,
           "thresholds": {"WATCH": WATCH, "RED": RED, "min_group_n": MIN_GROUP_N},
           "levels": levels, "results": res, "historical_resimulation": False, "api_calls": 0, "model_changed": False}
    if write:
        OUT_EXPOSURE_JSON.write_text(json.dumps(out, indent=1, default=float))
    return out


def run(root: Path | None = None, write: bool = True) -> dict:
    res = {"stats": {s: {} for s in STATS}, "combos": {c: {} for c in COMBOS}, "groups": {}, "combo_brier": {}}
    sizes = {}
    for season in SOURCES:
        df = load(season, root)
        bt = Boot(df["game_id"].to_numpy())
        sizes[season] = {"rows": int(len(df)), "games": int(bt.G)}
        st = strata(df)
        for s in STATS:
            res["stats"][s][season] = stat_metrics(df, s, bt)
            for g, m in st.items():
                res["groups"].setdefault(s, {}).setdefault(g, {})[season] = stat_metrics(df, s, bt, m)
        for c in COMBOS:
            res["combos"][c][season] = combo_metrics(df, c, bt)
            for g, m in st.items():
                res["groups"].setdefault(c, {}).setdefault(g, {})[season] = combo_metrics(df, c, bt, m)
        res["combo_brier"][season] = combo_brier(season, root)
        del df
    decision = {}
    for kind, block in (("stat", res["stats"]), ("combo", res["combos"])):
        for name, by in block.items():
            agg = classify(by, kind)
            sub = {g: subgroup_level(v, kind) for g, v in res["groups"][name].items()}
            red_sub = sorted(g for g, lv in sub.items() if lv == "RED")
            level = agg["level"]
            if red_sub and level == "GREEN":
                level = "WATCH"                                   # a concentrated (subgroup) persistent issue
            decision[name] = {"level": level, "aggregate": agg, "subgroup_levels": sub, "red_subgroups": red_sub}
    out = {"audit": "JM1_FINAL pre-launch directional-bias audit (diagnostic only; no model change)",
           "sign_convention": {"SIGNED_ERROR": "forecast_mean - actual",
                               "positive": "OVER-PREDICTION (outcomes below forecast)",
                               "negative": "UNDER-PREDICTION (outcomes above forecast)",
                               "mean_mid_pit": "> 0.5 UNDER-PREDICTION, < 0.5 OVER-PREDICTION (expected 0.5)",
                               "tail_asymmetry": "upper_tail_miss - lower_tail_miss; > 0 too much forecast mass BELOW outcomes "
                                                 "(UNDER-PREDICTION)",
                               "fixed_line_p_over_minus_observed": "> 0 model P(over) too high (OVER-PREDICTION)"},
           "sources": {s: {k: v for k, v in SOURCES[s].items()} for s in SOURCES},
           "seasons_not_available": NOT_AVAILABLE_SEASONS, "sizes": sizes,
           "uncertainty": f"game-clustered bootstrap, {BOOT} multinomial draws, seed {SEED}, 95% percentile intervals",
           "thresholds": {"WATCH": WATCH, "RED": RED, "min_group_n": MIN_GROUP_N},
           "historical_market_side_audit": "NOT_AVAILABLE (no historical sportsbook player-prop lines in the repository; "
                                           "BDL NCAAB carries game odds only; no API call made)",
           "decision": decision, "results": res,
           "historical_resimulation": False, "api_calls": 0, "model_changed": False}
    if write:
        OUT_JSON.write_text(json.dumps(out, indent=1, default=float))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.directional_audit")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--exposure", action="store_true", help="pregame-exposure audit (ruling 2026-10-21 item 2)")
    a = ap.parse_args(argv)
    if a.exposure:
        out = exposure_run(write=not a.no_write)
        print(json.dumps(out["levels"], indent=1))
        return
    out = run(write=not a.no_write)
    print(json.dumps({k: {"level": v["level"], "evidence": v["aggregate"]["evidence"], "red_subgroups": v["red_subgroups"]}
                      for k, v in out["decision"].items()}, indent=1))


if __name__ == "__main__":
    main()
