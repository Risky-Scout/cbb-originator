# CBBD backfill strategy (Stage 2B)

Rendered by `cbb_platform/stage2_reports.py` from `data/qa/cbbd_endpoint_equivalence.json` (bounded equivalence sample, `cbb_platform/event_equivalence.py`), the discovery results and the request log.

## Constraint that shapes everything

CBBD reports a per-account call quota in `X-CallLimit-Remaining` (124,943 when Stage 2 began). A per-game backfill of every family for 21 seasons (~5,800 games x 3 endpoints x 21) would need ~360k calls, so discovery comes first and bulk endpoints are used only where equivalence is proven. The client refuses to start a call when fewer than 20,000 calls remain (`QUOTA_RESERVE`).

## Endpoint chosen per family

| family | endpoint | seasons backfilled | why |
|---|---|---|---|
| PBP (+onFloor, shotInfo, participants) | `/plays/date` (UTC tip date and the next day) | 2006-2025 | event-for-event identical to `/plays/game` (below); ~2 calls per date instead of 1 per game |
| substitutions | `/substitutions/team` (every team in core.team_seasons) | 2013, 2024, 2025 | row-for-row identical to `/substitutions/game` restricted to the team; one call covers a team-season |
| lineups | `/lineups/game` | 2024, 2025 | `/lineups/team` has no gameId and its date filter returned no units: it loses game identity, so it is NOT equivalent |
| rosters | `/teams/roster?season=` (no team) | 2005-2025 | one call returns every team of a season |
| recruiting players / team rankings / portal | `/recruiting/players|teams|portal?year=` | class years 2004-2024 | one call per year; years whose target season is 2025-26 are excluded (lockbox) |
| venues | `/venues` | n/a | one call (reference data) |

Seasons were selected ONLY from measured discovery (`docs/CBBD_EVENT_COVERAGE_DISCOVERY.md`): PBP where the sample showed any PBP; substitutions/lineups where the game sample or the team-season escalation probe showed any rows. 2004-05 PBP was absent in 12 sampled games AND in 6 busy-date `/plays/date` probes (~635 games).

## Equivalence evidence (bounded sample)

| ET date | games | games equivalent | events per-game endpoint | events in /plays/date | duplicate event ids across UTC-date payloads | calls per-game vs date | bytes per-game vs date |
|---|---|---|---|---|---|---|---|
| 2008-01-11 | 8 | 8 | 1530 | 1530 | 0 | 8 vs 1 | 1,205,989 vs 13,069,968 |
| 2016-01-15 | 8 | 8 | 2755 | 2755 | 1683 | 8 vs 2 | 2,167,866 vs 48,187,029 |
| 2025-01-17 | 10 | 10 | 3230 | 3230 | 957 | 10 vs 2 | 4,343,008 vs 86,445,289 |

Busy date 2025-02-08: 155 ET games; /plays/date returned 154 of them (57,335 events, payload rows {'2025-02-08': 43783, '2025-02-09': 13552}) with no truncation; 5/5 spot-checked games identical to /plays/game.

`/plays/team` ({'season': 2025, 'team': 'Purdue'}): 35 of 36 games, 3/3 compared games identical. Equivalent but ~2x the payload bytes of /plays/date (each game arrives once per team), so not chosen.

`/substitutions/team` vs `/substitutions/game`: game 14: 28 team rows = 28 game rows for the team, identical=True; game 300: 33 team rows = 33 game rows for the team, identical=True; game 471: 41 team rows = 41 game rows for the team, identical=True. The team endpoint carries only the requested team's athletes, so EVERY team (including non-D-I opponents) is requested.

`/lineups/team`: payload has gameId = False; date-bounded calls vs game units: game 14: 0 vs 0; game 300: 0 vs 16; game 471: 0 vs 20.

## Deduplication keys

* plays: `(gameId, play id)`. `/plays/date` returns a whole game under BOTH adjacent UTC dates (measured), so copies are expected. Copies are compared by a content hash of every field; differing copies would be quarantined as `CONFLICTING_DUPLICATE_EVENT` (none may be silently merged). Preference: /plays/date > /plays/game > /plays/team, then file name.
* substitutions: content hash of `(gameId, teamId, athleteId, subIn, subOut)`; the game and team endpoints overlap for sample games.
* lineups: `(gameId, teamId, idHash)` from `/lineups/game` only.
* rosters: `(athleteId, teamId, season)`; recruits: recruit id; portal: transfer id; venues: venue id.

## Retry, rate-limit and checkpoint strategy (`cbb_platform/cbbd_client.py`)

* HTTP 429 -> honour `Retry-After` when supplied, otherwise exponential backoff; every worker pauses.
* HTTP 5xx and network timeouts -> bounded exponential backoff (5 retries, 2s..120s). Other 4xx -> permanent failure.
* Every failed attempt and every final outcome is an append-only line in `data/manifests/cbbd_request_log.jsonl`; `data/manifests/cbbd_backfill_manifest.json` is compiled from it. No header or credential is ever recorded.
* Checkpoint = a request key (endpoint + exact params) with a stored payload. Restarting skips it; a failed request is retried on the next run and is never interpreted as absent data.
* Raw payloads are written atomically (temp file + rename), gzip-compressed deterministically for large families, read-only, and hashed over the original response bytes.
* Concurrency was raised deliberately: 2 workers for discovery (0 retries in 803 calls) -> 3 -> 4 workers (0 429s in ~2,000 calls) -> a 6-worker trial produced 429s and was reverted to 4 workers / 0.2 s spacing.

## Measured API activity (request log)

```json
{
 "requests": 18539,
 "http_calls": 18587,
 "successful_calls": 18539,
 "empty_successful": 1866,
 "retries": 48,
 "logged_429": 39,
 "logged_5xx": 0,
 "logged_network_errors": 1,
 "permanent_failures": 0,
 "first_request_utc": "2026-10-05T18:33:28.699118+00:00",
 "last_request_utc": "2026-10-05T22:25:35.337121+00:00",
 "bytes_received": 32571493370,
 "requests_by_endpoint": {
  "lineups/game": 12769,
  "lineups/team": 79,
  "plays/date": 2973,
  "plays/game": 293,
  "plays/team": 1,
  "recruiting/players": 21,
  "recruiting/portal": 21,
  "recruiting/teams": 21,
  "substitutions/game": 262,
  "substitutions/team": 2077,
  "teams/roster": 21,
  "venues": 1
 },
 "final_quota_remaining": 107127
}
```

## Known limitations

* `/plays/date` coverage of a game can only be proven for dates requested; games with no CBBD PBP are reported as `games_tested_without_pbp`, never as failures.
* `/lineups/game` is one call per game; it was therefore restricted to the seasons where lineups exist (2023-24, 2024-25).
* Substitutions for non-D-I opponents exist only if CBBD tracks that team (team endpoint requested for every team-season appearing in games).
* The quota is account-wide; the reserve protects other use of the same account.
