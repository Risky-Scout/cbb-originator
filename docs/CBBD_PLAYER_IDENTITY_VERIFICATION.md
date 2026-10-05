# CBBD player identity verification

Rendered from `data/qa/cbbd_player_identity_verification.json` by `cbb_platform/cbbd_identity.py`. Do not edit by hand.

- **Field tested:** `/games/players players[].athleteId`
- **Status:** `MEASURED`
- **Classification:** `PERSISTENT_PLAYER_ID`
- **Anchor-compatible with Starter Pack season-file ids:** `True`
- **Raw CBBD payloads analysed:** 30 · player rows: 159
- **Generated:** 2026-10-05T16:41:17+00:00

## Summary (SMOKE SAMPLE)

- Sample: 159 player-game lines, 75 distinct (team, season, player) keys.
- Cross-game persistence: 55/55 multi-game players keep one id within a season; 48/48 keep it across seasons (7 players seen in 2 seasons). Ids shared by different players: 0.
- Multi-team contexts (transfers): 2/2 keep one id (Aden Holloway, Clifford Omoruyi).
- Roster agreement: 28/28 box ids equal `/teams/roster` ids.
- Starter Pack persistent-id agreement: 73/73 name-resolved players (1.0); 75/75 API ids are known Starter Pack season-file ids.
- Mismatches and causes: {'API_ID_IS_A_SEASON_FILE_ID_UNDER_A_DIFFERENT_NAME': 2}: Labaron Philon Jr. (API) vs Labaron Philon (season file), same id 85; Latrell Wrightsell (API) vs Latrell Wrightsell Jr. (season file), same id 91

## Mapping implication

API athleteId may anchor canonical players directly (provider 'CBBD').

## Decision rule

- `PERSISTENT_PLAYER_ID`: every player seen in at least 2 games keeps one id within and across seasons, and no id is shared by two players. Requires at least 10 multi-game players.
- `NON_PERSISTENT_RECORD_ID`: every multi-game player receives a new id in every game.
- `MIXED_UNRESOLVED`: anything else, too little evidence, or no data. **Stage 2 must not proceed on this value.**
- Direct anchoring also needs at least 99% agreement between the API id and the Starter Pack season-file id for the same (season, team, exact name).

## Evidence

```json
{
 "evidence": {
  "within_season": {
   "players": 75,
   "multi_game_players": 55,
   "multi_game_players_single_id": 55,
   "multi_game_players_new_id_every_game": 0,
   "ids_shared_by_different_players": 0,
   "examples_unstable": []
  },
  "across_seasons": {
   "players": 68,
   "multi_game_players": 48,
   "multi_game_players_single_id": 48,
   "multi_game_players_new_id_every_game": 0,
   "ids_shared_by_different_players": 0,
   "examples_unstable": [],
   "players_seen_in_2plus_seasons": 7
  },
  "multi_team_contexts": {
   "names_in_2plus_teams": 2,
   "names_with_one_id": 2,
   "names_with_different_ids": 0,
   "detail": [
    {
     "name": "Aden Holloway",
     "team_seasons": [
      [
       5,
       2025
      ],
      [
       16,
       2024
      ]
     ],
     "ids": [
      "92"
     ]
    },
    {
     "name": "Clifford Omoruyi",
     "team_seasons": [
      [
       5,
       2025
      ],
      [
       245,
       2024
      ]
     ],
     "ids": [
      "84"
     ]
    }
   ]
  },
  "reason": "every multi-game player keeps one id within and across seasons and across team contexts; no id reuse"
 },
 "athleteSourceId": {
  "classification": "PERSISTENT_PLAYER_ID",
  "evidence": {
   "within_season": {
    "players": 75,
    "multi_game_players": 55,
    "multi_game_players_single_id": 55,
    "multi_game_players_new_id_every_game": 0,
    "ids_shared_by_different_players": 0,
    "examples_unstable": []
   },
   "across_seasons": {
    "players": 68,
    "multi_game_players": 48,
    "multi_game_players_single_id": 48,
    "multi_game_players_new_id_every_game": 0,
    "ids_shared_by_different_players": 0,
    "examples_unstable": [],
    "players_seen_in_2plus_seasons": 7
   },
   "multi_team_contexts": {
    "names_in_2plus_teams": 2,
    "names_with_one_id": 2,
    "names_with_different_ids": 0,
    "detail": [
     {
      "name": "Aden Holloway",
      "team_seasons": [
       [
        5,
        2025
       ],
       [
        16,
        2024
       ]
      ],
      "ids": [
       "4684276"
      ]
     },
     {
      "name": "Clifford Omoruyi",
      "team_seasons": [
       [
        5,
        2025
       ],
       [
        245,
        2024
       ]
      ],
      "ids": [
       "4433155"
      ]
     }
    ]
   },
   "reason": "every multi-game player keeps one id within and across seasons and across team contexts; no id reuse"
  }
 },
 "roster_agreement": {
  "box_players": 75,
  "box_players_on_rostered_team_seasons": 28,
  "matched_by_name_on_roster": 28,
  "same_id_as_roster": 28,
  "agreement": 1.0,
  "mismatches": [],
  "box_names_not_on_roster": []
 },
 "season_file_compatibility": {
  "distinct_api_players": 75,
  "name_resolved_rows": 73,
  "api_id_equals_season_file_id": 73,
  "agreement": 1.0,
  "api_ids_known_as_persistent_season_file_ids": 75,
  "mismatches": [
   {
    "season": 2025,
    "team_id": "5",
    "name": "Labaron Philon Jr.",
    "api_id": "85",
    "season_file_name_for_api_id": "Labaron Philon",
    "cause": "API_ID_IS_A_SEASON_FILE_ID_UNDER_A_DIFFERENT_NAME"
   },
   {
    "season": 2025,
    "team_id": "5",
    "name": "Latrell Wrightsell",
    "api_id": "91",
    "season_file_name_for_api_id": "Latrell Wrightsell Jr.",
    "cause": "API_ID_IS_A_SEASON_FILE_ID_UNDER_A_DIFFERENT_NAME"
   }
  ],
  "mismatch_causes": {
   "API_ID_IS_A_SEASON_FILE_ID_UNDER_A_DIFFERENT_NAME": 2
  }
 }
}
```

The SMOKE SAMPLE coverage measurements and the date-bounded PIT experiment are rendered in `docs/CBBD_LIVE_SMOKE_RESULTS.md`.
