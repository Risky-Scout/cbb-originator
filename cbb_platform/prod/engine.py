"""Predictive-engine attribution: which predictive mathematics produced a forecast.

`model_version` names the trained artifacts (the frozen opening baseline or a daily walk-forward version). It does NOT
change when scoring-time mathematics change (2026-11-22: prod.rate_a2 changed the AST / FG3M / STL means under the same
model_version). Every forecast / evaluation / grade / performance row therefore also carries:

    engine_id     deterministic name of the scoring mathematics: the base architecture plus every promoted
                  scoring-time correction and the sha256 of its frozen constants (a code-only change that does not
                  touch the mathematics keeps the engine_id; a changed constant changes it)
    code_commit   the production release commit that ran (CBB_CODE_COMMIT, set by deploy/droplet/bin/cbb-originator-run)

Rows written before attribution existed are attributed by `legacy_engine(prediction_as_of)`: every production run is
serialized against deploys (cbb-deploy-release refuses while a unit is active), so a run's engine is exactly the one
deployed at its prediction_as_of. ENGINE_HISTORY is the frozen record of those switches (Droplet `current` symlink
mtimes); it is append-only.
"""
from __future__ import annotations

import hashlib
import json
import os

import pandas as pd

BASE = "JM1_FINAL"
FIELDS = ("engine_id", "code_commit")

# (effective_at UTC = Droplet release switch, engine_id, release commit) -- append-only, chronological
ENGINE_HISTORY = (
    ("2026-10-01T00:00:00+00:00", "JM1_FINAL", None),                   # every release before the first correction
    ("2026-11-22T21:45:31.499302+00:00", "JM1_FINAL+RA2V1[AST,FG3M,STL]#437c07dc928d",
     "01d4ab1bdb34a055c52f3b5a692a2151f4c55eec"),
)


def _corrections() -> list[tuple[str, dict]]:
    """(tag, frozen constants) of every promoted scoring-time correction, in application order."""
    from cbb_platform.prod import (context_adj as CX, dev_trend as DV, minutes_state as MS2, preseason_prior as PI, pts_compound as PTC,
                                   rate_a2 as RA2, tov_trend as TT)
    out = []
    if RA2.PARAMS:
        out.append((f"RA2V1[{','.join(RA2.PARAMS)}]", {"id": RA2.ID, "params": RA2.PARAMS, "w": list(RA2.W),
                                                       "d_clip": RA2.D_CLIP, "d_min": RA2.D_MIN_APPEARANCES,
                                                       "z_ref": RA2.Z_REF_MINUTES}))
    if PTC.enabled():
        out.append((f"PTSC1[{PTC.PHI_N:g}]", {"id": PTC.ID, "pv": list(PTC.PV), "phi_n": PTC.PHI_N, "nmax": PTC.NMAX}))
    if TT.enabled():
        out.append(("TOVT1", {"id": TT.ID, "b": TT.B, "ubar": TT.UBAR}))
    if DV.enabled():
        out.append(("DEV1", {"id": DV.ID, "delta": {k: list(v) for k, v in DV.DELTA.items()}, "w": list(DV.W), "k": DV.K_MINUTES}))
    if CX.enabled():
        out.append(("CTX1", {"id": CX.ID, "kappa_team": CX.KAPPA_TEAM, "kappa_opp": {k: (None if v == float("inf") else v)
                                                                                   for k, v in CX.KAPPA_OPP.items()},
                             "sims": CX.LEDGER_SIMS}))
    if PI.enabled():
        out.append(("PI2", {"id": PI.ID, "coef": {k: list(v) for k, v in PI.COEF.items()}, "clip": list(PI.CLIP),
                            "k": PI.K_NO_PRIOR, "table_sha256": PI.TABLE_SHA256}))
    if MS2.enabled():
        out.append(("PS2", {"id": MS2.ID, "qmap_sha256": MS2.QMAP_SHA256, "z_knot": MS2.Z_KNOT}))
    return out


def pre_context_tag() -> str:
    """Identity of the context ledger's incumbent: every promoted correction that acts in the ledger's counterfactual
    (context_adj.ledger_rows: RA2 / TOVT1 on the means, then prod.simulate.simulate_game, which installs PTSC1 and PS2).
    DEV1 / CTX1 / PI2 multiply the scoring means after the ledger's incumbent and are excluded. Stage 06 correction
    (2026-11-27): PS2 was excluded although simulate_game always applies it, so the ledger seeded 11-26 13:45Z without
    PS2 kept that incumbent after PS2 went live (22:48Z) and appended PS2 rows under the same tag (mixed incumbent)."""
    cs = [c for t, c in _corrections() if t not in ("DEV1", "CTX1", "PI2")]          # PI2 acts after CTX1
    return hashlib.sha256(json.dumps(cs, sort_keys=True, default=float).encode()).hexdigest()[:12]


def engine_id() -> str:
    cs = _corrections()
    if not cs:
        return BASE
    sha = hashlib.sha256(json.dumps([c for _, c in cs], sort_keys=True, default=float).encode()).hexdigest()[:12]
    return "+".join([BASE] + [t for t, _ in cs]) + f"#{sha}"


def code_commit() -> str | None:
    return os.environ.get("CBB_CODE_COMMIT") or os.environ.get("CBB_EXPECTED_COMMIT") or None


RUNTIME_FLAGS = ("CBB_INITIAL_AVAILABILITY", "CBB_GAME_AVAILABILITY_BASELINE", "CBB_GAME_MEAN_BASIS", "CBB_FEATURE_CLOSURE",
                 "CBB_GAME_MODEL", "CBB_HISTORY_SOURCE", "CBB_WF_ROOT", "CBB_WF_REFIT_TIME")


def runtime_flags() -> dict:
    """F9 (Stage 07): the raw value of every behaviour-changing environment flag at scoring time (None = unset, the
    code default) and the effective game mean basis -- recorded in every run identity, never part of engine_id."""
    out = {k: os.environ.get(k) for k in RUNTIME_FLAGS}
    try:
        from cbb_platform.game import joint as J
        out["effective_game_mean_basis"] = J.mean_basis()
        out["effective_game_joint_version"] = J.joint_version()
    except Exception as e:                                       # noqa: BLE001 - lineage only
        out["effective_game_mean_basis"] = f"UNRESOLVED {type(e).__name__}"
    return out


def identity() -> dict:
    return {"engine_id": engine_id(), "code_commit": code_commit(), "runtime_flags": runtime_flags()}


def legacy_engine(prediction_as_of) -> str | None:
    """engine_id of a row written before attribution existed, from the instant its run predicted."""
    if prediction_as_of is None or (not isinstance(prediction_as_of, str) and pd.isna(prediction_as_of)):
        return None
    t = pd.Timestamp(prediction_as_of)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    eid = None
    for at, e, _ in ENGINE_HISTORY:
        if t >= pd.Timestamp(at):
            eid = e
    return eid


def attribute(engine, prediction_as_of) -> str | None:
    """The recorded engine_id, else the legacy attribution."""
    if isinstance(engine, str) and engine:
        return engine
    return legacy_engine(prediction_as_of)
