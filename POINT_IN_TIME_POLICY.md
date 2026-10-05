# POINT_IN_TIME_POLICY.md — CBB Originator information-timing contract

**Version:** 1.2 (Stage 1 final unblock; supersedes 1.1) · **Scope:** pregame originator · **Companion:** `CANONICAL_SCHEMA.md`

The platform keeps one immutable, continuous point-in-time history. Five formal pregame states are materialized from it, and each may contain **only information that was legitimately observable at its cutoff**. Later information is never backfilled into an earlier state.

## 1. Timestamp vocabulary (mechanical definitions)

| Field | Meaning | Who sets it | Unknown → |
|---|---|---|---|
| `event_time` | When the basketball event happened. For game-level facts: the game's scheduled tip (`core.games.scheduled_tip_utc`). For PBP: game clock (`period`, `secondsRemaining`) within the game. **CBBD `wallclock` is NOT UTC**: measured in the live smoke, it is US/Eastern wall time stamped with a `Z` (first play −4.96 h and −4.91 h before the UTC tip in both sampled games). It must not be used as a UTC instant until a timezone rule is validated | source | NULL |
| `source_updated_at` | When the provider says it last changed the record (BDL `updated_at`; a Sharp Stats update time **only if** it is an unambiguous timestamp) | source | NULL |
| `effective_at` | When the fact became true in the real world (e.g. a player ruled out yesterday) | source, if stated | NULL |
| `received_at` (= `observed_at`) | When **our** system received the payload. It is the moment it entered the immutable raw store, or for the Starter Pack its one-time registration (`data/manifests/starter_pack_registration.json`) | our pipeline | never NULL |
| `processed_at` (= `ingested_at`) | When the canonical build produced the row | our pipeline | never NULL |
| `historical_knowable_at` | For **retrospective** data only: the earliest time the fact is treated as having been knowable, assigned by the HR rules below | our pipeline | never NULL on facts |
| `prediction_cutoff` | The instant a checkpoint's information set closes (§4) | feature store (Stage 5) | — |
| `feature_as_of_time` | The latest knowable time of any input used in a feature. Must be `< prediction_cutoff` | feature store (Stage 5) | — |

Unknown timestamps stay NULL. **No historical timestamp is ever invented.** User-declared capture times (`--observed-at`) are kept in raw metadata for audit; they are never the PIT time.

## 2. The legality rule

A record **R** may enter a prediction whose cutoff is **c** only if

```
knowable(R) < c
knowable(R) = received_at(R)                          for prospectively captured data (BDL, CBBD API, Sharp Stats)
            = historical_knowable_at(R)               for retrospective reconstruction (Starter Pack, backfills)
```

and every derived feature carries `feature_as_of_time = max(knowable(inputs)) < c`.

## 3. Historical reconstruction rules (retrospective data)

The Starter Pack was received in 2026, after every game it describes. Historical backtests therefore use these explicit, conservative reconstruction rules instead of pretending we received it then:

- **HR-1 Game outcomes.** A completed game's official box score (`fact.player_games`, `fact.team_games`, `fact.participation`) and its final score become knowable at:
  - `scheduled_tip_utc + 4 hours` when `tip_time_quality = EXACT`;
  - 06:00 US/Eastern on the day after the game's Eastern date when the tip time is `DATE_ONLY` or `TBD_FLAG`.
