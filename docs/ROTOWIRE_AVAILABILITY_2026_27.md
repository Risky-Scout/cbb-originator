# CBB Originator: RotoWire player availability (2026-27)

**Status:** integrated behind the existing availability interface. **Off by default** (`run.daily(rotowire_root=None)`,
CLI `--rotowire` not set): the live scheduler is unchanged. Wagering is disabled (`WAGERING_ENABLED = False`, ceiling
`SHADOW_ONLY`). `WIZARDOFODDS_PUBLICATION = DISABLED_PENDING_SSH_ACCESS`.

This is availability conditioning only. JM1 (marginals, minutes, dispersion, dependence, feature set) is unchanged and
nothing is refit. Code: `cbb_platform/prod/rotowire_acquire.py` (HTTP), `cbb_platform/prod/rotowire_feed.py` (events,
identity, point in time, gates), `cbb_platform/prod/inputs.apply_availability` (the existing interface).
Tests: `tests/test_rotowire_availability.py` (33, CI-safe).

## 1. Endpoints (documented REST API; no scraping)

| key | endpoint | role |
|---|---|---|
| `news_injuries` | `GET https://api.rotowire.com/Basketball/CBB/News/Injuries.php` | **primary** point-in-time evidence (timestamped updates) |
| `snapshot` | `GET https://api.rotowire.com/Basketball/CBB/Injuries.php` | current-state reconciliation only |
| `news_updates` | `GET https://api.rotowire.com/Basketball/CBB/News/Updates.php` | optional; **disabled** (`ROTOWIRE_INCLUDE_UPDATES = False`) |

- **The only query parameter is the API key.** The parameter name is `API_KEY_PARAM = "key"`, which the first live smoke test must confirm.
- A request with any other parameter is refused before it is made. No date, hours, priority or format parameter is assumed.
- The key comes from `ROTOWIRE_API_KEY` (`credentials.get_strict`). It is never stored, logged or serialized, and it is registered in `credentials.REGISTERED` for leak scans.

**Schema status.** `SCHEMA_VERIFIED = False`. The field paths (`rotowire_feed.SCHEMA`) follow the directive's field
list:
- `Id` (update);
- `Player.Id`, `Player.SportDataIOId`, `FirstName`, `LastName`, `Position`;
- `Team.Id`, `Team.Name`;
- `Injury.Status/Type/Location/Detail/Side/ReturnDate`;
- `DateTime`, `LastEdited`, `Priority`.

The official CBB field reference was not available here, and no key is configured. A record without its required fields
is `MALFORMED` and never becomes an event. A body that is not a single record list fails the poll. The first live smoke
test (`python -m cbb_platform.prod.rotowire_feed smoke`) reports field presence per path. Any path correction belongs in
`SCHEMA` only.

## 2. Status contract

The mapping is exact on codes, case- and whitespace-insensitive:

| raw | normalized | effect |
|---|---|---|
| ACT | ACTIVE | confirmed available, no gate |
| OUT | OUT | unavailable (`inputs.UNAVAILABLE`), not priced |
| SUSP | SUSPENDED | unavailable |
| NA | UNAVAILABLE | unavailable |
| IR, PUP | LONG_TERM_OUT | unavailable |
| GTD | GTD | **unresolved**: PRICED_BUT_NON_ACTIONABLE (§7a): forecast and priced with the model's own P(play); never actionable |
| Q | QUESTIONABLE | unresolved (as GTD) |
| D | DOUBTFUL | unresolved (as GTD) |
| IR-R | RETURN_DESIGNATION | unresolved (as GTD) |
| null / empty / anything else | UNKNOWN (raw kept) | gate `AVAILABILITY_STATUS_UNRECOGNIZED`: suppressed (fail closed) |
| DNP | POSTGAME_ONLY | **never pregame evidence, for any game** (stored for audit only) |

- **No status-to-probability mapping exists.** P(play) is never rewritten: a test asserts it is bit-identical with and without RotoWire.
- An OUT-like rotation player triggers the existing `UNSUPPORTED_ROTATION_SHOCK`.
- A gated (unresolved, unrecognized or conflicting) rotation player triggers `AVAILABILITY_UNCERTAIN_ROTATION` (game-level): the game is priced but never actionable. Rotation means `TOP5_MINUTES` or `min_mu ≥ 20`.
- `inputs.UNAVAILABLE` also gained `OUT_SEASON`. This fixes a latent mismatch: the Sharp Stats adapter emits `OUT_SEASON`, while the interface only listed `OUT_FOR_SEASON`.

## 3. Event store (append-only)

`data/prod/availability/rotowire/events.jsonl` (gitignored) holds one immutable line per new semantic event. Each event carries:
- the directive §5 field set (`rotowire_feed.EVENT_FIELDS`);
- `event_kind` ∈ {NEWS_UPDATE, SNAPSHOT_OBSERVATION, UPDATES_STREAM};
- `knowable_at`, `record_sha256`, `raw_payload_sha256`.

