"""CollegeBasketballData (CBBD) API adapter — the P0 basketball backbone.

Stage 1 scope: interface + bounded smoke fetch only (NO historical backfill).
Credential: CBBD_API_KEY in the environment (never written to disk). Without it, live fetch
is BLOCKED_CREDENTIAL; normalization is still exercised by tests with a labeled synthetic fixture.

    .venv/bin/python -m cbb_platform.adapters.cbbd smoke <cbbd_game_id> [<cbbd_game_id> ...]

The Starter Pack already holds games / box scores / season stats for 2005-2025, so the smoke
fetch targets only what the Starter Pack lacks: PBP (with onFloor + shotInfo), substitutions,
lineups, and one roster. The bounded Stage 1 closure sample lives in cbb_platform/cbbd_smoke.py.
Every request passes the 2025-26 lockbox guard (cbb_platform/lockbox.py, LOCKBOX_POLICY.md).

Normalization targets (all keyed to canonical ids):
    /plays/game/{id}          -> fact.plays (+ fact.shots for plays carrying shotInfo)
    /substitutions/game/{id}  -> fact.substitutions  (athlete-centric, as CBBD supplies it)
    /lineups/game/{id}        -> fact.lineup_stats   (five-man aggregates, NOT stints)
Every other endpoint is preserved raw for later stages.
"""
from __future__ import annotations

import json
import sys
import time

import duckdb
import requests  # noqa: F401  (tests patch cbbd.requests.get; source_fetch performs the call)

from cbb_platform import config as C, credentials, lockbox, raw_store, source_fetch

BASE = "https://api.collegebasketballdata.com"
PROVIDER = "cbbd"

# Endpoints this adapter knows how to call. Path templates use {gameId}.
ENDPOINTS = {
    "games": "/games", "games/players": "/games/players", "games/teams": "/games/teams",
    "plays/game": "/plays/game/{gameId}", "substitutions/game": "/substitutions/game/{gameId}",
    "lineups/game": "/lineups/game/{gameId}", "teams/roster": "/teams/roster",
    "conferences": "/conferences", "teams": "/teams", "venues": "/venues",
    "recruiting/players": "/recruiting/players", "recruiting/portal": "/recruiting/portal",
    "stats/player/season": "/stats/player/season", "stats/team/season": "/stats/team/season",
    # Stage 2 bulk / reference endpoints (cbb_platform/cbbd_client.py, docs/CBBD_BACKFILL_STRATEGY.md)
    "plays/date": "/plays/date", "plays/team": "/plays/team",
    "substitutions/team": "/substitutions/team", "lineups/team": "/lineups/team",
    "recruiting/teams": "/recruiting/teams",
}


def _default_game_season(game_id):
    """Season of a CBBD game id according to the built canonical database (None if unknown)."""
    if not C.DUCKDB_PATH.exists():
        return None
    con = duckdb.connect(str(C.DUCKDB_PATH), read_only=True)
    try:
        return lockbox.canonical_game_season(con, "cbbd")(game_id)
    finally:
        con.close()


def fetch(endpoint: str, params: dict, known_game_season=None) -> raw_store.RawRecord:
    """One live GET through the global lockbox-guarded fetch path (cbb_platform/source_fetch.py).

    The credential travels only in the Authorization header. It is never part of `params`, the
    raw payload, the sidecar metadata, or any exception text.
    """
    resolver = known_game_season or _default_game_season
    lockbox.check_request(PROVIDER, endpoint, params, resolver)      # before even reading the credential
    key = credentials.get("CBBD_API_KEY")
    path = ENDPOINTS[endpoint].format(**params)
    query = {k: v for k, v in params.items() if "{" + k + "}" not in ENDPOINTS[endpoint]}
    content, _ = source_fetch.get(PROVIDER, endpoint, BASE + path, query, {"Authorization": f"Bearer {key}"},
                                  known_game_season=resolver, guard_params=params)
    return raw_store.store(PROVIDER, endpoint, content, params=params)


def smoke(game_ids: list[int]) -> dict:
    """Bounded: a handful of games, three event endpoints each, plus one roster."""
    out = {}
    for gid in game_ids:
        for ep in ("plays/game", "substitutions/game", "lineups/game"):
            out[f"{ep}:{gid}"] = fetch(ep, {"gameId": gid}).path.name
            time.sleep(0.3)
    return out


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def _canonical_player(con: duckdb.DuckDBPyConnection, athlete_id) -> str | None:
    """CBBD API athlete id -> canonical id, only if that id is a known identity anchor AND the live
    identity verification proved API ids persistent and equal to the Starter Pack season-file ids
    (docs/CBBD_PLAYER_IDENTITY_VERIFICATION.md). Until then no API id is ever mapped."""
    from cbb_platform.cbbd_identity import verified_anchor_compatible
    if athlete_id is None or not verified_anchor_compatible():
        return None
    row = con.execute("SELECT canonical_player_id FROM metadata.player_id_map "
                      "WHERE provider = 'CBBD' AND provider_player_key = ?", [str(athlete_id)]).fetchone()
    return row[0] if row else None


