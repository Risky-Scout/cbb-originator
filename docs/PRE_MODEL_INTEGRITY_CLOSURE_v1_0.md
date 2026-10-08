# Pre-Model Integrity Closure v1.0 — Part 1 record

**Date:** 2026-10-07 · **Branch:** `research/stage3-pit-feature-store` · **Start HEAD:** `956ccdd` · **Start dataset:**
`cbb_data_v2.3+7bef640cccde` · **End dataset:** `cbb_data_v2.4+5f19d31e524a` (RESEARCH_CANDIDATE, **not MODEL_READY**).
Scope: governance freeze, clustered identity remediation, next-stage contracts. **No predictive model was trained. The
lockbox was not touched. No raw payload, Starter Pack file or unrelated project was modified.**

## 1. Preflight (read-only, before any change)

| item | value |
|---|---|
| branch / HEAD | `research/stage3-pit-feature-store` / `956ccdd6812c9bae4451a690d80e155ada5ac64e` (matched) |
| dataset | `DATASET_MANIFEST.json` = `cbb_data_v2.3+7bef640cccde`, `lockbox_touched = false` (matched) |
| running builds | none (an unrelated `uvicorn massey…` process belongs to another project and was not touched) |
| raw CBBD manifest hash | `b055525a97845b01c299328b1e12cbc91a46b0c049f3706e7d07d56c1d6bd71f` (18,569 payloads) |
| canonical content digest (v2.3) | `4fda164e10c871db15ed43119a60614158ed605f30c641c86d22ca10326d56fd` |
| Stage 3 feature / label fingerprints (v2.3) | `8c3633e3ac0f4de6…` / `c8a92b9d7803e680…` (sha of fingerprints) |
| fold manifests (v2.3) | `fold_manifests.json` sha256 `fdc510be…c4`, `core_walk_forward.json` `ad1d8434…da` |
| Python / DuckDB | 3.12.13 (project venv) / 1.5.6 |
| sizes | repository 15 GB total; raw 1.4 GB; canonical 10 GB; modeling store 3.3 GB; `.git` 12 MB |
| untracked at start | `data/qa/game_identity_questionable_only.csv` (pre-existing; left untouched, not committed) |

## 2. Findings

1. **Every whole-team "transfer" cluster is one mirrored head-to-head box score.** In 14 Starter Pack games, one or
   both teams' box lines are copied onto the opponent. CBBD season files then aggregate each copy into a one-game
   season row on the wrong team. That produced 164 player-seasons that looked like mid-season transfers
   (TCU/Washington 24, DePaul/Missouri State 22, Pennsylvania/Saint Joseph's 20, Bradley/Kansas State 20,
   LSU/Old Dominion 19, Coastal Carolina/Southern Utah 18, SEMO/UNC Asheville 10, UAPB/High Point 9, plus 6 smaller). The
   15th cluster (Illinois State/Presbyterian 10) is game 209587, whose box lines were all filed under Illinois State
   (already repaired in Stage 3.1 for box rows, but its season-file echo remained). Stage 3.1 had accepted all of these as
   CONFIRMED_SINGLE_PLAYER on chronology alone. **That rule is revoked.**
2. **Effect in v2.3:** the mirrored rows were all left unresolved (both copies). 154 real appearances were therefore
   missing from player histories and labels, and the 164 spurious one-game season rows made those players "members"
   of the opponent, which created phantom UNKNOWN participation rows in all of that opponent's games. `fact.participation`
   falls by 5,720 rows net in v2.4 (3,189,462 → 3,183,742).
3. **Event-side team attribution:** game 209587's entire event feed (PBP, shots, substitutions, lineups) is filed under
   Jacksonville, a team that did not play it.
4. **Merged provider identities** that chronology cannot reveal: CBBD athleteIds 105, 62580, 68710 and 19841 have careers
   spanning 8 to 21 seasons.
5. **Stale roster rows:** 4 in-window athletes are listed on two rosters in one season, with every box row for only one
   team.
6. **Team-id alias:** LIU's games 2013-14..2018-19 (29) are filed under a second team id ("LIU Brooklyn", non-D-I in
   `team_seasons`). Carver College and UT/Texas Permian Basin also have two ids each.
