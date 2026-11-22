# CBB audit 2026-11-22: live certification of 111e621, selection / actionability / market-disagreement audit

Production release audited: `111e6219d23ceaa66ff90a7ff23713f3a9314693` (Droplet, deployed 2026-11-21 22:49Z).
Evidence: frozen player evaluations 2026-11-16 .. 2026-11-21 (886, 832 graded through 11-20 + 11-21 pending),
corrected close history (rebuild 2026-11-21 22:52Z). Aggregates in `results/selection_audit.json` (no player-level
rows in this public repo). Script: `scripts/selection_audit.py` (run on the Droplet, read-only).

## 1. Live certification of 111e621 (real production evidence)

| check | evidence | result |
|---|---|---|
| PLAYER_CLOSE_CAPTURE | 11-21 slate: 44 / 44 tipped market rows EXACT_LINE close; 11-20: 222 EXACT_LINE + 10 SAME_LINE_NEAREST, 0 NO_CLOSE | PASS |
| GAME_CLOSE_CAPTURE | 11-21: 124 / 124 tipped game evaluations closed (80 EXACT, 17 SAME_LINE, 27 LINE_MOVED), 0 NO_CLOSE | PASS |
| STALE_OFFER_INVALIDATION | 3 `GAME_MARKET_STALE_OFFER` records frozen 23:00Z (ALT_TOTAL / ALT_SPREAD last posted 15:26-15:40Z); 0 evaluations priced on those markets | PASS |
| CLOSE_ONLY_LOCK_PATH | writer lock held 00:19:30-00:23:15Z (transient unit); 00:20Z tick logged `CBB writer lock busy; close-window game-line capture under the game-quotes lock (rc=0)`; capture GQ20261122T002003Z mode CLOSE_ONLY_WRITER_BUSY COMPLETE 2610 rows; tick then took the writer lock at release and continued | PASS |
| GRADES_NAMING | exports/2026-11-21/CBB_Grades_11_20_2026.xlsx; receipt EXPORT_DATE 2026-11-21, GRADED_SLATE_DATE 2026-11-20; no CBB_Grades_11_21_2026.xlsx | PASS |
| PERFORMANCE_UPDATE | CBB_Performance_11_21_2026.xlsx 23:02Z from dataset sha 66923dd9 (886 rows, no_close_n 0, exact_line 667) | PASS |

## 2. Historical close rebuild (verified)

886 / 886 frozen evaluations re-run through `market_eval.evaluate` on the persisted PMF + the record's own decision
observation reproduce the stored best offer exactly (book, line, side, decimal price, model probability, EV): the
forecast probability, PMF, side, line, book, decision and (re-graded) result were not altered. The rebuild receipt
(`../2026-11-21/results/player_close_rebuild_receipt.json`) shows 0 non-CLV differences.
Rows rebuilt 360 (353 graded + 7 VOID_DNP); NO_CLOSE 360 -> 0; EXACT_LINE 261; SAME_LINE_NEAREST 3; LINE_MOVED 0;
NO_DEVIG_PAIR 96 (one-sided OVER-only markets: unrecoverable as probability CLV, close line recorded).

Limitation: player probability CLV is degenerate (mean +0.0003, positive 2.6%): the player quote cadence makes the
entry observation and the close observation the same capture for most rows. CLV cannot discriminate player rules yet.

## 3-4. Observability + deterministic replay

Frozen player evaluations now persist `candidate_offer_set` (schema `cbb-candidate-offers-1`: every priced offer with
book, line, side, American / decimal odds, model p, push p, fair p, edge, EV gross / net, paired, quote timestamps,
quote_id, status; unpriceable and excluded quotes with their reason). Frozen game evaluations add one
`GAME_CANDIDATE_OFFERS` record per game (market type, side, point, team ids, PMF sha256, STALE_OFFER_INELIGIBLE offers).
`cbb_platform/prod/selection_replay.py` replays exactly four rules; replay hash
`0f824befdb0c083fe274ee70896a8ae2874260b3485c843e4879952ded2111db` (reproducible from frozen evidence).

