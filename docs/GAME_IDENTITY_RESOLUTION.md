# Canonical game identity resolution (Stage 2.1)

Rendered from the built database by `cbb_platform/game_identity_report.py`. Rule: **one real basketball contest = one trusted canonical game.** `core.games` keeps every source record unchanged; `metadata.game_alias_map` states which records are the same contest; `flagship.games` (and `metadata.trusted_game_map`, through which every flagship fact view and every game-derived aggregate is routed) contains each real contest exactly once.

* raw source game records: 272,560 -> trusted games: 120,631 (development seasons: 120,679 -> 120,631)
* map rows: 1204 {'CANONICAL': 48, 'DUPLICATE_ALIAS': 47, 'LEGITIMATE_DISTINCT_GAME': 1108, 'SOURCE_CORRUPT_MISATTRIBUTED': 1}
* methods (non-legitimate rows): {'ARCHITECT_ADJUDICATION_STAGE_2_1': 14, 'RULE_TRUE_DUPLICATE_PRIORITY(box attachment)': 82}
* aliases: 47; quarantined corrupt records: 1
* affected team-seasons: 85; removed duplicate contributions: 96
* aggregate totals over affected team-seasons, old -> new: {'games': [2835, 2739], 'wins': [1517, 1474], 'losses': [1289, 1246], 'points_for': [191895, 186016], 'points_against': [186067, 180188]}
* resolution hash: `1da90b6c090a1a282882f7bae3d4cf1c5335857d31db4d171c7bab4173855c87`

## Representative selection

TRUE_DUPLICATE: (1) verified box attachment, (2) richer event data, (3) complete non-zero score, (4) smaller source id. Every pair was decided at (1). Event facts are built after resolution; the build then STOPS if any alias or corrupt record carries facts (none do), so (2) could not have changed an outcome.

POSSIBLE_DUPLICATE and UNRESOLVED pairs follow the architect's Stage 2.1 adjudication. The build STOPS if the adjudicated result contradicts the box-attached record's score.

## Fact preservation (v2.0 -> v2.1)

| table | v2.0 rows | v2.1 rows | rows on alias/corrupt records | rows resolving to trusted games | trusted view rows |
|---|---|---|---|---|---|
| fact.player_games | 2368206 | 2368206 | 0 | 2368206 | 2368206 |
| fact.team_games | 235612 | 235612 | 0 | 235612 | 235612 |
| fact.participation | 3189460 | 3189460 | 0 | 3189460 | 3189460 |
| fact.plays | 29021691 | 29021691 | 0 | 29021404 | - |
| fact.shots | 13638660 | 13638660 | 0 | 13638528 | - |
| fact.substitutions | 858399 | 858399 | 0 | 858399 | - |
| fact.lineup_stats | 370401 | 370401 | 0 | 370401 | 370401 |

`rows_without_canonical_game` (in the JSON) are event rows of the two CBBD games absent from games.csv, already quarantined in Stage 2 as GAME_NOT_IN_CANONICAL; they were never part of a trusted game.

## Every non-legitimate pair

