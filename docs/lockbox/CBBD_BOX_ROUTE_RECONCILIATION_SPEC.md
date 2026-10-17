# CBBD box-score route: development reconciliation specification (declared BEFORE evaluation)

**Version:** 1.0 · **Date:** 2026-10-13 · **Status:** FROZEN before any route payload was fetched or compared.
**Scope:** validates `cbb_platform/cbbd_box_route.py` (lockbox data plan §2 route (a)) on the development seasons
**2023-24 and 2024-25 only** (canonical 2024, 2025). No 2025-26 request, file or value is involved.

## 1. What is compared

* **Reference build (dev):** the accepted development dataset `cbb_data_v2.4+5f19d31e524a`
  (`data/canonical/cbb.duckdb`), built from the Starter Pack, and its Stage 3 stores (`modeling/store/`).
* **Route build:** the unchanged `build_canonical.build` with the Starter Pack rows of 2024 and 2025 **replaced** by
  CBBD payloads reshaped by the route, written to a scratch directory; then the unchanged Stage 3 build
  (`stage3.build.build(root=<scratch>, canonical=<route db>, with_reports=False)`).
* Every other input (Starter Pack seasons 2005-2023, the CBBD event / roster / recruiting / portal raw store) is the
  same file set for both builds.

## 2. Differences that exist by construction (declared; excluded from content comparison, counted)

| id | difference | why |
|---|---|---|
| D1 | `fact.player_games.player_game_key`, `source_record_key`, `source_file`; the `UNRESOLVED:<player_game_key>` participation placeholder ids | the Starter Pack player-game `athleteId` is a per-record id; the API gives the persistent athleteId (`docs/CBBD_PLAYER_IDENTITY_VERIFICATION.md`). Canonical player identity is resolved by the frozen (season, team, exact name) rule in both builds |
| D2 | lineage columns (`source_file`, `source_record_key`) of every route-season row; `metadata.*` and `qa.exceptions` text | the route names the raw CBBD payload instead of a Starter Pack CSV |
| D3 | `core.venues.venue_country` | not supplied by the CBBD `games` endpoint |
| D4 | `fact.player_season_shooting`, `fact.team_season_shooting` rows of 2024 and 2025 absent | the season-shooting families are not part of the route; no v1 input reads them |

## 3. Comparison keys

| table / store | key | compared columns |
|---|---|---|
| E1 `core.games` (season 2024, 2025) | `canonical_game_id` | season, season_type, scheduled_tip_utc, tip_time_quality, game_date_et, neutral_site, home/away team ids, home/away points, period points, overtime count, game universe, team-box and player-box presence |
| E2 `fact.team_games` | (`canonical_game_id`, `canonical_team_id`) | every box column |
| E3 `fact.player_games` | (`canonical_game_id`, `canonical_team_id`, `source_player_name`) | canonical_player_id, identity_status, starter, minutes, every count, usage, accounting_issues |
| E4 `fact.participation` (resolved players) | (`canonical_game_id`, `canonical_team_id`, `canonical_player_id`) | status, evidence, source, starter, minutes |
| E5 `flagship.played_games` | `canonical_game_id` | membership |
| E6 Stage 3 `lab.player_game_labels`, `feat.player_features` (v1-consumed columns), `ex.player_game_examples` | `player_game_example_id` | every label column; every column the frozen v1 artifact reads |
| E7 all seasons 2014-2023 in the Stage 3 stores | per-table content fingerprint restricted to those seasons | must be **identical** (point-in-time: later seasons cannot change earlier features or labels) |
| E8 end to end | the fit-free lockbox runner on 2024-25 against each Stage 3 store, same evaluation artifact | every scorecard metric and every G1-G6 verdict |

## 4. Classification of any remaining difference

* **EXACT keys required:** the E1 game-id set and the E2 (game, team) key set must be identical. Canonical ids are
  deterministic hashes of CBBD ids, so any difference is a route defect.
* **PROVIDER_REVISION:** a row present in both builds whose value differs, where the route value equals the value in the
  stored raw CBBD payload when that payload is read **independently of the route code**. Listed individually.
* **PROVIDER_COVERAGE_REVISION:** a natural key present in one build only, likewise confirmed against the raw payload.
  Listed individually.
* **UNEXPLAINED:** anything else. **Any UNEXPLAINED difference fails the route.**

## 5. Pass criteria (all required)

1. E1 and E2 key sets identical; no duplicate key in any route table.
2. Zero UNEXPLAINED differences.
3. For every compared table and column, PROVIDER_REVISION + PROVIDER_COVERAGE_REVISION rows ≤ **0.1 %** of that table's
   route-season rows. (A mapping defect is systematic and exceeds this; sparse source corrections do not.)
4. E7 identical.
5. E8: every G1-G6 verdict identical; every gated metric differs by at most **1e-3** absolute (exactly 0 if E3/E6
   show no difference).
6. The route refuses 2025-26 under the current (closed) guard, before any network call.

## 6. Determinism

The route staging rows are sorted and de-duplicated by key; two different records under one key fail closed. Two route
staging passes over the same payloads must produce byte-identical staging rows (sha256 recorded).
