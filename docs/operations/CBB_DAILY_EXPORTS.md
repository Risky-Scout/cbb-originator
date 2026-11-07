# CBB daily Excel exports

> **SUPERSEDED IN PART (2026-11-07, Droplet deployment).** The installed layout is: immutable git-archive release
> `/opt/sports-models/cbb-originator/releases/<sha>` (+ root-owned `<sha>.sha256sums`, re-verified before every job)
> behind `current`; mutable state only under `/var/lib/sports-models/cbb-originator/{prod,walkforward,exports,locks}`
> (`CBB_PROD_ROOT` / `CBB_WF_ROOT` / `CBB_EXPORT_ROOT`); units `deploy/droplet/systemd/cbb-originator-{morning,
> refit-retry,availability@,quotes}`; shared heavy lock `/run/lock/sports-models/heavy.lock` (group
> `sports-models-heavy`, held by `nba-prop-daily.service` via an authorized drop-in and by the CBB walk-forward step
> only; busy for 30 min -> `DAILY_REFIT_STATUS = DEFERRED_SHARED_LOCK`, last-known-good kept, one 08:00 ET retry);
> heavy-unit limits `MemoryHigh=2800M` / `MemoryMax=3200M` / `MemorySwapMax=0` on the 8 GB host. The `.../app` checkout,
> bind mounts, `sports-model-heavy.lock` path and 12G/14G/13 GB values below are historical.


```
PYTHONPATH=. .venv/bin/python -m cbb_platform.prod.exports daily --date YYYY-MM-DD
```

`--date` is the slate date in America/New_York. The command writes three workbooks under
`data/prod/exports/YYYY-MM-DD/`. That directory is gitignored through `data/prod/`.

| Workbook | Sheets |
|---|---|
| `CBB_Boards_MM_DD_YYYY.xlsx` | Full Board · 60 Percent Plus (MODEL% ≥ 0.60) · 65 Percent Plus (MODEL% ≥ 0.65) |
| `CBB_Distributions_MM_DD_YYYY.xlsx` | Distributions (official marginal PMFs, full mass, folded tail point kept) |
| `CBB_Grades_MM_DD_YYYY.xlsx` | Graded Bets · Performance Summary (six sections) |

Optional overrides: `--ops-root`, `--runs-root`, `--out-root`, `--history-db` (team names only), `--state-root`.

Exit codes:
- **0**: all three workbooks were written.
- **2**: at least one workbook's evidence is absent. That workbook is **not** written, and its missing dependency is
  printed in the JSON report. Nothing is ever fetched to fill the gap.

## Evidence read (read-only; no network: the module imports no network or acquisition code)

| Need | Source |
|---|---|
| slate games | `ops/registry/games.jsonl`: the decided `PREDICTED` row per game (tick, run, scheduled tip, cutoff rule) |
| board / grade candidates | `ops/ticks/<tick>/side_monitor.jsonl.gz`: hash-chained QUOTE_SIDE receipts. There is one per prediction × sportsbook offer side, and the chain is verified on read. |
| official selections | `ops/selections/<D>/official_selections.jsonl`: one write-once, content-hashed receipt per GAME × PLAYER × PROP (see below) |
| outcomes | `ops/ticks/*/side_settlements.parquet` (CBBD final box). `NO_ACTION` becomes VOID; no settlement yet means PENDING. |
| PMFs | the durable evidence: `ops/ticks/<tick>/pmfs.parquet` for official predictions and `ops/forecasts/<D>/<snapshot>/pmfs.parquet` for forecast snapshots (`prod.pmf_evidence`). Per game, the latest prediction made before the game's cutoff is exported. Legacy fallback: `runs/<run_id>/market_pmfs.parquet` |
| names | `core.teams` of the frozen history store and of the current state increment (falls back to the team id) |

Field conventions:
- **MODEL%**: `p_side_nonpush`.
- **MARKET%**: the same book's two-sided no-vig probability, as in the prospective monitor. It is blank when the book
  quoted only one side.
- **EDGE**: MODEL% − MARKET%.
- **EV**: the receipt's `model_edge`.
- **WINDOW**: the registry cutoff rule.
- **CAPTURE_UTC**: the quote's `collector_received_at`.
- **PLAYER_ROLE**: computed from the receipt's prediction-time `predicted_minutes` (min_mu). It never uses realized
  minutes.
- **UNSUPPORTED**: American odds that are missing, non-finite or have |odds| < 100, and any side other than OVER/UNDER.
- **Profit**: one unit risked per row.

## Official best-offer selection (Board and Grades grain)

Boards and Grades have exactly one row per **GAME × PLAYER × PROP**. BOOK, LINE and PICK are not part of the grain.

