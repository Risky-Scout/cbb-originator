# Stage 05: injury, availability and confirmed-lineup mathematics (2026-11-27)

The successor is built on 93e5cd2 (Stage 04, CI 46037886213). Commits: abb466e (C1, C2), 594ec8b (docstring + evidence), then the closure commit (C3, §7).
All Droplet measurements were read-only. They ran in transient `systemd-run` units as `cbb-originator` (`ReadOnlyPaths=/var/lib/sports-models`,
`MemoryMax=1500M`, threads = 1, nice 10, `python -B`). Scripts are in `scripts/`, outputs in `results/`, and the Droplet copy is at
`/var/lib/sports-models/cbb-originator/audit/stage5-20261127`. No provider request was made. Nothing was written to production state.

## 1. The implemented availability path (traced in code)

| step | implementation | what is carried / lost |
|---|---|---|
| acquisition | `rotowire_web_acquire` news + snapshot (authorized existing receipts only) | news text is classified by regex (`RULES`): OUT / DOUBTFUL / GTD / QUESTIONABLE / RETURN_DESIGNATION / ACTIVE / POSTGAME. **"will start" / "in the starting lineup" collapse to ACTIVE**, so start information is discarded |
| timestamps | event `collected_at_utc`, `knowable_at`; per game `T = min(as_of, feature_cutoff)` | a provider source timestamp is not used for ordering; `knowable_at` (collection) is |
| identity | persisted maps + on-roster check (Stage 04) | unresolved → the game fails closed (AVAILABILITY_IDENTITY_UNRESOLVED) unless curated |
| canonical event / effective status | `player_state(events, T)`: news primary, snapshot reconciliation; news/snapshot conflict → UNKNOWN + `AVAILABILITY_SOURCE_CONFLICT` | newer knowable events win (also `apply_availability` keeps the last `received_at`) |
| participation probability | `props_v0_participation.predict` (frozen features) | **status never changes p_play**. OUT/UNAVAILABLE removes the player instead; GTD/DOUBTFUL/QUESTIONABLE/UNKNOWN/ACTIVE leave the model p_play unchanged |
| starting probability | **not implemented** (no p_start; `role_l5_minutes_rank` is a feature only) | |
| confirmed lineups | **not acquired from any source; no expected/confirmed lineup object** | |
| minutes | `P.predict` min_mu + band sigma; PS2 calibrated minutes; per-player draws | **independent per player. No team-minutes constraint, no redistribution** (Stage 09) |
| player simulation | `jm1_final_production.score_game`: draws **conditional on playing** for the `_scored` set; p_play passed through, never mixed in | OUT / UNAVAILABLE / missing state → not in the bank |
| stat PMFs / combos | base stats `BANK_MIXTURE_PMF`, combos `BANK_DRAWS` (exact sums of the same draws, `same_draw_check`) | |
| team strength → game | `game.forecast.team_strength` S = Σ p_play·PTS_mu over the scored set; mean += γ·ΔS (γ = 0.3 not fitted, β = 0); ratings carry everything else | a GTD/uncertain player moves nothing; confirmed ACTIVE does not raise p_play |
| market eligibility | OUT → `CONFIRMED_UNAVAILABLE` (not priced); gate → priced but never actionable; rotation OUT ≥ 20 min or TOP5 → `UNSUPPORTED_ROTATION_SHOCK` (game never wager-eligible); roster suppression applied **after** simulation (Stage 04) | |
| board / publication | p_play published beside conditional distributions | |
| which snapshots consume availability | official T-1h tick (`shadow_tick.availability_inputs`, since 11-26) and the 10/13/16/18 ET refreshes; **INITIAL 07:00 ET and REFIT_RETRY did not (corrected, C3, §7)** | |

## 2. Counterfactuals (actual production functions, one real game, fixed seed)

Game G-0648df8eb36bddd3, run R20261127T150445Z, 4,000 draws, D.game_seed. The baseline is J: no reports. Starter P-f43640a702b08161
(35.1 min, p_play 0.985, TOP5); reserve P-93de81d89c51786e (24.8 min, ROTATION_6_8). `results/counterfactual_*.json`.

