"""ONE coherent joint draw bank per game (cbb_platform/v2/jm1_final_production.score_game, unchanged): frozen random
player minutes x shared duration / environment -> structural conditional means -> conditional dispersion -> residual
J2 latent copula -> joint counts (sims x priced players x 7). Every player, stat, combo, line, side and sportsbook is
read from this one bank; nothing is re-simulated per market.

Distribution per market (both from the same bank):
    base stats  BANK_MIXTURE_PMF  -- the bank's own conditional count tables averaged over its draws (S.mixture_pmf;
                                     the pmf the 2025-26 confirmation scored)
    combos      BANK_DRAWS        -- empirical pmf of the summed joint draws (joint.derived_markets)
Distributions are CONDITIONAL ON PLAYING; P(play) is reported beside them and never mixed in.

Memory (2026-11-13 morning OOM): the frozen structural.mixture_tables materializes ~11 table-sized temporaries for the
whole game at once (NB pmf -> cdf -> AS241 thresholds over every (stat dim, mean grid) row), a ~2.1 GB transient per
20,000-draw game. bounded_mixture_tables returns EXACTLY the same tables (every row of every table is a function of its
own dim's column only) by calling the frozen function on whole-player column blocks and concatenating the results with
their global row offsets; simulate_game substitutes it for the scorer's call only. No model code changes (the frozen
files stay hash-pinned); tests/test_prod_bounded_tables.py proves array equality against the frozen function.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager

import numpy as np
import pandas as pd

from cbb_platform.prod import config as PC
from cbb_platform.v2 import dependence as D, jm1_final_production as JP, joint as J, structural as S

PMF_TOL = 1e-9
PSD_TOL = -1e-9
TABLE_BLOCK_PLAYERS = 2                       # whole players per frozen mixture_tables call (7 stat dims each)
_FROZEN_MIXTURE_TABLES = S.mixture_tables


class ScoringFailure(RuntimeError):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def bounded_mixture_tables(lm: np.ndarray, phi: np.ndarray, kmax: int = S.L.KMAX,
                           block_players: int = TABLE_BLOCK_PLAYERS, keep: tuple = ("pmf", "cdf", "thr")) -> dict:
    """structural.mixture_tables(lm, phi, kmax), identical arrays, peak memory bounded by one player block: rows are
    ordered (dim, grid) with each dim's grid / pmf / cdf / thresholds computed from its own column, so the full result
    is the block results concatenated, with inv / starts shifted by the rows of the preceding blocks. Blocks start at
    multiples of NS, so a block's local dim % NS is the global stat. `keep` drops tables the caller never reads (the
    scorer reads pmf via mixture_pmf and thr via counts, never cdf)."""
    d = lm.shape[1]
    step = block_players * S.NS if d % S.NS == 0 else d      # not player-aligned: one block (the frozen call itself)
    tabs = []
    for a in range(0, d, step):
        t = _FROZEN_MIXTURE_TABLES(lm[:, a:a + step], phi, kmax)
        tabs.append({k: v for k, v in t.items() if k in keep or k in ("inv", "starts")})
        del t
    off = np.concatenate([[0], np.cumsum([t["starts"][-1] for t in tabs])])
    out = {"inv": np.concatenate([t.pop("inv") + o for t, o in zip(tabs, off)], axis=1),
           "starts": np.concatenate([t["starts"][:-1] + o for t, o in zip(tabs, off)] + [off[-1:]])}
    for k in keep:                                           # one table at a time: block copies freed as each is joined
        out[k] = np.concatenate([t.pop(k) for t in tabs])
    return out


def _scorer_tables(lm: np.ndarray, phi: np.ndarray, kmax: int = S.L.KMAX) -> dict:
    return bounded_mixture_tables(lm, phi, kmax, keep=("pmf", "thr"))


@contextmanager
def _bounded_tables():
    S.mixture_tables = _scorer_tables
    try:
        yield
    finally:
        S.mixture_tables = _FROZEN_MIXTURE_TABLES


def simulate_game(prod: dict, gid: str, gp: pd.DataFrame, envd: dict, sims: int = PC.SIMS) -> dict:
    """The single bank for one game + validity checks. Raises ScoringFailure with a readiness reason."""
    if gp.loc[gp["_scored"], "team_id"].nunique() != 2:
        raise ScoringFailure("MISSING_PLAYER_STATE", f"{gid}: priced players on fewer than two teams")
    try:
        with _bounded_tables():
            r = JP.score_game(prod, gid, gp.reset_index(drop=True), envd, sims)
    except D.V2Phase2DataViolation as e:
        raise ScoringFailure("NON_PSD_DEPENDENCE", str(e)) from e
    except Exception as e:                                           # noqa: BLE001 -- any scorer failure blocks
        raise ScoringFailure("SCORING_FAILURE", f"{type(e).__name__}: {e}") from e
    if r["corr_invalid"] or r["corr_min_eig"] < PSD_TOL:
        raise ScoringFailure("NON_PSD_DEPENDENCE", f"{gid}: min eigenvalue {r['corr_min_eig']}")
    pmf = r["pmf"]
    if not np.isfinite(pmf).all() or (pmf < -1e-15).any() or np.abs(pmf.sum(axis=2) - 1).max() > PMF_TOL:
        raise ScoringFailure("PMF_NOT_NORMALIZED", f"{gid}: base-stat pmf not normalized")
    if r["draws"].shape != (sims, len(r["rows"]), J.NS) or (r["draws"] < 0).any():
        raise ScoringFailure("SCORING_FAILURE", f"{gid}: draw bank shape / sign")
    bank_sha = hashlib.sha256(np.ascontiguousarray(r["draws"]).tobytes()).hexdigest()
    return {**r, "game_id": gid, "sims": sims, "bank_sha256": bank_sha,
            "seeds": {"count_latents": D.game_seed(J.SIM_SEED, gid), "structural_base": 20271}}


def _draw_pmf(x: np.ndarray, kmax: int) -> np.ndarray:
    return np.bincount(x, minlength=kmax + 1)[: kmax + 1].astype(float) / len(x)


def market_distributions(bank: dict) -> pd.DataFrame:
    """One row per (priced player, market): pmf (np.ndarray), projection, method. Same bank for all."""
    rows = bank["rows"]
    draws = bank["draws"]
    combos = J.derived_markets(draws)
    out = []
    for i, r in enumerate(rows.itertuples(index=False)):
        for s in PC.BASE_STATS:
            pmf = bank["pmf"][i, J.SI[s]]
            emp = draws[:, i, J.SI[s]]
            out.append({"game_id": bank["game_id"], "team_id": r.team_id, "player_id": r.player_id, "market": s,
                        "pmf": pmf, "projection": float((pmf * np.arange(len(pmf))).sum()),
                        "bank_mean": float(emp.mean()), "method": "BANK_MIXTURE_PMF", "p_play": float(r.p_play)})
        for c in PC.COMBOS:
            x = combos[c][:, i]
            pmf = _draw_pmf(x, int(x.max()) + 1)
            out.append({"game_id": bank["game_id"], "team_id": r.team_id, "player_id": r.player_id, "market": c,
                        "pmf": pmf, "projection": float(x.mean()), "bank_mean": float(x.mean()),
                        "method": "BANK_DRAWS", "p_play": float(r.p_play)})
    return pd.DataFrame(out)


def same_draw_check(bank: dict) -> bool:
    """Combos are exact sums of the same simulated stat draws (no separate combo model)."""
    der = J.derived_markets(bank["draws"])
    return all(np.array_equal(der[c], bank["draws"][:, :, [J.SI[s] for s in parts]].astype(np.int64).sum(axis=2))
               for c, parts in PC.COMBOS.items())


def same_team_correlation(bank: dict, market: str = "PTS") -> dict:
    """Prospective-monitoring hook (warning C): predicted same-team / opposing pairwise correlation of `market` in
    this game's bank, kept beside the ledger so realized pair products can be compared after settlement."""
    x = bank["draws"][:, :, J.SI[market]].astype(float)
    team = (bank["rows"]["team_id"] != bank["rows"]["team_id"].min()).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        c = np.corrcoef(x, rowvar=False)
    n = x.shape[1]
    iu = np.triu_indices(n, 1)
    same = team[iu[0]] == team[iu[1]]
    vals = c[iu]
    ok = np.isfinite(vals)
    pids = bank["rows"]["player_id"].tolist()
    return {"game_id": bank["game_id"], "market": market,
            "means": {p: float(m) for p, m in zip(pids, x.mean(axis=0))},
            "sds": {p: float(s) for p, s in zip(pids, x.std(axis=0))},
            "pairs_same_team": int((same & ok).sum()), "pairs_opposing": int((~same & ok).sum()),
            "mean_corr_same_team": float(vals[same & ok].mean()) if (same & ok).any() else None,
            "mean_corr_opposing": float(vals[~same & ok].mean()) if (~same & ok).any() else None,
            "pair_corr": {f"{bank['rows']['player_id'].iat[a]}|{bank['rows']['player_id'].iat[b]}": float(v)
                          for a, b, v, k in zip(iu[0], iu[1], vals, ok) if k}}
