# CBB daily Excel exports

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
| board / grade population | `ops/ticks/<tick>/side_monitor.jsonl.gz`: hash-chained QUOTE_SIDE receipts. There is one per prediction × sportsbook offer side, and the chain is verified on read. |
| outcomes | `ops/ticks/*/side_settlements.parquet` (CBBD final box). `NO_ACTION` becomes VOID; no settlement yet means PENDING. |
| PMFs | `runs/<run_id>/market_pmfs.parquet` (written by `prod.run.daily`) or `ops/ticks/<tick>/market_pmfs.parquet` |
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

## Invariants

- Receipts captured at or after the tip are excluded and counted (`no_post_tip_leakage`).
- `grade_receipts.json`, kept beside the workbooks, records each receipt's immutable fields. A re-export may fill in
  PENDING outcomes, but it fails (`ExportIntegrityError`) if an immutable field changed or a receipt disappeared.
- A PMF with a negative value, or with mass more than 1e-9 from 1, fails the export. 1e-9 is the production PMF
  tolerance (`prod.pricing.SUM_TOL`).
- Reruns over unchanged evidence produce semantically identical workbooks (fixed zip timestamps also make them
  byte-identical).
- XLSX is written with the standard library, so no dependency was added.

## Current evidence gaps (2026-11-04)

1. Quotes:
   - No QUOTE_SIDE receipt exists yet. The quote integration is unvalidated, and every scored game so far is
     `ODDS_EVENT_NOT_YET_LISTED`.
   - Until a real priced offer exists, Boards and Grades report `MISSING_EVIDENCE`.
2. PMFs:
   - `market_pmfs.parquet` lives in the run directory. On the GitHub-hosted runner that directory is ephemeral, and it
     is not part of the ops evidence tree or the encrypted working set.
   - So Distributions has no real evidence for the GitHub-era ticks.
   - On a persistent host (the Droplet), `data/prod/runs` persists. Copying the file into the tick directory would be
     an evidence-serialization change and needs separate authorization.
3. Scheduling: none is wired. The command is ready to run after prediction production and the settlement refresh;
   scheduler integration is deferred to the cutover directive.