| scenario | player effect | teammates | game (home) |
|---|---|---|---|
| A OUT starter | removed from bank, not priced | input minutes/means **identical**; base-stat PMFs identical; combos/tails move by MC noise only (PRA −0.10, P(PTS≥20) +0.003) | ΔS −14.73 → home −4.42 pts, margin −4.42, total −4.42, P(home win) −0.151; flag UNSUPPORTED_ROTATION_SHOCK |
| B OUT reserve | removed | identical | ΔS −6.08 → −1.82 pts, P(home) −0.061; shock flag (24.8 min) |
| C GTD starter / D DOUBTFUL / K conflict | **no change** (p_play 0.985 kept, priced, non-actionable) | identical | no change; flag AVAILABILITY_UNCERTAIN_ROTATION |
| E confirmed ACTIVE / F reserve "will start" | **no change** | identical | no change |
| G starter to bench | no mechanism, no change | | |
| H return from absence (p_play 0.004 player ACTIVE) | **no change** (p_play stays 0.004) | | |
| I ineligible (curated verified) | identical to A, by the Stage 05 correction | | identical to A |
| J missing | model p_play (NO_REPORT calibrated: 0.464 predicted vs 0.447 realized, n = 13,887) | | |
| L stale OUT then newer ACTIVE / newer OUT after ACTIVE | newer status wins in both orders | | |

**What the architecture models:** confirmed absence removes the player and lowers the team mean through γ·ΔS. **What is structurally absent:** teammate minutes and opportunity redistribution, start/bench information, status-conditional participation, and confirmed lineups.

Participation calibration by status (`results/status_measure.json`; last pre-tip p_play vs realized minutes > 0, 11-20..27, 1,443
finished games):

| status | n | mean p_play | realized |
|---|---|---|---|
| NO_REPORT | 13,887 | 0.464 | 0.447 |
| GTD | 64 | 0.269 | 0.203 |
| GTD, rotation players | 8 | 0.862 | 0.625 |
| OUT (removed anyway) | 60 | 0.13 | 0.017 |
| ACTIVE | 6 | 0.571 | 1.000 |

The samples are too small to calibrate a status → p_play map now, so this goes to Stage 09.

## 3. Defects and corrections

**D5.1 (corrected, C1): verified non-participation never reached the predictive layer.** Curated NON_BLOCKING rows were
documented as "never conditioning any projection" because the player was assumed to be outside the active universe. The frozen pool simulates such players from prior appearances (`results/curated_presence.*`):
* RJ Luis, LSU: p_play 0.77 / 0.17 / 0.12.
* NJ Benson, Jaxon Edwards and DJ Stickman: about 0.01–0.03 each.
* Melvin Council Jr.: **priced** at p_play 0.32 / 0.26 on 11-20 and 11-23 while RotoWire had no status. His row was verified 11-26, so the correction does not apply to those runs, by point in time.

`availability_frame` now emits in-force NON_BLOCKING rows that name a deterministic `canonical_player_id` as `UNAVAILABLE`, or as `LONG_TERM_OUT` for
ROSTERED_LONG_TERM_OUT, with source `CURATED_VERIFIED_STATE` and knowable from the start (ET) of `verified_on`. The player is not simulated, not priced, and excluded from
S. Rules:
* A live RESOLVED RotoWire status for the same player governs.
* If RotoWire places the provider ID at another team, the row does not apply.
* Rows with no canonical id, and SOURCE_CONFLICT rows, never apply.
* The frame never adds a player, an identity or a roster row.
* The blocking logic and the RotoWire rows are untouched.

On the live 7-day slate at 17:47Z (367 games) this adds 16 rows for 11 players, with 0 duplicate keys
(`results/curated_after_now.json`). Council is correctly left to his live RotoWire OUT status.

