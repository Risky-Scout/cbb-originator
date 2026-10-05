"""Immutable raw payload store shared by every adapter.

Layout:  data/raw/<provider>/<endpoint_slug>/<received_at>__<sha256[:16]>.<ext>
         plus a sidecar <same name>.meta.json with endpoint, params, received_at, sha256.

Rules (tested in tests/test_raw_store.py):
  * a payload file is written once and never modified (written read-only, 0o444);
  * storing byte-identical content again FOR THE SAME REQUEST (same params) is a no-op that
    returns the existing record (idempotent re-fetch / re-ingest of the same snapshot);
  * byte-identical content from a DIFFERENT request (e.g. two games that both return `[]`) is a
    new record, so every request keeps its own provenance;
  * different content always creates a NEW file (append-only history).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cbb_platform import config as C


@dataclass(frozen=True)
class RawRecord:
    path: Path
    sha256: str
    received_at: str          # ISO-8601 UTC — our receipt time, the authoritative PIT time
    provider: str
    endpoint: str
    params: dict
    created: bool             # False when identical content was already stored


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "root"


def _same_params(a: dict, b: dict) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def store(provider: str, endpoint: str, payload: bytes, *, params: dict | None = None,
          ext: str = "json", received_at: datetime | None = None, root: Path | None = None) -> RawRecord:
    """Write `payload` immutably and return its record. Identical bytes are stored only once."""
    root = root or C.RAW
    params = params or {}
    sha = hashlib.sha256(payload).hexdigest()
    folder = root / provider / _slug(endpoint)
    folder.mkdir(parents=True, exist_ok=True)

    for meta_path in sorted(folder.glob(f"*__{sha[:16]}.{ext}.meta.json")):
        meta = json.loads(meta_path.read_text())
        if meta["sha256"] == sha and _same_params(meta["params"], params):
            return RawRecord(meta_path.parent / meta["file"], sha, meta["received_at"],
                             provider, endpoint, meta["params"], created=False)

    received = (received_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = received.strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f"{stamp}__{sha[:16]}.{ext}"
    if path.exists():                                  # never overwrite, even by accident
        raise FileExistsError(path)
    path.write_bytes(payload)
    os.chmod(path, 0o444)
    meta = {"provider": provider, "endpoint": endpoint, "params": params, "sha256": sha,
            "received_at": received.isoformat(), "size_bytes": len(payload), "file": path.name,
            "ingestion_version": C.INGESTION_VERSION}
    meta_path = Path(str(path) + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=1, sort_keys=True))
    os.chmod(meta_path, 0o444)
    return RawRecord(path, sha, meta["received_at"], provider, endpoint, params, created=True)


def records(provider: str, root: Path | None = None) -> list[tuple[Path, dict]]:
    """All stored payloads for a provider, oldest first: [(payload_path, meta), ...]."""
    root = root or C.RAW
    out = []
    for meta_path in sorted((root / provider).glob("*/*.meta.json")):
        meta = json.loads(meta_path.read_text())
        out.append((meta_path.parent / meta["file"], meta))
    return sorted(out, key=lambda pm: (pm[1]["received_at"], pm[1]["file"]))
