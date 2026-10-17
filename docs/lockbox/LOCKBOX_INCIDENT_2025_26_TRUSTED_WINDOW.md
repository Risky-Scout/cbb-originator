# Lockbox incident, 2026-10-16: interrupted 2025-26 opening (trusted-game window)

**Protocol:** `docs/LOCKBOX_OPENING_PROTOCOL.md` §3 ("If the procedure is interrupted, the partial data stays in the raw
store, flagged … The incident is recorded and the architect rules"). **Opening:** decision
`data/manifests/LOCKBOX_OPEN_DECISION_2025_26.json` (sha256 `819eae1fb6086514101edcca77be0027d7a56d3e4d8d7741d8068f34758e7013`,
unchanged by this record), opening commit `7dbc0b569271a42c8079946e3e27e76be86703c2` on `lockbox/open-2025-26`.

## 1. What happened

1. The opening commit applied guard v3 and the scoring-enable patch byte-for-byte and bound the decision record.
2. 2025-26 was fetched once from CBBD into the main raw store: 257 box-route requests + 730 context requests = 987
   successful requests, 0 failures, 0 refetches (`data/manifests/cbbd_request_log.jsonl`).
3. The successor canonical was built twice through the frozen route: identical identity `cbb_data_v3.0+85c8a2e5d04d`
   (content digest `841252e0bea37ef656cdf24b577a6cb0601b5e21a79f0022be85fac99c757535`, payload set
   `4aedc8c150d47e5afdbb7d2e3354b293031e3b0a6f391ba4a4b866cd06aa057b`), identical fingerprints of all 55 tables.
4. The frozen Stage 3 build on it produced **zero 2026 rows** (examples, features and labels).

## 2. The defect (data pipeline; not the model)

The canonical pipeline hard-codes the Stage 2.1 *audited* game-identity window to 2004-05 .. 2024-25:

| file | line | text |
|---|---|---|
| `cbb_platform/config.py` | 87 | `AUDITED_SEASONS = (2005, 2025)` |
| `cbb_platform/schema.sql` | 757 | `metadata.trusted_game_map`: `AND g.season BETWEEN 2005 AND 2025` |
| `cbb_platform/schema.sql` | 761 | `flagship.games`: `WHERE g.season BETWEEN 2005 AND 2025` |
| `cbb_platform/game_identity_audit.py` | 47 | identity-candidate scan: `WHERE g.season BETWEEN 2005 AND 2025` |

`metadata.game_status_resolution` is computed from `flagship.games` (`game_status.py:29`), and
`flagship.played_games`, `flagship.player_game_outcomes`, `flagship.team_game_outcomes` and `flagship.participation`
are routed through `flagship.games` / `metadata.trusted_game_map`. The Stage 3 universe reads `flagship.played_games`.

**Why Stage 3 received zero 2026 rows:** the successor canonical held the 2025-26 source facts (`core.games` 6,300
final games, of which 5,752 D-I vs D-I; `fact.player_games` 127,500; `fact.participation` 173,351; `core.team_seasons`
365 D-I), but `metadata.trusted_game_map`, `metadata.game_status_resolution` and `flagship.played_games` had **0** rows for
2026. The scoring-enable patch extended only the Stage 3 window (`spec.MODEL_SEASONS`) and the two loaders; the reviewed
data-opening plan did not account for the canonical trusted window.

## 3. State at interruption

* Discovered **before scoring**. `LOCKBOX_SCORECARD` was **not run**; `LOCKBOX_DECISION` remained **NONE**.
* No predictive or model change occurred (artifact sha256 `96464d0052413841a7309e910d0068b74190d423fe792e08f65c91aafbdcb34f`;
  FIT_CALLS 0; no refit, recalibration or selection).
* No 2025-26 model result was produced or inspected. The only 2025-26 facts examined were pipeline row counts.
* No 2026-27 data was touched. Partial data stays in place: raw payloads in `data/raw/cbbd/`, the two pre-remediation
  canonical builds and the empty Stage 3 store in `data/lockbox_2025_26/` (the Stage 3 build record is kept as
  `data/lockbox_2025_26/stage3_build.pre_remediation.json`).

## 4. Architect ruling and remediation

**Ruling (2026-10-16): APPROVE Option 1 — lockbox-scoped 2025-26 trusted-window extension.** Data-pipeline remediation
only; not a model change, not a scorecard change, no permission to tune.

* The audited window extends to 2025-26 **only** while the valid 2025-26 `LOCKBOX_OPEN` authorization is active
  (`lockbox.opening_authorized()`, the same condition as the scoring-enable patch); otherwise it stays exactly
  2005-2025 and the rendered SQL is byte-identical to the frozen text.
* The same generic identity, status, D-I, canonicalization and PIT rules apply to 2025-26; no game id is hard-coded and
  no 2025-26-specific rule exists.
* No refetch: the 987 preserved payloads are reused.
* Evidence of closed-state invariance (2005-2025 trusted content unchanged, by row content) and of the rebuild is recorded
  in `data/qa/lockbox_2025_26_trusted_window_remediation.json`.