## 5. Selection rules (active picks, graded WIN/LOSS)

| rule | N | mean model p | hit | gap | Brier | market Brier | LL | market LL | mean CLV | ROI |
|---|---|---|---|---|---|---|---|---|---|---|
| MAX_EV (incumbent) | 587 | .640 | .516 | +.124 | .2654 | .2483 | .728 | .690 | +.0003 | -2.7% |
| MAX_EDGE | 679 | .624 | .508 | +.116 | .2631 | .2475 | .723 | .688 | +.0003 | -4.6% |
| MAX_PROB_POS_EV | 587 | .641 | .516 | +.125 | .2664 | .2490 | .731 | .691 | +.0003 | -2.8% |
| CONSERVATIVE_MAX_EV (edge >= .03) | 576 | .643 | .514 | +.129 | .2654 | .2477 | .728 | .689 | +.0003 | -3.6% |

All four are equivalent within noise and all are worse than the de-vigged market by ~0.017 Brier / ~0.04 log loss.
No rule improves proper scores or CLV -> the incumbent max-EV rule is kept.

## 6. Market disagreement (model p - fair p)

Selection-free population (one OVER offer per market, 679): model mean P(OVER) .390, market .491, realized .487
(gap -9.7pp, ~5 SE). Information slope of (y - q) on (p - q): 0.14 +- 0.13 (0 = no information beyond the market,
1 = fully informative). Every disagreement bucket's hit rate tracks the market, not the model; the only buckets where
the model's Brier beats the market are tiny (>+10pp OVER, N = 17). Large disagreements are the model's location bias,
not information.

## 7. STAT x ROLE x SIDE (MAX_EV graded rows; flags declared before the results were read)

BAD: PTS STARTER UNDER (N 120, gap +.22), AST STARTER UNDER (88, +.19), PRA STARTER UNDER (106, +.11).
GOOD (small margin, not significant): FG3M STARTER UNDER (88). WATCH: REB STARTER UNDER (106).
Every other cell INSUFFICIENT_SAMPLE (50 cells). ROLE_INCREASE / ROLE_DECREASE / RETURNING_FROM_ABSENCE are not
derivable from frozen prediction-time evidence (no prior-season / absence fields persisted); almost every prop is on a
starter, so LOW_MINUTES_BENCH has no market rows.

## 8-9. Actionability

The incumbent screen's own subset (stored actionable = True, N 53): model p .679, hit .472, market .491, Brier .300 vs
market .250, ROI -9.8%. No minimum edge / EV / probability band, disagreement cap, stat family or side passes.
Policy: Is Actionable = NO for every player prop (`market_eval.ACTIONABILITY_VALIDATED = False`); 60 / 65 Percent Plus
remain reporting views of the unchanged screen. Selection, PMFs and pricing unchanged.

## 10. Game model (SHADOW, math unchanged)

274 graded game markets 11-19 .. 11-21: model Brier .2379 vs market .2366, LL .662, mean CLV +.0083, positive CLV 40%,
ROI +1.3% (noise). UNDER sides hit .391 vs model .576 (over-confident). Conference tier not yet mapped (all rows
NON_CONFERENCE in November). Stays SHADOW, never actionable.

## 11. Residual predictive deficiency (proposal only, not built)

With selection removed, the PMFs are biased at the market line for starters: P(OVER) too low by PTS -16pp, AST -18pp,
FG3M -10pp, PRA -7pp (REB +2pp, calibrated), HIGH_MINUTES_STARTER -18pp. This is a location (mean) deficiency for
high-usage starters early in the season, consistent with the 11-21 finding that the prior-season rate blend under-rates
improving starters, and not fixed by the rejected adaptive weighting (live re-price moved p .683 -> .680). A next
challenger is justified, scoped to the minutes x per-minute-rate location of starters, evaluated walk-forward against
the market line population; sample (4 graded slates, correlated within player) is small, so it must be confirmed on
more slates before any build.

