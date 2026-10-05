# Game identity audit (Stage 2K)

Rendered from `data/qa/game_identity_audit.json` by `cbb_platform/game_identity_audit.py`. Full candidate list: `data/qa/game_identity_candidates.csv`.

Scope: every pair of 2004-05..2024-25 canonical games between the same two teams (either orientation) whose US/Eastern dates are at most one day apart. **Nothing was merged or deleted.**

## Classes

| class | pairs |
|---|---|
| LEGITIMATE_DISTINCT_GAME | 554 |
| POSSIBLE_DUPLICATE | 6 |
| TRUE_DUPLICATE | 41 |
| UNRESOLVED | 1 |

## By season

| season | LEGITIMATE_DISTINCT_GAME | POSSIBLE_DUPLICATE | TRUE_DUPLICATE | UNRESOLVED |
|---|---|---|---|---|
| 2005 | 0 | 0 | 0 | 1 |
| 2012 | 0 | 0 | 8 | 0 |
| 2013 | 0 | 6 | 33 | 0 |
| 2014 | 1 | 0 | 0 | 0 |
| 2021 | 550 | 0 | 0 | 0 |
| 2022 | 3 | 0 | 0 | 0 |

## Downstream contamination scope

- Canonical ids involved in TRUE/POSSIBLE duplicates: 94
- Box-score double counting: none: no candidate pair has box rows on both ids
- Pairs where BOTH records carry a final score: 47 (TRUE_DUPLICATE: 41)
- Team-seasons whose ALL-RESULTS aggregates count both copies: 83 (columns: fact.team_season_game_derived.games_with_result, wins, losses, points_all_results, opp_points_all_results)
- Rows on the involved ids, per table: fact.team_games=94, fact.player_games=923, fact.participation=1261, core.game_schedule_versions=94, fact.plays=9082, fact.shots=4180, fact.substitutions=89, fact.lineup_stats=0

Box-score based aggregates (`games_with_team_box` and the box columns) are unaffected because no candidate pair has box rows on both ids. The all-results columns double count the result of each duplicated contest. **Remediation is not performed in Stage 2** (no destructive change is needed: the recommended fix is an exclusion view), and is reported for an architect decision.

## Stage 1 Purdue-Alabama note

Stage 1 reported two Starter Pack game ids for the same Purdue-Alabama date. Re-measured: id=671 on 2024-11-15 (87-78). games.csv, team_game_stats, player_game_stats and the CBBD API all carry exactly ONE id (671) for this contest; the observation is not reproducible and no candidate pair involves it.

## TRUE_DUPLICATE / POSSIBLE_DUPLICATE / UNRESOLVED pairs

