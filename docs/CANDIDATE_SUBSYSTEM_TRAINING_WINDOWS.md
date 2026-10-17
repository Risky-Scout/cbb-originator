# Candidate subsystem training windows (Stage 2T) -- PROPOSALS ONLY

Rendered by `cbb_platform/stage2_reports.py` from the measured per-season QA files and the canonical database. **Nothing here freezes a modeling window**; the quantitative architect approves/freezes after Stage 2. 2025-26 is the lockbox and is excluded everywhere. Latest development season for every subsystem: 2024-25 (2025).

Rule for a candidate window: the earliest season from which EVERY later development season meets every printed threshold (a contiguous window ending 2024-25). Seasons that pass in isolation before a failing season are listed as 'isolated qualifying seasons'. Metric definitions are under the table at the end.

## player latent talent

* Threshold: `player_box_cov` >= 0.9 and `player_id_res` >= 0.95.
* Earliest plausible season: **2014** (2013-14)
* Latest development season: 2025 (2024-25)
* Usable games with player box (D-I vs D-I) in the candidate window: **65,000**
* Isolated qualifying seasons outside the window: [2006, 2007, 2008, 2009, 2010, 2011]
* Seasons excluded and why: 2005 (player_box_cov=0.881<0.9); 2012 (player_box_cov=0.876<0.9); 2013 (player_box_cov=0.859<0.9)

## player shooting (shot-level)

* Threshold: `pbp_cov` >= 0.8 and `shooter` >= 0.95. Season-level shooting profiles also exist 2014-2025 in the Starter Pack.
* Earliest plausible season: **2014** (2013-14)
* Latest development season: 2025 (2024-25)
* Usable games with PBP in the candidate window: **66,333**
* Isolated qualifying seasons outside the window: none
* Seasons excluded and why: 2005 (pbp_cov=0.000<0.8, shooter=0.000<0.95); 2006 (pbp_cov=0.235<0.8); 2007 (pbp_cov=0.330<0.8); 2008 (pbp_cov=0.548<0.8); 2009 (pbp_cov=0.674<0.8); 2010 (pbp_cov=0.686<0.8); 2011 (pbp_cov=0.619<0.8); 2012 (pbp_cov=0.523<0.8); 2013 (pbp_cov=0.581<0.8)

## team/game environment

* Threshold: `team_box_cov` >= 0.9.
* Earliest plausible season: **2014** (2013-14)
* Latest development season: 2025 (2024-25)
* Usable games with team box (D-I vs D-I) in the candidate window: **65,018**
* Isolated qualifying seasons outside the window: [2006, 2007, 2008, 2009, 2010, 2011]
* Seasons excluded and why: 2005 (team_box_cov=0.881<0.9); 2012 (team_box_cov=0.876<0.9); 2013 (team_box_cov=0.859<0.9)

## participation/minutes

* Threshold: `player_box_cov` >= 0.9 and `player_id_res` >= 0.95. Box minutes only; exact on-court participation additionally needs substitutions (see rotation).
* Earliest plausible season: **2014** (2013-14)
* Latest development season: 2025 (2024-25)
* Usable games with player box in the candidate window: **65,000**
* Isolated qualifying seasons outside the window: [2006, 2007, 2008, 2009, 2010, 2011]
* Seasons excluded and why: 2005 (player_box_cov=0.881<0.9); 2012 (player_box_cov=0.876<0.9); 2013 (player_box_cov=0.859<0.9)

## lineup interaction

* Threshold: `valid5` >= 0.85 and `lineup_cov` >= 0.8.
* Earliest plausible season: **2024** (2023-24)
* Latest development season: 2025 (2024-25)
* Usable games with lineups in the candidate window: **11,085**
* Isolated qualifying seasons outside the window: none
* Seasons excluded and why: 2005 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2006 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2007 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2008 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2009 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2010 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2011 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2012 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2013 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2014 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2015 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2016 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2017 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2018 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2019 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2020 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2021 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2022 (valid5=0.000<0.85, lineup_cov=0.000<0.8); 2023 (valid5=0.000<0.85, lineup_cov=0.000<0.8)

## possession/event model