def _player_token(con, athlete_id) -> str | None:
    """Canonical id when mapped; otherwise an explicit provider-scoped token (never a fake canonical id)."""
    if athlete_id is None:
        return None
    return _canonical_player(con, athlete_id) or f"CBBD_ATHLETE:{athlete_id}"


def _game(con, gid) -> str:
    return con.execute("SELECT cid('G','game', ?)", [str(gid)]).fetchone()[0]


def _team(con, tid) -> str | None:
    return None if tid is None else con.execute("SELECT cid('T','team', ?)", [str(tid)]).fetchone()[0]


def normalize_all(con: duckdb.DuckDBPyConnection) -> None:
    """Stage 2: every CBBD raw payload family is normalized set-wise by cbb_platform/event_build.py
    (plays + participants + onFloor + shots, substitutions + derived pairs, lineups, rosters, recruiting,
    portal, venues, reconciliation, defect quarantine). The per-row Stage 1 helpers below are kept for
    reference only and are no longer called."""
    from cbb_platform import event_build
    event_build.build_events(con, C.RAW)


def _lin(lineage, key):
    """[source_system, source_file, source_record_key, received_at] in DDL column order."""
    src, file, received = lineage
    return [src, file, key, received]


def _plays(con, plays: list[dict], lineage) -> None:
    ordered = sorted(plays, key=lambda p: (p["period"], -p["secondsRemaining"], int(p["id"])))
    for seq, p in enumerate(ordered, start=1):
        gid = _game(con, p["gameId"])
        key = f"gameId={p['gameId']};playId={p['id']}"
        con.execute("""INSERT INTO fact.plays VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                       ?,?,?,getvariable('ver'),?,getvariable('proc'))""", [
            gid, str(p["id"]), seq, p["period"], p["clock"], p["secondsRemaining"], p["playType"],
            _team(con, p.get("teamId")), p["homeScore"], p["awayScore"], p.get("scoringPlay"),
            p.get("shootingPlay"), p.get("scoreValue"), p.get("playText"),
            [_player_token(con, x["id"]) for x in p.get("participants", [])],
            [_player_token(con, x["id"]) for x in p.get("onFloor", [])],   # exactly as supplied
            p.get("wallclock"), *_lin(lineage, key)])
        shot = p.get("shotInfo")
        if shot:
            loc = shot.get("location") or {}
            con.execute("""INSERT INTO fact.shots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,getvariable('ver'),?,getvariable('proc'))""", [
                gid, str(p["id"]), _player_token(con, (shot.get("shooter") or {}).get("id")), shot.get("made"),
                shot.get("range"), shot.get("assisted"), _player_token(con, (shot.get("assistedBy") or {}).get("id")),
                loc.get("x"), loc.get("y"), *_lin(lineage, key)])


def _substitutions(con, subs: list[dict], lineage) -> None:
    # stint_number = order of this athlete's sub-in events within the game.
    by_athlete: dict = {}
    for s in subs:
        by_athlete.setdefault((s["gameId"], s["athleteId"]), []).append(s)
    for (gid, aid), rows in sorted(by_athlete.items()):
        rows.sort(key=lambda s: (s["subIn"]["period"], -s["subIn"]["secondsRemaining"]))
        for n, s in enumerate(rows, start=1):
            i, o = s["subIn"], s["subOut"]
            con.execute("""INSERT INTO fact.substitutions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,getvariable('ver'),?,getvariable('proc'))""", [
                _game(con, gid), _team(con, s["teamId"]), _canonical_player(con, aid), str(aid), n,
                i["period"], i["secondsRemaining"], i["teamPoints"], i["opponentPoints"],
                o["period"], o["secondsRemaining"], o["teamPoints"], o["opponentPoints"],
                *_lin(lineage, f"gameId={gid};athleteId={aid};stint={n}")])


def _lineups(con, lineups: list[dict], lineage, game_id) -> None:
    for u in lineups:
        con.execute("""INSERT INTO fact.lineup_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,getvariable('ver'),?,getvariable('proc'))""", [
            _game(con, game_id), _team(con, u["teamId"]), u["idHash"],
            [_player_token(con, a["id"]) for a in u["athletes"]], u["totalSeconds"],
            u["teamStats"]["possessions"], u["teamStats"]["points"],
            u["opponentStats"]["possessions"], u["opponentStats"]["points"],
            *_lin(lineage, f"gameId={game_id};idHash={u['idHash']}")])


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "smoke":
        print(json.dumps(smoke([int(x) for x in sys.argv[2:]]), indent=1))
    else:
        print(__doc__)