- **HR-2 Target-game exclusion.** No outcome of the target game may be used for that game, at any checkpoint. HR-1 already places every outcome after tip; feature code must additionally exclude the target `canonical_game_id`.
- **HR-3 Season aggregates (corrected, Stage 1 closure).** Two different objects, two rules:
  - **Completed-season aggregate files** (`fact.player_season_stats`, `fact.player_season_shooting`, `fact.team_season_stats`, `fact.team_season_shooting`, `fact.team_season_game_derived`) describe a whole season. They are **illegal features for any prediction earlier in that same season**, at every checkpoint. They are legal only for a target in a **later** season, and only once their `historical_knowable_at` (= the season's last HR-1 time) is before the cutoff (`cbb_platform/pit.py::completed_season_aggregate_is_legal`).
  - **Season-to-date aggregates MAY be reconstructed at any historical prediction cutoff**, using only games/events available strictly before that cutoff: `historical_knowable_at < cutoff`, the target game excluded, and every game at or after the target's tip excluded, whatever the cutoff (`cbb_platform/pit.py::team_games_as_of` / `team_season_to_date`). They are derived from canonical game-level records. CBBD date-bounded endpoints were tested in the Stage 1 final unblock and classified **`VALIDATED_FOR_TESTED_FIELDS`** (`docs/CBBD_LIVE_SMOKE_RESULTS.md`). `/stats/player/season` and `/stats/team/season` include exactly the games with `scheduled_tip_utc ≤ endDateRange 00:00:00Z`. Three windows reconciled 33/33 player rows and 3/3 team rows on all tested primitives, and every competing reading failed; `/games` behaved the same way. A historical date-bounded request is legal for cutoff `c` only if every game it can include is knowable before `c`, i.e. `endDateRange 00:00Z + 4 h < c` (HR-1). Prospectively, its `received_at` must also be before `c`. The endpoint returns today's corrected values (HR-5). This validation covers the tested fields only and is a secondary path: canonical game-level derivation remains authoritative.
  - Game-level tables are the authoritative basis for these reconstructions, for teams and for players. Raw season summaries are secondary, QA, or prior sources, and game-derived values win on conflict wherever the field is reconstructable (`docs/SEASON_STATISTICS_AUTHORITY.md` §1 and §5).
- **HR-4 Non-date-bounded metrics** (CBBD `/ratings/*`, `/stats/team/leaderboard`, team-season overview, or any "today's final value" of a past season) are **prohibited** in historical states unless an archived snapshot with a genuine `received_at` before the cutoff exists. `GameInfo.homeTeamEloStart`/`awayTeamEloStart` may be used only after the coverage audit validates them as pregame values.
- **HR-5 Stat corrections.** The Starter Pack holds the final official record (corrections already applied, untimestamped). Canonical realized outcomes are, by policy, final official statistics, so this is accepted for **targets**. As **features**, the residual risk that a post-game correction was not yet public at the cutoff is documented and not modelled. Prospectively, each received version is kept and the version with `received_at < c` is used.
- **HR-6 Schedule.** Starter Pack schedules are a single **retrospective** version (`core.game_schedule_versions.is_retrospective = TRUE`, `observed_at = NULL`). Historical checkpoints anchor to that final scheduled tip, a documented limitation because reschedules are invisible. Prospectively, every schedule observation is a new version, and a checkpoint anchors to the **latest version with `observed_at` ≤ the checkpoint's evaluation time**. Earlier authentic snapshots are never rewritten after a reschedule.
- **HR-7 Rosters / availability.** No historical roster or injury state may be inferred from later information. Historical participation (who actually played) is an **outcome** (HR-1), never a pregame input for its own game.

## 4. The five checkpoints

```
anchor_tip  = scheduled tip known at the checkpoint (HR-6)
              -- DATE_ONLY/TBD games: 00:00 US/Eastern of the game date (conservative earliest possible tip)
cutoff(T-24h) = anchor_tip - 24h      cutoff(T-6h) = anchor_tip - 6h      cutoff(T-1h) = anchor_tip - 1h
cutoff(T-10m) = anchor_tip - 10m      cutoff(final pre-tip) = min(anchor_tip, actual tip if observed live)
```

| Information | Allowed in a checkpoint when |
|---|---|
| Other games' outcomes, box scores, participation | `knowable < cutoff` (HR-1), target game excluded (HR-2) |
| Completed-season aggregates | prior seasons only, and knowable before the cutoff (HR-3). Never the target's own season |
| Season-to-date aggregates | rebuilt from game-level rows with `historical_knowable_at < cutoff`, target game and all games at/after its tip excluded (HR-3, `pit.py`) |
| CBBD date-bounded aggregates | validated semantics: includes games with tip ≤ `endDateRange` 00:00Z; legal only if `endDateRange` 00:00Z + 4 h < cutoff, **and** received before the cutoff when captured prospectively |
| Rosters, recruiting, transfers | version received/valid before the cutoff; recruiting/transfer *season-level* timing only if the season boundary precedes the cutoff |
| Availability (Sharp Stats, provider `sharp_stats`) | `received_at < cutoff`. Per player, use the latest received event. Its raw status, raw note and `conflict_flag` travel together; `status_normalized` is never overridden by the note |
| Schedule / tip time | the schedule version observed by the checkpoint (HR-6) |
| Sportsbook prices (BDL odds/opening, CBBD lines) | **never** a flagship input. Benchmark comparison aligns the market snapshot by `received_at`/`source_updated_at` at the same checkpoint, in the benchmark namespace only |

All five checkpoints of one game belong to the same validation fold. Structural parameters may be shared across checkpoints; availability, minutes, rotation and lineup uncertainty evolve as tip approaches.

## 5. Explicit prohibitions

1. Using the target game's outcome, box score, participation, or anything derived from them.
2. Using a completed-season aggregate for any game of that same season (HR-3). (Season-to-date aggregates rebuilt as-of the cutoff are allowed.)
3. Using future roster states, future availability statuses, or future schedule changes.
4. Using closing (or any) market information as a flagship basketball feature.
5. Querying a finalized historical metric today and treating it as historically known without legitimate PIT reconstruction (HR-4).
6. Using a box-score file's `start_date`/`startDate` offset stamps as event times. They are measured to be 4–5 h late (CANONICAL_SCHEMA §9).
7. Letting a synthetic test fixture reach any flagship view (`is_synthetic_fixture` filter).
8. Touching the 2025-26 lockbox in any way listed in `LOCKBOX_POLICY.md`. Every live provider call passes `cbb_platform/lockbox.py` first. Stage 1 neither fetched nor ingested any 2025-26 data.

## 6. Answering "why was this record legal for the T-1h prediction?"

For a target game with `anchor_tip = 2025-01-15 00:00Z`, the T-1h cutoff is `2025-01-14 23:00Z`. Purdue's game `G-c7ea584e0f911a7c` (tip `2024-11-04 23:00Z`, quality `EXACT`) has `historical_knowable_at = 2024-11-05 03:00Z` < cutoff, so its box rows are legal. Purdue's completed-season `fact.player_season_stats` row for 2025 describes the target's own season, so it is illegal for that game at every checkpoint. Its 2024 row is legal. A Purdue 2024-25 season-to-date total *is* legal if `pit.team_season_to_date` rebuilds it from team-games knowable before the cutoff and tipped before the target. A Sharp Stats event is legal only if its `received_at` < `2025-01-14 23:00Z`. The answer is always read from these columns, not from memory.

## 7. Enforcement

- **Stage 1 (now):**
  - Every canonical fact row carries `event_time`, `historical_knowable_at` and `received_at`/`processed_at`.
  - `tests/test_canonical_qa.py::test_pit_timestamps_valid` proves:
    - no outcome is knowable at or before its tip;
    - every completed-season aggregate is knowable no earlier than its season's last game;
    - `processed_at ≥ received_at`.
  - `tests/test_stage1_closure.py` proves that an as-of aggregate never contains the target game or any later game, even with a mis-set cutoff, on a fixed example and a 150-game sample; that as-of totals equal an independent sum; and that a completed-season aggregate is illegal for its own season.
  - `test_schedule_versions_are_marked_retrospective` proves Starter Pack schedules cannot pose as authentic snapshots. `test_tip_times_match_independent_bdl_times` guards the tip-time interpretation.
- **Stage 5 (feature store) must add:**
  - an automated leakage test asserting `feature_as_of_time < prediction_cutoff` for every feature row;
  - target-game exclusion;
  - a check that no flagship feature reads outside `flagship.*`.