| season | ET date | game a | game b | matchup a | matchup b | score a | score b | box a/b | class |
|---|---|---|---|---|---|---|---|---|---|
| 2005 | 2004-12-22 | 118538 | 118539 | Indiana State vs USC | USC vs Indiana State | 64-68 | 81-78 | N/N | UNRESOLVED |
| 2012 | 2011-11-11 | 77802 | 77823 | Yale vs Central Connecticut | Yale vs Central Connecticut | 73-69 | 73-69 | N/Y | TRUE_DUPLICATE |
| 2012 | 2011-11-19 | 78252 | 78290 | NC State vs Vanderbilt | NC State vs Vanderbilt | 79-86 | 79-86 | Y/N | TRUE_DUPLICATE |
| 2012 | 2011-11-24 | 78567 | 78568 | San Francisco vs Dartmouth | San Francisco vs Dartmouth | 71-69 | 71-69 | N/Y | TRUE_DUPLICATE |
| 2012 | 2011-11-25 | 78582 | 78583 | Southern Miss vs UC Irvine | Southern Miss vs UC Irvine | 78-67 | 78-67 | N/Y | TRUE_DUPLICATE |
| 2012 | 2011-11-26 | 78649 | 78651 | Oklahoma vs Santa Clara | Oklahoma vs Santa Clara | 85-73 | 85-73 | N/Y | TRUE_DUPLICATE |
| 2012 | 2011-12-23 | 79893 | 79894 | Hawai'i vs Auburn | Hawai'i vs Auburn | 62-65 | 62-65 | N/Y | TRUE_DUPLICATE |
| 2012 | 2012-03-10 | 83340 | 83341 | UC Santa Barbara vs Cal Poly | UC Santa Barbara vs Cal Poly | 64-52 | 64-52 | N/Y | TRUE_DUPLICATE |
| 2012 | 2012-03-11 | 83363 | 83364 | New Mexico State vs Louisiana Tech | New Mexico State vs Louisiana Tech | 82-57 | 82-57 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-09 | 71900 | 71901 | UTSA vs Morgan State | UTSA vs Morgan State | 0-0 | 59-71 | N/Y | POSSIBLE_DUPLICATE |
| 2013 | 2012-11-09 | 72002 | 72003 | Arkansas-Pine Bluff vs Houston Christian | Arkansas-Pine Bluff vs Houston Christian | 68-72 | 68-72 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-10 | 72012 | 72013 | Hawai'i vs Maryland Eastern Shore | Hawai'i vs Maryland Eastern Shore | 76-64 | 76-64 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-11 | 72094 | 72095 | Morgan State vs Holy Cross | Morgan State vs Holy Cross | 0-0 | 73-74 | N/Y | POSSIBLE_DUPLICATE |
| 2013 | 2012-11-12 | 72168 | 72169 | Maryland Eastern Shore vs Arkansas-Pine Bluff | Maryland Eastern Shore vs Arkansas-Pine Bluff | 52-62 | 52-62 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-16 | 72328 | 72387 | Saint Joseph's vs Notre Dame | Saint Joseph's vs Notre Dame | 79-70 | 79-70 | Y/N | TRUE_DUPLICATE |
| 2013 | 2012-11-16 | 72334 | 72335 | North Carolina Central vs South Dakota | North Carolina Central vs South Dakota | 69-81 | 69-81 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-17 | 72419 | 72421 | Eastern Illinois vs UT Rio Grande Valley | Eastern Illinois vs UT Rio Grande Valley | 63-50 | 63-50 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-17 | 72444 | 72451 | Cal State Northridge vs Tulsa | Cal State Northridge vs Tulsa | 92-76 | 92-76 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-18 | 72475 | 72476 | Maryland Eastern Shore vs Loyola Chicago | Maryland Eastern Shore vs Loyola Chicago | 46-62 | 46-62 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-18 | 72517 | 72518 | Louisiana Tech vs Troy | Louisiana Tech vs Troy | 70-46 | 70-46 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-19 | 72541 | 72542 | South Alabama vs Tennessee State | South Alabama vs Tennessee State | 57-68 | 57-68 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-20 | 72648 | 72649 | Iowa vs Western Kentucky | Iowa vs Western Kentucky | 63-55 | 63-55 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-20 | 72656 | 72657 | Eastern Washington vs Utah Valley | Eastern Washington vs Utah Valley | 83-86 | 83-86 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-21 | 72659 | 72714 | Michigan vs Pittsburgh | Michigan vs Pittsburgh | 67-62 | 67-62 | Y/N | TRUE_DUPLICATE |
| 2013 | 2012-11-21 | 72671 | 72672 | Samford vs Toledo | Samford vs Toledo | 0-0 | 69-82 | N/Y | POSSIBLE_DUPLICATE |
| 2013 | 2012-11-21 | 72721 | 72722 | Northeastern vs UC Riverside | Northeastern vs UC Riverside | 61-52 | 61-52 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-22 | 72723 | 72724 | Alaska Anchorage vs Belmont | Alaska Anchorage vs Belmont | 0-0 | 60-74 | N/Y | POSSIBLE_DUPLICATE |
| 2013 | 2012-11-22 | 72725 | 72740 | Louisville vs Northern Iowa | Louisville vs Northern Iowa | 51-46 | 51-46 | Y/N | TRUE_DUPLICATE |
| 2013 | 2012-11-22 | 72726 | 72731 | Duke vs Minnesota | Duke vs Minnesota | 89-71 | 89-71 | Y/N | TRUE_DUPLICATE |
| 2013 | 2012-11-22 | 72741 | 72742 | Charlotte vs Texas State | Charlotte vs Texas State | 73-64 | 73-64 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-23 | 72746 | 72797 | Wisconsin vs Creighton | Wisconsin vs Creighton | 74-84 | 74-84 | Y/N | TRUE_DUPLICATE |
| 2013 | 2012-11-23 | 72765 | 72766 | Utah Valley vs South Carolina Upstate | Utah Valley vs South Carolina Upstate | 0-0 | 50-75 | N/Y | POSSIBLE_DUPLICATE |
| 2013 | 2012-11-23 | 72776 | 72784 | Arkansas vs Arizona State | Arkansas vs Arizona State | 68-83 | 68-83 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-24 | 72802 | 72803 | California vs Georgia Tech | California vs Georgia Tech | 68-57 | 68-57 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-11-25 | 72891 | 72892 | Charlotte vs Northeastern | Charlotte vs Northeastern | 67-59 | 67-59 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-12-15 | 73590 | 73609 | Butler vs Indiana | Butler vs Indiana | 88-86 | 88-86 | Y/N | TRUE_DUPLICATE |
| 2013 | 2012-12-15 | 73591 | 73653 | Michigan vs West Virginia | Michigan vs West Virginia | 81-66 | 81-66 | Y/N | TRUE_DUPLICATE |
| 2013 | 2012-12-21 | 73894 | 73896 | Wagner vs SMU | Wagner vs SMU | 63-53 | 63-53 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-12-22 | 73907 | 73922 | Temple vs Syracuse | Temple vs Syracuse | 83-79 | 83-79 | Y/N | TRUE_DUPLICATE |
| 2013 | 2012-12-22 | 73957 | 73966 | Mississippi Valley State vs Georgia Southern | Mississippi Valley State vs Georgia Southern | 52-63 | 52-63 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-12-22 | 73958 | 73963 | Richmond vs George Mason | Richmond vs George Mason | 64-67 | 64-67 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-12-22 | 73959 | 73960 | Hofstra vs Tulane | Hofstra vs Tulane | 62-83 | 62-83 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-12-22 | 73984 | 73987 | South Carolina vs Manhattan | South Carolina vs Manhattan | 63-57 | 63-57 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-12-22 | 73985 | 73986 | North Florida vs Cal State Bakersfield | North Florida vs Cal State Bakersfield | 80-70 | 80-70 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-12-22 | 74018 | 74019 | Colorado State vs Portland | Colorado State vs Portland | 70-53 | 70-53 | N/Y | TRUE_DUPLICATE |
| 2013 | 2012-12-23 | 74035 | 74036 | New Orleans vs Boise State | New Orleans vs Boise State | 49-67 | 51-67 | N/Y | POSSIBLE_DUPLICATE |
| 2013 | 2013-02-09 | 76044 | 76046 | New Orleans vs Houston Christian | New Orleans vs Houston Christian | 68-75 | 68-75 | N/Y | TRUE_DUPLICATE |
| 2013 | 2013-03-14 | 77491 | 77492 | San Diego State vs Boise State | San Diego State vs Boise State | 73-67 | 73-67 | N/Y | TRUE_DUPLICATE |
