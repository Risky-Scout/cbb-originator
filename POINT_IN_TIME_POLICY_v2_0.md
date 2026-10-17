# POINT_IN_TIME_POLICY v2.0 — five checkpoints with explicit fidelity

**Version:** 2.0 · **Date:** 2026-10-07 · **Status:** AUTHORITATIVE, **FROZEN (design)**. The historical checkpoint rebuild is
Phase A2. **Supersedes** `POINT_IN_TIME_POLICY.md` v1.2 (preserved) on: the checkpoint set (§2), fidelity/availability
classes (§3), schedule AS-OF semantics (§4), the conservative historical fallback (§5) and manufactured timestamps (§6).
**Unchanged and still authoritative from v1.2:** the timestamp vocabulary (§1 there), the legality rule (§2 there) and
historical reconstruction rules HR-1..HR-7, the prohibitions (§5 there) and the lockbox. Storage contract:
`docs/BITEMPORAL_CHECKPOINT_SCHEMA.md`.

## 1. Principle

A prediction at checkpoint *k* of game *g* may use an observation only if (a) the observation was legitimately knowable
before the checkpoint's cutoff, and (b) the evidence for (a) is of a **fidelity class that this checkpoint permits**.
Unknown timing is represented as unknown. It is never filled in with an invented instant.

## 2. The five checkpoints (required)

| code | nominal offset Δ from the anchor tip | role |
|---|---|---|
| `T24H` | 24 h | early line, schedule mostly settled |
| `T6H` | 6 h | game-day state |
| `T1H` | 1 h | near-final availability |
| `T30M` | 30 min | final availability window |
| `T10M` | 10 min | last formal checkpoint |

`cutoff_k = anchor_tip_k − Δ_k`, where `anchor_tip_k` is defined by §4 (prospective) or §5 (historical). v1.2's "final
pre-tip" state is **not** a formal checkpoint. Prospectively, it survives only as an operational publication lock at
the observed actual tip. All five checkpoints of one game belong to the same validation fold. Structural parameters may
be shared across checkpoints.

## 3. Fidelity / availability classes

Every observation (and, by weakest link, every checkpoint feature value) carries exactly one class:

| class | meaning | factual time fields | typical sources |
|---|---|---|---|
| `PROSPECTIVE_EXACT` | captured live by our system; `source_received_at` is our real receipt time | `source_received_at` (never NULL) | Sharp Stats / Covers snapshots, live schedule polls, CBBD/BDL fetched before the game |
| `DATE_KNOWN` | historical fact whose real-world event time or date is known. Knowability comes from an explicit HR rule (e.g. HR-1: tip + 4 h, or 06:00 ET next day) | `event_time` / date; `policy_available_from` computed by the named rule | completed-game box scores, PBP and participation of earlier games |
| `PRESEASON_ASSUMED` | known before the season by policy, not by a timestamp: prior completed seasons, previous-season rosters, recruiting class year s-1, portal year s-1 | `policy_available_from` = the season's **policy open boundary** (§5.3). There is no factual receipt time, and the class says so | prior-season state, recruits, transfers, previous-season roster returners |
| `RETROSPECTIVE_FINAL_SCHEDULE` | the only schedule we have is the final retrospective one; reschedule history is invisible | `scheduled_tip_utc` of the final version; `observed_at` NULL | Starter Pack `games.csv` (HR-6) |
| `SOURCE_RECEIPT_UNKNOWN` | a mutable state observed retrospectively, with unknown valid time and unknown receipt time | none usable | end-of-season roster snapshots, roster attributes, current-season roster rows, post-hoc corrected values |
| `UNUSABLE_FOR_HISTORICAL_PIT` | measured untrustworthy or "today's value" of a past state | — | CBBD `wallclock` (UNTRUSTED_MIXED), non-date-bounded metrics (HR-4), box-file `start_date` offsets |

Weakest-link order (best → worst): `PROSPECTIVE_EXACT` > `DATE_KNOWN` > `PRESEASON_ASSUMED` >
`RETROSPECTIVE_FINAL_SCHEDULE` > `SOURCE_RECEIPT_UNKNOWN` > `UNUSABLE_FOR_HISTORICAL_PIT`. A derived value inherits the
weakest class among its inputs and records it (`min_input_class`).

### 3.1 Permission matrix (what may enter a checkpoint feature)

