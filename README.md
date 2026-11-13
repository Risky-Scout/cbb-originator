# CBB Originator

A production NCAA men's basketball player-prop forecasting system that produces full player-stat probability
distributions, updates forecasts as availability information changes, and performs daily point-in-time walk-forward
retraining on newly completed games.

## What It Predicts

| market | stat |
|---|---|
| Points | PTS |
| Rebounds | REB |
| Assists | AST |
| Three-pointers made | FG3M |
| Steals | STL |
| Blocks | BLK |
| Turnovers | TOV |
| Points + Rebounds + Assists | PRA |
| Points + Rebounds | PR |
| Points + Assists | PA |
| Rebounds + Assists | RA |
| Blocks + Steals | STOCKS |

For every eligible player in every eligible game, the model produces a **full discrete probability mass function** for
each stat, not only a point estimate. Over/under prices at any line, including the push probability on integer lines,
are read directly from that PMF. Combination markets are sums of the same joint simulation draws, so they keep the
dependence between their components. P(play) is reported separately from the conditional-on-playing distribution.

## Modeling Architecture

The production model is the JM1 joint model (`modeling/player_props_v2/`, registry `modeling/MODEL_REGISTRY.json`).

- **Player-level marginals.** Count distributions per stat (negative binomial or Poisson, with fitted dispersion)
  driven by per-minute rates, shrunk toward position priors and built from season-to-date, prior-season and
  rolling-window player state.
- **Playing-time uncertainty.** A participation model gives P(play). A minutes model (ridge mean with SD bands) gives a
  minutes distribution. One minutes draw per player feeds all seven of that player's stats.
- **Shared game state.** A game-environment layer (pace, efficiency, game-level shocks, overtime law → expected game
  duration) is drawn once per game and shared by every player in it.
- **Interconnected simulation.** One 20,000-draw joint bank per game simulates environment → minutes → conditional
  means → counts for both rosters together.
- **Dependence structure.** A conditional-mean GLM ties each player's stat means to the shared state. Residual
  dependence between stats and teammates comes from a latent Gaussian copula (J2) fitted on true out-of-sample
  residuals. The game correlation matrix is checked for positive semi-definiteness.
- **Discrete PMFs.** Each player-stat PMF is normalized and non-negative, and is persisted as evidence for every
  forecast.
- **Point-in-time features.** Every feature row is built at the game's production cutoff (tip − 1 h) from the state
  version that existed before that cutoff. Target-game outcomes are stripped (`cbb_platform/stage3/`,
  `cbb_platform/prod/features.py`).
- **Walk-forward retraining.** Every morning the approved parameters are re-estimated on all legally available
  completed games, using the same procedure and the same frozen architecture (`cbb_platform/walkforward/`). The current
  season gets the highest weight in the marginal layer.
- **Deterministic repeat-fit checks.** Each candidate is fitted twice from identical inputs, and the two fits must
  produce bit-identical artifacts.
- **Promotion gates.** A candidate becomes ACTIVE only if every hard gate passes: input integrity, point-in-time
  legality, the leakage flags, unchanged architecture, finite coefficients and valid dispersion, deterministic repeat
  fit, scorer invariants, golden-slate production smoke, and rollback availability (`cbb_platform/walkforward/gates.py`).

Architecture changes are not made by the daily refit. They require a new artifact, a registry entry and an explicit
promotion.

## Daily Production Lifecycle

All times are America/New_York. Production runs on systemd timers on a single Droplet (`deploy/droplet/`).

```
overnight completed-game ingestion        quote tick every 10 min: incremental CBBD sync of newly final games
  → incomplete-final retries              completion backlog re-synced every 20 min, bounded to 24 h per game
  → pre-refit refresh                     dedicated sync in the 45 min before the refit cutoff, with bounded retries
  → morning training cutoff               06:00 ET; training state = latest state version as-of the cutoff
  → walk-forward refit                    07:00 morning job (01:30 feature warm-up; one 08:00 retry if deferred)
  → deterministic validation              two independent fits, bit-identical
  → candidate promotion or incumbent      atomic pointer swap if every gate passes, else ACTIVE stays
    retention
  → daily player-prop forecasts           INITIAL snapshot of the full eligible slate on the ACTIVE model
  → availability refreshes                08:30 acquisition poll; 10:00 / 13:00 / 16:00 / 18:00 refreshes
  → updated pre-cutoff forecasts          only games whose availability changed and whose cutoff has not passed
  → Boards / Distributions / Grades       Excel exports regenerated after each snapshot
```

Each game's official priced prediction is made once, at its tip − 1 h cutoff. A forecast snapshot is immutable: a later
refresh never rewrites an earlier snapshot or a game whose cutoff has passed.

## Reliability and Leakage Controls

- **Point-in-time legality.** A completed game trains a model only when it is final, box-complete, reconciled (player
  points = team points = game score), identity-resolved and knowable at the cutoff. A game that fails is DEFERRED to a
  later version, never discarded (`walkforward/eligibility.py`).
- **Stale-state rejection.** If no sync confirmed the state within 60 minutes before the refit cutoff, the refit is
  refused rather than trained on stale data.
- **Per-game acquisition failure isolation.** A failed provider request leaves only its own games incomplete. Every
  other game is still acquired and ingested (`prod/sync.py`).
