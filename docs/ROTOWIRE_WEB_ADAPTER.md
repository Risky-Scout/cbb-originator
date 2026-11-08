# RotoWire WEB availability adapter (ROTOWIRE_SOURCE=WEB)

Acquisition layer only (`cbb_platform/prod/rotowire_web_acquire.py`). The reconciliation engine
(`prod/rotowire_feed.ingest_parsed` → `player_state` → `availability_frame`) and the model interface
(`prod/inputs.apply_availability`) are unchanged and shared with the API adapter.

## Sources (three GETs per refresh, only at the 10:00 / 13:00 / 16:00 / 18:00 ET lifecycle refreshes)

| page | URL | role |
|---|---|---|
| injury report page | `/cbasketball/injury-report.php` | login-state check (`window.rwUserLoggedIn`) |
| injury report table | `/cbasketball/tables/injury-report.php?team=ALL&pos=ALL&conf=ALL` | SNAPSHOT rows (what the page's CSV / Excel export is built from) |
| injury news | `/cbasketball/news.php?view=injuries` | NEWS (append-only point-in-time items) |

## Subscriber session (Droplet only; never in git, never logged)

1. Log in to rotowire.com normally in a browser with the Fantasy Pass account.
2. Export the rotowire.com cookies as a Netscape `cookies.txt`, or copy the request `Cookie:` header of a logged-in
   page load (browser dev tools → Network → any rotowire.com request → Request Headers → `cookie`).
3. On the Droplet, as root:

   ```
   install -d -o cbb-originator -g cbb-originator -m 0700 /var/lib/sports-models/cbb-originator/secrets
   install -o cbb-originator -g cbb-originator -m 0600 /dev/null /var/lib/sports-models/cbb-originator/secrets/rotowire_web.cookies
   # paste the cookies.txt content (or the single "Cookie: ..." line) into that file with an editor; never echo it
   ```

4. Add to `/etc/sports-models/cbb-originator.env` (a path, not a secret):
   `ROTOWIRE_WEB_COOKIE_FILE=/var/lib/sports-models/cbb-originator/secrets/rotowire_web.cookies`

The adapter refuses a session file that is world-readable or group-writable. Rotated cookies returned by the site are
written back (0600, atomic) only after a verified logged-in acquisition. An expired session fails closed
(`ROTOWIRE_WEB_SESSION_EXPIRED`); renew it by repeating steps 1–3. No password is stored and the login is never
scripted (the login page loads an anti-fraud script; nothing here interacts with it).

## Live acceptance, then enable (in this order)

```
# as cbb-originator, env file loaded, from the adapter release (or a scratch checkout of that exact commit)
python -m cbb_platform.prod.rotowire_web_acquire --ops-root $CBB_PROD_ROOT/ops accept       # evidence + report only
python -m cbb_platform.prod.rotowire_web_acquire --ops-root $CBB_PROD_ROOT/ops build-maps   # persist exact map rows
```

`accept` writes `ops/markers/ROTOWIRE_WEB_ACCEPTANCE.json` (PASS only if auth, both HTTP 200, rows > 0, no reason
code, and TEAM_MAPPING_RATE = 1.0). Inspect `NEWS_EXAMPLES_BY_RULE` (explicit out, doubtful, uncertain, out-for-season, eligibility) by hand. Then
set `ROTOWIRE_SOURCE=WEB` and `ROTOWIRE_ENABLED=true` and enable the four `cbb-originator-availability-*.timer` units.
`cbb-originator-run` refuses WEB without a protected session file; `lifecycle.rotowire_state` keeps the refresh
disabled until the marker is PASS.

## Rules (summary)

* Date-only news: `provider_displayed_date` = the date, `provider_timestamp` = NULL, knowable at `collected_at_utc`.
* Identity: RotoWire player ID from `/cbasketball/player/<slug>-<ID>`; production resolution uses only the persisted
  `maps/rotowire_web_team_map.csv` and `maps/rotowire_web_player_map.csv` (exact deterministic additions, never
  overwritten; anything else → `maps/rotowire_web_map_review.jsonl` and `AVAILABILITY_IDENTITY_UNRESOLVED`).
  * Teams: exact normalized canonical name, else a row of the committed, curated
    `cbb_platform/prod/rotowire_web_team_aliases.csv` (exact string only; `verified_on` + `evidence` required; a
    conflict with an exact name or a persisted row goes to review, never overwrites). To add an alias: verify the one
    canonical on-roster team, add the row, add it to the test, commit, redeploy, rerun `build-maps`.
  * Players: exact normalized full name, unique on the mapped team's current roster. A position disagreement alone does
    not reject such a match; it is mapped with method `..._POSITION_DISCREPANCY` and recorded in
    `maps/rotowire_web_identity_discrepancies.jsonl`. Two roster rows with the name, or a canonical player already held
    by another RotoWire ID, stay unresolved. Unresolved reasons: `NOT_ON_CURRENT_TEAM_ROSTER`,
    `NO_EXACT_NAME_ON_TEAM_ROSTER_NEAR_NAME_NOT_MAPPED` (candidates listed for a human; never used to map),
    `EXACT_NAME_ONLY_ON_OTHER_TEAM_ROSTER`, `EXACT_NAME_MULTIPLE`, `CANONICAL_PLAYER_ALREADY_MAPPED`, `TEAM_UNMATCHED`.
  * `build-maps` writes an immutable audit record `maps/build_maps/<acquisition_id>.json` (rows added, discrepancies,
    every unresolved row with its reason).
* News coverage: the injury-news page exposes only the latest 25 items; no pagination, offset, cursor, load-more,
  archive, date filter or structured news endpoint is exposed by the authenticated page (inspected 2026-11-08), so
  `NEWS_COVERAGE_COMPLETE = UNPROVEN_25_ITEM_WINDOW`. Continuity guard: every window (item keys + RotoWire player IDs)
  is persisted in `news_windows.jsonl`; an INGEST window that shares no item with the previous INGEST window is
  `ROTOWIRE_WEB_NEWS_COVERAGE_GAP` — its items are kept as evidence but produce no news event and a `COVERAGE_GAP`
  ledger entry, and `rotowire_feed.freshness` fails every game closed until a later window overlaps. The first INGEST
  window is `BASELINE`; acceptance windows never set continuity. The full injury snapshot remains the current-state
  reconciliation source.
* News text: deterministic phrase rules; OUT-like / unresolved / explicit-available language; postgame (DNP) text is
  `POSTGAME_ONLY` (never pregame evidence); mixed signals → `UNKNOWN` (suppressed); no availability language → no event.
* Fail closed (no event, no PROCESSED ledger entry → the existing freshness gate blocks the games): missing / insecure /
  expired session, login redirect, subscription wall, challenge page, HTTP error, empty body, missing table or news
  container, schema change, missing provider IDs, row-count collapse.
* Raw evidence: `availability/rotowire_web/raw/YYYY-MM-DD/` (gzip payload per sha256 + one metadata JSON per fetch),
  `acquisitions.jsonl`, `news_items.jsonl` (every item, append-only). Local only: not in the ops git tree or the
  working-set upload.
