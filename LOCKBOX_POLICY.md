# LOCKBOX_POLICY.md — 2025-26 final historical holdout

**Status:** FROZEN (architect ruling, Stage 1 closure, 2026-10-05) · **Enforced by:** `cbb_platform/lockbox.py`, `cbb_platform/source_fetch.py`, `tests/test_stage1_closure.py`, `tests/test_stage1_final.py`

## 1. What the lockbox is

The **2025-26 season** is an untouched final historical holdout.

| Convention | Lockbox value |
|---|---|
| CBBD season (end year) | `2026` |
| BDL season (start year) | `2025` |
| US/Eastern game dates | `2025-08-01` through `2026-07-31` |

## 2. Rules

Until the lockbox is formally opened (§4), nobody (human, script, or agent) may:

1. fetch its completed game outcomes;
2. fetch its completed player/team game statistics;
3. inspect the season for model selection;
4. use it for feature engineering decisions;
5. use it for tuning;
6. use it for calibration design;
7. use it for architecture comparisons.

The lockbox also covers anything derived from those outcomes, including PBP, substitutions, lineups, season aggregates, ratings, and box-score-derived rosters.

## 3. Enforcement (global, provider-independent — Stage 1 final unblock)

- **One guard, one season normalizer.** `cbb_platform/lockbox.py::canonical_season(provider, value)` converts every provider season to the canonical end-year before the rule runs. Raw integers are never compared across providers. Dates are converted by `season_of_date` (US/Eastern season boundary: Aug 1). The lockbox can be spelled in several ways:

  | Provider | Convention | The lockbox is |
  |---|---|---|
  | `cbbd`, `starter_pack` | end year | `2026` |
  | `bdl` (BALLDONTLIE) | **start year** | `2025` (BDL `2026` is 2026-27) |
  | `torvik` (future Bart Torvik adapter) | end year | `year=2026` |
  | any provider | season label | `"2025-26"` / `"2025-2026"` |

- **One fetch path.** `cbb_platform/source_fetch.py::get` is the only network fetch interface. CBBD and BDL both call it, and so must any future adapter (Torvik or other). It runs the request guard, performs the call, reduces any HTTP or transport error to `provider endpoint status` (no body, no headers, no credential), and runs the response guard before the caller can store anything. `test_every_network_adapter_uses_the_global_guard` checks the adapters' source code.
- **Request guard** (`check_request(provider, endpoint, params, known_game_season)`). It refuses:
  - any lockbox season, in any provider spelling or as a list element;
  - any explicit date or date range touching 2025-08-01..2026-07-31, including open-ended ranges;
  - any game id whose canonical season cannot be proven from the canonical database to be ≤ 2025 (fail closed);
  - unbounded requests, whose provider default is "now";
  - seasons after the lockbox (§5);
  - **unregistered providers**;
  - **unknown endpoints**, which are treated as GUARDED.

  Pure reference endpoints (conference, team and venue lists; BDL players) are exempt. Market-only endpoints (BDL odds) are guarded too, for simplicity. Sharp Stats (`sharp_stats`) is a file-capture ingest classified `ALLOWED_AVAILABILITY_CAPTURE`: current and future availability snapshots expose no completed 2025-26 outcomes.
- **Response guard** (`check_response(provider, payload)`). At any nesting depth, it refuses a payload containing a lockbox season (normalized with the provider's convention) or a game date or wallclock inside the window. In that case nothing is written to `data/raw`.
- **Tests.**
  - `tests/test_stage1_final.py`:
    - normalization tables for every provider;
    - "the same integer means different seasons" (CBBD 2025 allowed, BDL 2025 refused);
    - 19 lockbox requests covering games, player stats, team stats, PBP, lineups, substitutions, historical results and odds across CBBD, BDL and Torvik spellings;
    - future and unbounded refusals;
    - BDL refusals before the network call and before storage.
  - `tests/test_stage1_closure.py` scans every stored CBBD and BDL payload for lockbox data.
- **Physical state at the Stage 1 final unblock.**
  - The Starter Pack ends with season 2025.
  - The CBBD live smoke covered seasons 2019, 2024 and 2025 only.
  - The BDL smoke data covers BDL seasons 2023 and 2024 (canonical 2024 and 2025).
  - No 2025-26 payload exists anywhere in `data/raw`.

## 4. Opening the lockbox

The lockbox is opened **only after all four of the following are frozen and recorded**:

1. the predictive architecture;
2. the challenger set;
3. the tuning policy;
4. the calibration methodology.

The opening is a single, documented evaluation event. The guard is relaxed by an explicit, reviewed code change that cites the freeze record. It is never relaxed by a flag or an environment variable.

## 5. Current and future seasons

Information for 2026-27 (CBBD season 2027) and later may be ingested for production or shadow purposes under separate rules that are not yet written. Until those rules exist, the guard also refuses those seasons. Such ingestion never authorizes fetching 2025-26 outcomes.
