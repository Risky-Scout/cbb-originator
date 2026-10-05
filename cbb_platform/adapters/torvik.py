"""Bart Torvik -- NON-NETWORK interface skeleton (Stage 2S). P1 non-blocking enrichment.

No acquisition is performed or authorized in Stage 2: there is no fetch function here, and the provider is
deliberately absent from source_fetch callers. The lockbox guard already knows Torvik's season convention
(end year, `year=` parameter), so a future adapter inherits the 2025-26 refusal automatically.

A future, separately authorized adapter must:
  * fetch only through cbb_platform/source_fetch.get (lockbox-guarded), never per-player page scraping at scale;
  * store raw payloads immutably via raw_store;
  * map players into metadata.player_id_map under provider 'TORVIK' with match_method / match_confidence /
    needs_manual_review (never silently onto a CBBD athlete id);
  * keep Torvik ratings out of flagship.* until a PIT rule exists for them (they are revised retroactively).
"""
from __future__ import annotations

PROVIDER = "torvik"
STATUS = "SKELETON_NO_NETWORK"


def fetch(*_args, **_kwargs):
    raise NotImplementedError("Torvik acquisition is not authorized in Stage 2 (P1 enrichment, non-blocking).")
