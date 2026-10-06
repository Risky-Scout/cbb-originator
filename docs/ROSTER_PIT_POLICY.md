# Historical roster point-in-time policy (Stage 3.1)

CBBD `/teams/roster` rows are **end-of-season snapshots**. A current-season roster row records who was on the team by the
end of the season; it does not prove that a player was known to belong to the team before any particular game. It is
therefore **never** membership evidence for a historical pregame player pool, never a participation-history input,
and never an approved feature.

## Roster-knowledge / membership evidence classes

Membership of a team-season is stored with the instant it became knowable (`ex.team_membership_evidence`). A player is in
the pool of game G only if some evidence row has `available_from <= prediction_cutoff_utc(G)`
(`cbb_platform/stage3/universe.py`).

| class | legal evidence | available from |
|---|---|---|
| PRIOR_APPEARANCE_CONFIRMED | a box row for this team earlier this season | that game's `historical_knowable_at` (tip + 4 h) |
| PRIOR_SEASON_RETURNER | the player's most recent window season (s-1, or s-2 after one sit-out season) was with this team; < 4 earlier seasons played (< 5 in 2021-22 / 2022-23, the COVID extra year) | preseason |
| PRIOR_SEASON_ROSTER_RETURNER | on this team's PREVIOUS-season roster (a completed season); < 4 seasons since its start season (5 in 2021-22 / 2022-23) | preseason |
| TRANSFER_PRESEASON_EVIDENCE | portal record year s-1, destination this team, identity from the ORIGIN roster | preseason |
| RECRUIT_PRESEASON_EVIDENCE | recruiting class year s-1 committed to this team (provider athlete id) | preseason |
| SEASON_ROSTER_ONLY_NOT_PIT_CONFIRMED | current-season roster only | never (research table `ex.roster_snapshot_research`) |
| UNKNOWN_AT_CUTOFF | no legal evidence | the player is not in the pool; if he plays, the row is a classified miss |

Exclusion: a returner with an outbound portal record from this team (year s-1) is not a returner.

## Consequences

* Participation history (`feat.player_participation_state`) uses the same evidence: a player is a candidate of an
  earlier team game only if his evidence was knowable at that game's cutoff. The NOT_ON_ROSTER participation outcome
  (which needs the end-of-season roster) is used for LABELS only.
* Roster-snapshot attributes (position, height, weight) are in `feat.player_profile_research`, registered as
  `RESEARCH_NON_PIT` and `approved_for_modeling = false`. Approved replacements: `prof_last_box_position` (most recent
  prior box row) and `prof_recruit_height_in` / `prof_recruit_weight_lb` (preseason recruiting record).
* Players whose first appearance has no legal prior evidence are NOT added retroactively: their first game is a
  classified miss (`lab.player_pool_misses`, `data/qa/player_pool_misses_after.csv`):
  `HISTORICALLY_UNOBSERVABLE` (first observed college appearance) or `VALID_COLD_START_MISS` (known player without legal
  evidence of membership of this team, e.g. pre-2021 transfers before portal data exist). `PIPELINE_BUG` must be 0 (tested).
* Production (2026-27+) can add a dated current-roster source (e.g. Sharp Stats captures) as a new PIT-confirmed class.
