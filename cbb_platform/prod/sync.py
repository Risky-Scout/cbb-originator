"""Incremental, atomic 2026-27 production sync (CBBD, production-only acquisition path).

    last-known-good state + new legally available 2026-27 information = candidate state -> validate -> promote

Stages (each its own request budget; a stage that would exceed MAX_LIVE_REQUESTS_PER_STAGE STOPs before the call):
    preseason   teams/roster season=2027, recruiting/players year=2026, recruiting/portal year=2026        (3 requests)
    schedule    games season=2027 in 7 chained month windows covering 2026-08-01 .. 2027-07-31            (7 requests)
    completed   only when a final game is newly knowable: games/teams + games/players per game date (window
                [d-1, d+1]), stats/player/season (frozen box identity name key), substitutions/game per game
Cached payloads younger than the stage TTL are reused (no call). source_cutoff = the sync instant: every payload used
was received at or before it. A completed game enters only when its result is knowable (canonical HR-1: tip + 4 h,
06:00 ET next day for inexact tips) at source_cutoff; target games never carry outcome rows (features.compact_canonical).

    .venv/bin/python -m cbb_platform.prod.sync run [--stages preseason,schedule,completed]
    .venv/bin/python -m cbb_platform.prod.sync status
Any failure (provider, budget, guard, validation) is logged to data/prod/sync/sync_log.jsonl and leaves CURRENT.json
(the last-known-good state) untouched.
"""
from __future__ import annotations

import argparse
import json
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd

from cbb_platform import cbbd_box_route as R
from cbb_platform.prod import cbbd_acquire as A, cbbd_feed as F, config as PC, state as ST

PRESEASON = [("teams/roster", {"season": PC.PRODUCTION_SEASON}), ("recruiting/players", {"year": PC.PRESEASON_CLASS_YEAR}),
             ("recruiting/portal", {"year": PC.PRESEASON_CLASS_YEAR})]
TTL_HOURS = {"preseason": 24.0, "schedule": 6.0, "completed": None}    # completed-game payloads of final games: immutable
SCHEDULE_CAP = 3000                                                    # CBBD /games returns at most 3,000 records


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def schedule_windows() -> list[tuple[str, str]]:
    """Chained month windows (CBBD date filter: startDate in [start 00:00Z, end 00:00Z]; cbbd_box_route._windows):
    each ends where the next starts, so every instant of the season window is covered exactly once."""
    lo, hi = date.fromisoformat(PC.PRODUCTION_SEASON_START), date.fromisoformat(PC.PRODUCTION_SEASON_END)
    edges = [lo, date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1), date(2027, 3, 1),
             date(2027, 4, 1), hi]
    return [(a.isoformat(), b.isoformat()) for a, b in zip(edges[:-1], edges[1:])]


def plan_schedule() -> list[tuple[str, dict]]:
    return [("games", {"season": PC.PRODUCTION_SEASON, "startDateRange": a, "endDateRange": b}) for a, b in schedule_windows()]


def knowable_at(tip, tip_quality: str, game_date_et) -> pd.Timestamp:
    """Canonical PIT rule HR-1 (build_canonical.KNOWABLE_SQL)."""
    if tip_quality == "EXACT" and tip is not None and pd.notna(tip):
        return _utc(tip) + pd.Timedelta(hours=4)
    d = pd.Timestamp(str(pd.Timestamp(game_date_et).date())) + pd.Timedelta(days=1, hours=6)
    return d.tz_localize("America/New_York").tz_convert("UTC")


def plan_completed(games: pd.DataFrame, sched: pd.DataFrame) -> list[tuple[str, dict]]:
    """Requests for newly completed games (games: flagship.games rows with a result to ingest)."""
    reqs = []
    lo, hi = date.fromisoformat(PC.PRODUCTION_SEASON_START), date.fromisoformat(PC.PRODUCTION_SEASON_END)
    days = sorted({_utc(t).date() for t in games["scheduled_tip_utc"].dropna()})
    if len(games):              # first: without the season player file no box row can be identity-resolved
        reqs.append(("stats/player/season", {"season": PC.PRODUCTION_SEASON}))
    for d in days:
        a, b = max(d - timedelta(days=1), lo), min(d + timedelta(days=1), hi)
        for ep in ("games/teams", "games/players"):
            reqs.append((ep, {"season": PC.PRODUCTION_SEASON, "startDateRange": a.isoformat(), "endDateRange": b.isoformat()}))
    if len(games):
        pid = sched.set_index("canonical_game_id")["provider_game_id"]
        reqs += [("substitutions/game", {"gameId": int(pid[g])}) for g in sorted(games["canonical_game_id"])]
    return reqs


