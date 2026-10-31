"""Portable, CBB-namespaced MODEL-VERSION STORE (runtime state, never Git). Root: WC.STORE_ROOT (env CBB_WF_ROOT).

    cbb-originator/
      models/<YYYY-MM-DD>/        one IMMUTABLE version per refit date (written once by os.replace of a complete,
                                  fsynced candidate directory; never edited, never overwritten)
          marginal.json  jm1_structural.json  residual_dependence.json  version_manifest.json  MANIFEST.sha256
          training_manifest.json  gates.json  eligibility.parquet  labels.parquet
      models/2026-11-01/          OPENING_DAY_BASELINE: byte copies of the frozen through-2025-26 artifacts (hash-verified;
                                  the originals under modeling/player_props_v2/ are never touched)
      active/ACTIVE_MODEL.json    the ONE pointer scoring reads; replaced atomically (tmp + fsync + os.replace)
      rollback/ROLLBACK_MODEL.json  the previous active version (rollback = pointer change only, never a refit)
      refused/<day>-<run>/        candidates that failed a hard gate (audit only; not loadable as a version)
      manifests/                  promotion_log.jsonl, runs.jsonl, corrections.jsonl, outcome_available_at.json
      training/features/<game>/   immutable pregame feature rows per 2026-27 game (training_rows.feature_rows)
      predictions/  monitoring/  locks/
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import date
from pathlib import Path

import pandas as pd

from cbb_platform import config as C
from cbb_platform.prod import config as PC, loader as LD
from cbb_platform.v2 import dependence as D, joint as J, latent as L
from cbb_platform.walkforward import config as WC

FILES = {"marginal": "marginal.json", "jm1_final": "jm1_structural.json", "residual_j2": "residual_dependence.json"}
MANIFEST = "version_manifest.json"


class StoreError(RuntimeError):
    pass


def _sha(p: Path) -> str:
    return LD.sha256_file(Path(p))


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def paths(root: Path = WC.STORE_ROOT) -> dict:
    root = Path(root)
    return {k: root / k for k in ("models", "active", "rollback", "refused", "manifests", "training", "predictions",
                                  "monitoring", "locks", "candidates")} | {"root": root}


def ensure(root: Path = WC.STORE_ROOT) -> dict:
    p = paths(root)
    for k, v in p.items():
        v.mkdir(parents=True, exist_ok=True)
    return p


def _fsync_dir(d: Path) -> None:
    fd = os.open(str(d), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_bytes(p: Path, data: bytes) -> str:
    with open(p, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return hashlib.sha256(data).hexdigest()


def write_json(p: Path, obj) -> str:
    return write_bytes(p, (json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n").encode())


def append_log(p: Path, rec: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_log(p: Path) -> list[dict]:
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()] if Path(p).exists() else []


def code_commit() -> dict:
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=C.PROJECT_ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=C.PROJECT_ROOT,
                                    capture_output=True, text=True, check=True).stdout.strip())
        return {"commit": head, "dirty": dirty}
    except Exception:                                            # noqa: BLE001 -- a deployed copy without .git
        return {"commit": os.environ.get("CBB_CODE_COMMIT", "UNKNOWN"), "dirty": None}


# ============================================================================ versions
def version_dir(root: Path, day: date) -> Path:
    return paths(root)["models"] / day.isoformat()


def day_of(version: str) -> date:
    s = version.removeprefix(WC.VERSION_PREFIX)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def versions(root: Path = WC.STORE_ROOT) -> list[str]:
    m = paths(root)["models"]
    return sorted(json.loads((d / MANIFEST).read_text())["MODEL_VERSION"] for d in m.iterdir()
                  if d.is_dir() and not d.name.startswith(".") and (d / MANIFEST).exists()) if m.exists() else []


def manifest(root: Path, version: str) -> dict:
    return json.loads((version_dir(root, day_of(version)) / MANIFEST).read_text())


def verify_version(root: Path, version: str) -> dict:
    """Recompute every file hash of a version against its manifest + the manifest's own sidecar hash."""
    return verify_dir(version_dir(root, day_of(version)), version)


