# RotoWire WEB identity review — the 26 unresolved players (2026-11-08)

Source capture: RotoWire web acquisitions `W20261108T150352Z-60c436` / `W20261108T151832Z-2ee2c4` (175-row injury
report, 25-item injury news). Project evidence consulted for every player: the persisted RotoWire web maps; the current
2026-27 canonical roster (`flagship.rosters`, CBBD `teams/roster` season 2027, the only roster-ingestion path); 2026-27
CBBD box scores (87 games at review time); the CBBD 2026 portal (`core.transfers`) and class-of-2026 recruits
(`core.recruits`); the canonical history rosters 2022-23 .. 2025-26 with CBBD persistent athlete IDs
(`metadata.player_id_map`); the identity-link quarantine. Season labels below are academic years.

Rules applied: no fuzzy matching; a mapping only when identity is unique and documented; `CANONICAL_ROSTER_OMISSION`
only when a second current-season project source (2026 portal destination / 2026 recruit commitment) corroborates the
RotoWire team; nothing is mapped to a player who is not on the canonical current roster (WebIdentity requires it), and
the canonical roster is never edited by hand — CBBD `teams/roster` is its only ingestion path.

| # | RotoWire name | School | RotoWire ID | Canonical candidate | Classification | Evidence | Action |
|---|---|---|---|---|---|---|---|
| 1 | Matthew Hodge | Villanova | 23638 | P-54149d39a85d5fcf Matt Hodge | EXACT_ALIAS | Matt = Matthew; only Hodge on Villanova 2026-27; CBBD athleteId 5362 at Villanova 2024-25..2026-27; F = Forward; no other Matt/Matthew Hodge on any roster; ID unmapped | MAP_ALIAS |
| 2 | Jason Drake | Temple | 25862 | P-87f1a00b199da75d Deuce Drake | EXACT_ALIAS | CBBD persistent athleteId 1214 = "Jason Drake" (Cleveland State, Drexel, Indiana) and "Deuce Drake" (Temple 2026-27 roster + box score); CBBD 2026 portal "Jason Drake" Indiana → Temple resolves to the same canonical ID (RESOLVED_ROSTER_CORROBORATED 0.99); Guard 73-74 in / 200 lb; only Drake on Temple | MAP_ALIAS |
| 3 | Mathias Vazquez | South Carolina | 28030 | P-72aebe2f7cc6559b Mathias Alessanco | EXACT_ALIAS (documented full-name form; external sources) | Full name "Mathias Alessanco Vazquez dos Santos" (Wikipedia); school release: Mathias Alessanco 6-8 235 Joinville, Brazil, Real Betis, Brazil national teams, redshirt 2026-27 after May ACL tear; Saturday Down South: Mathias Vazquez, Real Betis B, Brazil, May ACL tear, likely redshirt, committed to South Carolina; canonical row 80 in / 235 lb / Joinville, Brazil / freshman; RotoWire injury "Redshirt"; only Mathias on South Carolina | MAP_ALIAS |
| 4 | NJ Benson | Georgetown | 21195 | P-28b75d94f9a46335 | CANONICAL_ROSTER_OMISSION | CBBD 2026 portal DePaul → Georgetown (same canonical ID; DePaul 2024-25/2025-26); not on any 2026-27 roster; no conflicting record | REPAIR_CANONICAL_ROSTER_THEN_MAP |
| 5 | Jaxon Edwards | Green Bay | 21227 | P-4d09d7c26bdff49d | CANONICAL_ROSTER_OMISSION | CBBD 2026 portal IU Indianapolis → Green Bay; RotoWire out for season (knee) | REPAIR_CANONICAL_ROSTER_THEN_MAP |
| 6 | Jonathan Griman | Arizona State | 23073 | P-b8eadb49953f9937 | CANONICAL_ROSTER_OMISSION | CBBD 2026 portal California Baptist → Arizona State (Cal Baptist 2023-24..2025-26) | REPAIR_CANONICAL_ROSTER_THEN_MAP |
| 7 | Chris Nwuli | Seton Hall | 26154 | P-aaedc84d200cb3ab | CANONICAL_ROSTER_OMISSION | CBBD 2026 portal Rutgers → Seton Hall (Rutgers 2025-26); RotoWire out for season (not injury related) | REPAIR_CANONICAL_ROSTER_THEN_MAP |
| 8 | Jalen Overway | Green Bay | 27852 | none (no canonical player) | CANONICAL_ROSTER_OMISSION | CBBD 2026 portal Calvin University (non-D-I) → Green Bay; no canonical player exists yet | REPAIR_CANONICAL_ROSTER_THEN_MAP |
| 9 | John Mattingly | Nevada | 28337 | none (no canonical player) | CANONICAL_ROSTER_OMISSION | CBBD class-of-2026 recruit committed to Nevada (no CBBD athleteId linked); no canonical player exists yet | REPAIR_CANONICAL_ROSTER_THEN_MAP |
| 10 | Derek Simpson | Providence | 20395 | P-85780921596bddb4 | WRONG_TEAM_OR_SOURCE_STALE | Project: Rutgers 2022-23/2023-24, Saint Joseph's 2024-25/2025-26; no 2026 portal record; RotoWire: Providence (GTD, not injury related). Sources disagree on team | KEEP_UNRESOLVED |
| 11 | Joseph Pinion | Providence | 20354 | P-63fc9f2c1a76d168 | AMBIGUOUS_UNRESOLVED | 2026 portal out of South Florida, eligibility "PendingAppeal", no destination; no project source places him at Providence | KEEP_UNRESOLVED |
| 12 | Dillon Hunter | Clemson | 20474 | P-c5ee9993e78a9305 | AMBIGUOUS_UNRESOLVED | Clemson 2022-23..2025-26; 2026 portal entry from Clemson, no destination (conflicts with RotoWire listing him at Clemson) | KEEP_UNRESOLVED |
| 13 | Tae Davis | Oklahoma | 20532 | P-5e2e5516496fe238 | AMBIGUOUS_UNRESOLVED | Oklahoma 2025-26; 2026 portal entry from Oklahoma, no destination (conflicting) | KEEP_UNRESOLVED |
| 14 | Cade Tyson | Minnesota | 20936 | P-c1d01799a0cbb02a | AMBIGUOUS_UNRESOLVED | Minnesota 2025-26; 2026 portal entry from Minnesota, no destination (conflicting) | KEEP_UNRESOLVED |
| 15 | Brandon Maclin | DePaul | 23921 | P-ab089169e45cf23d | AMBIGUOUS_UNRESOLVED | DePaul 2025-26; 2026 portal entry from DePaul, no destination (conflicting) | KEEP_UNRESOLVED |
| 16 | Chaze Harris | Charlotte | 27385 | P-e94354c3d235b354 | AMBIGUOUS_UNRESOLVED | South Alabama 2025-26; 2026 portal out of South Alabama, no destination; no project source places him at Charlotte | KEEP_UNRESOLVED |
| 17 | KyeRon Lindsay-Martin | UAB | 20575 | P-c18a9d943dcea78c | AMBIGUOUS_UNRESOLVED | UAB 2025-26, no portal record; RotoWire news: "still fighting for eligibility for the 2026-27 season"; no current-season project source (eligibility ≠ membership) | KEEP_UNRESOLVED |
| 18 | Robbie Avila | Saint Louis | 21059 | P-5ce042f648e4fb1d | AMBIGUOUS_UNRESOLVED | Saint Louis 2023-24/2024-25 (Indiana State before), no portal record; not on 2026-27 roster; uncorroborated | KEEP_UNRESOLVED |
| 19 | Simeon Cottle | Kennesaw State | 21070 | P-ab45f6ef90f288af | AMBIGUOUS_UNRESOLVED | Kennesaw State 2022-23..2025-26, no portal record; RotoWire: suspension through season; uncorroborated | KEEP_UNRESOLVED |
| 20 | Drew Carter | Oregon | 20923 | P-3ca4ae07573aa31f | AMBIGUOUS_UNRESOLVED | Oregon 2024-25/2025-26, no portal record; uncorroborated | KEEP_UNRESOLVED |
| 21 | Reggie Ward | Bethune-Cookman | 22964 | P-303da6b7d95ecca4 | AMBIGUOUS_UNRESOLVED | Bethune-Cookman 2023-24..2025-26, no portal record; uncorroborated | KEEP_UNRESOLVED |
| 22 | CJ Hines | Temple | 25207 | P-840a1a85690c2bb1 | AMBIGUOUS_UNRESOLVED | Temple 2025-26 (Alabama State before), no portal record; uncorroborated | KEEP_UNRESOLVED |
| 23 | DJ Stickman | Fresno State | 26727 | P-e2e7b5969de91a19 | AMBIGUOUS_UNRESOLVED | Fresno State 2025-26, no portal record; RotoWire out for season (knee); uncorroborated | KEEP_UNRESOLVED |
| 24 | RJ Luis | LSU | 20703 | P-a64cdcf86d329ebd | AMBIGUOUS_UNRESOLVED | St. John's through 2024-25, 2025 portal "Withdrawn", on no 2025-26 or 2026-27 roster; no project source places him at LSU | KEEP_UNRESOLVED |
| 25 | Moe Odum | San José State | 20593 | none | AMBIGUOUS_UNRESOLVED | No project record of any kind (roster, portal, recruit, history) | KEEP_UNRESOLVED |
| 26 | Akoldah Gak | Oklahoma | 27904 | none | AMBIGUOUS_UNRESOLVED | No project record of any kind; RotoWire out through season (not injury related) | KEEP_UNRESOLVED |

