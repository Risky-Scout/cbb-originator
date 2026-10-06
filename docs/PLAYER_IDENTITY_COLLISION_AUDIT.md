# Player identity collision audit (Stage 3.1)

Rendered from `data/qa/player_identity_collision_audit.json` by `cbb_platform/player_identity_audit.py`. Window 2013-14 .. 2024-25. Repairs are rules in `cbb_platform/player_identity.py` applied inside every canonical build (derived rows only; raw sources unchanged) and logged in `metadata.player_identity_resolution`.

Candidates: **180**; by class: {'CONFIRMED_SINGLE_PLAYER': 173, 'CONFIRMED_COLLISION': 6, 'REVIEW_REQUIRED': 1}; by check: {'SEASON_FILE_MULTI_TEAM': 166, 'API_OVERLAP': 5, 'ROSTER_MULTI_TEAM': 6, 'SPLIT_ENTITY_LINK': 1, 'ROSTER_ATTRIBUTE_CONTAMINATION': 1, 'BOX_TEAM_MISATTRIBUTION': 1}; confirmed collisions left unrepaired: **0**.

## A.J. Lawson (CBBD athleteId 31449)

Classification: **SOURCE_ID_COLLISION (TWO_DISTINCT_PLAYERS_MERGED by CBBD athleteId 31449)**.

Evidence: CBBD's own season files carry athleteId 31449 for South Carolina AND McNeese in 2019-20 and 2020-21; the entity appears for both teams in games tipping within hours of each other (23 box pairs < 6 h, 22 PBP pairs); South Carolina and McNeese never met; CBBD recruiting links 31449 to the class-of-2018 South Carolina signee from Mississauga ON (roster attributes Brampton ON, born 2000), while a separate class-of-2016 recruit 'AJ Lawson' (Bryan TX, no athlete id) signed with North Texas -- so the 2016-17/2017-18 North Texas rows cannot belong to the South Carolina player. Per-game profiles differ (South Carolina 5.4-8.0 3PA and 1.2-1.9 AST per game vs McNeese 2.3-2.8 3PA and 4.2-5.5 AST).

Resolution (non-destructive split): South Carolina rows keep `P-7d2ea3fe693fd65b` (= cid(31449), the id CBBD recruiting attaches to that player); North Texas + McNeese rows move to `P-61f0fe8bc63621e8`. After repair: [{'canonical_player_id': 'P-61f0fe8bc63621e8', 'box_rows': 122, 'first_season': 2017, 'last_season': 2021, 'teams': ['McNeese', 'North Texas']}, {'canonical_player_id': 'P-7d2ea3fe693fd65b', 'box_rows': 81, 'first_season': 2019, 'last_season': 2021, 'teams': ['South Carolina']}].

## Repair rules applied

| rule | type | table | rows rewritten |
|---|---|---|---|
| BOX_TEAM_REATTRIBUTION_GAME_209587 | TEAM_REATTRIBUTION | fact.player_games | 10 |
| QUARANTINE_CBBD_ATHLETE_19801 | QUARANTINE | fact.play_on_floor.canonical_player_id | 0 |
| QUARANTINE_CBBD_ATHLETE_19801 | QUARANTINE | fact.play_participants.canonical_player_id | 2795 |
| QUARANTINE_CBBD_ATHLETE_19801 | QUARANTINE | fact.shots.assister_player_id | 153 |
| QUARANTINE_CBBD_ATHLETE_19801 | QUARANTINE | fact.shots.shooter_player_id | 1199 |
| QUARANTINE_CBBD_ATHLETE_19801 | QUARANTINE | fact.substitutions.canonical_player_id | 0 |
| SPLIT_CBBD_ATHLETE_31449 | SPLIT | core.rosters | 1 |
| SPLIT_CBBD_ATHLETE_31449 | SPLIT | fact.play_on_floor.canonical_player_id | 0 |
| SPLIT_CBBD_ATHLETE_31449 | SPLIT | fact.play_participants.canonical_player_id | 3182 |
| SPLIT_CBBD_ATHLETE_31449 | SPLIT | fact.player_games | 122 |
| SPLIT_CBBD_ATHLETE_31449 | SPLIT | fact.player_season_shooting | 4 |
| SPLIT_CBBD_ATHLETE_31449 | SPLIT | fact.player_season_stats | 4 |
| SPLIT_CBBD_ATHLETE_31449 | SPLIT | fact.shots.assister_player_id | 354 |
| SPLIT_CBBD_ATHLETE_31449 | SPLIT | fact.shots.shooter_player_id | 1544 |
| SPLIT_CBBD_ATHLETE_31449 | SPLIT | fact.substitutions.canonical_player_id | 0 |
| SPLIT_CBBD_ATHLETE_31449 | SPLIT | metadata.player_id_map | 4 |

