# Closing-quote forensics (section E), 2026-11-21

Evidence: Droplet ops copy (evaluations, game_quotes, quotes observations, performance parquet), the quotes-unit journal
since 11-19 00:00Z, and the availability / morning unit journal. Classifiers: `classify.py` (game) and
`classify_player.py` (player) in this directory. Per-row output: `game_no_close_rows.csv`, `player_no_close_rows.csv`,
`player_close_fixed_recompute.csv`.

## Game markets (graded 11-19 and 11-20: 245 rows)

Close rule (`game_quotes.closing`): the last row received before the scheduled tip and within `CLOSE_WINDOW_MIN = 30`.
Recomputing it reproduces the audit exactly: 104 of 245 rows are NO_CLOSE_QUOTE (103 on 11-19, 1 on 11-20).

Precedence (first match wins):
1. CUTOFF_ALREADY_PASSED: tip before B.1 (11-20 12:57Z), the game's last captured row was at or before its cutoff, and no
   row fell in the window.
2. QUOTE_CALL_FAILED
3. WRITER_LOCK_COLLISION (no COMPLETE capture in the window and a writer-lock skip within 10 minutes of it)
4. NO_DUE_TICK
5. NO_PROVIDER_EVENT (captures ran in the window, the game had no rows)
6. NO_MARKET_POSTED (the game was quoted in the window but this market type was not)
7. OTHER_EXPLICIT_REASON

| cause | n | % |
|---|---|---|
| CUTOFF_ALREADY_PASSED | 103 | 99.0 |
| NO_MARKET_POSTED | 1 | 1.0 |
| all other causes | 0 | 0 |

- **11-19 (103 rows, 21 games).** Before B.1, the capture slate used the player slate (`cutoff > now`), so each game left
  the capture set at T-60. Its last row was 68 to 98 minutes before tip, never inside the 30-minute window. B.1 commit
  7eafd9f fixed this. After that deploy, 141 of 142 rows have a close.
- **11-20 (1 row).** G-56f1541ac923e1a4 MONEYLINE: 1,296 rows for the game were captured in the window, but none was a
  moneyline. The last moneyline row was received at 14:10Z, 11 hours before tip.
  - Side finding, not fixed: the frozen entry `as_of(cutoff)` still priced that 11-hour-old moneyline. There is no
    staleness bound on game-market entry quotes. This is an evaluation-contract question.
- **Writer-lock collisions so far: 0 closes lost.** The quotes journal has 7 skipped wakes (busy > 600 s). All were
  between 12:00 and 12:50Z, during the morning chain, when no game was near tip.
  - The near miss is availability Phase B at 10:00, 13:00, 16:00 and 18:00 ET (15:00, 18:00, 21:00 and 23:00Z). It holds
    the writer lock for 12 to 20 minutes.
  - Quote wakes waited 467, 532, 534 and 581 seconds of the 600-second limit (11-20 15:10, 18:10, 21:10; 11-21 15:10).
  - The 16:00 and 18:00 ET windows overlap the close windows of 16:30 and 18:30 ET tips. As the slate grows, Phase B
    takes longer and those wakes will skip.
- 11-21: 14 closes resolved, 20 rows still pending tip.

## Player markets (832 graded): 353 NO_CLOSE_QUOTE (42.4%)

| cause | n | % |
|---|---|---|
| OTHER_EXPLICIT_REASON: CLOSE_CONTRACT_INDEX_COLLISION (bug) | 353 | 100 |

- `market_exports.Observations.contracts` ran `market_eval.eligible` over the whole observation. That function
  de-duplicates offers for one player × stat, keyed on (book, line, side), so only one player survived per
  (book, line, side).
- Example: the entry observation for Jervis AST UNDER 1.5 at betonlineag held only another player's UNDER 1.5 at the
  same book.
- With the per-(player, stat) grouping the Board already uses, re-running the production `closing()` on the same
  persisted observations gives:
  - NO_CLOSE_QUOTE: 353 → 0
  - EXACT_LINE: 262 → 667
  - NO_DEVIG_PAIR: 200 → 153
- Lock skips, missing ticks and failed calls caused no player NO_CLOSE rows.
- Caveat: the player close window ends at `min(tip, cutoff)`, and the entry is usually the last pre-cutoff observation.
  The median player probability CLV is therefore exactly 0 (mean +0.0003). Player CLV as defined carries little signal.

## Fix (branch fix/cbb-close-capture-lock-2026-11-21; NOT deployed)

1. **`cbb-originator-run tick` no longer waits blind when another writer holds the lock.** It first tries the writer lock
   non-blocking. If the lock is busy, it runs `lifecycle game-close-capture` and then waits up to 600 seconds as before.
   - The capture runs under a new narrow lock, `locks/cbb-originator.game-quotes.lock`, and never the writer lock.
   - It captures only when the normal cadence is due and a tip is at most 60 minutes away. The wake's normal capture
     then finds NOT_DUE, so there are no extra provider calls. Captures far from any tip still wait for the writer.
2. **Every capture, including the normal one in the quote wake, now takes the narrow lock.** The two paths never overlap.
3. **What the capture writes:** only its own append-only namespace (`ops/game_quotes/<D>/<cid>.{parquet,json}`, now
   written to a tmp file and renamed atomically, invisible to the `GQ*` globs until complete), one-line appends to the
   odds ledger, and exclusive-create raw cache files.
   - It never touches health, state, evaluations or exports, so the single-writer integrity of everything else is
     unchanged.
   - It records `mode` (QUOTE_WAKE or CLOSE_ONLY_WRITER_BUSY) in the receipt.
   - Its database reads are read-only DuckDB connections. If one is refused, the capture reports FAILED and the wake
     falls back to today's behaviour.
4. **Player close index:** contracts are built per (player, stat).
   - Historical FINAL Performance rows are reused by content hash, so the 353 stored NO_CLOSE rows stay as they are until
     a one-off Performance rebuild is authorised.

Still needed: deploy, then live certification (a Phase B window with tips inside 60 minutes; receipts with
`mode=CLOSE_ONLY_WRITER_BUSY`; NO_CLOSE_QUOTE ≈ 0 apart from NO_MARKET_POSTED).