"Uncorroborated" = the player's last project record is at the RotoWire team, no departure record exists, but no
2026-27 project source (roster, box score, portal destination, recruit commitment) confirms current membership. None of
these is classified STALE_OR_DEPARTED: no project evidence demonstrates a departure, and RotoWire currently lists them.

Persisted: `cbb_platform/prod/rotowire_web_player_aliases.csv` (rows 1-3, keyed by RotoWire ID, with evidence). Rows
4-9 map automatically by the exact-name rule once CBBD's `teams/roster` lists them (re-sync) — no RotoWire-only player
is created. Rows 10-26 stay unresolved; their non-ACTIVE statuses keep failing their teams' games closed
(`AVAILABILITY_IDENTITY_UNRESOLVED`).

## Final reconciliation — operational player states (2026-11-08, later)

Live CBBD `teams/roster` season=2027 (one request, 2026-11-08T22:37Z, HTTP 200, 1544 teams / 5541 players, sha256
`ee575c8d…`, byte-identical to the payload behind the production canonical roster): **none of the 23 players present**
(no exact name match anywhere, no same-team surname match) → `CANONICAL_SYNC_LAG_COUNT = 0`. Rows 4-9 above therefore are
not repairable by re-sync today; their evidence is retained, no roster row is invented.

School-site evidence (official 2026-27 roster pages, fetched 2026-11-08) and dated news were then gathered per player.
Classes are recorded in `cbb_platform/prod/rotowire_player_states.csv` (adapter metadata only; see
`rotowire_player_states.py`). Specialist hypotheses rejected by the evidence: Tae Davis (not current: awaiting medical
and legal clearance), Chaze Harris (current but CBBD omits him -> still blocks), Robbie Avila (injunction 2026-11-05:
eligible now -> still blocks), Akoldah Gak / Chris Nwuli (stale, not affiliated), Drew Carter (conflict resolved by the
official roster: practicing, seeking eligibility), DJ Stickman (with the program, no roster slot), Reggie Ward (no
2026 evidence of any kind -> stays unresolved), Brandon Maclin (RotoWire vs Wikipedia -> SOURCE_CONFLICT).