* Threshold: `pbp_cov` >= 0.8 and `recon` >= 0.9.
* Earliest plausible season: **2014** (2013-14)
* Latest development season: 2025 (2024-25)
* Usable games with PBP in the candidate window: **66,333**
* Isolated qualifying seasons outside the window: none
* Seasons excluded and why: 2005 (pbp_cov=0.000<0.8, recon=0.000<0.9); 2006 (pbp_cov=0.235<0.8, recon=0.864<0.9); 2007 (pbp_cov=0.330<0.8); 2008 (pbp_cov=0.548<0.8); 2009 (pbp_cov=0.674<0.8, recon=0.845<0.9); 2010 (pbp_cov=0.686<0.8); 2011 (pbp_cov=0.619<0.8); 2012 (pbp_cov=0.523<0.8); 2013 (pbp_cov=0.581<0.8)

## shot model (with location)

* Threshold: `pbp_cov` >= 0.8 and `shooter` >= 0.95 and `xy` >= 0.8.
* Earliest plausible season: **2022** (2021-22)
* Latest development season: 2025 (2024-25)
* Usable games with PBP in the candidate window: **24,232**
* Isolated qualifying seasons outside the window: none
* Seasons excluded and why: 2005 (pbp_cov=0.000<0.8, shooter=0.000<0.95, xy=0.000<0.8); 2006 (pbp_cov=0.235<0.8, xy=0.000<0.8); 2007 (pbp_cov=0.330<0.8, xy=0.000<0.8); 2008 (pbp_cov=0.548<0.8, xy=0.000<0.8); 2009 (pbp_cov=0.674<0.8, xy=0.000<0.8); 2010 (pbp_cov=0.686<0.8, xy=0.000<0.8); 2011 (pbp_cov=0.619<0.8, xy=0.000<0.8); 2012 (pbp_cov=0.523<0.8, xy=0.000<0.8); 2013 (pbp_cov=0.581<0.8, xy=0.000<0.8); 2014 (xy=0.000<0.8); 2015 (xy=0.000<0.8); 2016 (xy=0.000<0.8); 2017 (xy=0.000<0.8); 2018 (xy=0.000<0.8); 2019 (xy=0.268<0.8); 2020 (xy=0.682<0.8); 2021 (xy=0.747<0.8)

## substitution/rotation

* Threshold: `subs_cov` >= 0.8 and `timing` >= 0.99.
* Earliest plausible season: **2024** (2023-24)
* Latest development season: 2025 (2024-25)
* Usable games with substitutions in the candidate window: **11,477**
* Isolated qualifying seasons outside the window: none
* Seasons excluded and why: 2005 (subs_cov=0.000<0.8, timing=0.000<0.99); 2006 (subs_cov=0.000<0.8, timing=0.000<0.99); 2007 (subs_cov=0.000<0.8, timing=0.000<0.99); 2008 (subs_cov=0.000<0.8, timing=0.000<0.99); 2009 (subs_cov=0.000<0.8, timing=0.000<0.99); 2010 (subs_cov=0.000<0.8, timing=0.000<0.99); 2011 (subs_cov=0.000<0.8, timing=0.000<0.99); 2012 (subs_cov=0.000<0.8, timing=0.000<0.99); 2013 (subs_cov=0.034<0.8); 2014 (subs_cov=0.000<0.8, timing=0.000<0.99); 2015 (subs_cov=0.000<0.8, timing=0.000<0.99); 2016 (subs_cov=0.000<0.8, timing=0.000<0.99); 2017 (subs_cov=0.000<0.8, timing=0.000<0.99); 2018 (subs_cov=0.000<0.8, timing=0.000<0.99); 2019 (subs_cov=0.000<0.8, timing=0.000<0.99); 2020 (subs_cov=0.000<0.8, timing=0.000<0.99); 2021 (subs_cov=0.000<0.8, timing=0.000<0.99); 2022 (subs_cov=0.000<0.8, timing=0.000<0.99); 2023 (subs_cov=0.000<0.8, timing=0.000<0.99)

## Measured inputs by season

