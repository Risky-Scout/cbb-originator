# Player identity cluster audit (Pre-Model Integrity Closure v1)

Rendered from `data/qa/player_identity_cluster_audit.json` by `cbb_platform/identity_cluster_audit.py`; every candidate with its full evidence record is in `data/qa/player_identity_cluster_audit.csv`, clusters in `data/qa/player_identity_clusters.csv`; the pre-remediation diagnostic run on v2.3 is in `data/qa/pre_model_closure/before_v2.3/`. Remediation rules: `cbb_platform/team_attribution.py` (applied inside every canonical build; derived rows only; removed rows preserved in `qa.identity_remediation_quarantine`).

**Superseded rule.** The Stage 3.1 rule *non-overlapping multi-team appearances => CONFIRMED_SINGLE_PLAYER* is REVOKED. Chronology is never positive identity evidence; it only shows the absence of one kind of contradiction. `docs/PLAYER_IDENTITY_COLLISION_AUDIT.md` is preserved unchanged as a historical record.

## Summary

- window 2013-14 .. 2024-25; canonical players scanned: **23,916**
- candidates: **8,027** (multi-team: 8,021); by kind {'IN_SEASON_MULTI_TEAM': 166, 'CROSS_SEASON_LINK': 7568, 'EVENT_FOREIGN_ACTOR': 281, 'ROSTER_MULTI_TEAM': 6, 'PLAYER_IMPOSSIBILITY': 6}
- by classification: {'TEAM_ATTRIBUTION_DEFECT': 174, 'SOURCE_ID_COLLISION': 8, 'CONFIRMED_TRANSFER': 6986, 'UNRESOLVED': 596, 'CANONICAL_MAPPING_ERROR': 263}
- by confidence: {'HIGH': 2978, 'MEDIUM': 4467, 'LOW': 582}
- team-pair clusters: 7,404 (in-season 17, cross-season size >= 2: 114); whole-team-sized (>= 5): **10**
- whole-team cluster members accepted as transfer/single player without positive evidence: **0**
- remaining in-season multi-team identities after repair: box 0, season files 0; repaired games not reconciling: 0
- cross-season links quarantined (metadata.identity_link_quarantine): 936
- team-id alias pairs (CANONICAL_MAPPING_ERROR, merge deferred): {'LIU Brooklyn': 'Long Island University'}
- provider DOB reliability: 9,700 of 44,280 roster rows carry a DOB implying age < 16 at season start (by season {2005: 3471, 2006: 2651, 2007: 1856, 2008: 1026, 2009: 238, 2010: 37, 2011: 35, 2012: 36, 2013: 35, 2014: 35, 2015: 62, 2016: 29, 2017: 51, 2018: 44, 2019: 45, 2020: 47, 2023: 1, 2024: 1}); DOB is therefore informational only, never decisive
- classification hash: `6580c7e06f165c01e00ab91850b62802f9fedcded5059019d900098129218372`

## Classes per candidate kind

| kind | classes |
|---|---|
| CROSS_SEASON_LINK | {'CONFIRMED_TRANSFER': 6986, 'UNRESOLVED': 582} |
| EVENT_FOREIGN_ACTOR | {'CANONICAL_MAPPING_ERROR': 261, 'UNRESOLVED': 14, 'TEAM_ATTRIBUTION_DEFECT': 6} |
| IN_SEASON_MULTI_TEAM | {'TEAM_ATTRIBUTION_DEFECT': 164, 'SOURCE_ID_COLLISION': 2} |
| PLAYER_IMPOSSIBILITY | {'SOURCE_ID_COLLISION': 6} |
| ROSTER_MULTI_TEAM | {'TEAM_ATTRIBUTION_DEFECT': 4, 'CANONICAL_MAPPING_ERROR': 2} |

## Whole-team-sized clusters