def verify_dir(vdir: Path, version: str) -> dict:
    vdir = Path(vdir)
    if not (vdir / MANIFEST).exists():
        raise StoreError(f"{version}: no such version")
    man = json.loads((vdir / MANIFEST).read_text())
    if man["MODEL_VERSION"] != version:
        raise StoreError(f"{vdir} holds {man['MODEL_VERSION']}, not {version}")
    got = {k: _sha(vdir / f) for k, f in FILES.items()}
    bad = [k for k in FILES if got[k] != man["artifacts"][k]["sha256"]]
    side = (vdir / "MANIFEST.sha256").read_text().strip()
    if side != _sha(vdir / MANIFEST):
        bad.append("version_manifest")
    art = json.loads((vdir / FILES["jm1_final"]).read_text())
    dep = json.loads((vdir / FILES["residual_j2"]).read_text())
    if art.get("MARGINAL_ARTIFACT_SHA256") != got["marginal"] or dep.get("MARGINAL_ARTIFACT_SHA256") != got["marginal"]:
        bad.append("marginal_reference")
    if art.get("RESIDUAL_DEPENDENCE_ARTIFACT_SHA256") != got["residual_j2"]:
        bad.append("residual_reference")
    if bad:
        raise StoreError(f"{version}: integrity failure {bad}")
    return {"MODEL_VERSION": version, "artifact_sha256": got, "PRODUCTION_MANIFEST_HASH": side, "VERIFIED": True}


def finalize(root: Path, cand_dir: Path, day: date) -> Path:
    """Atomically publish a complete candidate directory as the immutable version of `day` (refused if it exists)."""
    final = version_dir(root, day)
    if final.exists():
        raise StoreError(f"version {day} exists (immutable)")
    for f in Path(cand_dir).iterdir():
        if f.is_file():
            with open(f, "rb") as h:
                os.fsync(h.fileno())
    _fsync_dir(cand_dir)
    os.replace(cand_dir, final)
    _fsync_dir(final.parent)
    return final


def new_candidate_dir(root: Path, day: date) -> Path:
    d = paths(root)["candidates"] / f".candidate-{day.isoformat()}-{uuid.uuid4().hex[:10]}"
    d.mkdir(parents=True)
    return d


def write_manifest(cand_dir: Path, man: dict) -> str:
    sha = write_json(cand_dir / MANIFEST, man)
    write_bytes(cand_dir / "MANIFEST.sha256", (sha + "\n").encode())
    return sha