| season | di_games | team_box_cov | player_box_cov | player_id_res | pbp_cov | recon | shooter | xy | valid5 | subs_cov | timing | lineup_cov |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2005 | 4621 | 0.881 | 0.881 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2006 | 4660 | 0.959 | 0.959 | 0.999 | 0.235 | 0.864 | 0.991 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2007 | 4856 | 0.965 | 0.965 | 1.000 | 0.330 | 0.917 | 0.988 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2008 | 4919 | 0.997 | 0.997 | 1.000 | 0.548 | 0.940 | 0.990 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2009 | 4987 | 0.998 | 0.998 | 1.000 | 0.674 | 0.845 | 0.995 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2010 | 5065 | 0.999 | 0.999 | 1.000 | 0.686 | 0.902 | 0.994 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2011 | 5383 | 0.999 | 0.999 | 1.000 | 0.619 | 0.935 | 0.995 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2012 | 5350 | 0.876 | 0.876 | 1.000 | 0.523 | 0.904 | 0.992 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2013 | 5470 | 0.859 | 0.859 | 1.000 | 0.581 | 0.933 | 0.983 | 0.000 | 0.000 | 0.034 | 1.000 | 0.000 |
| 2014 | 5503 | 0.999 | 0.998 | 1.000 | 0.878 | 0.957 | 0.999 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2015 | 5494 | 1.000 | 0.999 | 1.000 | 0.902 | 0.959 | 0.999 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2016 | 5514 | 0.999 | 0.998 | 1.000 | 0.924 | 0.952 | 0.999 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2017 | 5488 | 0.999 | 0.998 | 0.998 | 0.940 | 0.958 | 0.999 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2018 | 5501 | 1.000 | 0.999 | 0.999 | 0.949 | 0.960 | 0.998 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2019 | 5597 | 1.000 | 0.999 | 1.000 | 0.924 | 0.967 | 0.999 | 0.268 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2020 | 5328 | 1.000 | 1.000 | 0.999 | 0.945 | 0.970 | 0.998 | 0.682 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2021 | 3944 | 1.000 | 1.000 | 1.000 | 0.953 | 0.978 | 1.000 | 0.747 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2022 | 5475 | 1.000 | 1.000 | 1.000 | 0.979 | 0.985 | 1.000 | 0.800 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2023 | 5715 | 1.000 | 1.000 | 1.000 | 0.985 | 0.984 | 1.000 | 0.843 | 0.000 | 0.000 | 0.000 | 0.000 |
| 2024 | 5726 | 1.000 | 1.000 | 1.000 | 0.986 | 0.983 | 0.998 | 0.875 | 0.866 | 0.999 | 1.000 | 0.985 |
| 2025 | 5764 | 0.999 | 0.999 | 1.000 | 0.981 | 0.982 | 0.999 | 0.986 | 0.974 | 0.999 | 1.000 | 0.945 |

## Does source quality change by era?

Read directly from the table above and `data/qa/cbbd_event_coverage_by_season.csv`:

* **Box-score era (2004-05 onward):** team/player boxes exist for every season; coverage dips in 2011-12 and 2012-13 (`team_box_cov`), where the Starter Pack also holds orphan duplicate game records (docs/GAME_IDENTITY_AUDIT.md).
* **PBP era:** no CBBD PBP for 2004-05; partial and uneven coverage until the season where `pbp_cov` first stays above threshold; shotInfo (shooter, range) accompanies PBP throughout; x/y locations and wallclock arrive later (see `xy`).
* **Tracking era (2023-24, 2024-25 only):** onFloor, substitutions and lineups exist only here. Any lineup, rotation or on-court participation subsystem is limited to two development seasons.

Thresholds used: `{"box_cov": 0.9, "id_res": 0.95, "pbp_cov": 0.8, "recon": 0.9, "shooter": 0.95, "xy": 0.8, "valid5": 0.85, "subs_cov": 0.8, "timing": 0.99, "lineup_cov": 0.8}`.

Metric definitions: `di_games` D-I vs D-I canonical games; `team_box_cov` / `player_box_cov` share of them with a team / player box; `player_id_res` resolved share of D-I player-game rows; `pbp_cov` share of D-I games with CBBD PBP; `recon` share of PBP games whose final PBP score equals the official score; `shooter` resolved shooter share of shotInfo rows; `xy` share of field-goal shots with x/y; `valid5` VALID_5V5 share of plays with onFloor; `subs_cov` share of D-I games with substitutions; `timing` share of substitution rows with complete in/out timing; `lineup_cov` share of D-I games with /lineups/game units.