| cluster | kind | teams | size | classes | confidence |
|---|---|---|---|---|---|
| `2017|T-78e88036be40a9bf/T-cf2771d63aeecf02|G-288463c94080f2e0,G-515680720ab90acb` | IN_SEASON_MULTI_TEAM | TCU / Washington | 24 | {'TEAM_ATTRIBUTION_DEFECT': 24} | {'HIGH': 24} |
| `2017|T-0b2888570e7bb9e3/T-7dd7b1683d08fb35|G-a6397d60414a8581,G-b4c16eed109ac6d8` | IN_SEASON_MULTI_TEAM | DePaul / Missouri State | 22 | {'TEAM_ATTRIBUTION_DEFECT': 22} | {'HIGH': 22} |
| `2017|T-6031e28fdd4c8935/T-cc286af960db9fc8|G-f0b13435540b773e` | IN_SEASON_MULTI_TEAM | Saint Joseph's / Pennsylvania | 20 | {'TEAM_ATTRIBUTION_DEFECT': 20} | {'HIGH': 20} |
| `2020|T-15268382895e06f6/T-e6ae2d3ced544537|G-155528a23b53dd69` | IN_SEASON_MULTI_TEAM | Kansas State / Bradley | 20 | {'TEAM_ATTRIBUTION_DEFECT': 20} | {'HIGH': 20} |
| `2017|T-ac5ed60df62aa41e/T-f96156a09400fe48|G-3d7b8c395555939b` | IN_SEASON_MULTI_TEAM | Old Dominion / LSU | 19 | {'TEAM_ATTRIBUTION_DEFECT': 19} | {'HIGH': 19} |
| `2017|T-2df5ca2c186a42e6/T-9e263661fcf86fae|G-8f1577be74220798` | IN_SEASON_MULTI_TEAM | Southern Utah / Coastal Carolina | 18 | {'TEAM_ATTRIBUTION_DEFECT': 18} | {'HIGH': 18} |
| `2025|T-2b9903d17b7af850/T-d089e0f37b7fa960|G-068f83d05e504f44` | IN_SEASON_MULTI_TEAM | Presbyterian / Illinois State | 10 | {'TEAM_ATTRIBUTION_DEFECT': 10} | {'HIGH': 10} |
| `2025|T-94d18d70359b7d73/T-ca63a05e8e224cda|G-069441892260ad5e` | IN_SEASON_MULTI_TEAM | Southeast Missouri State / UNC Asheville | 10 | {'TEAM_ATTRIBUTION_DEFECT': 10} | {'HIGH': 10} |
| `2015|T-0ef2ade650d5a7c8/T-8499883bf85c6f3d|G-384f6902b0ad6852` | IN_SEASON_MULTI_TEAM | High Point / Arkansas-Pine Bluff | 9 | {'TEAM_ATTRIBUTION_DEFECT': 9} | {'HIGH': 9} |
| `2023>2024|T-54cf2735beaa29e8>T-8615e0dfa7358290` | CROSS_SEASON_LINK | Northwestern State / Austin Peay | 6 | {'UNRESOLVED': 4, 'CONFIRMED_TRANSFER': 2} | {'LOW': 4, 'HIGH': 2} |

## Game-level team-attribution decisions

| game | season | defect | decision | rows | restored | quarantined | home box/players | away box/players | reconciles |
|---|---|---|---|---|---|---|---|---|---|
| G-0b587005073ee457 | 2014 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 2 | 1 | 1 | 74/74 | 57/57 | True |
| G-384f6902b0ad6852 | 2015 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 18 | 9 | 9 | 62/62 | 74/74 | True |
| G-4bafed5047218ab4 | 2015 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 4 | 2 | 2 | 35/35 | 53/53 | True |
| G-288463c94080f2e0 | 2017 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 48 | 24 | 24 | 93/93 | 80/80 | True |
| G-3d7b8c395555939b | 2017 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 38 | 19 | 19 | 66/66 | 60/60 | True |
| G-8f1577be74220798 | 2017 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 36 | 18 | 18 | 83/83 | 68/68 | True |
| G-b4c16eed109ac6d8 | 2017 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 44 | 22 | 22 | 58/58 | 69/69 | True |
| G-f0b13435540b773e | 2017 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 40 | 20 | 20 | 78/78 | 71/71 | True |
| G-e32279065f8bdb15 | 2018 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 2 | 1 | 1 | 68/68 | 58/58 | True |
| G-155528a23b53dd69 | 2020 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 40 | 20 | 20 | 60/60 | 73/73 | True |
| G-6da0b160c8d909f8 | 2021 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 6 | 3 | 3 | 74/74 | 69/69 | True |
| G-c215c3b3eb09ee2c | 2022 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 8 | 4 | 4 | 65/65 | 56/56 | True |
| G-f2fe6558fc10a03d | 2022 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 2 | 1 | 1 | 78/78 | 62/62 | True |
| G-068f83d05e504f44 | 2025 | EVENT_TEAM_NOT_IN_GAME | QUARANTINED_EVENTS | 790 | 0 | 790 | None/None | None/None | None |
| G-069441892260ad5e | 2025 | MIRRORED_BOX_LINES | REMEDIATED_RECONCILED | 20 | 10 | 10 | 64/64 | 72/72 | True |

