"""EXPLICIT SCORER MODEL SOURCE (architect decision 2026-10-31, walk-forward finalization F).

    FROZEN_OPENING_BASELINE  the four frozen through-2025-26 artifacts through prod.loader.load (hash-verified). The
                             DEFAULT, and what the live scheduler uses: nothing about it changes here.
    ACTIVE_DAILY             the walk-forward ACTIVE pointer (cbb_platform/walkforward/store.py): the pointed-to version
                             must be a finalized immutable version under models/ whose manifest sidecar, every artifact
                             hash and the pointer's own manifest hash all verify, and whose training cutoff is not after
                             the prediction time. Candidate / refused directories are never loadable.

FAIL CLOSED: an ACTIVE_DAILY source that is missing, unverifiable or trained after the prediction time raises
ModelSourceUnavailable. There is NO automatic fallback to the frozen baseline: no approved policy defines one, and a
silent fallback would score with a model other than the one requested.

scoring_model (the production scorer rule) is the one place that substitutes the frozen opening artifacts when the ACTIVE
daily source is unavailable. Stage 07 (2026-11-27) made that substitution EXPLICIT and DEGRADED: the model identity
carries `model_source_status` (actual vs expected model, actual training cutoff, artifact age, exact reason code,
actionability policy, recovery requirement), the frozen identity is labelled model_kind FROZEN_OPENING_BASELINE (never
a daily refit), every scoring run adds the blocking run reason MODEL_SOURCE_FALLBACK (priced but blocked), and the event
is appended to <store>/manifests/model_source_degraded.jsonl for health. No last-known-good daily fallback is approved.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from cbb_platform.prod import config as PC, loader as LD

SOURCES = ("FROZEN_OPENING_BASELINE", "ACTIVE_DAILY")
DEFAULT_SOURCE = "FROZEN_OPENING_BASELINE"
FALLBACK_POLICY = "NONE (fail closed)"
SCORER_FALLBACK_POLICY = "FROZEN_OPENING_BASELINE_DEGRADED_PRICED_BLOCKED"
FROZEN_KIND = "FROZEN_OPENING_BASELINE"
# latest historical_knowable_at of a 2025-26 box line in the frozen lockbox (the national final, knowable 04-07 04:50Z):
# carries no 2026-27 outcome at all
FROZEN_TRAINING_CUTOFF = "2026-04-07T04:50:00+00:00"
DEGRADED_LOG = "model_source_degraded.jsonl"
FALLBACK_RUN_REASON = "MODEL_SOURCE_FALLBACK"


class ModelSourceUnavailable(RuntimeError):
    def __init__(self, source: str, detail: str, code: str = "UNAVAILABLE"):
        super().__init__(f"{source}: {detail}")
        self.source, self.reason = source, "MODEL_SOURCE_UNAVAILABLE"
        self.code, self.detail = code, detail


def resolve(source: str = DEFAULT_SOURCE, as_of=None, store_root: Path | None = None, replay: bool = False) -> LD.FrozenModel:
    """replay=True (historical replays, prod.replay_contract): the pointer must also have been promoted by as_of
    (FUTURE_PROMOTION). Prospective scoring resolves at the current instant, where a later promotion cannot exist."""
    if source not in SOURCES:
        raise ValueError(f"model source {source!r} not in {SOURCES}")
    if source == "FROZEN_OPENING_BASELINE":
        return LD.load()
    from cbb_platform.walkforward import config as WC, store as SO       # lazy: walkforward imports prod.run
    root = Path(store_root) if store_root is not None else WC.STORE_ROOT
    try:
        ptr = SO.active(root)
        if ptr is None:
            raise ModelSourceUnavailable(source, "no ACTIVE pointer", "NO_ACTIVE_POINTER")
        vdir = SO.version_dir(root, SO.day_of(ptr["ACTIVE_MODEL_VERSION"]))
        if vdir.parent.resolve() != SO.paths(root)["models"].resolve() or vdir.name.startswith("."):
            raise ModelSourceUnavailable(source, f"ACTIVE points outside the finalized versions: {vdir}", "POINTER_OUTSIDE_STORE")
        model = SO.load_active(root)                                      # every hash + the pointer manifest hash
    except ModelSourceUnavailable:
        raise
    except (SO.StoreError, LD.ArtifactIntegrityError, FileNotFoundError, KeyError, ValueError) as e:
        raise ModelSourceUnavailable(source, f"{type(e).__name__}: {e}", f"INTEGRITY:{type(e).__name__}") from e
    ident = model.identity
    if as_of is not None and ident["model_kind"] == WC.DAILY_KIND:
        tc = pd.Timestamp(ident["training_cutoff"])
        aso = pd.Timestamp(as_of)
        aso = aso.tz_localize("UTC") if aso.tzinfo is None else aso
        if tc > aso:
            raise ModelSourceUnavailable(source, f"FUTURE_LEAKAGE: {ident['model_version']} trained through {tc} > as_of {aso}",
                                         "FUTURE_LEAKAGE")
        pa = ptr.get("promoted_at")                                       # replay: a pointer promoted after as_of
        if replay and pa and pd.Timestamp(pa) > aso:                                  # was not the model in force at as_of
            raise ModelSourceUnavailable(source, f"FUTURE_PROMOTION: {ident['model_version']} promoted {pa} > as_of {aso}",
                                         "FUTURE_PROMOTION")
    ident["model_source"] = source
    ident["model_source_status"] = {"status": "OK", "source": source}
    return model


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def degraded_record(err: ModelSourceUnavailable, as_of, store_root, frozen_identity: dict) -> dict:
    """The explicit DEGRADED record of a frozen-model substitution (Stage 07 §7)."""
    from cbb_platform.walkforward import config as WC, store as SO
    aso = _utc(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC")
    try:
        ptr = SO.active(Path(store_root))
    except Exception as e:                                                # unreadable pointer: still a DEGRADED record
        ptr = {"unreadable": f"{type(e).__name__}: {e}"[:200]}
    day = aso.tz_convert("America/New_York").date()
    try:
        exp_cut = str(WC.refit_cutoff(day))
    except Exception:
        exp_cut = None
    tc = _utc(FROZEN_TRAINING_CUTOFF)
    return {"status": "DEGRADED", "reason_code": FALLBACK_RUN_REASON, "fallback_code": err.code,
            "fallback_reason": str(err.detail)[:500], "policy": SCORER_FALLBACK_POLICY,
            "actual": {"model_version": frozen_identity.get("model_version"), "model_kind": FROZEN_KIND,
                       "frozen_commit": frozen_identity.get("frozen_commit"),
                       "artifact_sha256": frozen_identity.get("artifact_sha256"),
                       "training_through_season": frozen_identity.get("training_through"),
                       "training_cutoff": FROZEN_TRAINING_CUTOFF},
            "artifact_age_days": round((aso - tc).total_seconds() / 86400, 2),
            "expected": {"source": "ACTIVE_DAILY", "store_root": str(store_root),
                         "pointer": {k: (ptr or {}).get(k) for k in ("ACTIVE_MODEL_VERSION", "PRODUCTION_MANIFEST_HASH",
                                                                    "promoted_at", "unreadable")} if ptr else None,
                         "expected_version": WC.version_id(day),
                         "expected_training_cutoff": exp_cut},
            "actionability_policy": "PRICED_BLOCKED_NON_ACTIONABLE",
            "recovery": "restore a verifiable ACTIVE pointer whose training cutoff <= as_of and promoted_at <= as_of, "
                        "then re-forecast (REFIT_RETRY / next tick); degraded rows are never graded as the daily-refit model",
            "as_of": str(aso), "detected_at": pd.Timestamp.now(tz="UTC").isoformat()}


def _log_degraded(store_root, rec: dict) -> None:
    import json
    try:
        d = Path(store_root) / "manifests"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / DEGRADED_LOG, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        rec["event_log"] = "WRITTEN"
    except OSError as e:
        rec["event_log"] = f"WRITE_FAILED: {type(e).__name__}"


def is_degraded(identity: dict | None) -> bool:
    return ((identity or {}).get("model_source_status") or {}).get("status") == "DEGRADED"


def scoring_model(as_of, store_root: Path | None, artifact_paths: dict | None = None) -> LD.FrozenModel:
    """THE production scorer model rule, shared by every scoring path (lifecycle forecasts AND quote-tick scoring):
    the ACTIVE walk-forward version whose training cutoff <= as_of, else the frozen opening artifacts (both
    hash-verified). store_root None = the frozen opening artifacts only (tests / explicit frozen runs).

    2026-11-24: quote ticks called run.daily without a model and scored every near-tip PMF with the frozen
    through-2025-26 artifacts while the lifecycle forecasts used the promoted daily fit (~76% of the 11-23 frozen
    evaluations came from tick PMFs labelled cbb-v2-jm1-final-shadow-1.0.0). One rule now serves both."""
    if store_root is None:
        m = LD.load(artifact_paths)
        if isinstance(getattr(m, "identity", None), dict):
            m.identity.update(model_kind=FROZEN_KIND, model_source=FROZEN_KIND,
                              model_source_status={"status": "EXPLICIT_FROZEN", "source": FROZEN_KIND})
        return m
    try:
        return resolve("ACTIVE_DAILY", as_of, store_root)
    except ModelSourceUnavailable as e:                  # Stage 07: explicit, DEGRADED, blocked -- never silent
        m = LD.load(artifact_paths)
        ident = m.identity if isinstance(getattr(m, "identity", None), dict) else {}
        rec = degraded_record(e, as_of, store_root, ident)
        _log_degraded(store_root, rec)
        if isinstance(getattr(m, "identity", None), dict):
            m.identity.update(model_kind=FROZEN_KIND, model_source=FROZEN_KIND, training_cutoff=FROZEN_TRAINING_CUTOFF,
                              model_source_status=rec)
        return m
