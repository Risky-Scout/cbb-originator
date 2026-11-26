"""Player correction PI2: preseason information state of transfers and players with no prior history
(docs/audits/2026-11-26-rootcause/ROOT_CAUSE_PROTOCOL.json PI1 / PI2_amendment). Promoted 2026-11-26.

Root cause (chronological OOS, replicated 2024-25 / 2025-26 / live 2026-27): a transfer's prior-season per-minute rates
are carried to the new team with no competition-level translation (moving to a weaker program under-forecast by 6-15%,
to a stronger one over-forecast by 6-10%), and players with no prior history are initialised too high (PTS ratio 0.90-0.95,
falling to ~0.67 at the first appearances).

Fix (stat means, PTS / AST / FG3M / TOV only; REB / STL / BLK unchanged -- PI2 amendment):
    TRANSFER   (pps_same_team == 0):  mu_s <- mu_s * (1 + s_prev d_s) / (1 + s_prev DV1_other_s),
               d_s = a_s + b_s z_d + c_s z_m,  z_d = clip(str_dest - str_origin, -25, 25) / 10  (2025-26 final strengths),
               z_m = (clip(prior-season minutes per appearance, 0, 36) - 20) / 8
    NO_PRIOR   (no prior season):     mu_s <- mu_s * (1 + e_s w),  w = 31 / (current-season minutes + 31)
    returners unchanged. The total correction factor (every promoted correction x PI2) is clipped to [0.7, 1.3] on the
    adjusted rows, exactly as the research arm.
Constants: TRAIN fit on 2024-25 only (results/r4_pi1_fit.json), frozen. Evaluation (PI2, common random numbers vs the
production engine): stacked log loss 2025-26 -0.00093 [-0.00106, -0.00080], live -0.00206 [-0.00282, -0.00134];
live graded prop events log loss 0.7047 vs 0.7080. Prospective confirmation on graded live events is declared.
The 2027 preseason table (transfer origin teams, 2025-26 final strengths) is sealed in preseason_prior_2027.json.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform.prod import dev_trend as DV

ID = "CBB_PLAYER_CORRECTION_PI2_PRESEASON_INFORMATION_STATE_V1"
STATS = ("PTS", "AST", "FG3M", "TOV")
COEF = {   # stat -> (a, b, c, e)
    "PTS": (0.007821825462899636, -0.10594201202742981, -0.017665267472063338, -0.4353603383893828),
    "AST": (-0.012644865525988199, -0.0442146608773204, 0.01273234380501893, -0.23398565598895196),
    "FG3M": (0.003576534308164465, -0.060328952952436704, 0.021480292773402243, -0.24510035037274727),
    "TOV": (0.02606988640053153, -0.11894354285787527, -0.024495688811816588, -0.1085699474158221),
}
CLIP = (0.7, 1.3)
K_NO_PRIOR = 31.0
TABLE = Path(__file__).resolve().parent / "preseason_prior_2027.json"
TABLE_SHA256 = "4132a5ec71ca28664d9e1727cf9d3b44555d47a75c69126160373a49a24818dd"
ENABLED = True


class PreseasonTableIntegrityError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(ENABLED)


@lru_cache(maxsize=1)
def table() -> dict:
    raw = TABLE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != TABLE_SHA256:
        raise PreseasonTableIntegrityError(f"{TABLE.name}: sha256 mismatch")
    return json.loads(raw)


def _num(df: pd.DataFrame, c: str, fill=np.nan) -> np.ndarray:
    return pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float) if c in df else np.full(len(df), fill)


def classes(cand: pd.DataFrame) -> np.ndarray:
    """SAME_TEAM_RETURNER / TRANSFER / NO_PRIOR_HISTORY from the prior-season state (pps_same_team: 1 / 0 / missing);
    agrees with the athleteId transition panel on 99.6% of 2026-27 rows."""
    s = _num(cand, "pps_same_team")
    return np.where(np.isnan(s), "NO_PRIOR_HISTORY", np.where(s == 1, "SAME_TEAM_RETURNER", "TRANSFER"))


def adjustments(cand: pd.DataFrame) -> tuple[dict, np.ndarray]:
    """Per-row PI2 factor per stat (1.0 for returners) and the class array."""
    t = table()
    cls = classes(cand)
    sp = DV.prior_share(cand)
    stp = t["strength_prev"]
    org = cand["canonical_player_id"].map(t["origin_team"]) if "canonical_player_id" in cand else pd.Series([None] * len(cand))
    dest = cand["canonical_team_id"] if "canonical_team_id" in cand else pd.Series([None] * len(cand))
    zd = np.array([(stp[d] - stp[o]) if isinstance(o, str) and o in stp and d in stp else 0.0 for d, o in zip(dest, org)])
    zd = np.clip(zd, -25, 25) / 10.0
    zm = (np.clip(np.nan_to_num(_num(cand, "pps_min_per_app"), nan=20.0), 0, 36) - 20.0) / 8.0
    w = K_NO_PRIOR / (np.nan_to_num(_num(cand, "cur_min"), nan=0.0) + K_NO_PRIOR)
    tr, nh = cls == "TRANSFER", cls == "NO_PRIOR_HISTORY"
    out = {}
    for s, (a, b, c, e) in COEF.items():
        adj = np.ones(len(cand))
        dv_other = 1.0 + sp * DV.DELTA[s][1]
        adj[tr] = ((1.0 + sp * (a + b * zd + c * zm)) / dv_other)[tr]
        adj[nh] = (1.0 + w * e)[nh]
        out[s] = np.nan_to_num(adj, nan=1.0)
    return out, cls


def apply(rows: pd.DataFrame, cand: pd.DataFrame, base_mu: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """rows = model input rows after every promoted correction (prod.context_adj); base_mu = the same rows' stat means
    BEFORE the promoted mean corrections (prod.dev_trend / context_adj), aligned by position. Returns rows with the
    PTS / AST / FG3M / TOV means of transfer / no-history players corrected and a receipt."""
    if not enabled() or rows.empty:
        return rows, {"id": ID, "enabled": enabled(), "rows": 0}
    c = cand.set_index("player_game_example_id").loc[rows["player_game_example_id"].to_numpy()].reset_index()
    adj, cls = adjustments(c)
    hit = cls != "SAME_TEAM_RETURNER"
    out = rows.copy()
    for s, a in adj.items():
        cur = out[f"{s}_mu"].to_numpy(dtype=float)
        base = base_mu[f"{s}_mu"].to_numpy(dtype=float)
        prior_factor = np.divide(cur, base, out=np.ones_like(cur), where=base > 0)
        total = np.clip(prior_factor * a, *CLIP)
        out[f"{s}_mu"] = np.where(hit & (base > 0), base * total, cur)
    return out, {"id": ID, "enabled": True, "rows": int(len(rows)),
                 "classes": {k: int(v) for k, v in zip(*np.unique(cls, return_counts=True))},
                 "factor_range": {s: [float(v[hit].min()), float(v[hit].max())] if hit.any() else None for s, v in adj.items()},
                 "table_sha256": TABLE_SHA256}
