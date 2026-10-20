"""Compact static output bundle (local only; candidate layout for an eventual, NOT yet approved Wizard path).

    index.html               static shadow board (banner: SHADOW -- NOT FOR WAGERING)
    predictions.json         run identity + board rows + per-player projections
    predictions.csv          board rows
    DEPLOYMENT_MANIFEST.json identity, artifact hashes, state version, readiness, file digests
    SHA256SUMS               written last; covers every other file (NCAAF verifier convention)
No development / training data, no stores, no draw banks, no QA payloads.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from pathlib import Path

import pandas as pd

from cbb_platform.prod import config as PC

FILES = ("index.html", "predictions.json", "predictions.csv", "DEPLOYMENT_MANIFEST.json")
ALL_FILES = FILES + ("SHA256SUMS",)
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
FORBIDDEN = (".env", "id_rsa", "id_ed25519", ".pem", ".key", ".netrc", ".duckdb", ".parquet", "secret", "token")
MAX_BYTES = 5 * 1024 * 1024
MANIFEST_KIND = "cbb_ncaab_player_props_shadow_bundle"


class BundleChecksumFailure(RuntimeError):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _html(board: pd.DataFrame, meta: dict) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in board.columns)
    body = "".join("<tr>" + "".join(f"<td>{html.escape('' if pd.isna(v) else (f'{v:.4f}' if isinstance(v, float) else str(v)))}</td>"
                                    for v in r) + "</tr>" for r in board.itertuples(index=False))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CBB Player Props Shadow</title>
<style>body{{font-family:system-ui,sans-serif;margin:16px;background:#fff;color:#111}}table{{border-collapse:collapse;font-size:12px}}
td,th{{border:1px solid #ccc;padding:3px 6px;text-align:left}}.b{{background:#fde68a;padding:8px;font-weight:600}}</style></head>
<body><p class="b">SHADOW &mdash; NOT FOR WAGERING. Readiness: {html.escape(meta['readiness_state'])}. Wagering enabled: false.
Data: {html.escape(meta['data_label'])}.</p>
<p>Model {html.escape(meta['model_version'])} &middot; prediction as of {html.escape(str(meta['prediction_as_of']))} &middot;
state {html.escape(str(meta['state_version']))} &middot; run {html.escape(meta['run_id'])}</p>
<div style="overflow-x:auto"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>
<p>Files: <a href="predictions.json">predictions.json</a> &middot; <a href="predictions.csv">predictions.csv</a> &middot;
<a href="DEPLOYMENT_MANIFEST.json">DEPLOYMENT_MANIFEST.json</a> &middot; <a href="SHA256SUMS">SHA256SUMS</a></p>
</body></html>
"""


def write(out_dir: Path, board: pd.DataFrame, projections: pd.DataFrame, meta: dict) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    b = board.copy()
    for c in b.columns:
        if pd.api.types.is_datetime64_any_dtype(b[c]):
            b[c] = b[c].astype(str)
    (out_dir / "index.html").write_text(_html(b, meta))
    (out_dir / "predictions.json").write_text(json.dumps(
        {"meta": meta, "board": json.loads(b.to_json(orient="records", date_format="iso")),
         "projections": json.loads(projections.to_json(orient="records", date_format="iso"))},
        indent=1, sort_keys=True, default=str) + "\n")
    b.to_csv(out_dir / "predictions.csv", index=False)
    man = {"kind": MANIFEST_KIND, "schema_version": 1, **{k: meta[k] for k in (
        "run_id", "model_version", "architecture", "artifact_sha256", "production_manifest", "frozen_commit", "frozen_tag",
        "state_version", "prediction_as_of", "readiness_state", "data_label", "validated_prediction_cutoff")},
        "wagering_enabled": False, "public_deployment_approved": False, "proposed_public_path": PC.PROPOSED_PUBLIC_PATH,
        "rows": int(len(b)), "artifacts": {f: _sha(out_dir / f) for f in FILES if f != "DEPLOYMENT_MANIFEST.json"}}
    (out_dir / "DEPLOYMENT_MANIFEST.json").write_text(json.dumps(man, indent=2, sort_keys=True, default=str) + "\n")
    sums = "".join(f"{_sha(out_dir / f)}  {f}\n" for f in sorted(FILES))
    (out_dir / "SHA256SUMS").write_text(sums)
    return {"dir": str(out_dir), "sha256sums_sha256": _sha(out_dir / "SHA256SUMS"), "manifest": man}


def verify(bundle_dir: Path, expect_artifacts: dict | None = None) -> dict:
    """NCAAF-style package verification. Raises BundleChecksumFailure on any violation."""
    d = Path(bundle_dir)
    errs = []
    present = sorted(p.name for p in d.iterdir())
    if sorted(ALL_FILES) != present:
        errs.append(f"files {present} != {sorted(ALL_FILES)}")
    for n in present:
        if not SAFE_NAME.match(n) or any(f in n.lower() for f in FORBIDDEN):
            errs.append(f"unsafe / forbidden name {n}")
    total = sum((d / n).stat().st_size for n in present if (d / n).is_file())
    if total > MAX_BYTES:
        errs.append(f"bundle {total} bytes > {MAX_BYTES}")
    if errs:
        raise BundleChecksumFailure("; ".join(errs))
    lines = [l.split("  ", 1) for l in (d / "SHA256SUMS").read_text().splitlines() if l.strip()]
    covered = sorted(n for _, n in lines)
    if covered != sorted(FILES):
        errs.append(f"SHA256SUMS covers {covered}")
    for h, n in lines:
        if _sha(d / n) != h:
            errs.append(f"checksum mismatch {n}")
    man = json.loads((d / "DEPLOYMENT_MANIFEST.json").read_text())
    for n, h in man.get("artifacts", {}).items():
        if _sha(d / n) != h:
            errs.append(f"manifest digest mismatch {n}")
    exp = expect_artifacts or {k: v[1] for k, v in PC.ARTIFACTS.items()}
    if man.get("artifact_sha256") != exp:
        errs.append("model artifact hashes differ from the frozen set")
    if man.get("readiness_state") not in ("BLOCKED", "SHADOW_ONLY"):
        errs.append(f"readiness {man.get('readiness_state')} above the SHADOW_ONLY ceiling")
    if man.get("wagering_enabled") is not False or man.get("public_deployment_approved") is not False:
        errs.append("wagering / public deployment flags must be false")
    if errs:
        raise BundleChecksumFailure("; ".join(errs))
    return {"BUNDLE_VALID": True, "files": present, "bytes": total, "sha256sums_sha256": _sha(d / "SHA256SUMS")}
