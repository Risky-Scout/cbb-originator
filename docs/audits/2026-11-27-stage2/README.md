# Stage 02 — Feature warm-up architectural optimization (2026-11-27)

Scope: the morning pregame feature warm-up (`walkforward/training_rows.feature_rows`, `walkforward/daily.warm_features`)
and its builder (`prod/features.build`). Production release unchanged (9f65e7f current, cae2a02 rollback). All Droplet
work ran as transient read-only units (`scripts/s2_run.sh`: uid cbb-originator, `ReadOnlyPaths=/var/lib/sports-models`,
write only to `audit/stage2-20261127`, MemoryMax 3200M, nice 10, IOWeight 50, one numerical thread, one job at a time).

## Defects repaired

| # | Defect (old path) | Repair (new path) | Old-path failure / new-path proof |
|---|---|---|---|
| D2.1 | Every warm-up build recomputed all player state streams of the whole history window (1.55–1.58 M player box rows, 2013-14 onward) to serve 1–24 target games. ~205–246 s per build, ~3.5 min per cutoff, 118–135 min heavy-lock occupancy per morning (11-25, 11-26) against a 150 min budget. | `prod/features.dependency_closure` (`TARGET_TEAM_PLAYER_CLOSURE_V1`): after the as-of mask, delete every player-keyed row that cannot reach a target row (keep: all rows of target teams T, all rows of every player U with any membership evidence for T). Team-keyed relations kept whole. Warm-up only (`CBB_FEATURE_CLOSURE`, default ON; OFF = incumbent comparator). Scoring/labels paths unchanged. | 17/17 production builds byte-equal (below); 8/8 benchmark builds byte-equal; fixture test every row × every cutoff × every state. |
| D2.2 | Cached feature row reused whenever `<cache>/<game>/` existed: a rescheduled tip, a replaced state version, a changed history DB or changed feature semantics silently reused stale rows. | Typed causal key (`PREGAME_CAUSAL_KEY_V1`: feature-semantics sha [Stage 3 sources + frame SQL + frozen config], history-DB sha, state version, increment sha, cutoff, build as_of, season, group-target sha). `cache_validity` re-checked at every reuse and in the warm-up to-do list; stale entries superseded into `.superseded/` (kept as evidence), rebuilt. Post-cutoff corrections never invalidate (no backdating). | `tests/test_feature_cache_causal.py`: old code 7/9 fail, new code 10/10 pass. |
| D2.3 | A cache directory without `meta.json` (crash between writes) blocked the atomic rename and then crashed the read. | Incomplete entry superseded (`STALE_INCOMPLETE`) and rebuilt; concurrent-winner check keyed on `meta.json`. | `test_interrupted_writes_are_ignored_and_rebuilt` (old: FileNotFoundError; new: pass). |
| D2.4 (guard) | The closure is exact only for the audited Stage 3 SQL. | `CLOSURE_AUDITED_STAGE3_SHA256` pin: any Stage 3 change → closure falls back to the full build (`CLOSURE_NOT_AUDITED_FULL_BUILD`) and `test_closure_is_pinned_to_the_audited_stage3_sql` fails until re-audit. | Unit test. |

## Dependency audit (why the closure is exact)

Audited against the frozen Stage 3 SQL (`cbb_platform/stage3`, unchanged since 2026-10-16, sha `257d8968…`):

* team features (`team_state`: SOS / opponent adjustment / rest / results) read only `played_games` / `team_games` — kept whole;
* player streams are partitioned by player (`player_box_state`, `pseason`, `identity_conflicts`, `rec` incl. its
  `arg_max(…, recruit_year)`, `trf`, previous-season `fin`) or by (player, team, season) (participation); target rows
  attach only partitions of their own pool players on the target team;
* pool membership for (T, s) is derived only from the player's own rows + team-season existence; U covers all five
  evidence classes (box on T s-2..s incl. one sit-out season, roster on T s-1..s, portal to/from T year s-1 with
  canonical or candidate id, recruit committed to T class s-1); portal-out exclusion uses the player's own transfer rows;
* team-level aggregates over players (previous-season team minutes over ALL box rows incl. NULL ids; substitution-feed
  completeness over all stints of the team-game; `team_used_on` from `team_games`) are joined only on target teams,
  whose rows are all kept;
* shots / plays / on-floor streams are empty in the compact canonical (`EMPTY_RELATIONS`), quarantine / teams /
  team_seasons are not restricted; rows of other players only feed partitions that are never attached.
* game model 1.2.0 (`game/forecast`) reads team ratings from history + increment, never the warm-up cache; its only
  player-engine input is the active fit artifact (byte-identical below). Game PMFs / derived game probabilities are
  therefore unaffected by construction.

## Equivalence results