| class | historical T24H..T10M | prospective T24H..T10M | condition |
|---|---|---|---|
| `PROSPECTIVE_EXACT` | n/a | yes | `source_received_at < cutoff_k` |
| `DATE_KNOWN` | yes | yes | `policy_available_from < cutoff_k` (HR-1..HR-3), target game excluded (HR-2) |
| `PRESEASON_ASSUMED` | yes, **labeled** | yes, labeled | `policy_available_from <= cutoff_k`, and the season is the one the policy names |
| `RETROSPECTIVE_FINAL_SCHEDULE` | as the **anchor** only (§5), labeled | no (live versions exist) | — |
| `SOURCE_RECEIPT_UNKNOWN` | **no** (research tables only) | no | a field-specific architect ruling may promote a field to another class with evidence |
| `UNUSABLE_FOR_HISTORICAL_PIT` | never | never | — |

A field may enter a historical checkpoint feature **only** when its class permits it. Historical evidence with an unknown
receipt time is **never silently treated as prospectively known**: it is `SOURCE_RECEIPT_UNKNOWN` unless an HR rule
assigns `DATE_KNOWN` or `PRESEASON_ASSUMED`.

## 4. Schedule-version AS-OF semantics (prospective)

Schedule observations are append-only versions `v_1, v_2, …`, ordered by `source_received_at` (`o_i`), each with
tip `τ_i`. For checkpoint offset Δ, the active version at time t is the latest `v_i` with `o_i <= t`. The checkpoint
fires at the first instant t at which `t = τ(t) − Δ`. Operationally:

1. Scan versions in receipt order. For `v_i`, active on `[o_i, o_{i+1})`: if `τ_i − Δ ∈ [o_i, o_{i+1})`, then
   `cutoff = τ_i − Δ`, `anchor = v_i`, fidelity `PROSPECTIVE_EXACT`.
2. If a received version already has `τ_j − Δ < o_j` (moved earlier, or first seen late), the checkpoint fires at
   `o_j`, `anchor = v_j`, fidelity `LATE_FIRED`. It is never back-dated.
3. A version received after its game's cutoff never changes that checkpoint. Earlier versions are never rewritten
   (`superseded_at` closes them).
4. `DATE_ONLY` / `TBD` tips anchor at 00:00 US/Eastern of the game date (the conservative earliest tip) until a timed
   version is received.

## 5. Conservative historical fallback (no true schedule-version history)

1. The anchor is the single `RETROSPECTIVE_FINAL_SCHEDULE` version. `anchor_tip` = its final `scheduled_tip_utc`
   (EXACT), or 00:00 ET of the game date (DATE_ONLY / TBD). The checkpoint row records `checkpoint_fidelity =
   RETROSPECTIVE_FINAL_SCHEDULE`. **No intermediate schedule version is fabricated.**
2. The cutoffs are `anchor_tip − Δ_k`. Values may differ between checkpoints only through `DATE_KNOWN` inputs that cross
   a cutoff (e.g. an earlier same-day game becomes knowable at tip + 4 h). Otherwise identical historical values across
   T24H..T10M are correct and expected. They are labeled, not perturbed.
3. **Season policy open boundary** for `PRESEASON_ASSUMED`: 00:00 US/Eastern on the date of the season's first played
   trusted D-I game. It is a policy instant, stored as `policy_available_from` with `policy_rule =
   'SEASON_OPEN_BOUNDARY'`, never in `source_received_at`.
4. Historical availability (injury) states do not exist for 2013-14..2024-25. Historical checkpoints carry
   availability features as `NOT_APPLICABLE / SOURCE_NOT_AVAILABLE`, never as inferred states (HR-7).
5. A historical evaluation report must state the share of each checkpoint's feature mass by class.

## 6. Manufactured timestamps are prohibited

No schedule revision, provider receipt time or availability time is invented. No artificial ancient timestamp (for
example `1900-01-01`) may stand as a factual claim of availability. **Known nonconformance:** Stage 3
(`cbb_platform/stage3/universe.py`, `PRESEASON`) stamps preseason membership evidence `1900-01-01`. Under v2.0 it is
interpreted as `PRESEASON_ASSUMED` with an unknown factual time. It must be replaced in the Phase A2 rebuild by
`policy_available_from = SEASON_OPEN_BOUNDARY` plus the class column, before any model consumes those features.

## 7. Enforcement (Phase A2 must add)

* every observation and checkpoint feature row carries `availability_class` / `checkpoint_fidelity` (NOT NULL);
* a test that no `SOURCE_RECEIPT_UNKNOWN` or `UNUSABLE_FOR_HISTORICAL_PIT` input reaches an approved feature;
* a test that no `source_received_at` exists for any non-`PROSPECTIVE_EXACT` row, and no timestamp literal before
  2000-01-01 appears in any availability column;
* the §4 schedule AS-OF algorithm tested on synthetic reschedules (later, earlier, after-cutoff, DATE_ONLY → timed);
* per-checkpoint class-mass reporting.