Storage rules:
- **Deduplication.** The semantic key covers kind, update id, player id, raw and normalized status, priority, injury fields, return date, `DateTime` and `LastEdited`. An identical response adds nothing, and the earliest collection time is kept.
- **Edits.** A changed `LastEdited` is a new version; prior lines are never rewritten.
- **Priority 1–5** is ingested as given and never filters.
- **Licensed text stays raw.** Notes / Analysis text is never copied into an event. The raw response bytes stay in the gitignored licensed raw cache (`data/prod/cache/raw/rotowire`, files `0o444`, sha256 sidecar) and are never published.

## 4. Point in time

- **Collection time.** `collected_at_utc` is when this process received the response.
- **Knowable time.** `knowable_at = max(collected_at, DateTime)`. A late-collected update is never backdated.
- **Usability.** An event is usable at T only if `knowable_at < T` and `collected_at < T` (PIT v2.0 §3.1, G4A item 7).
- **Scoring instant.** Each game uses the state at `min(prediction_as_of, feature_cutoff)`, where the feature cutoff is the validated TIP_MINUS_1H.
- **Naive timestamps** are never guessed into a zone (`NAIVE_UNRESOLVED`). The event then orders by its collection time.
- **Order within a player:** source `DateTime`, then `LastEdited`, then collection time, then sequence. A late edit of an older update never supersedes a newer update.

## 5. Snapshot reconciliation

- **Recording changes.** A change in the current list is recorded as a `SNAPSHOT_OBSERVATION`, timed by collection only and tagged `MATCHES_LATEST_NEWS` / `NO_MATCHING_NEWS_UPDATE`. A player leaving the list is recorded as `NOT_LISTED` / `LEFT_CURRENT_LIST`, which means no report, not ACTIVE. News lines are never touched.
- **Effective status rules:**
  1. The latest news update wins, unless a *newer* snapshot disagrees.
  2. A newer, disagreeing snapshot gives `UNKNOWN` with gate `AVAILABILITY_SOURCE_CONFLICT`. Neither source is picked, so the player fails closed.
  3. Snapshot-only evidence is used as-is.
- **Proposed for architect review:** how a persistent conflict should resolve, for example the snapshot winning after N consecutive polls. Until ruled on, a conflict stays gated.

## 6. Identity (strict; never fuzzy)

1. **Explicit maps:** `data/prod/maps/rotowire_player_map.csv` (keyed by `rotowire_player_id` or `sportdataio_player_id`) and `rotowire_team_map.csv` (keyed by `rotowire_team_id`).
2. **Team:** the exact normalized school or display name (`matching.team_index`).
3. **Player:** the exact normalized `FirstName LastName` within the resolved team's 2026-27 roster (`flagship.rosters`).
   - Several hits, or a position-group conflict (G/F/C), gives AMBIGUOUS.
   - No hit gives UNMATCHED.

Only RESOLVED events reach a player. An unresolved record whose current status is not ACTIVE / NOT_LISTED fails its games closed with `AVAILABILITY_IDENTITY_UNRESOLVED`: the games of its resolved team, or **every** game when its team is unresolved too. The record stays stored for QA.

## 7. Gates (all block readiness and actionability; `readiness.AVAILABILITY_*`; production-board matrix)

| code | level | when | fail-closed matrix row |
|---|---|---|---|
| `AVAILABILITY_FEED_UNAVAILABLE` | game | no PROCESSED news **and** snapshot acquisition at or before as_of | MISSING_STATE |
| `AVAILABILITY_STALE` | game | oldest of the two latest acquisitions is older than the policy bound | MISSING_STATE |
| `AVAILABILITY_IDENTITY_UNRESOLVED` | game | §6 | PLAYER_IDENTITY_AMBIGUITY |
| `AVAILABILITY_UNCERTAIN_ROTATION` | game | gated status on a rotation player | **UNRESOLVED_AVAILABILITY** |
| `AVAILABILITY_UNCERTAIN` | row | GTD / QUESTIONABLE / DOUBTFUL / RETURN_DESIGNATION | **UNRESOLVED_AVAILABILITY** |
| `AVAILABILITY_SOURCE_CONFLICT` / `AVAILABILITY_STATUS_UNRECOGNIZED` | row | conflicting sources / unrecognized code | UNSUPPORTED_PLAYER_STATUS |

**Freshness bound.** These are the repository's existing bounds (`PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md` §8), not a new number:

| minutes to tip | bound |
|---|---|
| ≥ 24 h | 6 h |
| ≥ 6 h | 90 min |
| < 6 h (T1H / T30M / T10M) | 20 min |

### 7a. Board policy (architect ruling 2026-10-31)