7. **Provider DOB is unreliable:** 9,700 of 44,280 roster rows carry a DOB that implies an age under 16 at season start
   (mostly 2005-2009, with repeated placeholder-like values). DOB is therefore informational only. A first attempt to
   quarantine such roster rows was **reverted before the final build**: it removed roster membership (pool evidence)
   because of an attribute defect.

## 3. Remediation (derived rows only; `cbb_platform/team_attribution.py`)

| rule | effect (v2.4) |
|---|---|
| `DEMIRROR_RECONCILED_GAMES` | 14/14 mirrored games repaired. Each was gated on all mirrored rows being decided **and** both teams reconciling PTS/FGM/3PM/FTM exactly. 154 true-team copies restored with ids; 154 opponent-side copies moved to `qa.identity_remediation_quarantine` |
| `SPURIOUS_SEASON_MEMBERSHIP` | 164 `player_season_stats` + 1 `player_season_shooting` rows quarantined; 164 `(season, team, name)` keys nulled |
| `EVENT_TEAM_NOT_IN_GAME` | game 209587 events excluded from trusted PBP / substitution / lineup views (790 rows; shots and on-floor follow PBP) |
| `QUARANTINE_ERA_CBBD_ATHLETE_{105,62580,68710,19841}` | the impossible era quarantined: 116 box rows → identity NULL; 8 season rows and 8 roster rows quarantined; 940 event actor references nulled (no reassignment) |
| `STALE_ROSTER_MULTI_TEAM` | 5 roster rows quarantined (4 in window) |
| `IDENTITY_LINK_QUARANTINE` | 936 cross-season links without positive evidence recorded (`metadata.identity_link_quarantine`): 535 no provider roster continuity (mostly 2011-13 roster coverage holes), 397 gaps of ≥ 3 seasons, 4 whole-team cluster members (Northwestern State → Austin Peay) without portal records. Stage 3 never joins history across them |
| team alias (LIU etc.) | **not remediated** (CANONICAL_MAPPING_ERROR, deferred to A2: a merge changes the D-I target set). Player identities are correct; the alias games' player event rows attach to the alias team id, which is never a target team |

Totals: canonical players changed 173; canonical games affected 131; player-game rows remapped 424
(154 restored + 154 quarantined + 116 collision-quarantined); event actor rows remapped 940. Every removed derived row
is preserved verbatim (343 rows in `qa.identity_remediation_quarantine`). Resolution hashes are in the dataset manifest
(`identity_remediation`).

## 4. Stage 3 impact (rebuilt on v2.4)

| measure | v2.3 | v2.4 |
|---|---|---|
| game examples (targets) | 65,048 | 65,048 |
| player-game examples / labels | 2,970,366 | 2,970,075 (−291, −0.010 %) |
| labels with a box row | 1,279,005 | 1,279,149 (+144) |
| target box rows unresolved | 510 | 215 |
| pool misses | 16,235 | 16,232 |
| features fingerprint sha | `8c3633e3…` | `745bff83…` |
| labels fingerprint sha | `c8a92b9d…` | `0f8e5616…` |
| fold definition (`core_walk_forward.json`) | `ad1d8434…` | unchanged |
| fold manifests | `fdc510be…` | `9e9577eb…` (counts / hashes per fold) |

The modeling target population is unchanged, and the player universe changes by 0.01 %. This is **not** a material change
(STOP condition not triggered).

## 5. Gates

| gate | criterion | result |
|---|---|---|
| **G0** | crosswalk and precedence map exist; v2.3 preserved (manifest sha256 `6ee97887…b500bf`, tested); authoritative set unambiguous | PASS |
| **G1** | full-window cluster audit (23,916 players, 8,027 candidates); 0 whole-team members accepted without positive evidence; confirmed defects remediated non-destructively; every candidate has evidence + class + confidence; repaired games reconcile; no canonical player in overlapping games or on two teams in a season; adversarial tests pass | PASS (team alias deferred, see §3 and §6) |
| contracts | bitemporal checkpoint, possession/stint, eligibility, production / ledger, WizardOfOdds CBB, lockbox opening, environment, G4A/G4B | FROZEN |
| data | successor v2.4 with v2.3 parent; Stage 3 rebuilt; deterministic rebuild; full suite | see `data/qa/pre_model_closure/` and the final report |