**D5.2 (corrected, C2): the game-layer baseline attributed non-availability drift to availability.** ΔS was measured against the
day's INITIAL snapshot. INITIAL never carries RotoWire availability: 698 INITIAL team-rows had 0 OUT rows. So since the 11-26 S fix, the
availability content matches the news-free expectation. However:
* Any other model-input change between INITIAL and a later snapshot entered "availability".
* A game with no earlier snapshot that day (recovery or late slate) got ΔS = 0.

ΔS is now S(scored set) − S(every candidate at the model's own p_play) **in the same snapshot**. γ is unchanged, no coefficient is added, and
`CBB_GAME_AVAILABILITY_BASELINE=INITIAL` restores the old baseline. Replay of the real snapshots with `game.forecast.forecast`
(`results/game_baseline_*`):
* The INITIAL mode reproduces 11-27 production **bit for bit (91/91 pmf_sha256)**.
* NEWS_FREE vs INITIAL on the same code: 11-20 0/51 games changed, 11-23 1/32 (|ΔP(home)| 0.0015, margin 0.056), 11-26 0/20, 11-27 0/45.

The pre-fix 11-20..25 production rows did not reflect OUT mass (39.5% overall). That was the
2026-11-26 S fix, which is already deployed in 9f65e7f, not this baseline.

**Not defects (verified):**
* Stale information cannot overwrite newer information.
* Missing information stays at the calibrated model p_play and is not set to 1.
* Combos are exact sums of the same draws.
* The player-to-game bridge has a single term (γ·ΔS), so availability is not double counted.
* Historical forecasts are immutable run/snapshot directories and are not touched.

## 4. Acceptance (§9)

* Confirmed OUT, or verified ineligible with a deterministic identity: not in the bank, so 0 participation and 0 minutes in every simulated state. Not priced. Leaves S. PASS.
* Ineligible handled consistently with OUT. PASS (C1).
* Genuine uncertainty (GTD / DOUBTFUL / conflict) stays probabilistic at the model p_play and is never actionable. PASS. Its p_play is not
  status-informed, which is a Stage 09 dependency.
* Missing information is not treated as confirmed health. PASS. Stale cannot overwrite newer. PASS. Combos coherent. PASS. No double count. PASS.
  PIT preserved (`verified_on`, `knowable_at`). PASS. Unaffected behaviour protected: INITIAL replay bit-identical, and the RotoWire frame rows are unchanged. PASS.
* **Not certified:** joint minutes conservation and teammate opportunity redistribution. Neither mechanism exists.

## 5. Tests

Focused local runs (threads = 1, nice 10):
* `test_rotowire_web_adapter.py` (+5 Stage 05 tests and 1 updated), `test_player_game_coherence.py` (+2), `test_stage4_roster_identity.py`, `test_rotowire_availability.py`: **176 passed**.
* `test_release_b_lifecycle.py`, `test_player_game_coherence.py`, `test_prod_live_feed.py`, `test_availability_refresh_split.py`, `test_game_*`: **120 passed**.
* `test_lifecycle_availability_changes.py`, `test_prod_identity_conflicts.py`: **20 passed**.

The full local tree was not run. GitHub CI is recorded in the ledger key `stage5_availability_math_20261127`.

## 6. Dependencies recorded for later stages

* **Stage 09** (joint participation and minutes):
  * no team-minutes constraint, and no redistribution of an absent player's minutes;
  * status-conditional p_play (GTD rotation 0.86 vs 0.63 realized; ACTIVE 0.57 vs 1.0; small n);
  * no p_start, no expected or confirmed lineup;
  * "will start" and bench information are discarded;
  * returning-player p_play does not respond to ACTIVE.
* **Stage 11** (opportunity redistribution, conditional production): teammates' usage, rates and stat means do not respond to an absence. Combo/tail changes are MC noise only.
* **Stage 16** (player-to-team translation): γ = 0.3 is a conservative prior, not fitted, with β = 0. The translation of ΔS to points and the confirmed-ACTIVE asymmetry in S remain open.
* **Stage 07** (historical information availability): unchanged from Stage 04. Additionally, event ordering uses collection `knowable_at`, not provider source time.
* Operational: `UNSUPPORTED_ROTATION_SHOCK` now also fires for a curated LONG_TERM_OUT / ineligible player whose stale min_mu is ≥ 20 or who is TOP5. This is the same rule as for a RotoWire OUT and is fail-closed.

## 7. Stage 05 closure: the news-free reference vs production forecasts

**Conclusion.** The news-free quantity is an **internal mathematical reference only**: `team_strength(model_inputs, news_free=True)` is computed inside
each snapshot to form ΔS (C2) and is never published, priced or exported. It does not need any production snapshot to be news-free.

**Defect D5.3 (corrected, C3): the INITIAL 07:00 ET and REFIT_RETRY snapshots were actual production forecasts that suppressed valid information.**
They persist PMFs and drive the morning Distributions and Boards exports, but `lifecycle.forecast` gave them `rotowire_root=None`. So every
availability state already knowable in the event store (prior-day refreshes, earlier news) was ignored until the 10:00 ET refresh, or for the whole day when no
later lifecycle snapshot covered the game. The suppression was a side effect of the old INITIAL game-layer baseline, which needed INITIAL to be news-free; C2 removed that need. Measured
over every real INITIAL snapshot, 11-08..27 (`results/initial_gap.*`; point in time at each snapshot's `executed_at`, knowable < T):

* 268 player-game rows were simulated although a confirmed OUT or LONG_TERM_OUT was already knowable: 258 RotoWire, 10 curated (C1).
  * 34 of them were simulated at p_play ≥ 0.5 (max 0.958).
  * 37 rows were on 11-27.
* 20 rows were never corrected by a later lifecycle snapshot:
  * 11-24: 19 rows in 13 games, when the 10/13/16 ET refreshes did not publish;
  * 11-26: one early game that tipped before 10 ET.
* Gated (GTD/DOUBTFUL/conflict) players simulated without their gate: 0–43 per snapshot.

**Every other production forecast consumes the latest authorized, applicable information.**
* The official T-1h tick reads the canonical event store at the same point in time (since the 2026-11-26 repair), and a stale state fails the game closed rather than suppressing the forecast.
* The refresh windows consume it and re-forecast.
* Acquisition itself is unchanged: authorized existing WEB sessions only, with the same enablement rule (`rotowire_state`: source WEB, protected session, PASS acceptance marker). All units share the env file and wrapper preflight.

**Correction C3.**
* INITIAL and REFIT_RETRY now consume the knowable availability under that same rule. They never acquire, and they record `availability_source` in the snapshot and index (`rotowire_refresh` stays refresh-only).
* `CBB_INITIAL_AVAILABILITY=OFF` restores the news-free INITIAL (in-place fallback).
* The C2 game-layer fallback `CBB_GAME_AVAILABILITY_BASELINE=INITIAL` is honoured **only** together with `CBB_INITIAL_AVAILABILITY=OFF`. Otherwise it would drop the morning's known absences, so it reverts to NEWS_FREE.
* Actionability is unchanged and stays fail-closed. INITIAL boards were AVAILABILITY_SOURCE_NOT_CONFIGURED before. They are now subject to the freshness bound and the identity gate.
* Historical snapshots are immutable and not rewritten.
* Tests: `test_availability_refresh_split.py::test_full_snapshots_consume_availability_already_knowable[INITIAL|REFIT_RETRY]` covers three things:
  * an OUT knowable before the snapshot is `CONFIRMED_UNAVAILABLE` in the forecast;
  * the next refresh does not report it as a change;
  * OFF restores the old output.
  * `test_player_game_coherence.py` covers the fallback pairing. Focused regression: 260 passed.

**Recorded for Stage 19 (not changed here).** `availability_changes` compares the stored `NO_REPORT` with "no row" as different. So every
refresh re-forecasts every remaining pre-cutoff game: on 11-27 at 10:00 ET that was 45/45 games and 2,006 "changed" players. This is wasted writer-lock time, not lost information: the
forecasts are recomputed with the newer state.

**Stage 07 note.** The INITIAL gap rows use collection `knowable_at`. Bitemporal source/collection handling stays assigned to Stage 07.
