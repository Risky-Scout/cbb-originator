# CBBD event coverage discovery (Stage 2A)

Rendered from `data/qa/cbbd_event_coverage_discovery.json` by `cbb_platform/event_discovery.py`. Do not edit by hand.

**These are SAMPLE findings** (a fixed, hash-ordered stratified sample of ~13 games per season; manifest `data/qa/cbbd_event_coverage_sample_manifest.json`). They decide *where to backfill*; they are never a claim of full-season coverage. Measured full-season coverage is in `data/qa/cbbd_event_coverage_by_season.csv` (Stage 2O).

Cell = present/tested games (failed requests are counted separately and never as absence).

| season | games | pbp | onfloor | shotinfo | substitutions | lineups | rosters | onFloor share (mean) | valid 5v5 share (mean) | shotInfo share (mean) |
|---|---|---|---|---|---|---|---|---|---|---|
| 2005 | 12 | NONE_IN_SAMPLE 0/12 | NOT_TESTED 0/0 | NOT_TESTED 0/0 | NONE_IN_SAMPLE 0/12 | NONE_IN_SAMPLE 0/12 | PRESENT (1535 teams) | - | - | - |
| 2006 | 12 | PARTIAL 5/12 | NONE_IN_SAMPLE 0/5 | PRESENT 5/5 | NONE_IN_SAMPLE 0/12 | NONE_IN_SAMPLE 0/12 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2007 | 12 | PARTIAL 5/12 | NONE_IN_SAMPLE 0/5 | PRESENT 5/5 | NONE_IN_SAMPLE 0/12 | NONE_IN_SAMPLE 0/12 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2008 | 12 | PARTIAL 7/12 | NONE_IN_SAMPLE 0/7 | PRESENT 7/7 | NONE_IN_SAMPLE 0/12 | NONE_IN_SAMPLE 0/12 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2009 | 12 | PARTIAL 9/12 | NONE_IN_SAMPLE 0/9 | PRESENT 9/9 | NONE_IN_SAMPLE 0/12 | NONE_IN_SAMPLE 0/12 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2010 | 13 | PARTIAL 9/13 | NONE_IN_SAMPLE 0/9 | PRESENT 9/9 | NONE_IN_SAMPLE 0/13 | NONE_IN_SAMPLE 0/13 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2011 | 12 | PARTIAL 7/12 | NONE_IN_SAMPLE 0/7 | PRESENT 7/7 | NONE_IN_SAMPLE 0/12 | NONE_IN_SAMPLE 0/12 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2012 | 12 | PARTIAL 5/12 | NONE_IN_SAMPLE 0/5 | PRESENT 5/5 | NONE_IN_SAMPLE 0/12 | NONE_IN_SAMPLE 0/12 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2013 | 12 | PARTIAL 8/12 | NONE_IN_SAMPLE 0/8 | PRESENT 8/8 | PARTIAL 1/12 | NONE_IN_SAMPLE 0/12 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2014 | 12 | PARTIAL 11/12 | NONE_IN_SAMPLE 0/11 | PRESENT 11/11 | NONE_IN_SAMPLE 0/12 | NONE_IN_SAMPLE 0/12 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2015 | 13 | PRESENT 13/13 | NONE_IN_SAMPLE 0/13 | PRESENT 13/13 | NONE_IN_SAMPLE 0/13 | NONE_IN_SAMPLE 0/13 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2016 | 13 | PARTIAL 11/13 | NONE_IN_SAMPLE 0/11 | PRESENT 11/11 | NONE_IN_SAMPLE 0/13 | NONE_IN_SAMPLE 0/13 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2017 | 13 | PRESENT 13/13 | NONE_IN_SAMPLE 0/13 | PRESENT 13/13 | NONE_IN_SAMPLE 0/13 | NONE_IN_SAMPLE 0/13 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2018 | 13 | PARTIAL 12/13 | NONE_IN_SAMPLE 0/12 | PRESENT 12/12 | NONE_IN_SAMPLE 0/13 | NONE_IN_SAMPLE 0/13 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2019 | 12 | PRESENT 12/12 | NONE_IN_SAMPLE 0/12 | PRESENT 12/12 | NONE_IN_SAMPLE 0/12 | NONE_IN_SAMPLE 0/12 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2020 | 10 | PRESENT 10/10 | NONE_IN_SAMPLE 0/10 | PRESENT 10/10 | NONE_IN_SAMPLE 0/10 | NONE_IN_SAMPLE 0/10 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2021 | 13 | PARTIAL 11/13 | NONE_IN_SAMPLE 0/11 | PRESENT 11/11 | NONE_IN_SAMPLE 0/13 | NONE_IN_SAMPLE 0/13 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2022 | 13 | PRESENT 13/13 | NONE_IN_SAMPLE 0/13 | PRESENT 13/13 | NONE_IN_SAMPLE 0/13 | NONE_IN_SAMPLE 0/13 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2023 | 13 | PARTIAL 12/13 | NONE_IN_SAMPLE 0/12 | PRESENT 12/12 | NONE_IN_SAMPLE 0/13 | NONE_IN_SAMPLE 0/13 | PRESENT (1535 teams) | 0.000 | 0.000 | 1.000 |
| 2024 | 13 | PRESENT 13/13 | PRESENT 13/13 | PRESENT 13/13 | PRESENT 13/13 | PRESENT 13/13 | PRESENT (1535 teams) | 1.000 | 0.865 | 1.000 |
| 2025 | 12 | PRESENT 12/12 | PRESENT 12/12 | PRESENT 12/12 | PRESENT 12/12 | PRESENT 12/12 | PRESENT (1535 teams) | 1.000 | 0.991 | 1.000 |

## Escalation probes (/plays/date on busy dates)

Run only for seasons whose game sample returned no PBP. Each date covers dozens of games.

- 2005 2004-12-04 (ET games that date: 111): request OK, plays 0, games with plays 0
- 2005 2004-12-11 (ET games that date: 89): request OK, plays 0, games with plays 0
- 2005 2005-01-15 (ET games that date: 131): request OK, plays 0, games with plays 0
- 2005 2005-02-05 (ET games that date: 129): request OK, plays 0, games with plays 0
- 2005 2005-02-19 (ET games that date: 125): request OK, plays 0, games with plays 0
- 2005 2005-03-10 (ET games that date: 50): request OK, plays 0, games with plays 0

## Empty strata

- 2020: no candidate game in POST/MAJOR, POST/MID, POST/SMALL