## 6. Unresolved (carried to Part 2 or to the architect)

1. Team-id alias merge (LIU Brooklyn / Long Island University; Carver College ×2; UT/Texas Permian Basin).
2. Stage 3 `1900-01-01` preseason sentinel → `PRESEASON_ASSUMED` (PIT v2.0 §6).
3. 582 in-window cross-season links classified UNRESOLVED. Their modeling effect is nil to small (Stage 3 reads at
   most s-2 history), but they need independent evidence or permanent quarantine.
4. Provider DOB unreliability (9,700 roster rows). In-window rows with impossible DOB (≈ 315) need an identity look in
   A2.
5. Availability source naming (Sharp Stats vs StatSharp vs Covers) and the lockbox guard's refusal of 2026-27
   reference data (blocks current-season roster mapping for G4B).
6. Off-machine destination (no git remote is configured).
7. The 2026-10-06 audit blueprint document is not in the repository. Phase definitions are reconstructed from the directive.
8. `data/qa/game_status_resolution.{csv,json}` and `docs/GAME_STATUS_RESOLUTION.md` (Stage 2.2 reports) still show
   v2.3 evidence strings for the 14 repaired games. Status and exclusions are unchanged, and v2.4's status hash is
   recomputed in the dataset manifest.

## 7. Four-failure reconciliation (2026-10-07, after the Part 1 full suite: 412 passed, 4 failed)

No model was trained. The lockbox, the Starter Pack and the canonical database were not modified. The dataset remains
`cbb_data_v2.4+5f19d31e524a`. Evidence came before any test change: `data/qa/pre_model_closure/four_failure_reconciliation.json` and
`player_game_154_row_reconciliation.csv`.

1. **`test_raw_starter_pack_still_carries_the_mirror`: test implementation defect.** The query filtered
   `start_date LIKE '2019-11-27%'`. The column is UTC (`2019-11-28 04:00:00+00:00`), so it matched nothing (0 ≠ 2).
   Starter Pack bytes are unchanged: all 111 data files match `starter_pack_sha256.json` (digest `cc6ced32…`). The only
   non-matching file of 131 is `data/.DS_Store`, which is Finder metadata, rewritten 2026-10-05 17:49 local (before Stage 2.1), and
   excluded from the digest by design. The raw mirror is intact: source game 31324 lists Xavier Sneed under Bradley and
   Kansas State with one identical box line. The test is now keyed on the source game id. It asserts both halves: the raw defect is
   preserved, and the canonical layer keeps the Kansas State copy and quarantines the Bradley copy.
2. **`test_no_unrelated_project_was_modified`: PRE_EXISTING_WORKSPACE_DRIFT.** `CBB Starter Pack.zip` is absent,
   `CBB_Originator_Model.zip` grew from 361,964 to 7,599,567,013 bytes, and `SSH root.docx` is new. The parent directory's
   mtime (last add/remove) is 2026-10-07T00:22:09Z. The Pre-Model Closure session started at 2026-10-07T00:45:26Z. No session
   transcript contains a command touching these entries. No copy of the ZIP exists (the Stage 2 snapshot has no hash), so it
   cannot be restored byte-identically. The registered source is the extracted Starter Pack, which is hash-verified. The
   Stage 2 snapshot is kept as history. `workspace_snapshot_pre_model_closure_v1.json` is the new baseline, with a drift ledger.