New matrix row `UNRESOLVED_AVAILABILITY` = (publication yes, pricing yes, actionable **no**, retry yes, alert no).
Internal pricing rows carry `board_state` ∈ {`PRICED_ACTIONABLE`, `PRICED_BLOCKED`, `SUPPRESSED`},
`actionable_block_reasons`, `actionable_bet_allowed`, `stake` (always 0.0: no staking policy) and the RotoWire
`availability_status` / `availability_received_at` the row was priced with. The status is internal only (it is not a
public field, and actionability codes are removed from a publishable row's public `suppression_reasons`).

| status | board |
|---|---|
| GTD / Q / D / unresolved IR-R | projection, PMF, fair probabilities and prices, diagnostic raw edge vs a valid quote; `PRICED_BLOCKED`, `UNRESOLVED_AVAILABILITY` |
| OUT / SUSP / NA / LONG_TERM_OUT | `SUPPRESSED` (`CONFIRMED_UNAVAILABLE`) |
| conflict / unrecognized | `SUPPRESSED` |
| ACT / no report | actionable only when every gate passes (below) |
| DNP | POSTGAME_ONLY: never pregame evidence |

**Actionability** (`production_board.actionable_block_reasons`; ACTIONABLE only with no reason). Every input is from ONE run.
- `UNRESOLVED_AVAILABILITY`: the status the row was priced with is unresolved.
- `AVAILABILITY_SOURCE_NOT_CONFIGURED` / `AVAILABILITY_FEED_UNAVAILABLE` / `AVAILABILITY_STALE_AT_CHECKPOINT`: the run's availability observation must be within the §7 bound at the checkpoint.
- `QUOTE_PREDATES_AVAILABILITY_STATE`: the sportsbook quote must be posted after the status in force became knowable.
- `REPRICE_REQUIRED`: a status newer than the run's prediction instant.
- Global: `AUTOMATIC_ACTIONABLE_WAGER_DISABLED`, `BETTING_POLICY_MISSING`, `DISABLED_BY_AVAILABILITY_VALIDATION` (G4B), `WAGERING_DISABLED`.

**GTD/Q/D → ACT never unlocks an earlier price.** The run that priced the player while uncertain keeps that status at every
later checkpoint. Only a new run (fresh availability → current player/minutes state → fresh quote posted after the ACT
confirmation → fresh price and edge → all gates) can be actionable (`test_uncertain_to_active_requires_fresh_quote_and_reprice`).

**Rights register.** `RotoWire CBB API (availability)` is added as **UNVERIFIED**, so any public transport refuses. This only makes the gate stricter.
Changing it to `PRODUCTION_APPROVED` needs a new, approved register version (COMMERCIAL_DATA_RIGHTS_AND_PRODUCTION_SOURCE_POLICY §2–§3) backed by:
1. the executed RotoWire license / subscription agreement (or order form) naming the licensee and the CBB feeds used;
2. the permitted uses: commercial internal modeling and pricing, and whether derived outputs (projections, prices, availability status) may be published on WizardOfOdds;
3. display / attribution and redistribution restrictions, especially on the raw status, news and analysis text;
4. retention and caching terms for the raw licensed payloads;
5. a written architect attestation recording 1–4 with its date.

## 8. Polling (configurable; PROPOSED_NOT_APPROVED)

| mode | interval | when |
|---|---|---|
| `CRITICAL_PRE_TIP` | 5 min | a tip within `ROTOWIRE_CRITICAL_WINDOW_HOURS = 3` |
| `GAME_DAY` | 10 min | a tip within 24 h |
| `OFF_DAY` | 180 min | otherwise |

- **One poll** is news then snapshot: 2 live calls, budget 6 per stage. `poll` runs only when due, and provider failures are recorded, never retried in a storm (2 retries with backoff).
- **No schedule is installed.** Wiring `rotowire_feed poll` into a scheduler is a separate, authorized change.
- **Cadence against the bound.** At the 5-minute cadence the 20-minute T1H bound tolerates 3 missed polls.

## 9. G4A / G4B (docs/AVAILABILITY_SOURCE_GATES_G4A_G4B.md applies to RotoWire separately)

| gate | status |
|---|---|
| G4A-1 real snapshot | **OPEN** (no key: `ROTOWIRE_LIVE_TEST = BLOCKED_NO_KEY`) |
| G4A-2 / 3 / 4 / 7 | mechanism READY (tests) |
| G4A-5 mapping report | per-poll identity counts (`ingest`), `smoke` identity counts; first real report on the first live poll |
| G4A-6 schema accepted | **OPEN** (`SCHEMA_VERIFIED = False`) |
| G4B | **ACCUMULATING** (`PC.AVAILABILITY_G4B_STATUS`): frozen item 1 is ≥ 14 consecutive operating days **or** ≥ 250 material rows, *whichever is larger*; plus ≥ 99 % team and ≥ 97 % player mapping. Until PASS: `REAL_MONEY_ACTIONABILITY = DISABLED_BY_AVAILABILITY_VALIDATION`. Collection, projections, pricing and prospective scoring still run. |

## 10. Not integrated (possible future challenger inputs)

RotoWire CBB Lineups, Depth Chart and DailyProjectedMinutes are **not** used. Each would be a model input, which needs its
own chronological validation as a challenger. Each is out of scope here.
