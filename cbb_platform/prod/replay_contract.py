"""THE historical replay information contract (Stage 07, 2026-11-27). Stage 08 must use it for CORRECTED_CANDIDATE_REPLAY.

The earliest legitimate availability of an observation is the latest of three times:
* its structural rule time (historical_knowable_at = tip + 4 h, or the next day 06:00 ET when the tip is inexact);
* its provider publication time (RotoWire source_datetime; box lines carry none);
* its actual acquisition by production.

Acquisition is never replaced by the provider's own timestamp. Each information source is resolved as follows:

| information | replay source at instant T | acquisition bound |
|---|---|---|
| box scores, results, rosters, portal, recruits, identities | the immutable state version as-of T (`walkforward.eligibility.state_asof`: created_at <= T and source_cutoff <= T); the rule mask is applied inside it | the version's source_cutoff. Late boxes, provider revisions and later roster snapshots are therefore absent. `prod.features.compact_canonical` refuses a later state (LATER_INFORMATION_STATE) |
| RotoWire availability events | `rotowire_feed.usable`: knowable_at = max(collected_at, source_datetime) < T and collected_at < T | collected_at (append-only event store) |
| curated player states / roster omissions | the CSV as deployed in the release in force at T (`curated_csv_at`); never the current CSV applied from verified_on 00:00 ET | first deploy of the row's exact content. The current CSV backdates 18-43 h (`docs/audits/2026-11-27-stage7`) |
| CTX1 context ledger | `context_adj.load_ledger(..., replay=True)`: rows with added_at <= T only; legacy rows without lineage are refused | added_at |
| player model | `model_source.resolve("ACTIVE_DAILY", T, replay=True)`: training_cutoff <= T and promoted_at <= T | promoted_at |
| game ratings | refit on the state as-of the day's cutoff; persisted daily states record `information_state` | state source_cutoff |

A replay never rewrites an original prospective forecast. Its outputs are a separate population, keyed by code commit, engine_id,
joint_version / mean_basis and the information-state identities above.
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path

import pandas as pd

from cbb_platform import config as C
from cbb_platform.prod import config as PC

CURATED = {"rotowire_player_states": PC.ROTOWIRE_PLAYER_STATES, "roster_omissions": PC.ROSTER_OMISSIONS}


class ReplayContractError(RuntimeError):
    pass


def curated_csv_at(release_commit: str, which: str, show) -> pd.DataFrame:
    """The curated CSV exactly as deployed in `release_commit`. `show(commit, repo_relative_path) -> text | None` is
    supplied by the replay harness (e.g. `git show`; the production layer runs no subprocess). None = the file did not
    exist in that release (no rows)."""
    if which not in CURATED:
        raise ValueError(f"{which!r} not in {sorted(CURATED)}")
    rel = Path(CURATED[which]).resolve().relative_to(Path(C.PROJECT_ROOT).resolve()).as_posix()
    text = show(release_commit, rel)
    if text is None:
        return pd.DataFrame()
    return pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)


def release_in_force(deploys: list[tuple[str, str]], at) -> str | None:
    """The release commit in force at `at`, from an ordered [(commit, first_in_force_utc), ...] deploy history (the
    first production run on each commit: an upper bound on the deploy instant, so this is conservative)."""
    t = pd.Timestamp(at)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    cur = None
    for c, when in sorted(deploys, key=lambda x: pd.Timestamp(x[1])):
        if pd.Timestamp(when) <= t:
            cur = c
    return cur