## Largest clusters

| cluster | kind | teams | size | classes |
|---|---|---|---|---|
| `2017|T-78e88036be40a9bf/T-cf2771d63aeecf02|G-288463c94080f2e0,G-515680720ab90acb` | IN_SEASON_MULTI_TEAM | TCU / Washington | 24 | {'TEAM_ATTRIBUTION_DEFECT': 24} |
| `2017|T-0b2888570e7bb9e3/T-7dd7b1683d08fb35|G-a6397d60414a8581,G-b4c16eed109ac6d8` | IN_SEASON_MULTI_TEAM | DePaul / Missouri State | 22 | {'TEAM_ATTRIBUTION_DEFECT': 22} |
| `2017|T-6031e28fdd4c8935/T-cc286af960db9fc8|G-f0b13435540b773e` | IN_SEASON_MULTI_TEAM | Saint Joseph's / Pennsylvania | 20 | {'TEAM_ATTRIBUTION_DEFECT': 20} |
| `2020|T-15268382895e06f6/T-e6ae2d3ced544537|G-155528a23b53dd69` | IN_SEASON_MULTI_TEAM | Kansas State / Bradley | 20 | {'TEAM_ATTRIBUTION_DEFECT': 20} |
| `2017|T-ac5ed60df62aa41e/T-f96156a09400fe48|G-3d7b8c395555939b` | IN_SEASON_MULTI_TEAM | Old Dominion / LSU | 19 | {'TEAM_ATTRIBUTION_DEFECT': 19} |
| `2017|T-2df5ca2c186a42e6/T-9e263661fcf86fae|G-8f1577be74220798` | IN_SEASON_MULTI_TEAM | Southern Utah / Coastal Carolina | 18 | {'TEAM_ATTRIBUTION_DEFECT': 18} |
| `2025|T-2b9903d17b7af850/T-d089e0f37b7fa960|G-068f83d05e504f44` | IN_SEASON_MULTI_TEAM | Presbyterian / Illinois State | 10 | {'TEAM_ATTRIBUTION_DEFECT': 10} |
| `2025|T-94d18d70359b7d73/T-ca63a05e8e224cda|G-069441892260ad5e` | IN_SEASON_MULTI_TEAM | Southeast Missouri State / UNC Asheville | 10 | {'TEAM_ATTRIBUTION_DEFECT': 10} |
| `2015|T-0ef2ade650d5a7c8/T-8499883bf85c6f3d|G-384f6902b0ad6852` | IN_SEASON_MULTI_TEAM | High Point / Arkansas-Pine Bluff | 9 | {'TEAM_ATTRIBUTION_DEFECT': 9} |
| `2023>2024|T-54cf2735beaa29e8>T-8615e0dfa7358290` | CROSS_SEASON_LINK | Northwestern State / Austin Peay | 6 | {'UNRESOLVED': 4, 'CONFIRMED_TRANSFER': 2} |
| `2022>2023|T-34ca3cb013944831>T-a0872d6f669210f1` | CROSS_SEASON_LINK | Cleveland State / Missouri | 4 | {'CONFIRMED_TRANSFER': 4} |
| `2022|T-1a9e558a301cf056/T-8499883bf85c6f3d|G-c215c3b3eb09ee2c,G-126911bec914c14b` | IN_SEASON_MULTI_TEAM | High Point / Winthrop | 4 | {'TEAM_ATTRIBUTION_DEFECT': 4} |
| `2024>2025|T-3062010809b10e73>T-079651bf85ec9cc7` | CROSS_SEASON_LINK | Kentucky / Arkansas | 4 | {'CONFIRMED_TRANSFER': 4} |
| `2024>2025|T-e20a3042aeed02ba>T-bc937613fef6d0d1` | CROSS_SEASON_LINK | Eastern Washington / Washington State | 4 | {'CONFIRMED_TRANSFER': 4} |
| `2014>2016|T-29f153e69b95afda>T-c3a55c03c625da8e` | CROSS_SEASON_LINK | Loyola Chicago / IU Indianapolis | 3 | {'CONFIRMED_TRANSFER': 3} |
| `2021>2022|T-9625fb47a311c96d>T-5ebc95189141f6ec` | CROSS_SEASON_LINK | UNC Greensboro / Cincinnati | 3 | {'CONFIRMED_TRANSFER': 3} |
| `2021>2022|T-abb0c9c290f1fd39>T-53acc554d02371df` | CROSS_SEASON_LINK | Penn State / Fordham | 3 | {'CONFIRMED_TRANSFER': 3} |
| `2021>2022|T-e20a3042aeed02ba>T-6b472af42da14679` | CROSS_SEASON_LINK | Eastern Washington / Portland | 3 | {'CONFIRMED_TRANSFER': 3} |
| `2021|T-21e57d257133ab84/T-741bbe7f2eef9df3|G-718dbb3af4550135,G-6da0b160c8d909f8` | IN_SEASON_MULTI_TEAM | Oklahoma State / Texas Tech | 3 | {'TEAM_ATTRIBUTION_DEFECT': 3} |
| `2022>2023|T-0b2888570e7bb9e3>T-54cf2735beaa29e8` | CROSS_SEASON_LINK | Missouri State / Northwestern State | 3 | {'CONFIRMED_TRANSFER': 3} |
| `2022>2023|T-8105ae18388a83f9>T-ac5ed60df62aa41e` | CROSS_SEASON_LINK | Murray State / LSU | 3 | {'CONFIRMED_TRANSFER': 3} |
| `2023>2024|T-733be5839fe6bb13>T-5f19b78c4a0a91dd` | CROSS_SEASON_LINK | Iona / St. John's | 3 | {'CONFIRMED_TRANSFER': 3} |
| `2023>2024|T-a646e58ba46256a0>T-04628678dec561a1` | CROSS_SEASON_LINK | Utah State / VCU | 3 | {'CONFIRMED_TRANSFER': 3} |
| `2024>2025|T-009f8d48fa60b794>T-84846d5112415644` | CROSS_SEASON_LINK | Indiana State / Saint Louis | 3 | {'CONFIRMED_TRANSFER': 3} |
| `2024>2025|T-0961634b0b84e1ee>T-a250df98d6193f68` | CROSS_SEASON_LINK | Charleston / Louisville | 3 | {'CONFIRMED_TRANSFER': 3} |

## Evidence rule

Positive evidence admitted (independence noted): independent CBBD portal record matching normalized name + origin + destination + transfer year (independent of the athlete-id linkage); mirrored / misfiled head-to-head box lines in the Starter Pack source rows plus exact reconciliation of both teams' PTS/FGM/3PM/FTM; PBP on-floor team of the athlete in the boundary game (CBBD API, independent of the Starter Pack box file); provider roster continuity (same athlete id on both team-season rosters -- provider-asserted, NOT independent, admitted only at MEDIUM confidence and never for a whole-team-sized cluster). Contradictions (decisive): < 4 h overlapping appearances, eligibility-impossible career length, conflicting provider profile across team-seasons. Provider DOB vs appearance season is recorded but NOT decisive: it is measured unreliable (summary above). Not admitted as positive evidence: chronology / non-overlap, exact name equality alone (names are the resolution key itself), provider roster attributes (DOB, hometown, height) as *confirmation* -- they are attributes of the provider entity and therefore repeat whatever the provider merged (A.J. Lawson). BDL holds 130 player-game rows locally (2024-25 sample) and contributes no cluster evidence.