| Player | RotoWire team | CBBD 2027 | Canonical (current) | Class | Blocks game | Review by |
|---|---|---|---|---|---|---|
| Tae Davis | Oklahoma | no | history only | AFFILIATED_INACTIVE_ELIGIBILITY | no | 2026-11-22 |
| Derek Simpson | Providence | no | history only | AFFILIATED_INACTIVE_ELIGIBILITY | no | 2026-11-10 (hearing) |
| Joseph Pinion | Providence | no | history only | AFFILIATED_INACTIVE_ELIGIBILITY | no | 2026-11-10 (hearing) |
| Dillon Hunter | Clemson | no | history only | AFFILIATED_INACTIVE_ELIGIBILITY | no | 2026-11-22 |
| Cade Tyson | Minnesota | no | history only | AFFILIATED_INACTIVE_ELIGIBILITY | no | 2026-11-22 |
| KyeRon Lindsay-Martin | UAB | no | history only | AFFILIATED_INACTIVE_ELIGIBILITY | no | 2026-11-22 |
| Drew Carter | Oregon | no | history only | AFFILIATED_INACTIVE_ELIGIBILITY | no | 2026-11-22 |
| NJ Benson | Georgetown | no | history only | AFFILIATED_INACTIVE_ELIGIBILITY | no | 2026-11-22 |
| RJ Luis | Louisiana State | no | history only | AFFILIATED_ROSTER_LIMIT | no | 2026-11-13 (hearing) |
| DJ Stickman | Fresno State | no | history only | AFFILIATED_ROSTER_LIMIT | no | 2026-11-22 |
| Moe Odum | San Jose State | no | none | DELAYED_ELIGIBILITY (eligible_from 2026-12-18) | no | 2026-11-22 |
| Simeon Cottle | Kennesaw State | no | history only | STALE_NONCURRENT | no | 2026-11-22 |
| CJ Hines | Temple | no | history only | STALE_NONCURRENT | no | 2026-11-22 |
| Akoldah Gak | Oklahoma | no | none | STALE_NONCURRENT | no | 2026-11-22 |
| Chris Nwuli | Seton Hall | no | history only | STALE_NONCURRENT | no | 2026-11-22 |
| Brandon Maclin | DePaul | no | history only | SOURCE_CONFLICT | **yes** | 2026-11-22 |
| Robbie Avila | Saint Louis | no | history only | IDENTITY_UNRESOLVED (eligible now; CBBD omits) | **yes** | — |
| Chaze Harris | UNC-Charlotte | no | history only | IDENTITY_UNRESOLVED (on school roster; CBBD omits) | **yes** | — |
| Jonathan Griman | Arizona State | no | history only | IDENTITY_UNRESOLVED (eligible, debut 2026-11-10; CBBD omits) | **yes** | — |
| John Mattingly | Nevada | no | none | IDENTITY_UNRESOLVED (on school roster; CBBD omits) | **yes** | — |
| Jaxon Edwards | Wisconsin-Green Bay | no | history only | IDENTITY_UNRESOLVED (on school roster, out for season; CBBD omits) | **yes** | — |
| Jalen Overway | Wisconsin-Green Bay | no | none | IDENTITY_UNRESOLVED (on school roster, out for season; CBBD omits) | **yes** | — |
| Reggie Ward | Bethune-Cookman | no | history only | IDENTITY_UNRESOLVED (no 2026 evidence) | **yes** | — |

Players who are on a school's active roster but missing from CBBD (Avila, Harris, Griman, Mattingly, Edwards, Overway)
are *not* exempted: none of the curated classes describes them, and for the four who can play, the model would project
the team without a real rotation player. They clear only when CBBD rosters them (normal sync). Full per-row evidence is
in the CSV; every row lapses at `review_by` and fails closed again until re-verified.