- **Completion backlog.** A played, knowable game that is not yet complete in state re-triggers a sync until it is
  complete or its retry window expires.
- **Bounded retries.** Retry windows, attempt counts and unit time budgets are fixed in configuration
  (`prod/config.py`, `availability_refresh.py`, the systemd units).
- **Single-writer production state.** One host is the production writer (`CBB_SCHEDULER_ROLE=PRIMARY_WRITER`), and
  writer-lock holders never overlap.
- **Availability-refresh lock isolation.** RotoWire acquisition runs outside the writer lock. Publication takes the lock
  only afterwards, under a watchdog-bounded hold (`cbb_platform/availability_refresh.py`).
- **Atomic promotion.** Model versions are written complete, fsynced, then published with a single `os.replace`. The
  ACTIVE and ROLLBACK pointers are swapped atomically.
- **Last-known-good fallback.** A failed sync leaves the last-known-good state in place. A refused or deferred refit
  leaves the last-known-good model ACTIVE, and the day is still forecast with it.
- **CI.** `.github/workflows/cbb-production-ci.yml` runs on every push to a `production/**` branch. A release is
  deployed only from a pushed, CI-green commit.
- **Rollback.** Model rollback is a human-approved pointer change, never a refit. Code rollback is an atomic symlink
  swap to a previous immutable release (`docs/operations/CBB_DROPLET_PRODUCTION_RUNBOOK.md` §4).

## Outputs

Written per slate date by `cbb_platform/prod/exports.py`. It is read-only over persisted evidence and makes no network
calls.

| file | sheets | content |
|---|---|---|
| `CBB_Boards_MM_DD_YYYY.xlsx` | Full Board, 60 Percent Plus, 65 Percent Plus | priced pre-tip predictions: projection, model probability, two-sided no-vig market probability, expected value |
| `CBB_Distributions_MM_DD_YYYY.xlsx` | Distributions | the full PMF of every forecast player-stat from the latest pre-cutoff forecast |
| `CBB_Grades_MM_DD_YYYY.xlsx` | Graded Bets, Performance Summary | prior-day priced predictions settled against official final box scores (DNP → void) |

## Repository Map

| path | contents |
|---|---|
| `cbb_platform/prod/` | production ingestion, forecasting, availability, evidence and exports |
| `cbb_platform/walkforward/` | daily eligibility, training, deterministic fitting and promotion |
| `cbb_platform/stage3/` | point-in-time features and labels |
| `modeling/` | model artifacts and retained research evidence |
| `deploy/droplet/` | production entrypoints and systemd configuration |
| `tests/` | model, equivalence, production lifecycle and reliability tests |
| `docs/` | architecture, governance and operations |

A detailed module map is in `docs/REPOSITORY_MAP.md`. The walk-forward design is in
`docs/DAILY_WALK_FORWARD_REFIT_2026_27.md`, and operations are in `docs/operations/`.

## Status

An active 2026-27 daily walk-forward forecasting system. Daily model versions are named `cbb-jm1-2026-27-YYYYMMDD`.
The season opened on the baseline trained through the complete 2025-26 season, and each morning's version adds the
newly eligible 2026-27 games.

Wagering and third-party publication are disabled in configuration.

## Running Locally

Fast checks (no network, no credentials, no retraining):

```bash
python -m pytest -q tests/test_prod_shadow.py tests/test_prod_golden_equivalence.py tests/test_model_registry.py
python -m cbb_platform.prod.run verify-artifacts      # sha256 + identity of the frozen opening artifacts
```

The CI test list is in `.github/workflows/cbb-production-ci.yml`. The full suite and any refit need the local
historical stores under `data/` (gitignored, rebuildable).

Credentials (`CBBD_API_KEY`, `ODDS_API_KEY`, `ROTOWIRE_API_KEY`, ...) are read from the environment only
(`cbb_platform/credentials.py`). They are never committed or logged. `deploy/droplet/env/cbb-originator.env.example`
lists the variables with empty values.

## Governance

- `docs/ARCHITECTURE_PRECEDENCE.md` decides which document wins and which documents are historical records.
- `CBB_Blueprint_v1_4.md` is the governing architecture, and `PROJECT_EXECUTION_ORDER_v1_1.md` is the phase plan.
- The point-in-time contract is `POINT_IN_TIME_POLICY_v2_0.md`, with `POINT_IN_TIME_POLICY.md` v1.2 still authoritative
  for the HR-1..HR-7 legality rules.
- The lockbox rules are in `LOCKBOX_POLICY.md`, the one-time 2025-26 opening is in `LOCKBOX_POLICY_v2.md`, and the
  outcome is in `docs/LOCKBOX_RESULT_2025_26.md`.
- Scoring and retraining policy is in `PRODUCTION_SCORING_RETRAINING_AND_DEPLOYMENT_POLICY_v1_0.md`, as amended for the
  daily refit by `docs/DAILY_WALK_FORWARD_REFIT_2026_27.md`.
- The historical training dataset is `cbb_data_v2.4+5f19d31e524a` (`data/manifests/DATASET_MANIFEST.json`). Its
  manifest status label is `RESEARCH_CANDIDATE`, which is the dataset vocabulary and not the status of the production
  system.