## 12. Prospective evidence window (decision-gate population)

`REPLAYABLE_EVIDENCE_START` = the `frozen_at` of the first production player MARKET_EVALUATION carrying its persisted
`candidate_offer_set` (`selection_replay.replayable_evidence`; shown on CBB_Performance Overall and in
`performance_receipt.json`). The first game record (`GAME_CANDIDATE_OFFERS`, 2026-11-21 slate) was frozen
2026-11-22T03:00:13Z by d313729 and replays all 5 stored game evaluations exactly; the 11-21 slate had no player prop
market after that release went live, so the player start is the first 2026-11-22 cutoff. Records frozen earlier are
never back-filled; the retrospective reconstruction above is a diagnostic only.

Deterministic audit (Droplet, read-only, no provider call; writes only `<out>`):

```
systemd-run --pipe --wait --uid=cbb-originator --gid=cbb-originator \
  -p EnvironmentFile=/etc/sports-models/cbb-originator.env -p ReadOnlyPaths=/var/lib/sports-models \
  -p ReadWritePaths=<out> -p WorkingDirectory=/ -p MemoryMax=2000M -p CPUWeight=20 \
  -E PYTHONPATH=/opt/sports-models/cbb-originator/current -E PYTHONDONTWRITEBYTECODE=1 \
  -E OMP_NUM_THREADS=1 -E OPENBLAS_NUM_THREADS=1 \
  /opt/sports-models/cbb-originator/venv/bin/python -B -m cbb_platform.prod.selection_audit \
  audit /var/lib/sports-models/cbb-originator/prod/ops <out>          # prospective (default): the gate
# ... selection_audit verify-live <ops> <out>                         # LIVE_CANDIDATE_PERSISTENCE / _SELECTION_REPLAY
# ... selection_audit audit <ops> <out> --mode retrospective          # diagnostic incl. reconstructed pre-start sets
```

Output `<out>/selection_audit.json`: the four rules (N, mean p, hit, calibration gap, Brier, log loss, paired
model-minus-market Brier / log loss with SE, CLV, positive CLV %, ROI, mean edge / EV) overall and by stat, role,
side, probability bucket, disagreement bucket and CLV status; STAT x ROLE x SIDE flags per rule; the selection-free
ALL_MARKETS disagreement table and information slope; `model_beats_market`; the single pre-declared policy candidate;
game candidate replay checks; the decision gate and the actionability recommendation.

Decision gate, declared before any prospective evidence existed (`selection_replay`, not to be tuned on it):
`SELECTION_POLICY_VALIDATED` when one of the four rules or `POLICY_CANDIDATE` (CONSERVATIVE_MAX_EV, EV > 0, edge >=
0.03, |disagreement| <= 0.10, >= 3 two-sided books at the line) has >= 300 graded picks over >= 10 slates, paired
Brier below the market by > 2 SE, log loss below the market and |calibration gap| <= 0.05;
`SELECTION_POLICY_FAILED_MODEL_CHALLENGER_JUSTIFIED` when the sample is sufficient, nothing validates and a
selection-free STAT x ROLE cell (N >= 40) misses the realized OVER rate by > 0.05 and > 2 SE; otherwise
`INSUFFICIENT_SAMPLE`. Even VALIDATED only opens a promotion review: `ACTIONABILITY_VALIDATED` stays False until that
review is explicitly approved.

Retrospective diagnostic on 2026-11-22 (886 / 886 reconstructed exactly, 5 graded slates incl. 11-21): MAX_EV N 628,
Brier .2648 vs market .2481 (paired +.0168 +- .0064), every rule worse than the market; policy candidate N 15;
`model_beats_market` empty; information slope 0.17 +- 0.13; localized defects AST / PTS / PRA starters. Prospective:
0 replayable records, gate INSUFFICIENT_SAMPLE.
