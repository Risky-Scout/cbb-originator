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
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from cbb_platform.prod import loader as LD

SOURCES = ("FROZEN_OPENING_BASELINE", "ACTIVE_DAILY")
DEFAULT_SOURCE = "FROZEN_OPENING_BASELINE"
FALLBACK_POLICY = "NONE (fail closed)"


class ModelSourceUnavailable(RuntimeError):
    def __init__(self, source: str, detail: str):
        super().__init__(f"{source}: {detail}")
        self.source, self.reason = source, "MODEL_SOURCE_UNAVAILABLE"


def resolve(source: str = DEFAULT_SOURCE, as_of=None, store_root: Path | None = None) -> LD.FrozenModel:
    if source not in SOURCES:
        raise ValueError(f"model source {source!r} not in {SOURCES}")
    if source == "FROZEN_OPENING_BASELINE":
        return LD.load()
    from cbb_platform.walkforward import config as WC, store as SO       # lazy: walkforward imports prod.run
    root = Path(store_root) if store_root is not None else WC.STORE_ROOT
    try:
        ptr = SO.active(root)
        if ptr is None:
            raise SO.StoreError("no ACTIVE pointer")
        vdir = SO.version_dir(root, SO.day_of(ptr["ACTIVE_MODEL_VERSION"]))
        if vdir.parent.resolve() != SO.paths(root)["models"].resolve() or vdir.name.startswith("."):
            raise SO.StoreError(f"ACTIVE points outside the finalized versions: {vdir}")
        model = SO.load_active(root)                                      # every hash + the pointer manifest hash
    except (SO.StoreError, LD.ArtifactIntegrityError, FileNotFoundError, KeyError, ValueError) as e:
        raise ModelSourceUnavailable(source, f"{type(e).__name__}: {e}") from e
    ident = model.identity
    if as_of is not None and ident["model_kind"] == WC.DAILY_KIND:
        tc = pd.Timestamp(ident["training_cutoff"])
        aso = pd.Timestamp(as_of)
        aso = aso.tz_localize("UTC") if aso.tzinfo is None else aso
        if tc > aso:
            raise ModelSourceUnavailable(source, f"FUTURE_LEAKAGE: {ident['model_version']} trained through {tc} > as_of {aso}")
    ident["model_source"] = source
    return model
