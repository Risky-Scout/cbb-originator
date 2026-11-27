# Stage 04: permanent roster, transfer and player-identity integrity (2026-11-27)

Candidate **`af1998f88208465bd1ea033ce7601dc290ca5e7e`** on `research/root-cause-20261126`. Lineage:
deployed 9f65e7f → 56f3dae (Stage 02) → 25b83ed (Stage 03 successor) → a3ffa1b (Stage 03 ledger) → **af1998f**.
It contains 25b83ed unchanged and supersedes it by lineage. Only one candidate is deployed: af1998f.
CI `cbb-production-ci.yml` run **45947348593**: **success**. Deployment status is in `DEPLOY_PROCEDURE.md` and the ledger key
`stage4_roster_identity_20261127`.

All Droplet measurements were read-only. They ran in transient `systemd-run` units as `cbb-originator` with `ReadOnlyPaths=/var/lib/sports-models`,
`MemoryMax=1500M`, every BLAS/OMP thread variable = 1, `nice 10` and `python -B`. Scripts are in `scripts/`, outputs in `results/`,
and the Droplet copy is at `/var/lib/sports-models/cbb-originator/audit/stage4-20261127`.

## 1. Root causes

| id | defect | evidence |
|---|---|---|
| **RC1** | **Rank 2 of the 11-09 source precedence (current-season game participation) was only implemented as hand-curated `roster_omissions.csv` rows, and those rows lapse.** A player who appeared in a team's box scores without a CBBD `flagship.rosters` row was (a) `NOT_ON_CURRENT_ROSTER` → `SUPPRESSED` in the universe (simulated, never priced), and (b) absent from the identity roster, so the RotoWire WEB mapper could never resolve him. | `results/roster_gap_participation.csv`: 23 (player, team) pairs with 2026-27 box lines and no CBBD row; 1,472 min = 0.29 % of all minutes; 0 on any other team's roster; 0 multi-team. The 3 omission rows (Harris, Griman, Mattingly) all lapsed 11-23. |
| **RC2** | **Off-list records were never re-resolved.** A RotoWire player who had left the current snapshot kept his last (UNMATCHED) identity event forever, even after the maps or roster could resolve him. | Avila 21059, Lindsay-Martin 20575, Mattingly 28337, Rich 22290: latest event unresolved, resolvable against the current roster. |
| **RC3** | **No maintenance signal for curated state.** Expiry, a team change (RotoWire moved Nwuli to Rutgers while the row said Seton Hall), contradiction by newer participation, and newly resolvable rows were all invisible until a game blocked. | 5 of 5 blocked games on 11-27 traced to a lapsed or mismatched curated row (Avila, Luis, Carter: lapsed; Nwuli: team mismatch; Maclin: genuine conflict). |

## 2. Corrections (the existing architecture is reused; this is not a new roster platform)