def completed_reuse_age(endpoint: str, params: dict, games: pd.DataFrame, sched: pd.DataFrame, cache_root: Path,
                        as_of) -> float | None:
    """Cache reuse for a completed-stage request (max_age_hours for Session.get).

    substitutions/game of a final game is immutable: any cached copy is reused. A date-window box payload
    (games/teams, games/players) or the season player file is reused only when the cached copy already carries every
    newly completed game it must carry; otherwise it is re-fetched. (A window cached when its first game finished
    omits a later game of the same window; reusing that copy forever would never ingest the later game.)"""
    if endpoint == "substitutions/game":
        return None
    hit = A.cached(endpoint, params, cache_root, as_of)
    if hit is None:
        return None
    path, meta = hit
    if endpoint == "stats/player/season":
        need = max(knowable_at(t, q, d) for t, q, d in zip(games["scheduled_tip_utc"], games["tip_time_quality"],
                                                            games["game_date_et"]))
        return None if _utc(meta["received_at"]) >= need else 0.0
    a, b = date.fromisoformat(params["startDateRange"]), date.fromisoformat(params["endDateRange"])
    pid = sched.set_index("canonical_game_id")["provider_game_id"].astype(str)
    want = {pid[g] for g, t in zip(games["canonical_game_id"], games["scheduled_tip_utc"])
            if pd.notna(t) and a <= _utc(t).date() < b and g in pid.index}
    have = {str(r.get("gameId")) for r in (A.read(path) or []) if isinstance(r, dict)}
    return None if want <= have else 0.0


