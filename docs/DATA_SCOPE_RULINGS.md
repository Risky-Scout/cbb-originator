# Data scope rulings (architect)

## Pre-2005 seasons: OUT_OF_MODELING_SCOPE (Stage 2.2)

All seasons before 2004-05 (end-year < 2005; 151,881 source game records back to 1948-49) are **out of modeling scope**.

* They stay preserved, unchanged, in raw/core historical storage (`core.games`, Starter Pack `games.csv`).
* Their duplicate-game identities are NOT adjudicated (877 candidate pairs were measured in Stage 2.1 and deliberately left
  unadjudicated; `data/qa/game_identity_resolution.json` -> `unaudited_pre_window`).
* They never enter `flagship.games`, `flagship.played_games`, any flagship fact view, PIT feature construction, training,
  validation or any trusted modeling view. Enforced in `cbb_platform/schema.sql` (season window 2005..2025,
  `config.AUDITED_SEASONS`) and tested (`tests/test_stage2_2.py`).

## Played-game universe (Stage 2.2)

A game enters the modeling universe (`flagship.played_games`) only with positive evidence that it was played
(`cbb_platform/game_status.py`, `docs/GAME_STATUS_RESOLUTION.md`). Scheduled-but-unplayed records stay in
`flagship.games` (trusted schedule/history) for any future schedule modeling.

## 2025-26 lockbox

Unchanged: the final out-of-sample holdout; never fetched, inspected or aggregated (`LOCKBOX_POLICY.md`).

## CBBD wallclock (Stage 2.1)

`UNTRUSTED_MIXED`: raw preserved; prohibited for PIT, availability, latency, tip reconstruction and live timing. Closed.
