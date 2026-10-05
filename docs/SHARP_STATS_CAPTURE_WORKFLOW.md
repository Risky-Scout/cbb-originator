# Sharp Stats prospective capture workflow (Stage 2Q)

Sharp Stats is the P0 **current-season** availability source. Historical (pre-2026) Sharp Stats data is
**not required** and is not sought. This workflow lets authorized snapshots accumulate prospectively without
any downstream schema change.

## Access rule

There is **no scraper and no automated access**. A person with legitimate access to Sharp Stats saves or
exports the injury page themselves (browser "Save Page As… → Web Page, HTML only", or the site's own export
if one is offered) and ingests that file. Nothing in this repository logs in to, crawls, or polls Sharp Stats.

## Capture (each time you look at the page)

1. Save the page / export. Accepted formats:
   * `.html`: a saved web page. Every `<table>` is read. The team is the nearest preceding heading or `<caption>`.
   * `.csv`: header `team, player, position, class, injury, status, updated, note` (case-insensitive).
2. Ingest it immediately (the ingest time is the authoritative point-in-time):

   ```bash
   .venv/bin/python -m cbb_platform.adapters.availability ingest sharp_stats ~/Downloads/sharp_stats_injuries.html \
       --observed-at 2026-11-04T21:00:00Z      # optional: when you SAW the page, if earlier than now
   ```

3. Rebuild the canonical database (`.venv/bin/python -m cbb_platform.build_canonical`) whenever events are needed.

## What is preserved

| field | where | rule |
|---|---|---|
| raw snapshot bytes | `data/raw/sharp_stats/injuries/<received_at>__<sha>.html` | immutable, read-only, never overwritten |
| `received_at_utc` | `availability.events.received_at` | our ingest time, authoritative PIT time |
| declared observation time | raw sidecar `params.observed_at_declared` | audit only |
| `source_updated_date` | `source_updated_raw` (verbatim); `source_updated_at` only when unambiguous | never invented |
| team / player / position / class | `source_team_name`, `source_player_name`, `source_position`, `source_class` | verbatim |
| `injury_raw`, `status_raw`, `note_raw` | same-named columns | verbatim |
| `status_normalized` | PROBABLE / QUESTIONABLE / DOUBTFUL / OUT / OUT_SEASON / UNKNOWN | from the structured status only; the note never overwrites it |
| conflict flags | `conflict_flag`, `conflict_type` | status vs note contradictions (documented rules) |
| identity | `canonical_team_id`, `canonical_player_id`, `identity_status`, `team_match_confidence`, `player_match_confidence` | exact matches only; a prior-season match is `RESOLVED_NEEDS_REVIEW_*` at 0.6 |

## Append-only guarantees (tested)

* Identical snapshot bytes are an idempotent no-op (same raw record, same events).
* A changed snapshot becomes a **new** raw file and **new** events. Older snapshots and their events remain.
* `availability_event_id = hash(provider, snapshot sha256, line number)`: events from different snapshots never collide.
* Synthetic fixtures (`tests/fixtures/SYNTHETIC_*`) are flagged `is_synthetic_fixture` and never reach `flagship.availability_events`.

## Status at the end of Stage 2

* Workflow and adapter: ready.
* Real snapshots ingested: **none** (no authorized capture has been supplied). The adapter is proven on the
  labeled synthetic fixtures only.
* The 2025-26 lockbox is unaffected: availability capture is a file ingest classified
  `ALLOWED_AVAILABILITY_CAPTURE` and is intended for 2026-27 onward.