## All candidates

| check | entity | season | detail | class | resolution |
|---|---|---|---|---|---|
| SEASON_FILE_MULTI_TEAM | athleteId 52269 | 2014 | ['J-Mychal Reese'] on ['Houston', 'Texas A&M'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 16493 | 2020 | ['Antonio Thomas'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 21233 | 2020 | ['Shaun Williams'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 28308 | 2020 | ['David Sloan'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 31475 | 2020 | ['Cartier Diarra'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35462 | 2020 | ['Nate Kennell'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35993 | 2020 | ['Xavier Sneed'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 21449 | 2020 | ["Ja'Shon Henry"] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35461 | 2020 | ['Koch Bar'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 16406 | 2020 | ['DaJuan Gordon'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 21450 | 2020 | ['Ville Tahvanainen'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 26836 | 2020 | ['Antonio Gordon'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 27975 | 2020 | ['Mike McGuirl'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 31643 | 2020 | ['Elijah Childs'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35463 | 2020 | ['Darrell Brown'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35994 | 2020 | ['Makol Mawien'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 21237 | 2020 | ['Stephan Gabriel'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 25974 | 2020 | ['Levi Stockard III'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 27530 | 2020 | ['Danya Kingsby'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 27587 | 2020 | ['Ari Boya'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35995 | 2020 | ['Pierson McAtee'] on ['Bradley', 'Kansas State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 31449 | 2020 | ['A.J. Lawson'] on ['McNeese', 'South Carolina'] | CONFIRMED_COLLISION | REPAIRED_SPLIT (SPLIT_CBBD_ATHLETE_31449) |
| SEASON_FILE_MULTI_TEAM | athleteId 4854 | 2022 | ['Christian Wright'] on ['Georgia', 'Northwestern'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5155 | 2022 | ['Jaden House'] on ['High Point', 'Winthrop'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 15205 | 2022 | ['Emmanuel Izunabor'] on ['High Point', 'Winthrop'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 15213 | 2022 | ['Brock Williams'] on ['High Point', 'Winthrop'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 16876 | 2022 | ['John-Michael Wright'] on ['High Point', 'Winthrop'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47028 | 2015 | ['Austin White'] on ['Arkansas-Pine Bluff', 'High Point'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53266 | 2015 | ['Tarique Thompson'] on ['Arkansas-Pine Bluff', 'High Point'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 57395 | 2015 | ['Adam Weary'] on ['Arkansas-Pine Bluff', 'High Point'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 57398 | 2015 | ['K.J. James'] on ['Arkansas-Pine Bluff', 'High Point'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 57393 | 2015 | ['Lorenzo Cugini'] on ['Arkansas-Pine Bluff', 'High Point'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53265 | 2015 | ['Anthony Lindauer'] on ['Arkansas-Pine Bluff', 'High Point'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 57397 | 2015 | ['Haiishen McIntyre'] on ['Arkansas-Pine Bluff', 'High Point'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 62597 | 2015 | ['Devante Wallace'] on ['Arkansas-Pine Bluff', 'High Point'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 62598 | 2015 | ['Brian Richardson'] on ['Arkansas-Pine Bluff', 'High Point'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 32058 | 2017 | ['Ivan Madunic'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 40211 | 2017 | ['Jaylan Robertson'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 42562 | 2017 | ['Josh Coleman'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 46842 | 2017 | ['Demario Beck'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47309 | 2017 | ['Randy Onwuasor'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52730 | 2017 | ['Elijah Wilson'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 27728 | 2017 | ['Algevon Eichelberger'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 32567 | 2017 | ['Brandon Cyrus'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 42835 | 2017 | ['Obediah Church'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 42837 | 2017 | ['Jarred Dixon'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 42839 | 2017 | ['Ryan Kreklow'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47140 | 2017 | ['Jarrid Rhodes'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47180 | 2017 | ['Austin Ruder'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52529 | 2017 | ['Jordan Martin'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52531 | 2017 | ['Tyler McCullough'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53500 | 2017 | ['Chris Harrison-Docks'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53502 | 2017 | ['Levi Cook'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 37368 | 2017 | ['Skylar Mays'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 37520 | 2017 | ['Travis Fields Jr.'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 42178 | 2017 | ['Elbert Robinson III'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 42349 | 2017 | ['Kieran Hayward'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 43033 | 2017 | ['Zoran Talley Jr.'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 43315 | 2017 | ['Jalyn Patterson'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 31903 | 2017 | ['Ryan Betley'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 44083 | 2017 | ['Charlie Brown Jr.'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47834 | 2017 | ['Caleb Wood'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 48082 | 2017 | ['Jai Williams'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 51589 | 2017 | ['Matt Howard'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53252 | 2017 | ['Javon Baumann'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 34822 | 2017 | ['Lamarr Kimble'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35379 | 2017 | ['Tyler Hamilton'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 42359 | 2017 | ['Jackson Donahue'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47833 | 2017 | ['Sam Jones'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 46841 | 2017 | ['Jaylen Shaw'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47675 | 2017 | ['Christian Musoko'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52414 | 2017 | ['Will Joyce'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52728 | 2017 | ['Joseph Williams-Powell'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52729 | 2017 | ['Colton Ray-St Cyr'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 27767 | 2017 | ['Josh Parrish'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 43443 | 2017 | ['Noah Dickerson'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 43445 | 2017 | ['Matisse Thybulle'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 43446 | 2017 | ['David Crisp'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 43447 | 2017 | ['Dominic Green'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 43464 | 2017 | ['JD Miller'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 43465 | 2017 | ['Alex Robinson'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47399 | 2017 | ['Matthew Atewe'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47691 | 2017 | ['Devenir Duruisseau'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 51852 | 2017 | ['Michael Williams'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53509 | 2017 | ['Markelle Fultz'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 36201 | 2017 | ['Carlos Johnson'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 37332 | 2017 | ['Desmond Bane'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47485 | 2017 | ['Kenrich Williams'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47489 | 2017 | ['Dalton Dry'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 48410 | 2017 | ['Malique Trent-Street'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 37834 | 2017 | ['Artur Labinowicz'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 43135 | 2017 | ['Jacob Calloway'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52413 | 2017 | ['Brayden Holker'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52415 | 2017 | ['John Marshall'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 43634 | 2017 | ['Elijah Cain'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53503 | 2017 | ['R.J. Curington'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 46416 | 2017 | ['Brandan Stith'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 46417 | 2017 | ['Randy Haynes'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47305 | 2017 | ['Wayde Sims'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35981 | 2017 | ['Devon Goodman'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 44085 | 2017 | ['Markell Lodge'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 44086 | 2017 | ['Chris Clover'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47832 | 2017 | ['Darnell Foreman'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 48079 | 2017 | ['James Demery'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 26780 | 2017 | ['Nicholas Robinson'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35092 | 2017 | ['Collin McManus'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35980 | 2017 | ['AJ Brodeur'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 41655 | 2017 | ['Max Rothschild'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53251 | 2017 | ['Brendan Casper'] on ['Pennsylvania', "Saint Joseph's"] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 36941 | 2017 | ['Amidou Bamba'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47678 | 2017 | ['James McGee'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52412 | 2017 | ['Race Parsons'] on ['Coastal Carolina', 'Southern Utah'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 36204 | 2017 | ['Jaylen Fisher'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 37637 | 2017 | ['Sam Timmins'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 51850 | 2017 | ['Brandon Parrish'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53508 | 2017 | ['Malik Dime'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47484 | 2017 | ['Vladimir Brodziansky'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47488 | 2017 | ['Austin Sottile'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 51848 | 2017 | ['Karviar Shepherd'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 51849 | 2017 | ['Chris Washburn'] on ['TCU', 'Washington'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 31449 | 2021 | ['A.J. Lawson'] on ['McNeese', 'South Carolina'] | CONFIRMED_COLLISION | REPAIRED_SPLIT (SPLIT_CBBD_ATHLETE_31449) |
| SEASON_FILE_MULTI_TEAM | athleteId 7555 | 2021 | ['Rondel Walker'] on ['Oklahoma State', 'Texas Tech'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 16954 | 2021 | ['Avery Anderson III'] on ['Oklahoma State', 'Texas Tech'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5104 | 2025 | ['Iverson King'] on ['Illinois State', 'Presbyterian'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5099 | 2025 | ['Kaleb Scott'] on ['Illinois State', 'Presbyterian'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5101 | 2025 | ['Jamahri Harvey'] on ['Illinois State', 'Presbyterian'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5102 | 2025 | ['Kobe Stewart'] on ['Illinois State', 'Presbyterian'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5105 | 2025 | ['Carl Parrish'] on ['Illinois State', 'Presbyterian'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5111 | 2025 | ['Houston Jones'] on ['Illinois State', 'Presbyterian'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 4612 | 2025 | ['Troy Cole Jr.'] on ['Southeast Missouri State', 'UNC Asheville'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 4606 | 2025 | ['TJ Biel'] on ['Southeast Missouri State', 'UNC Asheville'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 4613 | 2025 | ['Damarion Walkup'] on ['Southeast Missouri State', 'UNC Asheville'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 4615 | 2025 | ['BJ Ward'] on ['Southeast Missouri State', 'UNC Asheville'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 4616 | 2025 | ['Teddy Washington Jr.'] on ['Southeast Missouri State', 'UNC Asheville'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 4605 | 2025 | ['Brendan Terry'] on ['Southeast Missouri State', 'UNC Asheville'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 4607 | 2025 | ['Rob Martin'] on ['Southeast Missouri State', 'UNC Asheville'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 4608 | 2025 | ['Elliot Lowndes'] on ['Southeast Missouri State', 'UNC Asheville'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 4611 | 2025 | ['David Idada'] on ['Southeast Missouri State', 'UNC Asheville'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 4614 | 2025 | ['Marqueas Bell'] on ['Southeast Missouri State', 'UNC Asheville'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 42535 | 2018 | ['Jake Wright'] on ['Hartford', 'Miami (OH)'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52786 | 2015 | ['Devin Waddell'] on ['Auburn', 'Tulsa'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47139 | 2017 | ['Alize Johnson'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47817 | 2017 | ["Tre'Darius McCallum"] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47819 | 2017 | ['Joe Hanel'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52526 | 2017 | ['Dequon Miller'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53498 | 2017 | ['Billy Garrett Jr.'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 35856 | 2017 | ['Aaron Carver'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 42179 | 2017 | ['Ahmad Caver'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 42180 | 2017 | ['B.J. Stith'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 57511 | 2015 | ['Cole Stockton'] on ['Auburn', 'Tulsa'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 31789 | 2017 | ['Devin Gage'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47141 | 2017 | ['Ronnie Rousseau III'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 52530 | 2017 | ['Chris Kendrix'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53499 | 2017 | ['Darrick Wood'] on ['DePaul', 'Missouri State'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 41842 | 2017 | ['Trey Porter'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47306 | 2017 | ['Duop Reath'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 47307 | 2017 | ['Brandon Sampson'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 51548 | 2017 | ['Denzell Taylor'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 51549 | 2017 | ['Jordan Baker'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53103 | 2017 | ['Antonio Blakeney'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 53105 | 2017 | ['Craig Victor II'] on ['LSU', 'Old Dominion'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5106 | 2025 | ['Quadir Pettaway'] on ['Illinois State', 'Presbyterian'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5098 | 2025 | ['Jaylen Peterson'] on ['Illinois State', 'Presbyterian'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5100 | 2025 | ['Kory Mincy'] on ['Illinois State', 'Presbyterian'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 5108 | 2025 | ['Triston Wilson'] on ['Illinois State', 'Presbyterian'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| SEASON_FILE_MULTI_TEAM | athleteId 31717 | 2021 | ['Cade Cunningham'] on ['Oklahoma State', 'Texas Tech'] | CONFIRMED_SINGLE_PLAYER | sequential mid-season team change; no overlapping appearances |
| API_OVERLAP | athleteId 19801 |  | 16 overlapping game pairs (Tulane 2014-2016 vs NC State) | CONFIRMED_COLLISION | REPAIRED_QUARANTINE (QUARANTINE_CBBD_ATHLETE_19801) |
| API_OVERLAP | athleteId 22371 |  | 1 overlapping game pair(s) ['G-c215c3b3eb09ee2c/G-e95271d0deaa32f7'] | CONFIRMED_SINGLE_PLAYER | game-level event attribution (whole rosters in one game pair: High Point/Hampton 2022-01-24 PBP misattribution or Kansas City vs Kansas Christian doubleheader 2020-12-19); not an identity collision |
| API_OVERLAP | athleteId 26094 |  | 1 overlapping game pair(s) ['G-c215c3b3eb09ee2c/G-e95271d0deaa32f7'] | CONFIRMED_SINGLE_PLAYER | game-level event attribution (whole rosters in one game pair: High Point/Hampton 2022-01-24 PBP misattribution or Kansas City vs Kansas Christian doubleheader 2020-12-19); not an identity collision |
| API_OVERLAP | athleteId 26095 |  | 1 overlapping game pair(s) ['G-c215c3b3eb09ee2c/G-e95271d0deaa32f7'] | CONFIRMED_SINGLE_PLAYER | game-level event attribution (whole rosters in one game pair: High Point/Hampton 2022-01-24 PBP misattribution or Kansas City vs Kansas Christian doubleheader 2020-12-19); not an identity collision |
| API_OVERLAP | athleteId 31449 |  | 19 overlapping game pairs | CONFIRMED_COLLISION | REPAIRED_SPLIT (SPLIT_CBBD_ATHLETE_31449) |
| ROSTER_MULTI_TEAM | athleteId 1108 | 2024 | rosters ['T-1b0ba36fa0176fe6', 'T-0f17138f990d1895']; box teams 1 | CONFIRMED_SINGLE_PLAYER | season snapshot lists a mid-season move; appearances on at most one team |
| ROSTER_MULTI_TEAM | athleteId 37907 | 2020 | rosters ['T-1af2344db7382f24', 'T-be3f082d3b0a1b03']; box teams 0 | CONFIRMED_SINGLE_PLAYER | season snapshot lists a mid-season move; appearances on at most one team |
| ROSTER_MULTI_TEAM | athleteId 37907 | 2019 | rosters ['T-be3f082d3b0a1b03', 'T-1af2344db7382f24']; box teams 0 | CONFIRMED_SINGLE_PLAYER | season snapshot lists a mid-season move; appearances on at most one team |
| ROSTER_MULTI_TEAM | athleteId 21897 | 2022 | rosters ['T-a646e58ba46256a0', 'T-4b494301d8f704cc']; box teams 1 | CONFIRMED_SINGLE_PLAYER | season snapshot lists a mid-season move; appearances on at most one team |
| ROSTER_MULTI_TEAM | athleteId 19806 | 2023 | rosters ['T-af2fbd740320e5b8', 'T-3c4f2604e01220d1']; box teams 1 | CONFIRMED_SINGLE_PLAYER | season snapshot lists a mid-season move; appearances on at most one team |
| ROSTER_MULTI_TEAM | athleteId 3046 | 2024 | rosters ['T-5af17dc89476af51', 'T-3c4f2604e01220d1']; box teams 1 | CONFIRMED_SINGLE_PLAYER | season snapshot lists a mid-season move; appearances on at most one team |
| SPLIT_ENTITY_LINK | P-61f0fe8bc63621e8 |  | North Texas 2016-17/2017-18 and McNeese 2019-20/2020-21 rows kept as ONE entity (same name, guard, a transfer sit-out season between); not proven distinct | REVIEW_REQUIRED | kept together (no automatic split) |
| ROSTER_ATTRIBUTE_CONTAMINATION | athleteId 31449 | 2020 | McNeese 2020 roster row carries the South Carolina player's hometown (Brampton ON) and DOB (2000-07-15) | CONFIRMED_COLLISION | row re-mapped to the split entity; attributes research-only (not PIT) |
| BOX_TEAM_MISATTRIBUTION | game 209587 | 2025 | 10 Presbyterian box rows filed under Illinois State | CONFIRMED_COLLISION | REPAIRED_TEAM_REATTRIBUTION (reconciles 70 / 78) |
