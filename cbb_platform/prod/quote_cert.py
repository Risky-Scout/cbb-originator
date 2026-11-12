"""Quote-integration certification gate (The Odds API NCAAB player props). Persistent after a PASS; closed again only
by a MATERIAL invalidation. No network, no model code: this module only reads / appends the ops markers.

    markers/quote_certifications.jsonl               append-only: one line per certification PASS (the receipt)
    markers/QUOTE_INTEGRATION_VALIDATED.json         write-once evidence of the FIRST PASS (kept; never rewritten)
    markers/quote_certification_invalidations.jsonl  append-only: one line per material invalidation event

CERTIFIED  <=>  the latest PASS was made by the CURRENT integration (its fingerprint equals fingerprint()) and no
material invalidation was recorded after it. There is no daily re-certification and no expiry.

Material invalidation (MATERIAL_REASONS):
    INTEGRATION_CHANGED       fingerprint() differs from the certified one: provider endpoint / sport key / the 12
                              market keys / fixed request parameters, or the source of the quote normalizer
                              (prod/quotes.py), the strict matcher (prod/matching.py) or the pricing
                              (prod/pricing.py, props_v1_pricing.py) changed. Detected automatically (no line written).
    PROVIDER_SCHEMA_CHANGE    a per-event response failed the response guard (malformed / different event / not an
                              object): odds_acquire status REFUSED_RESPONSE_GUARD
    CREDENTIAL_FAILURE        the provider refused the key (HTTP 401 / 403) on a per-event endpoint
    REPEATED_MALFORMED_PAYLOADS / SYSTEMATIC_MATCH_FAILURE / CRITICAL_PRICING_INVARIANT / MANUAL
                              architect action (invalidate(), CLI `shadow_tick --invalidate-certification`)
One rejected sportsbook quote never invalidates the integration: the quote is excluded with its reason instead.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from cbb_platform.prod import ops_store as OS

CERT_LOG = "quote_certifications.jsonl"
FIRST_PASS = "QUOTE_INTEGRATION_VALIDATED.json"
INVALIDATIONS = "quote_certification_invalidations.jsonl"
CERT_VERSION = "cbb-prod-quote-certification-2.0.0"
MATERIAL_REASONS = ("PROVIDER_SCHEMA_CHANGE", "CREDENTIAL_FAILURE", "REPEATED_MALFORMED_PAYLOADS",
                    "SYSTEMATIC_MATCH_FAILURE", "CRITICAL_PRICING_INVARIANT", "MANUAL")
_PROD = Path(__file__).resolve().parent
FINGERPRINT_SOURCES = (_PROD / "quotes.py", _PROD / "matching.py", _PROD / "pricing.py",
                       _PROD.parent / "props_v1_pricing.py")


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def fingerprint_parts() -> dict:
    from cbb_platform.prod import odds_acquire as OA, quotes as QT     # late: keep this module import-light
    return {"provider": QT.PROVIDER, "base": OA.BASE, "sport": QT.SPORT_KEY, "paths": OA.PATHS, "fixed": OA.FIXED,
            "markets": list(QT.REQUEST_MARKETS), "key_to_stat": dict(sorted(QT.KEY_TO_STAT.items())),
            "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in FINGERPRINT_SOURCES}}


def fingerprint() -> str:
    return hashlib.sha256(json.dumps(fingerprint_parts(), sort_keys=True).encode()).hexdigest()


def certifications(markers: Path) -> list[dict]:
    return [r for r in OS.read_jsonl(Path(markers) / CERT_LOG) if r.get("status") == "PASS"]


def invalidations(markers: Path) -> list[dict]:
    return OS.read_jsonl(Path(markers) / INVALIDATIONS)


def state(markers: Path, now=None, fp: str | None = None) -> dict:
    """{"certified", "certification" (latest PASS), "reason" (why not certified), "fingerprint", ...}."""
    fp = fp or fingerprint()
    certs = certifications(markers)
    if now is not None:
        certs = [c for c in certs if _utc(c["validated_at"]) <= _utc(now)]
    out = {"fingerprint": fp, "certifications": len(certs), "certification": None, "certified": False,
           "invalidated_by": None, "reason": "NOT_CERTIFIED"}
    if not certs:
        return out
    c = max(certs, key=lambda r: _utc(r["validated_at"]))
    out["certification"] = c
    inv = [i for i in invalidations(markers) if _utc(i["invalidated_at"]) > _utc(c["validated_at"])
           and (now is None or _utc(i["invalidated_at"]) <= _utc(now))]
    if inv:
        out.update(invalidated_by=inv[-1], reason=f"INVALIDATED_{inv[-1]['reason']}")
    elif c.get("integration_fingerprint") != fp:
        out.update(reason="INVALIDATED_INTEGRATION_CHANGED")
    else:
        out.update(certified=True, reason="CERTIFIED")
    return out


def certify(markers: Path, receipt: dict) -> dict:
    """Persist one PASS. The first PASS also writes the write-once QUOTE_INTEGRATION_VALIDATED.json."""
    if receipt.get("status") != "PASS":
        raise ValueError("only a PASS receipt certifies the integration")
    rec = {"QUOTE_INTEGRATION_VALIDATED": True, "certification_version": CERT_VERSION,
           "integration_fingerprint": fingerprint(), **receipt}
    OS.append_jsonl(Path(markers) / CERT_LOG, [rec])
    first = Path(markers) / FIRST_PASS
    if not first.exists():
        OS.write_once(first, rec)
    return rec


def invalidate(markers: Path, reason: str, detail: str, now, evidence: dict | None = None) -> dict | None:
    """Append one material invalidation (only when currently certified: nothing to close otherwise)."""
    if reason not in MATERIAL_REASONS:
        raise ValueError(f"not a material invalidation reason: {reason}")
    if not state(markers, now)["certified"]:
        return None
    row = {"invalidated_at": _utc(now).isoformat(), "reason": reason, "detail": str(detail)[:500],
           "evidence": evidence or {}}
    OS.append_jsonl(Path(markers) / INVALIDATIONS, [row])
    return row


def material_failure(entries: list[dict]) -> tuple[str, dict] | None:
    """An Odds API session's ledger entries -> (reason, evidence) of the first material provider failure, else None.
    Only per-event endpoints count; budget / quota refusals and transient 5xx / network errors are not material."""
    for e in entries:
        if e.get("endpoint") not in ("event_markets", "event_odds"):
            continue
        if e.get("status") == "REFUSED_RESPONSE_GUARD":
            return "PROVIDER_SCHEMA_CHANGE", {k: e.get(k) for k in ("endpoint", "requested_at", "error", "http_status")}
        if e.get("status") == "FAILED" and e.get("http_status") in (401, 403):
            return "CREDENTIAL_FAILURE", {k: e.get(k) for k in ("endpoint", "requested_at", "http_status")}
    return None


def check_session(markers: Path, entries: list[dict], now) -> dict | None:
    """Record a material invalidation when this session hit one (no-op when not certified)."""
    hit = material_failure(entries)
    if hit is None:
        return None
    return invalidate(markers, hit[0], f"detected automatically in an Odds API session: {hit[1]}", now, hit[1])
