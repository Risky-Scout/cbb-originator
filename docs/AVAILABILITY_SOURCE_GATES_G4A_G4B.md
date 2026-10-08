# Availability source gates G4A / G4B

**Version:** 1.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE, **FROZEN** (Blueprint v1.4 §5, amendment B). It applies to
Sharp Stats (the implemented adapter, `cbb_platform/adapters/availability.py`, provider `sharp_stats`) and identically
to any other availability source (e.g. Covers, Amendment V2), each source separately. Capture procedure:
`docs/SHARP_STATS_CAPTURE_WORKFLOW.md` (unchanged). **No new provider is ingested by this task. No real snapshot has been
ingested to date.**

## G4A — model-integration entry (before availability may condition ANY model)

| # | requirement | how it is shown | status 2026-10-07 |
|---|---|---|---|
| 1 | ≥ 1 real authorized snapshot | `data/raw/sharp_stats/injuries/*` with `synthetic_fixture = false` | **OPEN** (none) |
| 2 | raw bytes preserved append-only | `raw_store.store`: never overwrites, files `0o444`, sha256 sidecar. Tested on synthetic fixtures (`tests/test_adapters.py::test_duplicate_snapshot_is_idempotent_and_changed_snapshot_appends`) | mechanism READY, to re-verify on the real file |
| 3 | exact `received_at` | raw sidecar `received_at` → `availability.events.received_at`; `--observed-at` kept for audit only | mechanism READY |
| 4 | raw status and note preserved | `status_raw`, `note_raw`, `injury_raw` verbatim; `status_normalized` only from the structured status; conflict flags | mechanism READY |
| 5 | team / player mapping measured | per snapshot: team map rate, player map rate by `identity_status`, unresolved list | **GAP**: no mapping report yet (a 10-line SQL report, to add with the first real snapshot) |
| 6 | schema accepted | the real page / export parses to ≥ 1 row per listed player with status present; parser version recorded | **OPEN**: the HTML parser is proven only on synthetic layouts |
| 7 | no target-outcome leakage | availability rows carry only `received_at`-timed statuses; no historical feature reads availability (Stage 3 never reads `availability.events`); synthetic rows never reach `flagship.availability_events`; future checkpoint features admit a row only if `received_at < cutoff` (PIT v2.0 §3.1) | READY for historical; to re-assert in the checkpoint store |

**Readiness verdict:** the workflow is ready to receive a real manually saved / exported snapshot. Items 1, 5 and 6 are
done on the first real capture. Known risk: player mapping uses each player's **most recent prior-season team**
(`_resolve_player`), so incoming freshmen and transfers stay `UNRESOLVED_NO_MATCH` until a dated current-season roster
source exists. Note: `cbb_platform/lockbox.py` refuses seasons *after* the lockbox, so CBBD 2026-27 rosters cannot
currently be fetched. That guard rule needs an architect ruling, because current-season reference data contains no
2025-26 outcomes. Without such a source G4B item 3 is at risk.

## G4B — shadow / production reliability (before shadow production, NOT before Phase C)

| # | requirement |
|---|---|
| 1 | ≥ 14 consecutive operating days **or** ≥ 250 material status rows, **whichever is larger** (both conditions, the larger span governs; "material" = status ∈ {OUT, OUT_SEASON, DOUBTFUL, QUESTIONABLE} or a change of status for a player in a projected rotation) |
| 2 | ≥ 99 % team mapping over the period |
| 3 | ≥ 97 % player mapping after documented review rules (the manual review log is part of the evidence) |
| 4 | snapshots append-only throughout (hash-verified) |
| 5 | update-to-affected-game inference rehearsal ≤ 60 s (status received → affected games re-priced end to end), measured p95 over the period |
| 6 | no structural retraining on the hot path (conditioning only) |

The historical challenger program (Phase C) does not wait for G4B. Historical models never consume availability. Only the
prospective conditioning layer does, after G4A.