| source game | class | resolves to | method | evidence |
|---|---|---|---|---|
| 71900 | DUPLICATE_ALIAS | G-42f90dec3f520dbc | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 71900/71901 2013 2012-11-09: UTSA 0-0 Morgan State (box 0/0) vs UTSA 59-71 Morgan State (box 2/19); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'Morgan State': 71, 'UTSA': 59} = record 71901 (box-attached); 71900 is the orphan |
| 71901 | CANONICAL | G-42f90dec3f520dbc | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 71900/71901 2013 2012-11-09: UTSA 0-0 Morgan State (box 0/0) vs UTSA 59-71 Morgan State (box 2/19); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'Morgan State': 71, 'UTSA': 59} = record 71901 (box-attached); 71900 is the orphan |
| 72002 | DUPLICATE_ALIAS | G-d709388aebffb6bd | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72002/72003 2013 2012-11-09: Arkansas-Pine Bluff 68-72 Houston Christian (box 0/0) vs Arkansas-Pine Bluff 68-72 Houston Christian (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72003 | CANONICAL | G-d709388aebffb6bd | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72002/72003 2013 2012-11-09: Arkansas-Pine Bluff 68-72 Houston Christian (box 0/0) vs Arkansas-Pine Bluff 68-72 Houston Christian (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72012 | DUPLICATE_ALIAS | G-8ff6197df69bf405 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72012/72013 2013 2012-11-10: Hawai'i 76-64 Maryland Eastern Shore (box 0/0) vs Hawai'i 76-64 Maryland Eastern Shore (box 2/22); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72013 | CANONICAL | G-8ff6197df69bf405 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72012/72013 2013 2012-11-10: Hawai'i 76-64 Maryland Eastern Shore (box 0/0) vs Hawai'i 76-64 Maryland Eastern Shore (box 2/22); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72094 | DUPLICATE_ALIAS | G-57f592eeddf4dd2d | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 72094/72095 2013 2012-11-11: Morgan State 0-0 Holy Cross (box 0/0) vs Morgan State 73-74 Holy Cross (box 2/20); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'Holy Cross': 74, 'Morgan State': 73} = record 72095 (box-attached); 72094 is the orphan |
| 72095 | CANONICAL | G-57f592eeddf4dd2d | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 72094/72095 2013 2012-11-11: Morgan State 0-0 Holy Cross (box 0/0) vs Morgan State 73-74 Holy Cross (box 2/20); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'Holy Cross': 74, 'Morgan State': 73} = record 72095 (box-attached); 72094 is the orphan |
| 72168 | DUPLICATE_ALIAS | G-c80da2a62227e7a2 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72168/72169 2013 2012-11-12: Maryland Eastern Shore 52-62 Arkansas-Pine Bluff (box 0/0) vs Maryland Eastern Shore 52-62 Arkansas-Pine Bluff (box 2/16); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72169 | CANONICAL | G-c80da2a62227e7a2 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72168/72169 2013 2012-11-12: Maryland Eastern Shore 52-62 Arkansas-Pine Bluff (box 0/0) vs Maryland Eastern Shore 52-62 Arkansas-Pine Bluff (box 2/16); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72328 | CANONICAL | G-eb74d9eddd9d154f | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72328/72387 2013 2012-11-16: Saint Joseph's 79-70 Notre Dame (box 2/14) vs Saint Joseph's 79-70 Notre Dame (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72334 | DUPLICATE_ALIAS | G-3cd442a167822660 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72334/72335 2013 2012-11-16: North Carolina Central 69-81 South Dakota (box 0/0) vs North Carolina Central 69-81 South Dakota (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72335 | CANONICAL | G-3cd442a167822660 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72334/72335 2013 2012-11-16: North Carolina Central 69-81 South Dakota (box 0/0) vs North Carolina Central 69-81 South Dakota (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72387 | DUPLICATE_ALIAS | G-eb74d9eddd9d154f | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72328/72387 2013 2012-11-16: Saint Joseph's 79-70 Notre Dame (box 2/14) vs Saint Joseph's 79-70 Notre Dame (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72419 | DUPLICATE_ALIAS | G-b9f63976ca55d561 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72419/72421 2013 2012-11-17: Eastern Illinois 63-50 UT Rio Grande Valley (box 0/0) vs Eastern Illinois 63-50 UT Rio Grande Valley (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72421 | CANONICAL | G-b9f63976ca55d561 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72419/72421 2013 2012-11-17: Eastern Illinois 63-50 UT Rio Grande Valley (box 0/0) vs Eastern Illinois 63-50 UT Rio Grande Valley (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72444 | DUPLICATE_ALIAS | G-1287a31664b9a8d9 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72444/72451 2013 2012-11-17: Cal State Northridge 92-76 Tulsa (box 0/0) vs Cal State Northridge 92-76 Tulsa (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72451 | CANONICAL | G-1287a31664b9a8d9 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72444/72451 2013 2012-11-17: Cal State Northridge 92-76 Tulsa (box 0/0) vs Cal State Northridge 92-76 Tulsa (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72475 | DUPLICATE_ALIAS | G-2c3d7269225c2251 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72475/72476 2013 2012-11-18: Maryland Eastern Shore 46-62 Loyola Chicago (box 0/0) vs Maryland Eastern Shore 46-62 Loyola Chicago (box 2/18); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72476 | CANONICAL | G-2c3d7269225c2251 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72475/72476 2013 2012-11-18: Maryland Eastern Shore 46-62 Loyola Chicago (box 0/0) vs Maryland Eastern Shore 46-62 Loyola Chicago (box 2/18); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72517 | DUPLICATE_ALIAS | G-15f2dfacfaf788b3 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72517/72518 2013 2012-11-18: Louisiana Tech 70-46 Troy (box 0/0) vs Louisiana Tech 70-46 Troy (box 2/23); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72518 | CANONICAL | G-15f2dfacfaf788b3 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72517/72518 2013 2012-11-18: Louisiana Tech 70-46 Troy (box 0/0) vs Louisiana Tech 70-46 Troy (box 2/23); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72541 | DUPLICATE_ALIAS | G-1163a59a3e7d8de3 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72541/72542 2013 2012-11-19: South Alabama 57-68 Tennessee State (box 0/0) vs South Alabama 57-68 Tennessee State (box 2/22); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72542 | CANONICAL | G-1163a59a3e7d8de3 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72541/72542 2013 2012-11-19: South Alabama 57-68 Tennessee State (box 0/0) vs South Alabama 57-68 Tennessee State (box 2/22); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72648 | DUPLICATE_ALIAS | G-b1abd5ac4cfc59f4 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72648/72649 2013 2012-11-20: Iowa 63-55 Western Kentucky (box 0/0) vs Iowa 63-55 Western Kentucky (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72649 | CANONICAL | G-b1abd5ac4cfc59f4 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72648/72649 2013 2012-11-20: Iowa 63-55 Western Kentucky (box 0/0) vs Iowa 63-55 Western Kentucky (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72656 | DUPLICATE_ALIAS | G-e2f83c54a02f98ad | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72656/72657 2013 2012-11-20: Eastern Washington 83-86 Utah Valley (box 0/0) vs Eastern Washington 83-86 Utah Valley (box 2/20); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72657 | CANONICAL | G-e2f83c54a02f98ad | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72656/72657 2013 2012-11-20: Eastern Washington 83-86 Utah Valley (box 0/0) vs Eastern Washington 83-86 Utah Valley (box 2/20); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72659 | CANONICAL | G-3cd5b2579ce1574b | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72659/72714 2013 2012-11-21: Michigan 67-62 Pittsburgh (box 2/20) vs Michigan 67-62 Pittsburgh (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72671 | DUPLICATE_ALIAS | G-da419651a845912d | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 72671/72672 2013 2012-11-21: Samford 0-0 Toledo (box 0/0) vs Samford 69-82 Toledo (box 2/19); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'Toledo': 82, 'Samford': 69} = record 72672 (box-attached); 72671 is the orphan |
| 72672 | CANONICAL | G-da419651a845912d | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 72671/72672 2013 2012-11-21: Samford 0-0 Toledo (box 0/0) vs Samford 69-82 Toledo (box 2/19); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'Toledo': 82, 'Samford': 69} = record 72672 (box-attached); 72671 is the orphan |
| 72714 | DUPLICATE_ALIAS | G-3cd5b2579ce1574b | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72659/72714 2013 2012-11-21: Michigan 67-62 Pittsburgh (box 2/20) vs Michigan 67-62 Pittsburgh (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72721 | DUPLICATE_ALIAS | G-9f4e4027c8974009 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72721/72722 2013 2012-11-21: Northeastern 61-52 UC Riverside (box 0/0) vs Northeastern 61-52 UC Riverside (box 2/20); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72722 | CANONICAL | G-9f4e4027c8974009 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72721/72722 2013 2012-11-21: Northeastern 61-52 UC Riverside (box 0/0) vs Northeastern 61-52 UC Riverside (box 2/20); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72723 | DUPLICATE_ALIAS | G-5c7f9ce56ae2e5ae | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 72723/72724 2013 2012-11-22: Alaska Anchorage 0-0 Belmont (box 0/0) vs Alaska Anchorage 60-74 Belmont (box 2/13); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'Belmont': 74, 'Alaska Anchorage': 60} = record 72724 (box-attached); 72723 is the orphan |
| 72724 | CANONICAL | G-5c7f9ce56ae2e5ae | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 72723/72724 2013 2012-11-22: Alaska Anchorage 0-0 Belmont (box 0/0) vs Alaska Anchorage 60-74 Belmont (box 2/13); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'Belmont': 74, 'Alaska Anchorage': 60} = record 72724 (box-attached); 72723 is the orphan |
| 72725 | CANONICAL | G-286ca672b07ee60d | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72725/72740 2013 2012-11-22: Louisville 51-46 Northern Iowa (box 2/19) vs Louisville 51-46 Northern Iowa (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72726 | CANONICAL | G-625669ea07b9934e | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72726/72731 2013 2012-11-22: Duke 89-71 Minnesota (box 2/20) vs Duke 89-71 Minnesota (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72731 | DUPLICATE_ALIAS | G-625669ea07b9934e | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72726/72731 2013 2012-11-22: Duke 89-71 Minnesota (box 2/20) vs Duke 89-71 Minnesota (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72740 | DUPLICATE_ALIAS | G-286ca672b07ee60d | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72725/72740 2013 2012-11-22: Louisville 51-46 Northern Iowa (box 2/19) vs Louisville 51-46 Northern Iowa (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72741 | DUPLICATE_ALIAS | G-958a1a91d62fc228 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72741/72742 2013 2012-11-22: Charlotte 73-64 Texas State (box 0/0) vs Charlotte 73-64 Texas State (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72742 | CANONICAL | G-958a1a91d62fc228 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72741/72742 2013 2012-11-22: Charlotte 73-64 Texas State (box 0/0) vs Charlotte 73-64 Texas State (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72746 | CANONICAL | G-c3b4dacf99d6f296 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72746/72797 2013 2012-11-23: Wisconsin 74-84 Creighton (box 2/18) vs Wisconsin 74-84 Creighton (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72765 | DUPLICATE_ALIAS | G-ad31189890285cd4 | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 72765/72766 2013 2012-11-23: Utah Valley 0-0 South Carolina Upstate (box 0/0) vs Utah Valley 50-75 South Carolina Upstate (box 2/21); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'South Carolina Upstate': 75, 'Utah Valley': 50} = record 72766 (box-attached); 72765 is the orphan |
| 72766 | CANONICAL | G-ad31189890285cd4 | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 72765/72766 2013 2012-11-23: Utah Valley 0-0 South Carolina Upstate (box 0/0) vs Utah Valley 50-75 South Carolina Upstate (box 2/21); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'South Carolina Upstate': 75, 'Utah Valley': 50} = record 72766 (box-attached); 72765 is the orphan |
| 72776 | DUPLICATE_ALIAS | G-ee7e1387a077371e | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72776/72784 2013 2012-11-23: Arkansas 68-83 Arizona State (box 0/0) vs Arkansas 68-83 Arizona State (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72784 | CANONICAL | G-ee7e1387a077371e | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72776/72784 2013 2012-11-23: Arkansas 68-83 Arizona State (box 0/0) vs Arkansas 68-83 Arizona State (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72797 | DUPLICATE_ALIAS | G-c3b4dacf99d6f296 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72746/72797 2013 2012-11-23: Wisconsin 74-84 Creighton (box 2/18) vs Wisconsin 74-84 Creighton (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72802 | DUPLICATE_ALIAS | G-72d692b946a49bbc | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72802/72803 2013 2012-11-24: California 68-57 Georgia Tech (box 0/0) vs California 68-57 Georgia Tech (box 2/17); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72803 | CANONICAL | G-72d692b946a49bbc | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72802/72803 2013 2012-11-24: California 68-57 Georgia Tech (box 0/0) vs California 68-57 Georgia Tech (box 2/17); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72891 | DUPLICATE_ALIAS | G-3091c119fb3aadce | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72891/72892 2013 2012-11-25: Charlotte 67-59 Northeastern (box 0/0) vs Charlotte 67-59 Northeastern (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 72892 | CANONICAL | G-3091c119fb3aadce | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 72891/72892 2013 2012-11-25: Charlotte 67-59 Northeastern (box 0/0) vs Charlotte 67-59 Northeastern (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73590 | CANONICAL | G-87aaca9ec3826ef7 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73590/73609 2013 2012-12-15: Butler 88-86 Indiana (box 2/20) vs Butler 88-86 Indiana (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73591 | CANONICAL | G-a53b1d4842731317 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73591/73653 2013 2012-12-15: Michigan 81-66 West Virginia (box 2/20) vs Michigan 81-66 West Virginia (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73609 | DUPLICATE_ALIAS | G-87aaca9ec3826ef7 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73590/73609 2013 2012-12-15: Butler 88-86 Indiana (box 2/20) vs Butler 88-86 Indiana (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73653 | DUPLICATE_ALIAS | G-a53b1d4842731317 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73591/73653 2013 2012-12-15: Michigan 81-66 West Virginia (box 2/20) vs Michigan 81-66 West Virginia (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73894 | DUPLICATE_ALIAS | G-5b3d17fc41de4f31 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73894/73896 2013 2012-12-21: Wagner 63-53 SMU (box 0/0) vs Wagner 63-53 SMU (box 2/18); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73896 | CANONICAL | G-5b3d17fc41de4f31 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73894/73896 2013 2012-12-21: Wagner 63-53 SMU (box 0/0) vs Wagner 63-53 SMU (box 2/18); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73907 | CANONICAL | G-360263259fec7ade | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73907/73922 2013 2012-12-22: Temple 83-79 Syracuse (box 2/18) vs Temple 83-79 Syracuse (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73922 | DUPLICATE_ALIAS | G-360263259fec7ade | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73907/73922 2013 2012-12-22: Temple 83-79 Syracuse (box 2/18) vs Temple 83-79 Syracuse (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73957 | DUPLICATE_ALIAS | G-fa06b56763797bcb | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73957/73966 2013 2012-12-22: Mississippi Valley State 52-63 Georgia Southern (box 0/0) vs Mississippi Valley State 52-63 Georgia Southern (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73958 | DUPLICATE_ALIAS | G-fa0f093e4f4f6c58 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73958/73963 2013 2012-12-22: Richmond 64-67 George Mason (box 0/0) vs Richmond 64-67 George Mason (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73959 | DUPLICATE_ALIAS | G-579f5aec08b3c4e8 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73959/73960 2013 2012-12-22: Hofstra 62-83 Tulane (box 0/0) vs Hofstra 62-83 Tulane (box 2/25); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73960 | CANONICAL | G-579f5aec08b3c4e8 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73959/73960 2013 2012-12-22: Hofstra 62-83 Tulane (box 0/0) vs Hofstra 62-83 Tulane (box 2/25); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73963 | CANONICAL | G-fa0f093e4f4f6c58 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73958/73963 2013 2012-12-22: Richmond 64-67 George Mason (box 0/0) vs Richmond 64-67 George Mason (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73966 | CANONICAL | G-fa06b56763797bcb | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73957/73966 2013 2012-12-22: Mississippi Valley State 52-63 Georgia Southern (box 0/0) vs Mississippi Valley State 52-63 Georgia Southern (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73984 | DUPLICATE_ALIAS | G-884abfb8d00efb5b | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73984/73987 2013 2012-12-22: South Carolina 63-57 Manhattan (box 0/0) vs South Carolina 63-57 Manhattan (box 2/20); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73985 | DUPLICATE_ALIAS | G-f2c2511bebe5bd2c | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73985/73986 2013 2012-12-22: North Florida 80-70 Cal State Bakersfield (box 0/0) vs North Florida 80-70 Cal State Bakersfield (box 2/20); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73986 | CANONICAL | G-f2c2511bebe5bd2c | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73985/73986 2013 2012-12-22: North Florida 80-70 Cal State Bakersfield (box 0/0) vs North Florida 80-70 Cal State Bakersfield (box 2/20); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 73987 | CANONICAL | G-884abfb8d00efb5b | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 73984/73987 2013 2012-12-22: South Carolina 63-57 Manhattan (box 0/0) vs South Carolina 63-57 Manhattan (box 2/20); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 74018 | DUPLICATE_ALIAS | G-0a952c58d8d9e6c7 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 74018/74019 2013 2012-12-22: Colorado State 70-53 Portland (box 0/0) vs Colorado State 70-53 Portland (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 74019 | CANONICAL | G-0a952c58d8d9e6c7 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 74018/74019 2013 2012-12-22: Colorado State 70-53 Portland (box 0/0) vs Colorado State 70-53 Portland (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 74035 | DUPLICATE_ALIAS | G-ede7814392718a30 | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 74035/74036 2013 2012-12-23: New Orleans 49-67 Boise State (box 0/0) vs New Orleans 51-67 Boise State (box 2/24); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'Boise State': 67, 'New Orleans': 51} = record 74036 (box-attached); 74035 is the orphan |
| 74036 | CANONICAL | G-ede7814392718a30 | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 74035/74036 2013 2012-12-23: New Orleans 49-67 Boise State (box 0/0) vs New Orleans 51-67 Boise State (box 2/24); audit: POSSIBLE_DUPLICATE (same orientation and tip, but a score is missing/0-0 or differs); architect result {'Boise State': 67, 'New Orleans': 51} = record 74036 (box-attached); 74035 is the orphan |
| 76044 | DUPLICATE_ALIAS | G-0409deaa496f2dc3 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 76044/76046 2013 2013-02-09: New Orleans 68-75 Houston Christian (box 0/0) vs New Orleans 68-75 Houston Christian (box 2/20); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 76046 | CANONICAL | G-0409deaa496f2dc3 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 76044/76046 2013 2013-02-09: New Orleans 68-75 Houston Christian (box 0/0) vs New Orleans 68-75 Houston Christian (box 2/20); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 77491 | DUPLICATE_ALIAS | G-3efd29186277d152 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 77491/77492 2013 2013-03-14: San Diego State 73-67 Boise State (box 0/0) vs San Diego State 73-67 Boise State (box 2/16); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 77492 | CANONICAL | G-3efd29186277d152 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 77491/77492 2013 2013-03-14: San Diego State 73-67 Boise State (box 0/0) vs San Diego State 73-67 Boise State (box 2/16); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 77802 | DUPLICATE_ALIAS | G-090158edd5188f41 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 77802/77823 2012 2011-11-11: Yale 73-69 Central Connecticut (box 0/0) vs Yale 73-69 Central Connecticut (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 77823 | CANONICAL | G-090158edd5188f41 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 77802/77823 2012 2011-11-11: Yale 73-69 Central Connecticut (box 0/0) vs Yale 73-69 Central Connecticut (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 78252 | CANONICAL | G-f43b96e56bf93b01 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 78252/78290 2012 2011-11-19: NC State 79-86 Vanderbilt (box 2/19) vs NC State 79-86 Vanderbilt (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 78290 | DUPLICATE_ALIAS | G-f43b96e56bf93b01 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 78252/78290 2012 2011-11-19: NC State 79-86 Vanderbilt (box 2/19) vs NC State 79-86 Vanderbilt (box 0/0); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 78567 | DUPLICATE_ALIAS | G-c03b1245b4d7cf2e | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 78567/78568 2012 2011-11-24: San Francisco 71-69 Dartmouth (box 0/0) vs San Francisco 71-69 Dartmouth (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 78568 | CANONICAL | G-c03b1245b4d7cf2e | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 78567/78568 2012 2011-11-24: San Francisco 71-69 Dartmouth (box 0/0) vs San Francisco 71-69 Dartmouth (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 78582 | DUPLICATE_ALIAS | G-397ca0643898409d | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 78582/78583 2012 2011-11-25: Southern Miss 78-67 UC Irvine (box 0/0) vs Southern Miss 78-67 UC Irvine (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 78583 | CANONICAL | G-397ca0643898409d | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 78582/78583 2012 2011-11-25: Southern Miss 78-67 UC Irvine (box 0/0) vs Southern Miss 78-67 UC Irvine (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 78649 | DUPLICATE_ALIAS | G-d3d4d3995270c516 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 78649/78651 2012 2011-11-26: Oklahoma 85-73 Santa Clara (box 0/0) vs Oklahoma 85-73 Santa Clara (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 78651 | CANONICAL | G-d3d4d3995270c516 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 78649/78651 2012 2011-11-26: Oklahoma 85-73 Santa Clara (box 0/0) vs Oklahoma 85-73 Santa Clara (box 2/21); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 79893 | DUPLICATE_ALIAS | G-df2a3d904726095c | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 79893/79894 2012 2011-12-23: Hawai'i 62-65 Auburn (box 0/0) vs Hawai'i 62-65 Auburn (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 79894 | CANONICAL | G-df2a3d904726095c | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 79893/79894 2012 2011-12-23: Hawai'i 62-65 Auburn (box 0/0) vs Hawai'i 62-65 Auburn (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 83340 | DUPLICATE_ALIAS | G-e14ab388fc09c380 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 83340/83341 2012 2012-03-10: UC Santa Barbara 64-52 Cal Poly (box 0/0) vs UC Santa Barbara 64-52 Cal Poly (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 83341 | CANONICAL | G-e14ab388fc09c380 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 83340/83341 2012 2012-03-10: UC Santa Barbara 64-52 Cal Poly (box 0/0) vs UC Santa Barbara 64-52 Cal Poly (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 83363 | DUPLICATE_ALIAS | G-ac5b3288d49153f6 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 83363/83364 2012 2012-03-11: New Mexico State 82-57 Louisiana Tech (box 0/0) vs New Mexico State 82-57 Louisiana Tech (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 83364 | CANONICAL | G-ac5b3288d49153f6 | RULE_TRUE_DUPLICATE_PRIORITY(box attachment) | pair 83363/83364 2012 2012-03-11: New Mexico State 82-57 Louisiana Tech (box 0/0) vs New Mexico State 82-57 Louisiana Tech (box 2/19); audit: TRUE_DUPLICATE (same orientation, tip, non-zero score and venue; at most one record has box scores) |
| 118538 | CANONICAL | G-922b191be91082c1 | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 118538/118539 2005 2004-12-22: Indiana State 64-68 USC (box 0/0) vs USC 81-78 Indiana State (box 0/0); audit: UNRESOLVED (evidence neither proves one contest nor two); architect: 118538 is the contest ({'USC': 68, 'Indiana State': 64}); 118539's 81-78 score is separately represented by [id=118601 USC 81-78 UAB] on the same date, so excluding 118539 removes no real contest |
| 118539 | SOURCE_CORRUPT_MISATTRIBUTED | EXCLUDED | ARCHITECT_ADJUDICATION_STAGE_2_1 | pair 118538/118539 2005 2004-12-22: Indiana State 64-68 USC (box 0/0) vs USC 81-78 Indiana State (box 0/0); audit: UNRESOLVED (evidence neither proves one contest nor two); architect: 118538 is the contest ({'USC': 68, 'Indiana State': 64}); 118539's 81-78 score is separately represented by [id=118601 USC 81-78 UAB] on the same date, so excluding 118539 removes no real contest |

## Aggregate impact

Full table: `data/qa/game_identity_aggregate_impact.csv`.

| team | season | games old->new | W old->new | L old->new | PF old->new | PA old->new | removed | source ids |
|---|---|---|---|---|---|---|---|---|
| Indiana State | 2005 | 32->31 | 11->11 | 21->20 | 1968->1890 | 2090->2009 | 1 | 118538 118539 |
| USC | 2005 | 29->28 | 13->12 | 16->16 | 2178->2097 | 2235->2157 | 1 | 118538 118539 |
| Auburn | 2012 | 32->31 | 16->15 | 16->16 | 2007->1942 | 2045->1983 | 1 | 79893 79894 |
| Cal Poly | 2012 | 34->33 | 16->16 | 16->15 | 2054->2002 | 1946->1882 | 1 | 83340 83341 |
| Central Connecticut | 2012 | 30->29 | 13->13 | 17->16 | 2098->2029 | 2111->2038 | 1 | 77802 77823 |
| Dartmouth | 2012 | 31->30 | 5->5 | 26->25 | 1796->1727 | 1975->1904 | 1 | 78567 78568 |
| Hawai'i | 2012 | 33->32 | 16->16 | 17->16 | 2418->2356 | 2452->2387 | 1 | 79893 79894 |
| Louisiana Tech | 2012 | 35->34 | 18->18 | 17->16 | 2415->2358 | 2437->2355 | 1 | 83363 83364 |
| NC State | 2012 | 38->37 | 24->24 | 14->13 | 2776->2697 | 2587->2501 | 1 | 78252 78290 |
| New Mexico State | 2012 | 37->36 | 27->26 | 10->10 | 2894->2812 | 2516->2459 | 1 | 83363 83364 |
| Oklahoma | 2012 | 32->31 | 16->15 | 16->16 | 2227->2142 | 2196->2123 | 1 | 78649 78651 |
| San Francisco | 2012 | 34->33 | 21->20 | 12->12 | 2494->2423 | 2322->2253 | 1 | 78567 78568 |
| Santa Clara | 2012 | 31->30 | 8->8 | 23->22 | 2158->2085 | 2330->2245 | 1 | 78649 78651 |
| Southern Miss | 2012 | 35->34 | 26->25 | 9->9 | 2504->2426 | 2279->2212 | 1 | 78582 78583 |
| UC Irvine | 2012 | 33->32 | 12->12 | 21->20 | 2246->2179 | 2387->2309 | 1 | 78582 78583 |
| UC Santa Barbara | 2012 | 31->30 | 21->20 | 10->10 | 2243->2179 | 2025->1973 | 1 | 83340 83341 |
| Vanderbilt | 2012 | 37->36 | 26->25 | 11->11 | 2701->2615 | 2418->2339 | 1 | 78252 78290 |
| Yale | 2012 | 29->28 | 20->19 | 9->9 | 2008->1935 | 1879->1810 | 1 | 77802 77823 |
| Alaska Anchorage | 2013 | 4->3 | 2->2 | 1->1 | 209->209 | 216->216 | 1 | 72723 72724 |
| Arizona State | 2013 | 36->35 | 23->22 | 13->13 | 2595->2512 | 2431->2363 | 1 | 72776 72784 |
| Arkansas | 2013 | 33->32 | 19->19 | 13->12 | 2356->2288 | 2209->2126 | 1 | 72776 72784 |
| Arkansas-Pine Bluff | 2013 | 32->30 | 17->16 | 15->14 | 2010->1880 | 2122->1998 | 2 | 72002 72003 72168 72169 |
| Belmont | 2013 | 34->33 | 26->26 | 7->7 | 2509->2509 | 2105->2105 | 1 | 72723 72724 |
| Boise State | 2013 | 34->32 | 22->21 | 12->11 | 2477->2343 | 2217->2095 | 2 | 74035 74036 77491 77492 |
| Butler | 2013 | 37->36 | 28->27 | 9->9 | 2583->2495 | 2385->2299 | 1 | 73590 73609 |
| Cal State Bakersfield | 2013 | 31->30 | 14->14 | 17->16 | 2111->2041 | 2150->2070 | 1 | 73985 73986 |
| Cal State Northridge | 2013 | 32->31 | 15->14 | 17->17 | 2425->2333 | 2385->2309 | 1 | 72444 72451 |
| California | 2013 | 34->33 | 22->21 | 12->12 | 2286->2218 | 2180->2123 | 1 | 72802 72803 |
| Charlotte | 2013 | 35->33 | 23->21 | 12->12 | 2369->2229 | 2353->2230 | 2 | 72741 72742 72891 72892 |
| Colorado State | 2013 | 36->35 | 27->26 | 9->9 | 2623->2553 | 2282->2229 | 1 | 74018 74019 |
| Creighton | 2013 | 37->36 | 29->28 | 8->8 | 2764->2680 | 2348->2274 | 1 | 72746 72797 |
| Duke | 2013 | 37->36 | 31->30 | 6->6 | 2867->2778 | 2421->2350 | 1 | 72726 72731 |
| Eastern Illinois | 2013 | 33->32 | 11->10 | 21->21 | 1928->1865 | 2038->1988 | 1 | 72419 72421 |
| Eastern Washington | 2013 | 32->31 | 10->10 | 21->20 | 2160->2077 | 2308->2222 | 1 | 72656 72657 |
| George Mason | 2013 | 36->35 | 21->20 | 15->15 | 2446->2379 | 2369->2305 | 1 | 73958 73963 |
| Georgia Southern | 2013 | 34->33 | 15->14 | 19->19 | 2113->2050 | 2200->2148 | 1 | 73957 73966 |
| Georgia Tech | 2013 | 32->31 | 16->16 | 16->15 | 2050->1993 | 2001->1933 | 1 | 72802 72803 |
| Hawai'i | 2013 | 33->32 | 18->17 | 15->15 | 2412->2336 | 2369->2305 | 1 | 72012 72013 |
| Hofstra | 2013 | 33->32 | 7->7 | 26->25 | 1930->1868 | 2212->2129 | 1 | 73959 73960 |
| Holy Cross | 2013 | 31->30 | 11->11 | 17->17 | 1732->1732 | 1824->1824 | 1 | 72094 72095 |
| Houston Christian | 2013 | 33->31 | 16->14 | 17->17 | 2129->1982 | 2216->2080 | 2 | 72002 72003 76044 76046 |
| Indiana | 2013 | 37->36 | 29->29 | 8->7 | 2917->2831 | 2324->2236 | 1 | 73590 73609 |
| Iowa | 2013 | 39->38 | 26->25 | 13->13 | 2728->2665 | 2443->2388 | 1 | 72648 72649 |
| Louisiana Tech | 2013 | 35->34 | 28->27 | 7->7 | 2540->2470 | 2197->2151 | 1 | 72517 72518 |
| Louisville | 2013 | 41->40 | 36->35 | 5->5 | 3030->2979 | 2397->2351 | 1 | 72725 72740 |
| Loyola Chicago | 2013 | 32->31 | 16->15 | 15->15 | 1986->1924 | 1899->1853 | 1 | 72475 72476 |
| Manhattan | 2013 | 33->32 | 14->14 | 19->18 | 1922->1865 | 1979->1916 | 1 | 73984 73987 |
| Maryland Eastern Shore | 2013 | 31->28 | 2->2 | 29->26 | 1674->1512 | 2109->1909 | 3 | 72012 72013 72168 72169 72475 72476 |
| Michigan | 2013 | 41->39 | 33->31 | 8->8 | 3080->2932 | 2595->2467 | 2 | 72659 72714 73591 73653 |
| Minnesota | 2013 | 35->34 | 21->21 | 14->13 | 2408->2337 | 2204->2115 | 1 | 72726 72731 |
| Mississippi Valley State | 2013 | 29->28 | 5->5 | 24->23 | 1817->1765 | 2132->2069 | 1 | 73957 73966 |
| Morgan State | 2013 | 34->32 | 17->17 | 14->14 | 2101->2101 | 2022->2022 | 2 | 71900 71901 72094 72095 |
| New Orleans | 2013 | 28->26 | 8->8 | 20->18 | 1908->1791 | 2100->1958 | 2 | 74035 74036 76044 76046 |
| North Carolina Central | 2013 | 32->31 | 22->22 | 10->9 | 2148->2079 | 1839->1758 | 1 | 72334 72335 |
| North Florida | 2013 | 33->32 | 14->13 | 19->19 | 2202->2122 | 2305->2235 | 1 | 73985 73986 |
| Northeastern | 2013 | 35->33 | 21->20 | 14->13 | 2393->2273 | 2373->2254 | 2 | 72721 72722 72891 72892 |
| Northern Iowa | 2013 | 33->32 | 19->19 | 14->13 | 2130->2084 | 1988->1937 | 1 | 72725 72740 |
| Notre Dame | 2013 | 36->35 | 25->25 | 11->10 | 2520->2450 | 2301->2222 | 1 | 72328 72387 |
| Pittsburgh | 2013 | 34->33 | 24->24 | 10->9 | 2344->2282 | 1914->1847 | 1 | 72659 72714 |
| Portland | 2013 | 33->32 | 11->11 | 22->21 | 2004->1951 | 2259->2189 | 1 | 74018 74019 |
| Richmond | 2013 | 35->34 | 19->19 | 16->15 | 2444->2380 | 2329->2262 | 1 | 73958 73963 |
| SMU | 2013 | 33->32 | 14->14 | 17->16 | 1999->1946 | 1940->1877 | 1 | 73894 73896 |
| Saint Joseph's | 2013 | 33->32 | 18->17 | 14->14 | 2168->2089 | 2080->2010 | 1 | 72328 72387 |
| Samford | 2013 | 33->32 | 11->11 | 20->20 | 1892->1892 | 2046->2046 | 1 | 72671 72672 |
| San Diego State | 2013 | 35->34 | 24->23 | 11->11 | 2429->2356 | 2144->2077 | 1 | 77491 77492 |
| South Alabama | 2013 | 30->29 | 17->17 | 13->12 | 2044->1987 | 2024->1956 | 1 | 72541 72542 |
| South Carolina | 2013 | 33->32 | 14->13 | 18->18 | 2104->2041 | 2200->2143 | 1 | 73984 73987 |
| South Carolina Upstate | 2013 | 34->33 | 16->16 | 17->17 | 2315->2315 | 2194->2194 | 1 | 72765 72766 |
| South Dakota | 2013 | 31->30 | 11->10 | 20->20 | 2194->2113 | 2277->2208 | 1 | 72334 72335 |
| Syracuse | 2013 | 41->40 | 30->30 | 11->10 | 2895->2816 | 2430->2347 | 1 | 73907 73922 |
| Temple | 2013 | 35->34 | 25->24 | 10->10 | 2539->2456 | 2388->2309 | 1 | 73907 73922 |
| Tennessee State | 2013 | 33->32 | 20->19 | 13->13 | 2351->2283 | 2273->2216 | 1 | 72541 72542 |
| Texas State | 2013 | 35->34 | 12->12 | 23->22 | 2462->2398 | 2631->2558 | 1 | 72741 72742 |
| Toledo | 2013 | 29->28 | 15->15 | 13->13 | 1928->1928 | 1911->1911 | 1 | 72671 72672 |
| Troy | 2013 | 34->33 | 12->12 | 22->21 | 2092->2046 | 2273->2203 | 1 | 72517 72518 |
| Tulane | 2013 | 34->33 | 20->19 | 14->14 | 2364->2281 | 2210->2148 | 1 | 73959 73960 |
| Tulsa | 2013 | 34->33 | 16->16 | 17->16 | 2254->2178 | 2290->2198 | 1 | 72444 72451 |
| UC Riverside | 2013 | 32->31 | 6->6 | 26->25 | 1881->1829 | 2123->2062 | 1 | 72721 72722 |
| UT Rio Grande Valley | 2013 | 33->32 | 16->16 | 17->16 | 2151->2101 | 2218->2155 | 1 | 72419 72421 |
| UTSA | 2013 | 33->32 | 8->8 | 22->22 | 1909->1909 | 2097->2097 | 1 | 71900 71901 |
| Utah Valley | 2013 | 34->32 | 15->14 | 18->18 | 2243->2157 | 2248->2165 | 2 | 72656 72657 72765 72766 |
| Wagner | 2013 | 32->31 | 20->19 | 12->12 | 2199->2136 | 2132->2079 | 1 | 73894 73896 |
| West Virginia | 2013 | 33->32 | 13->13 | 20->19 | 2182->2116 | 2259->2178 | 1 | 73591 73653 |
| Western Kentucky | 2013 | 37->36 | 20->20 | 17->16 | 2464->2409 | 2427->2364 | 1 | 72648 72649 |
| Wisconsin | 2013 | 36->35 | 22->22 | 13->12 | 2271->2197 | 1972->1888 | 1 | 72746 72797 |

## Scope of the trusted relation

`flagship.games` covers the audited development seasons 2005..2025 (end-year). Seasons < 2005 (151,881 raw records) remain raw-only in `core.games`: the same duplicate pattern exists there (877 candidate pairs, {'POSSIBLE_DUPLICATE': 740, 'UNRESOLVED': 135, 'TRUE_DUPLICATE': 2}), it has not been adjudicated, and no box or event data exist for those seasons.

## Wallclock (architect ruling, closed)

CBBD raw wallclock is **UNTRUSTED_MIXED**: preserved verbatim, prohibited for PIT cutoffs, feature availability, event latency, tip reconstruction and live timing. No correction is attempted.
