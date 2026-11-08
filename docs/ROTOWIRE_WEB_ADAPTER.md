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
code). Inspect `NEWS_EXAMPLES_BY_RULE` (explicit out, doubtful, uncertain, out-for-season, eligibility) by hand. Then
set `ROTOWIRE_SOURCE=WEB` and `ROTOWIRE_ENABLED=true` and enable the four `cbb-originator-availability-*.timer` units.
`cbb-originator-run` refuses WEB without a protected session file; `lifecycle.rotowire_state` keeps the refresh
disabled until the marker is PASS.

## Rules (summary)

* Date-only news: `provider_displayed_date` = the date, `provider_timestamp` = NULL, knowable at `collected_at_utc`.
* Identity: RotoWire player ID from `/cbasketball/player/<slug>-<ID>`; production resolution uses only the persisted
  `maps/rotowire_web_team_map.csv` and `maps/rotowire_web_player_map.csv` (exact deterministic additions, never
  overwritten; anything else → `maps/rotowire_web_map_review.jsonl` and `AVAILABILITY_IDENTITY_UNRESOLVED`).
* News text: deterministic phrase rules; OUT-like / unresolved / explicit-available language; postgame (DNP) text is
  `POSTGAME_ONLY` (never pregame evidence); mixed signals → `UNKNOWN` (suppressed); no availability language → no event.
* Fail closed (no event, no PROCESSED ledger entry → the existing freshness gate blocks the games): missing / insecure /
  expired session, login redirect, subscription wall, challenge page, HTTP error, empty body, missing table or news
  container, schema change, missing provider IDs, row-count collapse.
* Raw evidence: `availability/rotowire_web/raw/YYYY-MM-DD/` (gzip payload per sha256 + one metadata JSON per fetch),
  `acquisitions.jsonl`, `news_items.jsonl` (every item, append-only). Local only: not in the ops git tree or the
  working-set upload.