# ============================================================================ pointers (atomic)
def _write_pointer(p: Path, body: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".{p.name}.{uuid.uuid4().hex}"
    write_json(tmp, body)
    os.replace(tmp, p)
    _fsync_dir(p.parent)


def active(root: Path = WC.STORE_ROOT) -> dict | None:
    p = paths(root)["active"] / "ACTIVE_MODEL.json"
    return json.loads(p.read_text()) if p.exists() else None


def rollback_pointer(root: Path = WC.STORE_ROOT) -> dict | None:
    p = paths(root)["rollback"] / "ROLLBACK_MODEL.json"
    return json.loads(p.read_text()) if p.exists() else None


def promote(root: Path, version: str, reason: str, run_id: str | None = None) -> dict:
    """ACTIVE <- version (after its gates passed); ROLLBACK <- the previous active. Each pointer write is atomic."""
    v = verify_version(root, version)
    prev = active(root)
    p = paths(root)
    if prev is not None and prev["ACTIVE_MODEL_VERSION"] != version:
        _write_pointer(p["rollback"] / "ROLLBACK_MODEL.json",
                       {"ROLLBACK_MODEL_VERSION": prev["ACTIVE_MODEL_VERSION"], "set_at": _now().isoformat(),
                        "PRODUCTION_MANIFEST_HASH": prev["PRODUCTION_MANIFEST_HASH"]})
    body = {"ACTIVE_MODEL_VERSION": version, "PRODUCTION_MANIFEST_HASH": v["PRODUCTION_MANIFEST_HASH"],
            "promoted_at": _now().isoformat(), "reason": reason, "run_id": run_id,
            "PREVIOUS_ACTIVE_MODEL_VERSION": prev["ACTIVE_MODEL_VERSION"] if prev else None}
    _write_pointer(p["active"] / "ACTIVE_MODEL.json", body)
    append_log(p["manifests"] / "promotion_log.jsonl", {"event": "PROMOTE", **body})
    return body


def rollback(root: Path, reason: str, approved_by: str) -> dict:
    """Human-approved rollback (production policy v1.0 section 8 / 10): pointer change only, never a refit."""
    rb = rollback_pointer(root)
    if rb is None:
        raise StoreError("no rollback version recorded")
    v = verify_version(root, rb["ROLLBACK_MODEL_VERSION"])
    cur = active(root)
    body = {"ACTIVE_MODEL_VERSION": v["MODEL_VERSION"], "PRODUCTION_MANIFEST_HASH": v["PRODUCTION_MANIFEST_HASH"],
            "promoted_at": _now().isoformat(), "reason": f"ROLLBACK: {reason}", "approved_by": approved_by,
            "PREVIOUS_ACTIVE_MODEL_VERSION": cur["ACTIVE_MODEL_VERSION"] if cur else None}
    _write_pointer(paths(root)["active"] / "ACTIVE_MODEL.json", body)
    append_log(paths(root)["manifests"] / "promotion_log.jsonl", {"event": "ROLLBACK", **body})
    return body


# ============================================================================ opening-day baseline
def register_opening(root: Path = WC.STORE_ROOT) -> dict:
    """models/2026-11-01 = byte copies of the four frozen artifacts (hash-verified against prod.config.ARTIFACTS);
    ACTIVE <- it when nothing is active yet. Idempotent."""
    ensure(root)
    day = WC.OPENING_DATE
    version = WC.version_id(day)
    if version_dir(root, day).exists():
        v = verify_version(root, version)
        if active(root) is None:
            promote(root, version, "OPENING_DAY_BASELINE")
        return {**v, "registered": False}
    LD.verify()                                                  # the frozen originals, before anything is copied
    cand = new_candidate_dir(root, day)
    src = {k: C.PROJECT_ROOT / v[0] for k, v in PC.ARTIFACTS.items()}
    sha = {}
    for k, f in FILES.items():
        sha[k] = write_bytes(cand / f, src[k].read_bytes())
        if sha[k] != PC.ARTIFACTS[k][1]:
            raise StoreError(f"opening copy of {k} does not hash like the frozen artifact")
    shutil.copyfile(src["production_manifest"], cand / "production_manifest_frozen.json")
    frozen = json.loads(src["production_manifest"].read_text())
    from cbb_platform.walkforward import fit as WF                 # local import: fit pulls heavy research modules
    art = json.loads(src["jm1_final"].read_text())
    man = {"MODEL_VERSION": version, "MODEL_KIND": WC.OPENING_KIND, "CREATED_AT": _now().isoformat(),
           "ARCHITECTURE": WC.ARCHITECTURE, "PRODUCTION_SEASON": WC.PRODUCTION_SEASON,
           "TRAINING_CUTOFF": "SEASON_2025_26_COMPLETE (frozen lockbox store; no 2026-27 outcome)",
           "TRAINING_THROUGH_SEASON": WC.OPENING_THROUGH, "TRAINING_THROUGH_DATE": opening_through_date(),
           "LATEST_INCLUDED_GAME": None, "N_INCLUDED_2026_27_GAMES": 0, "N_INCLUDED_2026_27_PLAYER_GAMES": 0,
           "FEATURE_STATE_HASH": None,
           "MARGINAL_ARTIFACT_HASH": sha["marginal"], "STRUCTURAL_ARTIFACT_HASH": sha["jm1_final"],
           "DISPERSION_HASH": WF.content_sha(art["conditional_dispersion"]), "DEPENDENCE_HASH": sha["residual_j2"],
           "FROZEN_PRODUCTION_MANIFEST_HASH": PC.ARTIFACTS["production_manifest"][1],
           "CODE_COMMIT": PC.FROZEN_COMMIT, "CONFIG_HASH": None, "TRAINING_DATA_HASH": "frozen lockbox store (see frozen manifest data_versions)",
           "PREVIOUS_MODEL_VERSION": None,
           "artifacts": {k: {"file": f, "sha256": sha[k], "frozen_source": PC.ARTIFACTS[k][0]} for k, f in FILES.items()},
           "supported_stats": frozen["supported_stats"], "supported_combos": frozen["supported_combos"],
           "random_seed_policy": frozen["random_seed_policy"], "known_monitoring_warnings": frozen["known_monitoring_warnings"]}
    write_manifest(cand, man)
    finalize(root, cand, day)
    v = verify_version(root, version)
    if active(root) is None:
        promote(root, version, "OPENING_DAY_BASELINE")
    return {**v, "registered": True}


def opening_through_date() -> str:
    """ET date of the last 2025-26 game in the frozen training store."""
    import duckdb
    from cbb_platform.v2 import provenance as PV
    if not PV.LB_FEATURES.exists():
        return "2025-26 season (store not present)"
    con = duckdb.connect(str(PV.LB_FEATURES), read_only=True)
    d = con.execute("SELECT max(game_date_et) FROM ex.game_examples WHERE season = 2026").fetchone()[0]
    con.close()
    return str(pd.Timestamp(d).date())


# ============================================================================ loading
def load_version(root: Path, version: str) -> LD.FrozenModel:
    """A verified store version in the form prod.run.daily(model=...) consumes (jm1_final_production.load_production
    layout; the frozen-marginal identity check is replaced by the version's own internal hash references)."""
    return load_dir(version_dir(root, day_of(version)), version)


def load_dir(vdir: Path, version: str) -> LD.FrozenModel:
    v = verify_dir(vdir, version)
    vdir = Path(vdir)
    man = json.loads((vdir / MANIFEST).read_text())
    mg = json.loads((vdir / FILES["marginal"]).read_text())
    a = json.loads((vdir / FILES["jm1_final"]).read_text())
    d = json.loads((vdir / FILES["residual_j2"]).read_text())
    checks = {"architecture": a.get("architecture") == PC.ARCHITECTURE and man["ARCHITECTURE"] == PC.ARCHITECTURE,
              "production_season": a.get("PRODUCTION_SEASON") == d.get("PRODUCTION_SEASON") == mg.get("PRODUCTION_SEASON")
                                   == PC.PRODUCTION_SEASON,
              "supported_stats": tuple(man["supported_stats"]) == PC.BASE_STATS == tuple(L.STATS),
              "supported_combos": {k: tuple(x) for k, x in man["supported_combos"].items()} == PC.COMBOS
                                  == {k: tuple(x) for k, x in J.COMBOS.items()},
              "draws_per_game": man["random_seed_policy"]["draws_per_game"] == PC.SIMS}
    if not all(checks.values()):
        raise LD.ArtifactIntegrityError("ARTIFACT_HASH_MISMATCH", f"{version}: {checks}")
    jm1 = {"struct": {"env": a["structure"]["env"], "glm": a["structure"]["glm"], "T": a["structure"]["T"]},
           "s4": {"dispersion": a["conditional_dispersion"], "residual_dependence": d["parameters"]},
           "e_dur": a["e_dur"], "residual_structure": D.structure("J2", d["parameters"])}
    ident = {"model_version": version, "architecture": PC.ARCHITECTURE, "jm1_final_version": a["version"],
             "frozen_commit": man["CODE_COMMIT"], "frozen_tag": None, "production_season": PC.PRODUCTION_SEASON,
             "training_through": man["TRAINING_THROUGH_SEASON"],
             "production_manifest": f"cbb-originator/models/{version_dir(Path('.'), day_of(version)).name}/{MANIFEST}",
             "artifact_sha256": v["artifact_sha256"] | {"production_manifest": v["PRODUCTION_MANIFEST_HASH"]},
             "checks": checks, "model_store_version": version, "model_kind": man["MODEL_KIND"],
             "training_cutoff": man["TRAINING_CUTOFF"], "training_through_date": man["TRAINING_THROUGH_DATE"],
             "data_cutoff": man.get("DATA_CUTOFF", man["TRAINING_CUTOFF"]),
             "latest_included_game": man["LATEST_INCLUDED_GAME"], "previous_model_version": man["PREVIOUS_MODEL_VERSION"],
             "checkpoint": WC.CHECKPOINT}
    return LD.FrozenModel(identity=ident, marginal_cfg=mg["config"], v0_state=mg["v0_state"],
                          participation_state=mg["participation_state"], jm1=jm1, manifest=man)


def load_active(root: Path = WC.STORE_ROOT) -> LD.FrozenModel:
    a = active(root)
    if a is None:
        raise StoreError("no ACTIVE model")
    m = load_version(root, a["ACTIVE_MODEL_VERSION"])
    if m.identity["artifact_sha256"]["production_manifest"] != a["PRODUCTION_MANIFEST_HASH"]:
        raise StoreError("ACTIVE pointer manifest hash disagrees with the version")
    return m