1. **Eligible offers.** A pre-tip QUOTE_SIDE receipt qualifies when all of these hold:
   - side OVER or UNDER;
   - valid American odds (|odds| ≥ 100);
   - finite MODEL%, EV and LINE;
   - a quote timestamp strictly before the game's cutoff and not after the evaluation time;
   - a prediction at or before the cutoff.
2. **Primary rule.** The official row is the single offer with the **maximum production EV** (`model_edge`) across
   every book, line and side. No side or line is chosen first, and no side is favored.
3. **Exact ties only** (EV equal after rounding to 1e-12) are broken in this order:
   1. Higher MODEL%.
   2. If every remaining candidate has the same PICK, the bettor-favorable line: OVER lower, UNDER higher. Skipped when
      the PICKs differ.
   3. Higher decimal odds (`1 + odds/100` if odds > 0, else `1 + 100/|odds|`).
   4. Higher EDGE (blank sorts last).
   5. OVER before UNDER.
   6. Normalized BOOK (trimmed, lower case), ascending.

   The ties are resolved by sequential filtering, so the result never depends on input order.
4. **Receipts** are written to `ops/selections/<D>/official_selections.jsonl`, append-only.
   - Before the game's cutoff the current best offer is a `PROVISIONAL` receipt. A later quote refresh that changes it
     appends a new `PROVISIONAL` receipt; identical content is idempotent.
   - The first export at or after the cutoff writes the `OFFICIAL_FROZEN` receipt. After that, no quote, line, book or
     price can replace it; different content for a frozen key is a hard failure.
   - Each receipt records:
     - game, player and team ids;
     - stat, pick, line, book and odds;
     - MODEL%, MARKET%, EDGE and EV;
     - expected minutes, PLAYER_ROLE and model version;
     - forecast snapshot id, prediction, quote and selection timestamps;
     - window and cutoff;
     - `content_hash` and `receipt_hash`. A tampered receipt fails the export.
5. **Sheets.**
   - Full Board holds the frozen or the latest provisional row per key. A duplicate key aborts the workbook.
   - 60 Percent Plus and 65 Percent Plus are pure filters of Full Board (MODEL% ≥ 0.60 / 0.65).
   - Graded Bets and every Performance Summary table use `OFFICIAL_FROZEN` receipts only, with the frozen odds.
     Rejected offers are never graded.
6. **Quote refresh vs. forecast refresh.**
   - `python -m cbb_platform.prod.lifecycle exports` runs after every Droplet shadow tick (`cbb-originator-run tick`).
     It rebuilds the workbooks from persisted evidence and never fits or re-forecasts.
   - Quotes are bought only by the official shadow tick, inside `[cutoff - 50 min, cutoff)`, and priced on that tick's
     PMFs.

## Distributions snapshot selection

Each game uses, independently, the **latest valid prediction made at or before the game's cutoff**:
1. The official tick's `pmfs.parquet`. It is ignored if made after the cutoff.
2. Otherwise the latest forecast snapshot.
3. Otherwise the legacy run PMFs.

Post-cutoff snapshots are never used, and nothing is chosen by outcome.

## Invariants

- Receipts captured at or after the tip are excluded and counted (`no_post_tip_leakage`).
- `grade_receipts.json`, kept beside the workbooks, records each receipt's immutable fields. A re-export may fill in
  PENDING outcomes, but it fails (`ExportIntegrityError`) if an immutable field changed or a receipt disappeared.
- A PMF with a negative value, or with mass more than 1e-9 from 1, fails the export. 1e-9 is the production PMF
  tolerance (`prod.pricing.SUM_TOL`).
- Reruns over unchanged evidence produce semantically identical workbooks (fixed zip timestamps also make them
  byte-identical).
- XLSX is written with the standard library, so no dependency was added.

## Evidence status (updated 2026-11-04, live daily lifecycle directive)

1. **PMFs (fixed).**
   - Each official prediction tick writes `pmfs.parquet` into its tick directory, which is part of the committed
     evidence tree.
   - Each forecast snapshot writes `pmfs.parquet` into `ops/forecasts/<D>/<snapshot>/`.
   - Distributions therefore no longer depend on the runner's temporary run directory. The fields are listed in
     `cbb_platform/prod/pmf_evidence.py`.
2. **Distributions are line-independent.**
   - The workbook is written whenever durable PMFs exist for the slate, including forecast snapshots that have no
     sportsbook quote.
   - Boards and Grades still require real pre-tip priced QUOTE_SIDE receipts. Unquoted forecasts are never graded as
     bets.
3. **Scheduling.** `prod.lifecycle` runs:
   - 07:00 ET: settlement, the prior-day Grades, the walk-forward update, the INITIAL forecast and the exports.
   - 10:00 / 13:00 / 16:00 / 18:00 ET: the RotoWire refresh, then the exports.

   The Droplet unit templates are in `deploy/droplet/lifecycle/`; they are prepared but not installed.
   See `docs/operations/CBB_LIVE_DAILY_LIFECYCLE_2026-11-04.md`.