3. **`test_box_facts_are_not_lost[player_games]`: intentionally superseded invariant** (see
   `docs/ARCHITECTURE_PRECEDENCE.md` §5). 2,368,206 → 2,368,052 (−154). All 154 removed rows are MIRRORED_DUPLICATE
   (0 TEAM_MISATTRIBUTED_COPY without a twin, 0 SOURCE_CORRUPT_QUARANTINED, **0 VALID_FACT_LOST**, 0 OTHER). They are
   detected from raw rows alone, keyed on (source game, normalised name, 18-stat box vector) present on both teams. That
   detection finds exactly 154 keys in the whole Starter Pack, all in the 14 repaired games. Each copy's true team is
   decided independently of the canonical mapping, from raw rows only: the player has 0 rows for the copy's team in other
   games of the season and ≥ 1 for the twin's team. Each surviving twin is RESOLVED with a player id, its season box total
   equals the season file on its team (154/154 points and games), and all 28 team-games reconcile
   PTS/FGM/3PM/FTM/AST/STL/BLK against the raw team box (10/28 before, 28/28 after). Over all 2,367,991 fact keys, none is
   lost, none changes count except the 154 mirror keys (2 → 1), and v2.4 box values equal raw on every row.
4. **`test_box_facts_are_not_lost[participation]`: same supersession.** 3,189,462 (v2.3) → 3,183,742 (−5,720). That is
   2,368,206 + 821,256 → 2,368,052 + 815,690. The v2.3 membership inputs were reconstructed from v2.4 plus the
   verbatim quarantine records (recomputed v2.3 total = 3,189,462 exactly; recomputed v2.4 set = actual set). Each rule
   was then applied in build order:

   | step | box | membership | reason |
   |---|---|---|---|
   | v2.3 | 2,368,206 | 821,256 | |
   | DEMIRROR_RECONCILED_GAMES | −154 | −308 | 154 mirror-copy box rows. Membership: 154 UNKNOWN rows of the true team superseded by the restored box row, and 154 opponent-echo rows in the mirrored game |
   | SPURIOUS_SEASON_MEMBERSHIP | 0 | −5,146 | phantom UNKNOWN rows in the opponent's other games, all on the 164 quarantined one-game season rows |
   | SOURCE_ID_COLLISION eras | 0 | −112 | 19841: 81, 68710: 26, 62580: 5 (105: 0) |
   | **v2.4** | **2,368,052** | **815,690** | unexplained: 0; rows added by any step: 0 |

   BOX_SCORE_ROW participation = `fact.player_games` = 2,368,052, 1:1 by source record. None exists for a quarantined copy.
5. **Working-tree correction.** At 06:42 local, after the v2.4 build (02:11) and during the full-suite run, the Part 1 session
   edited `cbb_platform/team_attribution.py` so that mirror copies would stay in `fact.player_games` as
   `QUARANTINED_MIRROR_COPY`. The edit was never built or validated, and it contradicts §3 and the built database (whose audit log
   reads "moved to qa.identity_remediation_quarantine"). It was reverted to the code that built v2.4, using the exact text
   from the session transcript. No data changed.
6. **Non-blocking hygiene note (architect ruling, 2026-10-08).** `cbb_platform/stage3/coverage.py` now writes
   `data/qa/player_pool_misses_after.csv` with explicit LF line endings (content proven identical: 16,233 parsed rows equal;
   bytes equal after CRLF→LF). The same function still writes `data/qa/player_pool_misses.csv` with Python csv's CRLF
   default. That file is byte-identical to HEAD, so it was deliberately **not** rewritten (no artificial full-file
   formatting diff). Normalize its writer to LF the next time that artifact legitimately changes.
   Same class, found at commit time by `git diff --cached --check` (the working-tree `git diff --check` does not
   inspect new files) and committed unchanged as accepted evidence: CRLF (csv default, no real trailing whitespace) in
   `data/qa/player_identity_cluster_audit.csv`, `data/qa/player_identity_clusters.csv` (writer:
   `cbb_platform/identity_cluster_audit.py`), their byte-preserved `data/qa/pre_model_closure/before_v2.3/` snapshots, and
   `data/qa/pre_model_closure/player_game_154_row_reconciliation.csv`; one extra blank line at the end of the generated
   `docs/PLAYER_IDENTITY_CLUSTER_AUDIT.md`. Normalize those writers when their artifacts next legitimately change.

**Acceptance:** Part 1 ACCEPTED by the quantitative architect on 2026-10-08. Record: `data/manifests/PRE_MODEL_PART1_ACCEPTANCE.json`;
tag `pre-model-integrity-part1-v2.4`. `cbb_data_v2.4+5f19d31e524a` is the accepted RESEARCH baseline (not MODEL_READY).