def _log(entry: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def _str_cols(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype("string")
    return df


def _complete_in_state(state: dict | None) -> set:
    """Games already ingested with result + both team boxes + player box (not re-requested)."""
    if state is None:
        return set()
    con = duckdb.connect(str(state["increment_db"]), read_only=True)
    try:
        return {r[0] for r in con.execute("""SELECT canonical_game_id FROM flagship.games WHERE home_points IS NOT NULL
                                              AND has_team_box_score AND has_player_box_score""").fetchall()}
    finally:
        con.close()


def run(*, as_of=None, stages=("preseason", "schedule", "completed"), history_db: Path = PC.HISTORY_CANONICAL,
        state_root: Path = PC.STATE_ROOT, cache_root: Path | None = None, ledger_path: Path | None = None,
        sync_log: Path = PC.SYNC_LOG, work_root: Path | None = None, http_get=None, credential=None,
        max_live_requests: int = PC.MAX_LIVE_REQUESTS_PER_STAGE, completed_budget: int | None = None,
        provider_kind: str = "LIVE", clock=None, refresh_schedule_windows=()) -> dict:
    """One sync. Returns the sync record (status PROMOTED | UNCHANGED | FAILED). `clock` (tests) drives every request /
    receipt timestamp; source_cutoff = as_of, else the clock AFTER acquisition (every payload used is <= it).
    refresh_schedule_windows: startDateRange values of schedule windows re-fetched regardless of their TTL (a window
    holding a game whose result has become knowable since the last sync); every other window keeps its TTL.
    Completed stage: when its live-request budget is exhausted, the games whose requests all completed are ingested and
    the rest wait for a later sync (`completed_budget_exhausted`); the per-stage budget itself is unchanged."""
    clock = clock or _now
    sync_id = "Y" + _utc(clock()).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    cut = _utc(as_of) if as_of is not None else None
    cache_root = Path(cache_root or PC.CBBD_CACHE_RAW)
    work = Path(work_root or (PC.PROD_ROOT / "sync" / "work")) / sync_id
    work.mkdir(parents=True, exist_ok=True)
    rec = {"sync_id": sync_id, "stages": list(stages), "feed_version": PC.FEED_VERSION, "requests": {}}
    kw = dict(cache_root=cache_root, ledger_path=ledger_path, http_get=http_get, credential=credential, clock=clock)
    sessions: list = []
    try:
        prev = ST.current(state_root)
        # ------------------------------------------------------------ acquisition (production path, cached)
        if "preseason" in stages:
            s = A.Session(f"{sync_id}:preseason", max_live_requests=max_live_requests, **kw)
            sessions.append(s)
            for ep, p in PRESEASON:
                s.get(ep, p, max_age_hours=TTL_HOURS["preseason"], purpose="2026-27 preseason identity")
            rec["requests"]["preseason"] = s.summary()
        if "schedule" in stages:
            s = A.Session(f"{sync_id}:schedule", max_live_requests=max_live_requests, **kw)
            sessions.append(s)
            force = {str(x) for x in refresh_schedule_windows}
            for ep, p in plan_schedule():
                e = s.get(ep, p, max_age_hours=0.0 if p["startDateRange"] in force else TTL_HOURS["schedule"],
                          purpose="2026-27 schedule")
                if (e.get("record_count") or 0) >= SCHEDULE_CAP:
                    raise F.FeedUnavailable("PROVIDER_FAILURE", f"schedule window {p} hit the {SCHEDULE_CAP}-record cap")
            rec["requests"]["schedule"] = s.summary()
        # ------------------------------------------------------------ snapshot (latest payload <= source_cutoff)
        if cut is None:            # the sync instant, never earlier than a payload this sync received
            got = [_utc(e["received_at"]) for ss in sessions for e in ss.entries if e.get("received_at")]
            cut = max([_utc(clock())] + got)
        rec["source_cutoff"] = cut.isoformat()
        snap = work / "snapshot"
        prov = F.snapshot(PRESEASON, cut, snap, cache_root)
        roster, roster_meta = F.payload("teams/roster", PRESEASON[0][1], cut, cache_root)
        teams = F.division_one(roster)
        games, sched_received = [], []
        for ep, p in plan_schedule():
            body, meta = F.payload(ep, p, cut, cache_root)
            if body is None:
                raise F.FeedUnavailable("MISSING_SCHEDULE", f"no cached schedule window {p}")
            prov[A.request_key(ep, p)] = {"endpoint": ep, "params": p, "received_at": meta["received_at"], "sha256": meta["sha256"]}
            sched_received.append(_utc(meta["received_at"]))
            games += [(g, meta) for g in body]
        schedule_as_of = min(sched_received)
        G, S, newteams = F.schedule_rows(games, teams, schedule_as_of)
        # results only once knowable at the cutoff (HR-1); later-final games wait for a later sync
        kn = [knowable_at(t, q, d) for t, q, d in zip(G["scheduled_tip_utc"], G["tip_time_quality"], G["game_date_et"])]
        not_yet = G["home_points"].notna() & (pd.Series(kn, index=G.index) > cut)
        G.loc[not_yet, ["home_points", "away_points"]] = pd.NA
        rec["results_deferred_not_yet_knowable"] = int(not_yet.sum())
        # ------------------------------------------------------------ frozen preseason identity
        # lineage instant of derived rows = the newest input payload receipt (deterministic: identical inputs -> identical
        # rows -> an unchanged re-sync is idempotent)
        lineage_at = max(_utc(v["received_at"]) for v in prov.values())
        pre, pre_rep = F.preseason_identity(history_db, snap, work / "preseason.duckdb", lineage_at)
        rec["preseason"] = pre_rep
        # ------------------------------------------------------------ completed games
        done = _complete_in_state(prev)
        newly = G[G["home_points"].notna() & ~G["canonical_game_id"].isin(done)]
        box_rel, overrides = {}, None
        rec["completed_games_new"] = int(len(newly))
        if len(newly) and "completed" in stages:
            s = A.Session(f"{sync_id}:completed", max_live_requests=completed_budget or max_live_requests,
                          game_season=lambda gid: PC.PRODUCTION_SEASON if str(gid) in set(S["provider_game_id"]) else None,
                          **kw)
            plan = plan_completed(newly, S)
            box = {"games": [g for g, _ in games], "games/teams": [], "games/players": [], "stats/player/season": []}
            subs_req, got_stats = [], False
            for n_done, (ep, p) in enumerate(plan):
                try:
                    e = s.get(ep, p, max_age_hours=completed_reuse_age(ep, p, newly, S, cache_root, clock()),
                              purpose="2026-27 completed games")
                except A.BudgetExceeded:            # STOP before the call; finished requests are kept (cached)
                    rec["completed_budget_exhausted"] = True
                    rec["completed_requests_deferred"] = len(plan) - n_done
                    break
                if ep == "substitutions/game":
                    subs_req.append((ep, p))
                else:
                    box[ep] += e["body"]
                    got_stats |= ep == "stats/player/season"
            subs_ok = {F.cid("G", "game", p["gameId"]) for _, p in subs_req}
            rec["requests"]["completed"] = s.summary()
        if len(newly) and "completed" in stages and not got_stats:
            rec["completed_note"] = "budget exhausted before the season player file: results withheld this sync"
            rec["completed_games_ingested"] = 0
        elif len(newly) and "completed" in stages:
            # the completed-game payloads were received after the schedule snapshot: the state's source_cutoff moves to
            # the latest receipt (results were admitted only if knowable at the EARLIER instant, which is stricter)
            cut = max([cut] + [_utc(e["received_at"]) for e in s.entries if e.get("received_at")])
            rec["source_cutoff"] = cut.isoformat()
            F.snapshot(subs_req, cut, snap, cache_root)
            lineage_at = max([lineage_at] + [_utc(e["received_at"]) for e in s.entries if e.get("received_at")])
            box_rel, overrides, crep = F.completed_games(history_db, box, snap, work / "completed.duckdb", lineage_at,
                                                         extra_anchor_ids=pre["flagship.rosters"]["source_athlete_id"])
            crep.pop("players_seen", None)
            rec["completed"] = crep
            keep = set(newly["canonical_game_id"])
            overrides = overrides[overrides["canonical_game_id"].isin(keep)].reset_index(drop=True)
            overrides["scheduled_tip_utc"] = pd.to_datetime(overrides["scheduled_tip_utc"], utc=True)
            overrides["game_date_et"] = pd.to_datetime(overrides["game_date_et"])
            ok = overrides["has_team_box_score"].astype(bool) & overrides["has_player_box_score"].astype(bool)
            # the frozen build's game fields (tip / tip quality from the team box, HR-1) for newly completed games;
            # a final game whose result is not yet knowable at the cutoff, or whose boxes are incomplete, waits
            ovk = [knowable_at(t, q, d) for t, q, d in zip(overrides["scheduled_tip_utc"], overrides["tip_time_quality"],
                                                          overrides["game_date_et"])]
            ok &= pd.Series(ovk, index=overrides.index) <= cut
            ok &= overrides["canonical_game_id"].isin(subs_ok)
            overrides = overrides[ok].reset_index(drop=True)
            keep = set(overrides["canonical_game_id"])
            for k in box_rel:
                box_rel[k] = box_rel[k][box_rel[k]["canonical_game_id"].isin(keep)].reset_index(drop=True)
            rec["completed_games_ingested"] = int(len(keep))
        elif len(newly):
            rec["completed_note"] = "completed stage not requested: results recorded without box rows are withheld"
        if overrides is not None and len(overrides):
            ov = overrides.set_index("canonical_game_id")
            m = G["canonical_game_id"].isin(ov.index)
            for c in ("scheduled_tip_utc", "tip_time_quality", "game_date_et", "home_points", "away_points",
                      "has_team_box_score", "has_player_box_score"):
                G.loc[m, c] = G.loc[m, "canonical_game_id"].map(ov[c])
        prev_done = done
        pending = G["home_points"].notna() & ~G["canonical_game_id"].isin(prev_done) & \
            ~G["canonical_game_id"].isin(set(overrides["canonical_game_id"]) if overrides is not None else set())
        G.loc[pending, ["home_points", "away_points"]] = pd.NA          # never a result without its box rows
        if prev_done:
            G.loc[G["canonical_game_id"].isin(prev_done), ["has_team_box_score", "has_player_box_score"]] = True
        G = _str_cols(G, ("season_type", "game_type", "tournament", "home_team_id", "away_team_id", "home_conference_id",
                          "away_conference_id", "canonical_venue_id", "source_record_key", "source_system"))
        S = _str_cols(S, ("provider_game_id", "status", "home_team_id", "away_team_id", "venue_id", "venue", "city", "state",
                          "provider_home_team_id", "provider_away_team_id", "home_team_name", "away_team_name"))
        S["substitutions_acquired"] = S["canonical_game_id"].isin(
            (set(overrides["canonical_game_id"]) if overrides is not None else set()) | prev_done)
        # ------------------------------------------------------------ candidate rows
        hist = duckdb.connect(str(history_db), read_only=True)
        hist_teams = {r[0] for r in hist.execute("SELECT canonical_team_id FROM core.teams").fetchall()}
        hist.close()
        new_rows = {"flagship.games": G, "metadata.prod_schedule": S,
                    "core.team_seasons": F.team_season_rows(teams), **pre, **box_rel}
        if len(newteams):
            nt = newteams[~newteams["canonical_team_id"].isin(hist_teams)]
            if len(nt):
                new_rows["core.teams"] = nt.reset_index(drop=True)
        replace = [r for r in ST.SNAPSHOT_RELATIONS if r in new_rows]
        # freshness = the latest instant the provider confirmed the roster content (an unchanged re-fetch keeps the
        # original payload file but re-verifies it)
        ver = [e["verified_at"] for ss in sessions for e in ss.entries
               if e.get("endpoint") == "teams/roster" and e.get("verified_at") and _utc(e["verified_at"]) <= cut]
        roster_as_of = max([_utc(roster_meta["received_at"])] + [_utc(v) for v in ver]).isoformat()
        evidence = {"source": "CBBD_API_PRODUCTION", "roster_2027_acquired": True, "portal_2026_complete": True,
                    "recruits_2026_complete": True,
                    "portal_as_of": prov[A.request_key(*PRESEASON[2])]["received_at"],
                    "recruits_as_of": prov[A.request_key(*PRESEASON[1])]["received_at"],
                    "note": "complete = the provider's full 2026 portal / class-of-2026 payloads were acquired; recruits the "
                            "provider has not linked to a roster athlete stay suppressed (RECRUIT_IDENTITY_UNAVAILABLE)",
                    "preseason_identity": pre_rep}
        extra = {"schedule_as_of": schedule_as_of.isoformat(), "roster_as_of": roster_as_of,
                 "last_successful_sync": cut.isoformat(), "sync_id": sync_id, "feed_provenance": prov,
                 "feed_version": PC.FEED_VERSION,
                 "division_one_2026_27": {"teams": int(sum(v["di"] for v in teams.values())),
                                          "method": "NON_EMPTY_CBBD_ROSTER_2026_27"}}
        provider = {"kind": provider_kind, "name": "CBBD_API_PRODUCTION", "feed_version": PC.FEED_VERSION}
        man = ST.update(new_rows, cut, provider, evidence, root=state_root, created_at=max(cut, _now()) if provider_kind == "LIVE" else cut,
                        replace=replace, manifest_extra=extra)
        rec.update({"status": "UNCHANGED" if man.get("idempotent_no_change") else "PROMOTED",
                    "state_version": man["state_version"], "coverage": {k: v for k, v in man["coverage"].items()
                                                                         if k != "relation_sha256"}})
    except (A.ProductionGuardViolation, A.BudgetExceeded, A.ProviderFailure, F.FeedUnavailable, ST.StateUpdateFailed) as e:
        reason = ("PROVIDER_FAILURE" if isinstance(e, (A.ProviderFailure, A.BudgetExceeded)) else
                  getattr(e, "reason", None) or ("STATE_UPDATE_FAILED" if isinstance(e, ST.StateUpdateFailed) else "PROVIDER_FAILURE"))
        rec.update({"status": "FAILED", "reason": reason, "error_type": type(e).__name__, "error": str(e)[:1500]})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    rec["finished_at"] = max(_utc(clock()), _now() if clock is _now else _utc(clock())).isoformat()
    _log(rec, Path(sync_log))
    return rec


def last_sync(sync_log: Path = PC.SYNC_LOG) -> dict | None:
    p = Path(sync_log)
    if not p.exists():
        return None
    lines = [l for l in p.read_text().splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else None


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.sync")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--stages", default="preseason,schedule,completed")
    r.add_argument("--max-live-requests", type=int, default=PC.MAX_LIVE_REQUESTS_PER_STAGE)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        out = run(stages=tuple(a.stages.split(",")), max_live_requests=a.max_live_requests)
        print(json.dumps(out, indent=1, default=str))
    else:
        st = ST.current()
        print(json.dumps({"current": st["pointer"] if st else None, "last_sync": last_sync()}, indent=1, default=str))


if __name__ == "__main__":
    main()
