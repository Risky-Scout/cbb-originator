# CBB production footprint minimization audit — 2026-11-04

Scope: a read-only audit of what the two production paths read from the large historical stores. Branch
`production/cbb-daily-walk-forward-2026-27` @ `6cb7daa`. Frozen architecture: JM1_FINAL.

What this audit did **not** do:
- no model rebuild, refit research or recalibration;
- no change to any production reader;
- no change to `deploy/droplet/STATE_TRANSFER_MANIFEST.json`;
- no transfer and no Droplet access;
- no deletion. **The original stores were NOT deleted.** Every store listed below is still on the Mac, byte-identical.

**Decision: `MINIMIZATION_NOT_PROVEN`.** Tracing shows production consumes only a small subset of every large store.
Only the scoring path's reduction is proven by an executed equivalence. The walk-forward reduction is not proven.

## 1. Current manifest footprint

Totals:
- **`CURRENT_MANIFEST_REQUIRED_BYTES` = 12,250,062,927.** These are the REQUIRED entries with a byte count: the five
  historical stores plus the ops working set (13,373,440 B, sized at the 2026-11-01 asset).
- **`CURRENT_REQUIRED_HISTORICAL_BYTES` = 12,236,689,487.** This equals the manifest's `required_untracked_bytes`.

Not counted:
- Git-cloned code, including the frozen JSON artifacts and the golden fixture.
- The recreated Python environment.
- The empty walk-forward store.
- The ops evidence branch, which is cloned by git.
- `history_extract`, which the manifest marks OPTIONAL.
- Every entry marked NOT_REQUIRED.

| Manifest entry | Source | Bytes | sha256 (measured = manifest) | Required | Production reader | Classification |
|---|---|---|---|---|---|---|
| history_canonical_lockbox | data/lockbox_2025_26/canonical/cbb.duckdb | 9,455,546,368 | c4a0e3a1…80c1 | REQUIRED | prod/features.compact_canonical (scoring + walk-forward), cbbd_feed seed/completed_games, sync, run._names | RUNTIME_SUBSET_ONLY |
| stage3_features | data/lockbox_2025_26/stage3/features.duckdb | 2,436,116,480 | 38e89053…a1cc | REQUIRED | walk-forward only: marginal.load_frames / load_participation, structural.load_team_frame, phase3.season_inputs, store.opening_through_date, daily.history_identity (size check) | RUNTIME_SUBSET_ONLY |
| stage3_labels | data/lockbox_2025_26/stage3/labels.duckdb | 236,990,464 | 86c77e3c…651f | REQUIRED | walk-forward only (same loaders) | RUNTIME_SUBSET_ONLY |
| oos_phase1b | modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet | 80,090,263 | d0859e48…e425 | REQUIRED | walk-forward phase3.load_store (F2024–F2026), gates integrity (F2026) | RUNTIME_SUBSET_ONLY (all rows, 46/56 columns) |
| oos_phase1b_F2023 | modeling/v2_oos/oos_forecasts_phase1b_v1ref_F2023.parquet | 27,945,912 | aeb1d115…84cc | REQUIRED | walk-forward phase3.load_store (F2023) | RUNTIME_SUBSET_ONLY (all rows, 46/56 columns) |
| ops_working_set | private release (encrypted) | 13,373,440 | per asset | REQUIRED | every tick | RUNTIME_INTRINSIC (not historical) |
| history_extract | data/prod/history/history_extract.duckdb | 126,627,840 | 0338b32d…ca71 | OPTIONAL | prod/history_extract.resolve (used for scoring when the canonical is absent) | DUPLICATE_REPRESENTATION (verified subset of the canonical) |
| walkforward_store | CBB_WF_ROOT | 0 | — | REQUIRED | walk-forward | RUNTIME_INTRINSIC (created on first use) |
| lockbox_canonical_parquet | data/lockbox_2025_26/canonical/parquet/ | 1,530,042,617 | — | NOT_REQUIRED | none | NOT_REQUIRED, **`CANONICAL_PARQUET_MIGRATION_STATUS = EXCLUDE`** |
| research_canonical, research_stage3_store, starter_pack, protected_qa_file, rotowire_event_store | — | — | — | NOT_REQUIRED | none | NOT_REQUIRED / RESEARCH_ONLY |

Other untracked stores over 25 MB that neither path reads:

| Store | Bytes | Classification |
|---|---|---|
| modeling/v2_oos/oos_forecasts_phase1.parquet (read only by research `v2/phase1b.run`) | 80,089,121 | RESEARCH_ONLY |
| data/canonical/cbb.duckdb | 9,342,300,160 | RESEARCH_ONLY |
| modeling/store/features.duckdb | 2,272,538,624 | RESEARCH_ONLY |
| modeling/store/labels.duckdb | 218,116,096 | RESEARCH_ONLY |
| data/qa/large/** | — | RESEARCH_ONLY |
| data/prod/smoke/runs/*/features/*.duckdb (per-run scratch) | — | NOT_REQUIRED |

## 2. Path A — scoring (`prod.shadow_tick.tick` → `prod.run.daily`)

`shadow_tick` resolves `history_db = history_extract.resolve()`. That returns the canonical if it is present, otherwise
the extract, and only when the extract's sha256 equals the manifest. All of the scoring path's historical content
reads go through `prod/features.compact_canonical` (features.py:93-154):

| Relation | Rows / filter | Columns |
|---|---|---|
| flagship.played_games | seasons 2014–2026: 76,886 rows | all |
| flagship.team_game_outcomes | seasons 2014–2026: 153,597 rows, masked `historical_knowable_at <= as_of` | all |
| flagship.player_game_outcomes | seasons 2014–2026: 1,555,270 rows, same mask | all |
| flagship.rosters | all rows: 208,281 | all |
| core.transfers | all rows: 5,102 | all |
| core.recruits | all rows: 10,701 | all |
| core.team_seasons | all rows: 27,427 | all |
| core.teams | all rows: 1,535 | all |
| core.players | all rows: 94,034 | all |
| metadata.identity_link_quarantine | all rows: 985 | all |
| flagship.substitutions | `season = 2027`: 0 rows | all |
| flagship.plays, shots_trusted, on_floor_trusted | schema only (`LIMIT 0`) | — |

Other history reads on this path:
- `cbbd_feed` seeds core.players, metadata.player_id_map (`provider='CBBD'`, 94,033 rows) and rosters (season 2026).
- `sync` reads core.teams.
- `run._names` reads core.teams and core.players.

The scoring path never reads Stage 3 or OOS.

## 3. Path B — daily walk-forward (`walkforward.daily morning`)

| Step | Store / relation | Rows | Columns |
|---|---|---|---|
| `history_identity` (daily.py:89-98) | the 4 frozen data_versions stores | — | **file size check only** (exact byte sizes) |
| training rows + labels (TR.feature_rows / label_rows) | canonical, through compact_canonical | the same relations and filters as Path A | as Path A |
| `marginal.load_frames(2014–2026)` | feat.player_features ⋈ lab.player_game_labels | 1,384,635 (PLAYED ∧ box_row_present ∧ minutes_label_available) | 46 feature + 12 label columns |
| same | feat.player_box_state | 1,480,479 scanned → 63,869 finals | 15 columns |
| `marginal.load_participation(2024–2026)` | player_features ⋈ ex.player_game_examples ⋈ labels | 774,511 | 15 numeric + 4 evidence + participation_status |
| `structural.load_team_frame` | feat.team_features ⋈ lab.team_game_labels | 141,600 | 15 + 3 |
| `phase3.load_store(2023–2026)` | the two OOS parquets | all 774,511 + 273,032 rows | 46 STORE_COLS of 56 |
| gates integrity | phase1b F2026 and team_features 2026 | first 3 games | as above |
| `store.opening_through_date` | ex.game_examples | 1 value (max game_date_et, season 2026) | game_date_et |

`morning()` and `predict()` use `PC.HISTORY_CANONICAL` directly (daily.py:285). Unlike scoring, they do not call
`history_extract.resolve`.

## 4. Answers

| Question | Answer | Consumed subset |
|---|---|---|
| FULL_CANONICAL_REQUIRED_FOR_SCORING | **FALSE** | exactly `history_extract.RELATIONS`: 2,227,851 rows, 126,627,840 B |
| FULL_CANONICAL_REQUIRED_FOR_WALKFORWARD | **FALSE** (content) | same subset; walk-forward is hard-wired to the canonical path |
| FULL_STAGE3_FEATURE_STORE_REQUIRED | **FALSE** | 4 of 28 tables, as listed in §3 (≈205,757,647 B as zstd parquet, measured) |
| FULL_STAGE3_LABEL_STORE_REQUIRED | **FALSE** | lab.player_game_labels (12 columns) and lab.team_game_labels (3 columns) (≈46,278,621 B) |
| OOS stores | keep as is | every row and 46 of 56 columns. A column extract measured 90,030,965 B, so it saves nothing material. |
| COMPACT_HISTORY_SCORING_SUFFICIENT | **TRUE** | see below |
| COMPACT_HISTORY_WALKFORWARD_SUFFICIENT | **FALSE** | see below |

Why the compact history is sufficient for scoring:
- The extract is byte-identical (sha 0338b32d…ca71) to the one proven in
  `docs/PROSPECTIVE_SHADOW_HISTORY_EXTRACT_EQUIVALENCE.json` (PASS: 3 games, 1,392 predictions, equal banks).
- No history-read line of the scoring readers changed since that proof (diff check of features.py, run.py,
  shadow_tick.py, sync.py, cbbd_feed.py, history_extract.py). The proof is therefore reused (§12 of the directive).
- One read was not covered by that proof: the `completed_games` read of player_id_map. Its relation is inside the
  extract.

Why the compact history is not sufficient for walk-forward:
- Walk-forward also needs Stage 3 features, Stage 3 labels and both OOS stores.
- `history_identity` checks the exact byte sizes of the original Stage 3 / OOS files.
- Walk-forward does not resolve to the extract.

## 5. Minimal historical baseline spec (traced)

PATH A needs exactly the `history_extract.RELATIONS` set. Its row grain and keys are those of the canonical relations:
- games: canonical_game_id, seasons 2014–2026;
- outcomes: (canonical_game_id, canonical_team_id) and (canonical_game_id, canonical_player_id);
- rosters: (season, team, player);
- identity: canonical_player_id / canonical_team_id / provider key.

These carry the minutes, participation and structural fields used by `compact_canonical`. Their column lists are
pinned in `cbb_platform/prod/history_extract_manifest.json`.

PATH B adds the following, keyed by (game_example_id / canonical_game_id, canonical_player_id | canonical_team_id,
season):
- feat.player_features: 46 columns, seasons 2014–2026 (ids, season, cutoff, is_home, prof_last_box_position,
  cold_start_class, the pstd_* sums, pstd_appearances, 22 minutes features), plus pp_last_game_participation,
  ev_prior_season_roster, ev_transfer_preseason and ev_recruit_preseason, plus the 15 participation numerics;
- feat.player_box_state: 15 columns;
- feat.team_features: 15 columns;
- ex.player_game_examples: join keys, seasons 2024–2026;
- ex.game_examples: game_date_et, season 2026 (one value);
- lab.player_game_labels: min, pts, reb, ast, three_pm, stl, blk, tov, counting_labels_available, participation_status,
  box_row_present, minutes_label_available;
- lab.team_game_labels: team_points, possessions_source, game_minutes;
- OOS: forecast seasons F2023 to F2026, the 46 STORE_COLS (unchanged files).

## 6. Candidate, equivalence and bytes

**No compact candidate was created** (`COMPACT_CANDIDATE_CREATED = FALSE`).
- For scoring, Method A (the existing extract, unchanged) already suffices.
- For walk-forward, a Method B Stage 3 extract cannot be executed against the authorized production path without
  changing production readers. Those readers are the `history_identity` byte-size check and the hard-wired
  `HISTORY_CANONICAL` in `walkforward.daily`, and changing them is forbidden here by §22.
- `WALKFORWARD_EQUIVALENCE` therefore cannot be shown, and the walk-forward reduction stays unproven.

| Figure | Bytes |
|---|---|
| HISTORICAL_INPUT_EQUIVALENCE / WALKFORWARD_EQUIVALENCE | NOT_REQUIRED (no candidate) |
| SCORING_EQUIVALENCE | NOT_REQUIRED (scoring route unchanged; the prior extract proof stands) |
| Minimal **proven** migration data | 12,250,062,927 (the current footprint) |
| Bytes eliminated (proven) | 0 (0.000 %) |
| Traced but **unproven** target | ≈486,700,283 historical (extract 126,627,840 + Stage 3 subsets ≈252,036,268 + OOS 108,036,175) |
| Potential reduction (unproven) | ≈11,749,989,204 B (≈96.02 %) |

Files shown not to be needed for migration (classification only, nothing deleted):
- the canonical parquet export (EXCLUDE);
- `oos_forecasts_phase1.parquet`;
- the research canonical;
- the research Stage 3 store;
- the Starter Pack;
- per-run smoke feature DBs.

## 7. Next step

The next step needs a separately authorized directive to run the walk-forward on a Method B candidate. That directive
must allow:
1. building the Stage 3 / canonical extract under `data/qa/footprint_minimization/`;
2. a reader substitution for `history_identity` and for walk-forward's history path, so the morning refit can be run
   CONTROL vs CANDIDATE (same code, cutoff, seeds and threads) and compared bit for bit.

Only after that can the transfer manifest be reduced.