| Check | Population | Result |
|---|---|---|
| Production replay, closure vs cached incumbent rows (`equivalence_results.jsonl`) | 13 closure builds (12 evenly spaced over 284 consistent cached groups 11-02→11-26 + the largest, 24 games), 56 games, 3 544 player rows (+ team rows) | all cand + team frames byte-equal (sha of values, columns, dtypes, order); PIT violations 0; target-outcome rows 0; post-as-of rows 0 |
| Incumbent re-run on 4 of those groups | 11-02, 11-10 (24 games), 11-15, 11-23 | byte-equal to cache (incumbent reproducible) |
| Benchmark re-runs (`bench_results.jsonl`) | 5 closure + 3 incumbent, one process each | 8/8 byte-equal |
| Fixture: every pregame row at every cutoff (`test_every_pregame_row_at_every_cutoff_is_identical`) | labeled synthetic 2026-27 season over the real frozen history: corrected box (v1→v2), half-boxed final, in-progress game, late result, two rounds; each cutoff and cutoff+30 min (same results, different elapsed time) × every state version | identical values / rows / columns / dtypes; PIT 0 |
| Fixture: full morning + predict, closure ON vs OFF (`test_morning_fit_and_predictions_identical_closure_on_vs_off`) | D7 walk-forward refit + promotion, then predictions | fitted artifacts (`marginal`, `jm1_structural`, `residual_dependence`) byte-equal; training frame / finals / OOS sha equal; labels, eligibility, gates byte-equal; all feature-cache keys equal; player PMFs (852 rows) equal in every column except `run_id`; provenance (860 rows: probabilities, push/nonpush, projections, minutes, pmf_q75) equal except `prediction_id` = H(run_id, key), `run_id` and the `production_manifest` hash (covers the wall-clock `CREATED_AT`); manifests differ only by `CREATED_AT` and the closure build-provenance record |
| Cache census of the production cache under the new rules (`cache_census.json`) | 1 083 cached entries | 1 083 `LEGACY_VALID` (pre-key, every other check passes): first deploy forces 0 rebuilds |

Legacy acceptance contract: a pre-key entry is reused only if status BUILT, its recorded cutoff equals the game's
current cutoff, and its state version equals the state as-of that cutoff (or that state was retired). This proves
compatibility, not correctness; correctness of those 1 083 rows rests on the incumbent having built them (13 sampled
groups re-derived byte-equal by the closure, 4 by the incumbent). New entries carry the full causal key.

## Performance (measured, Droplet 4 vCPU / 8 GB, production data, niced, concurrent with the production quote timer)

See `bench_table.md` / `summary.json`. Per build:

| | incumbent | closure |
|---|---|---|
| player box rows read | 1 555 369 – 1 583 218 | 9 725 – 229 876 |
| wall | 204.8 – 246.4 s (median 240.4) | 28.4 – 62.9 s (median 31.2) |
| CPU (user+sys) | 225 – 257 s | 36 – 68 s |
| peak RSS | 1 051 – 1 124 MB | 922 – 943 MB |
| disk writes | 3.8 – 5.3 GB | 227 – 307 MB |
| bytes read (rchar) | 3.6 – 4.7 GB | 139 – 231 MB |
| scratch | 502 – 511 MB | 185 – 239 MB |

Paired (same inputs): wall 6.9× (1 game, 11-02), 6.9× (2 games, 11-23), 4.0× (24 games, 11-10); writes 16.5–22.4× lower.
Cache validation (`validation_overhead.json`, fresh process, 1 083 entries, 299 states): history-DB sha 3.75 s once per
process (667 MB, memoized), state index 0.29 s, metas 0.47 s, validity 0.28 s, `cached_valid_games` 0.39 s; < 5 s/morning.
Profile of one incumbent build (`profile_…json`): Stage 3 221 s of 241 s; top statements over 1.5 M box / 3.5 M
participation rows (f_pbox 60.7 s, part_cand 48.9 s, pg_k 40.9 s, f_part 23.2 s, pseason 19.1 s).

Scaling: incumbent cost grows with the season (205 s on 11-02 → 246 s on 11-23; substitution rows 436 → 92 643) and
with every history season; closure cost depends on target teams (U, T rows), not on history size: 1-game builds
31.0 s (11-02) → 33.9 s (11-26). Remaining floor ≈ 30 s = compact-canonical copy (~227 MB written) + fixed Stage 3.

### Lock occupancy and deadline (journal; measured before, projected after)

Warm-up holds the shared heavy lock for its whole run. Measured: 11-24 88.4 min, 11-25 134.9 min, 11-26 118.3 min
(CPU 87.9 / 134.3 / 117.7 min); budget 9 000 s (150 min); 11-26 had 34 cutoffs.
**Projected (not measured)** with the candidate: ≤ 34 builds × 31–63 s = 18–36 min on an 11-26-sized morning (fewer if
46e07f3 groups identical information states), + < 5 s validation; at 60 builds × 63 s = 63 min still within budget.
The real figure needs a natural morning on the candidate (after the 11-27 receipt for 46e07f3).

## Resource safety

Every Droplet job checked MemAvailable ≥ 4 GiB (observed 5.8–6.9 GB of 8 GB) and free disk ≥ 38.5 GB (observed 41 GB of
154 GB); peak job RSS ≤ 1.13 GB under MemoryMax 3200M; work dirs removed after each build (audit dir 4.2 MB at end).
No production unit was started, stopped or delayed; the production quote timer ran throughout.
Deviation: one `echo 1 > /proc/sys/vm/drop_caches` (clean page cache only) before the validation-overhead measurement —
host-wide and not needed; recorded here.

## Files

`equivalence_results.jsonl`, `bench_results.jsonl`, `cache_census.json`, `validation_overhead.json`,
`profile_S20261125T152533Z-fbba09a7a326.json`, `local_morning_equivalence_pytest.log`, `summary.json`,
`bench_table.md`, `summarize.py`, `scripts/`. Droplet copy: `/var/lib/sports-models/cbb-originator/audit/stage2-20261127`.