* **`prod/roster_omissions.py` `participation_rows`** derives rank-2 roster rows (`roster_source = BOX_PARTICIPATION`) from
  `flagship.player_game_outcomes`. It uses only RESOLVED lines with `historical_knowable_at ≤ as_of` (the HR-1 tip + 4 h rule, the same as the
  frozen pool's PRIOR_APPEARANCE_CONFIRMED). The fail-closed decisions are evaluated in order:
  1. `REJECTED_MULTI_TEAM_PARTICIPATION`
  2. `MOOT_CBBD_LISTS_PLAYER`
  3. `REJECTED_ON_OTHER_TEAM_CBBD_ROSTER`
  4. `REJECTED_COMPETING_CANONICAL_CANDIDATE` (a same-name roster row on that team under another id)
  5. `REJECTED_IDENTITY_CONFLICT_SUPPRESSED`
  6. `WITHHELD_CURATED_NONCURRENT` (an in-force STALE/PROFESSIONAL_NONCURRENT row for that player and team)
  7. `REJECTED_NO_BOX_NAME`
  8. otherwise `APPLIED`

  Curated rows can only withhold. They never add.
* **`prod/universe.py`** sets pool players with a committed participation row from `NOT_ON_CURRENT_ROSTER` (suppressed) to
  `ON_CURRENT_ROSTER_BOX_PARTICIPATION` (scoreable). This is the same as rank-1 presence.
* **`prod/rotowire_feed.py`** extends the identity roster with the participation rows (`box_lines`, `with_omissions(box=…)`).
  A player who is off the snapshot and whose stored identity becomes resolvable gets exactly one `IDENTITY_RERESOLVED_OFF_LIST` event. It is
  append-only and idempotent, and stays NOT_LISTED.
* **`prod/rotowire_web_acquire.py`** maps by exact normalized name on a box-participation roster row
  (`EXACT_NAME_ON_BOX_PARTICIPATION_ROSTER`), and applies the same map extension to stale unresolved off-list players
  (`stale_identity_maps`, dry run first and then only the rows it would add). It adds `WebIdentity.resolve_stored` and `team_name_for`.
* **`prod/rotowire_player_states.py` `reconcile`** (pure, no I/O) runs after every web acquisition through
  `lifecycle.identity_reconciliation` → `ops/identity/curated_reconciliation/{latest.json,history.jsonl}`. It flags:
  * `EXPIRED_BLOCKING`
  * `EXPIRING_SOON_BLOCKING_IF_LAPSED` (7 d)
  * `TEAM_CHANGED_BLOCKING`
  * `CONTRADICTED_BY_PARTICIPATION`
  * `UNRESOLVED_NOW_RESOLVABLE`
  * `UNRESOLVED_UNCURATED_BLOCKING`
  * `OMISSION_EXPIRED_NOT_MOOT`

  The daily receipt carries the INFORMATIONAL warning `CURATED_IDENTITY_ACTION_REQUIRED`. It is never a blocker, **never extends a date
  and never edits a table**. Lapse still fails closed.
* **Curated tables** (dated web evidence in each row's `evidence`):
  * `rotowire_player_states.csv`: Nwuli is re-keyed to Rutgers (STALE_NONCURRENT); Luis is STALE_NONCURRENT; Carter is SOURCE_CONFLICT/UNKNOWN; the
    Avila (wrong team, Saint Louis) and Lindsay-Martin rows are removed because box participation now resolves them.
  * `roster_omissions.csv` is emptied: Harris and Griman are now CBBD-listed, and Mattingly is covered by participation.

## 3. Per-player evidence (directive §2)

| player | before | finding (sources dated) | after |
|---|---|---|---|
| **Chris Nwuli** 26154 | curated row at Seton Hall; RotoWire snapshot 11-26 23:00Z at **Rutgers** (Out For Season) → team mismatch → Rutgers@Indiana blocked | scarletknights.com portal tracker 2026-07-21 outgoing; The Setonian 2026-05 committed to Seton Hall; NJ.com (Zagoria) via RotoWire 07-24 staying in Las Vegas, not with Seton Hall; no 2026-27 box line for any team | **Verified and corrected**: STALE_NONCURRENT at Rutgers, review_by 12-24. Game unblocked. No roster or identity invented: he is still unmapped, and correctly so. |
| **RJ Luis** 20703 | curated row lapsed → Wake Forest@LSU blocked | one game, 11-18 vs UL-Monroe, 15 min (ESPN/CBS/Forbes 11-19; box P-a64cdcf86d329ebd "RJ Luis Jr.") under the 11-13 injunction; LSU president halted his play and he withdrew the suit (Louisiana Radio Network 11-20, WBRZ, Yahoo); RotoWire 11-20 "won't play" | **Verified and corrected**: STALE_NONCURRENT at LSU, review_by 12-11. His box line is `WITHHELD_CURATED_NONCURRENT`, so LSU membership is not re-added. Game unblocked. |
| **Robbie Avila** 21059 | row named Saint Louis (prior school), lapsed; no CBBD TCU row → TCU game blocked | TCU added him 11-12 (Washington Post, CBS, Yahoo); TCU box lines from 11-19 (3 games, 70 min) | **Verified and corrected** by rule, not by hand: BOX_PARTICIPATION TCU row → map 21059→P-5ce042f648e4fb1d → RESOLVED. The TCU row is absent at 11-20T00:00Z (point in time). RotoWire news vs snapshot still conflicts, so his games go to the normal `AVAILABILITY_UNCERTAIN` rotation handling, not identity-blocked. |
| **Drew Carter** 20923 | row lapsed → Oregon@UCLA blocked | Fox Sports/247 (Prehm): practicing, appears to have another season; Wikipedia team page, 247 tracker, Daily Hoosier: departed or exhausted; goducks.com roster 11-08 did not list him; no Oregon box line through 11-26; RotoWire GTD 11-09 | **Genuine ambiguity**: SOURCE_CONFLICT, current_game_available UNKNOWN, review_by 12-11. **Still blocks** (by design). |
| **Brandon Maclin** 23921 | SOURCE_CONFLICT FALSE, in force; RotoWire GTD → North Alabama@DePaul blocked | Big East check-in 2026-07-09 says eligibility expired; RotoWire 10-05 says awaiting an eligibility decision; SI tracker says "Ineligible (Case TBD)"; Fox says awaiting a decision; no 2026-27 appearance | **Genuine ambiguity**, unchanged. It still blocks via `CONFLICT_POSSIBLY_ACTIVE_GTD` while RotoWire says GTD. Eligibility is not assumed in either direction. |

KyeRon Lindsay-Martin 20575 (UAB) debuted 11-22 under a 14-day TRO (uabsports.com 11-22). He is now resolved through UAB box participation. If the TRO
lapses he returns through ordinary RotoWire status handling, not through a curated eligibility date.

## 4. Full unresolved non-active population (directive §6)

The same state version, S20261127T041333Z-288bf8cbe390, was used for both sides. Before is the deployed tables at 05:16Z (`results/population_before.json`). After is
the candidate code plus tables at 05:31Z (`results/after_now.json`).

| | before | after |
|---|---|---|
| unresolved non-active RotoWire records | **22** (all UNMATCHED) | **21** |
| … not covered by an in-force curated row | 5 | **0** (except the 2 genuine conflicts, which are intentionally blocking) |
| games blocked by identity (7 d, of 364) | **5** | **2** (Oregon@UCLA: Carter; North Alabama@DePaul: Maclin) |
| curated reconciliation `action_required` | not measured (no monitor) | **0** |
| identity-roster rows | CBBD 5,583 | CBBD 5,583 + BOX_PARTICIPATION 18 |
| off-list players re-resolved | 0 | 4 (Avila, Lindsay-Martin, Mattingly, Rich); NOT_LISTED ones were outside the 22 |

Classification of the 22 before records:

| class | n | records |
|---|---|---|
| Verified and corrected | 3 | Avila (resolved), Nwuli (re-keyed), Luis (reclassified) |
| Obsolete or irrelevant with supporting evidence (STALE_NONCURRENT, in force) | 7 | Gunn, Cottle, Lane, Koureissi, Shell, Hart, Gak |
| Missing authoritative information: CBBD carries no roster row or provider id for a player whose team affiliation and non-participation are verified and curated in force | 10 | Pinion, Simpson, Hunter, Davis, Tyson, Benson (AFFILIATED_INACTIVE_ELIGIBILITY); Odum (DELAYED_ELIGIBILITY); Edwards, Stickman, Overway (ROSTERED_LONG_TERM_OUT) |
| Genuine ambiguity (blocking) | 2 | Carter, Maclin |
| External authorization limitation | 0 | none is blocked by authorization alone. Commercial use of RotoWire data as a whole is unauthorized (§8) |

None of the 10 "missing authoritative information" players has a 2026-27 box line, so participation cannot and does not add them.
Their rows must be re-verified by their `review_by` dates. The monitor raises `EXPIRING_SOON_BLOCKING_IF_LAPSED` 7 days ahead.

## 5. Predictive consequences (directive §5)

**Operational (eligibility for pricing), not mathematical.** No model, PMF, parameter, feature, simulation, game-model or
export-format change. Engine and game-model ids are unchanged.

* **Pool / participation / lineups / minutes.** The 18 APPLIED pairs (1,321 of the 1,472 gap minutes) move from suppressed to
  scoreable. The universe change only touches players already in the frozen pool (PRIOR_APPEARANCE_CONFIRMED) and already
  simulated, so their minute and stat projections and team totals do not change. The exact number of pool players flipped per
  run is measured post-deploy from the universe (`ON_CURRENT_ROSTER_BOX_PARTICIPATION` count). Only player-market eligibility changes. Of the 197 `NOT_ON_CURRENT_ROSTER` rows in the
  R20261125 universe, the remainder (stale returners with no 2026-27 box line) stay suppressed.
* **Rejected or withheld (stay suppressed, fail closed).** The 4 `REJECTED_COMPETING_CANONICAL_CANDIDATE` players (Mayhugh, Cate, Ware, I. Brown;
  136 min) have a same-name roster row on the same team under another id, which is a probable duplicate canonical id. It is left to the
  identity-conflict process. Luis is withheld by his curated row.
* **Team strength / game forecasts.** Unchanged. The game model consumes the same simulated rosters. Forecast publication changes only in
  that the 3 unblocked games (Rutgers@Indiana, Wake Forest@LSU, the TCU game) are no longer identity-blocked. They still pass through every other gate.
* **Market eligibility.** +18 players eligible for props. 3 games released from `AVAILABILITY_IDENTITY_UNRESOLVED`. 2 stay blocked.

## 6. Temporal integrity

* Participation uses `historical_knowable_at ≤ as_of` only (tests `test_participation_rows_point_in_time`, the off-list
  re-resolution point-in-time test). On the Droplet, Avila's TCU row is **absent at 2026-11-20T00:00Z** and present now
  (`avila_tcu_row_at_1120T0000Z: false`, `participation_temporal_ok: true`).
* **No multi-team roster at the same time.** `max_teams_per_player_in_identity_roster = 1`. Multi-team participants are rejected
  (`REJECTED_MULTI_TEAM_PARTICIPATION`), and a player on another team's CBBD roster is rejected.
* Universe participation reads the as-of canonical input database of the run, not the live state. Events are append-only, and
  re-resolution writes a new event instead of rewriting history.

## 7. Tests

* Focused local (threads = 1, nice 10):
  * `tests/test_stage4_roster_identity.py` (15 new) + `test_rotowire_web_adapter.py` + `test_rotowire_availability.py`: **163 passed** (6.4 s).
  * `test_prod_live_feed.py`, `test_prod_identity_conflicts.py`, `test_availability_refresh_split.py`, `test_prod_daily_lifecycle.py`: **99 passed** (133 s,
    peak RSS 1.5 GB).
  * The 54-minute full tree was **not** re-run (directive §7).
* GitHub CI `cbb-production-ci.yml` run 45947348593 on af1998f: **success**.

## 8. Commercial authorization (kept separate)

No new provider requests were made in Stage 04. All work used existing receipts, the existing event log, existing state and public web sources.
A consumer RotoWire subscription is **not** treated as commercial authorization. That remains an owner decision and is independent of the
roster and identity correctness above.

## 9. Stage 19 requirement (recorded, not done here)

D3.2: idle quote wakes rebuild unchanged exports and recompute D-1 grades/performance. 141 wakes per 24 h hold the writer lock for 11.39 h (**47 %**).
Stage 19 must add a content-fingerprint skip, or an equivalent, for the export rebuild and bring writer-lock occupancy down. Stage 04 does
not touch it.

## 10. Closure answers (2026-11-27 ~17:00Z)

### Q1: predictive roster vs market eligibility
`results/predictive_presence.{csv,json}` (`scripts/s4_predictive.py`, all production runs since 11-20): **all 18 APPLIED
players appear in 41 player-game rows. Every row is in the frozen pool (39 PRIOR_APPEARANCE_CONFIRMED, 2 PRIOR_SEASON_ROSTER_RETURNER),
and every row is in `model_inputs.parquet` (participation p_play, min_mu/min_sigma, stat families), so all of them were simulated.** Every row was
`NOT_ON_CURRENT_ROSTER → SUPPRESSED`. Examples: Smrekar p_play 0.98 / 33.4 min, Avila 0.96 / 34.9, Ewin 0.97 / 26.3.

Code path: `prod/run.py` builds candidates (`cand`) from the frozen pool features, independent of the universe. `IN.marginal_rows` (participation and
minutes), RA2/TT/DV/CX/PI2 and `SM.simulate_game` all run on them. `roster_sup` (the universe suppression map) is read
only after simulation, where it sets `method = NOT_PRICED` on that player's market distributions (run.py:268). Game forecasts come
from the team-ratings game model, not from player rows. **So suppression was a pricing-only exclusion. The predictive rotation
was already correct for these players, and Stage 04 changes only their pricing.** No player was found who is genuinely absent from a
rotation because of the roster gap (`players_never_in_pool = []`).

Corrections to §5:
* The 4 `REJECTED_COMPETING_CANONICAL_CANDIDATE` players were **already SCOREABLE**
  (`ON_CURRENT_ROSTER_PROVIDER_ID_CHANGED`: the team's roster lists the same name under another provider id). Rejecting a duplicate participation
  row leaves them priced exactly as before. The underlying duplicate canonical identities remain an open item.
* RJ Luis is simulated for LSU (p_play 0.77 on 11-21, then 0.17, then 0.12) and never priced. Because the draw bank is conditional on playing and has no
  team-minutes constraint, his unpriced rows do not move teammates' distributions or the game forecast. His p_play is not zeroed because his
  RotoWire identity stays unresolved: CBBD has no LSU row, and the box participation is withheld. This is a **Stage 09 joint-minutes item**, not a
  Stage 04 change.

### Q2: actual point-in-time availability
* Per-row box lines carry only the HR-1 rule time (`historical_knowable_at` = tip + 4 h, or the next day 06:00 ET when the tip is inexact). There is
  **no per-row provider receipt or revision timestamp** in `flagship.player_game_outcomes`. Payload `received_at` lives in the sync
  provenance and state manifests.
* In production the rule is supplemented by actual ingestion. Sync withholds results until the rule time has passed (`results_deferred_not_yet_knowable`), and a line
  exists only in state versions built after its payload was received. Every run (universe, identity roster, participation) reads the state
  current at run time. Effective knowability is therefore max(rule, actual ingestion).
* Measured over all 307 retained state versions (`results/box_ingest_lag.{csv,json}`, `scripts/s4_ingest_lag.py`), for 1,438 games:
  * **0 games appeared in any state before their rule time.**
  * Actual first ingestion came after the rule time: median 2.7 h, p90 5.4 h, p99 8.2 h, max 28.9 h for games knowable since 11-18, where versions are dense. Earlier games
    have sparse retained versions, which overstate the lag.
* Prospectively, delayed or corrected observations cannot establish membership before they were actually ingested. Original
  forecasts are immutable run directories and are not altered.
* **Replay exposure (not a production defect):** a replay that applies `as_of` to a *later* state uses the rule only and can see a line
  hours before it was actually ingested. A provider revision that adds a line also carries the rule time. Both are **Stage 07**
  (historical provenance) items. My own 11-20T00:00Z Avila check is rule-based and is consistent with this: his first TCU game was 11-19.

### Release consistency (deployment safety)
Defect found and repaired (commit 93e5cd2): `cbb-originator-run` verified `readlink -f current` against `CBB_EXPECTED_COMMIT`
but then `cd`'ed into, and set `PYTHONPATH` to, the **symlink path**. A swap during a running job could therefore mix lazily imported modules
of two releases. After verification the job now runs from `releases/<sha>`. A test covers it, and the focused deploy-package
and quote-yield tests give 42 passed and 5 skipped (flock, on macOS). Remaining fail-closed race: a unit that starts between the env edit and the symlink swap in
`cbb-deploy-release` is refused by the identity check (exit 3) and runs at its next scheduled wake. Units do not change in this candidate.
The read-only selection audit sets `PYTHONPATH=/opt/.../current` inside its own transient unit. That is minor, and it is listed for Stage 19.
