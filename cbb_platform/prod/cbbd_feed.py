"""2026-27 CBBD production payloads -> canonical increment relations (the format prod/state.py validates and the frozen
Stage 3 SQL reads). Every identity / PIT / accounting rule is the FROZEN canonical code, re-run on a scratch DuckDB
that holds only production rows; nothing in the frozen canonical history is modified or rebuilt.

    preseason_identity()   rosters (2026-27) + recruiting class 2026 + portal 2026 through the frozen
                           event_build.build_rosters / _known_players / build_recruiting, on a scratch DB seeded with the
                           frozen history's CBBD player anchors, players and 2025-26 (origin) rosters:
                             * returning players keep their canonical id (CBBD athleteId is the verified persistent id:
                               roster id -> cid('P','athlete', id), the same anchor every historical row uses);
                             * transfers: the frozen portal rule (origin 2025-26 roster first/last name -> identity;
                               2026-27 destination roster corroborates -> RESOLVED_ROSTER_CORROBORATED); ambiguous names
                               stay UNRESOLVED_AMBIGUOUS_NAME (no identity, no membership: suppressed);
                             * freshmen: the frozen recruit rule. CBBD has not linked any 2026-class recruit to an
                               athleteId yet (0 / 396 on 2026-10-20), so the frozen rule yields only
                               CANDIDATE_COMMITTED_ROSTER_NAME (unique exact name on the committed team's 2026-27 roster).
                               Production identity adaptation P-2 promotes exactly that candidate to the canonical id
                               (measured: 1,419 / 1,419 agreement with CBBD's own athleteId link over the 2021-2025
                               classes, 0 disagreements, 0 ambiguous). Ambiguous / unmatched recruits are not promoted.
    schedule_rows()        2026-27 schedule (CBBD /games) -> flagship.games rows (build_canonical.build_games field rules)
                           + metadata.prod_schedule (provider id, status, source timestamp, listing presence)
    division_one()         D-I team-seasons for 2026-27: a team with a non-empty CBBD 2026-27 roster (measured on 2025-26:
                           non-empty roster == the frozen D-I proxy for 365 / 365 D-I and 362 / 362 non-D-I teams)
    completed_games()      final 2026-27 games: games / games/teams / games/players / stats/player/season reshaped by the
                           frozen box route (cbbd_box_route.reshape, dedupe, final_games_only, validate_team_assignment)
                           and built by the frozen build_canonical.build_games / build_team_games /
                           build_players_and_player_games / flag_accounting + event_build.build_substitutions. Box
                           identity is the frozen (season, team, exact name) rule; knowable_at is the frozen HR-1.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from cbb_platform import build_canonical as B, cbbd_box_route as R, config as C, event_build as E, raw_store
from cbb_platform.prod import cbbd_acquire as A, config as PC

SRC = "CBBD_API_PRODUCTION"
P2 = "PROD_P2_COMMITTED_ROSTER_NAME_UNIQUE"
P3 = "PROD_P3_REISSUED_ATHLETE_ID"
P3_COLS = ["new_player_id", "hist_player_id", "source_transfer_id", "new_athlete_id", "hist_athlete_id", "name",
           "origin_team_id", "destination_team_id", "height_inches", "hometown_city"]


class FeedUnavailable(RuntimeError):
    def __init__(self, reason: str, msg: str):
        super().__init__(f"{reason}: {msg}")
        self.reason = reason


def cid(prefix: str, entity: str, provider_id) -> str:
    """Python twin of build_canonical.ID_MACROS cid(): prefix-sha256('CBBD|entity|id')[:16]."""
    return f"{prefix}-" + hashlib.sha256(f"CBBD|{entity}|{provider_id}".encode()).hexdigest()[:16]


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# ============================================================================ snapshot selection
def snapshot(requests_: list[tuple[str, dict]], as_of, out_root: Path, cache_root: Path | None = None,
             required: bool = True) -> dict:
    """Copy the LATEST cached payload of each request received at or before `as_of` into a scratch raw_store root (so the
    frozen event_build readers see exactly one snapshot per request, never a stale earlier copy). Returns provenance."""
    out_root = Path(out_root)
    prov = {}
    for ep, params in requests_:
        hit = A.cached(ep, params, cache_root, as_of)
        if hit is None:
            if required:
                raise FeedUnavailable("MISSING_ROSTER" if ep == "teams/roster" else "LIVE_DATA_FEED_UNAVAILABLE",
                                      f"no cached {ep} {params} at or before {as_of}")
            continue
        path, meta = hit
        dst = out_root / "cbbd" / raw_store._slug(ep)
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dst / meta["file"])
        (dst / (meta["file"] + ".meta.json")).write_text(json.dumps(meta, sort_keys=True))
        prov[A.request_key(ep, params)] = {"endpoint": ep, "params": params, "received_at": meta["received_at"],
                                           "sha256": meta["sha256"], "file": meta["file"]}
    return prov


def payload(ep: str, params: dict, as_of, cache_root: Path | None = None):
    hit = A.cached(ep, params, cache_root, as_of)
    if hit is None:
        return None, None
    return A.read(hit[0]), hit[1]


# ============================================================================ D-I + teams (2026-27)
def division_one(roster_body: list) -> dict:
    """team canonical id -> {'provider_team_id', 'school', 'conference', 'di'} from the 2026-27 roster payload."""
    out = {}
    for t in roster_body:
        if t.get("teamId") is None:
            continue
        out[cid("T", "team", t["teamId"])] = {"provider_team_id": str(t["teamId"]), "school": t.get("team"),
                                               "conference": t.get("conference"), "di": bool(t.get("players"))}
    return out


def team_season_rows(teams: dict) -> pd.DataFrame:
    return pd.DataFrame([{"canonical_team_id": k, "season": PC.PRODUCTION_SEASON, "is_division_one": v["di"],
                          "division_one_method": "NON_EMPTY_CBBD_ROSTER_2026_27"} for k, v in sorted(teams.items())],
                        columns=["canonical_team_id", "season", "is_division_one", "division_one_method"])


# ============================================================================ schedule
SCHEDULE_COLS = ["canonical_game_id", "provider_game_id", "season", "status", "start_time", "start_time_tbd",
                 "tip_time_quality", "game_date_et", "home_team_id", "away_team_id", "provider_home_team_id",
                 "provider_away_team_id", "home_team_name", "away_team_name", "neutral_site", "venue_id", "venue",
                 "city", "state", "source_received_at", "listed_in_latest_schedule", "schedule_as_of"]


def _universe(h, a, di: dict) -> str:
    hs, as_ = di.get(h), di.get(a)
    if h is None or a is None or hs is None or as_ is None:
        return "UNKNOWN"
    if hs["di"] and as_["di"]:
        return "DI_VS_DI"
    if hs["di"] or as_["di"]:
        return "DI_VS_NON_DI"
    return "NON_DI_ONLY"


def schedule_rows(games: list[tuple[dict, dict]], di: dict, schedule_as_of) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """games: [(game record, cache meta)] from every schedule window of one snapshot. Returns
    (flagship.games rows, metadata.prod_schedule rows, new core.teams rows). Field rules = build_canonical.build_games:
    tip = startDate (UTC); tip_time_quality DATE_ONLY (no tip) / TBD_FLAG (startTimeTbd) / EXACT; game_date_et = the
    US/Eastern date of the tip; result fields only when status is exactly 'final' (cbbd_box_route.is_final_status)."""
    seen = {}
    for g, meta in games:
        k = g["id"]
        body = {x: y for x, y in g.items()}
        if k in seen and seen[k][0] != body:
            raise FeedUnavailable("PROVIDER_FAILURE", f"conflicting CBBD schedule records for game {k}")
        seen[k] = (body, meta)
    rows, sched, teams = [], [], {}
    for k in sorted(seen, key=int):
        g, meta = seen[k]
        if str(g.get("season")) != str(PC.PRODUCTION_SEASON):
            raise FeedUnavailable("POST_CUTOFF_DATA", f"schedule record {k} is season {g.get('season')}")
        tip = _utc(g["startDate"]) if g.get("startDate") else None
        tq = "DATE_ONLY" if tip is None else ("TBD_FLAG" if g.get("startTimeTbd") else "EXACT")
        et = tip.tz_convert("America/New_York").date() if tip is not None else None
        final = R.is_final_status(g.get("status"))
        h = cid("T", "team", g["homeTeamId"]) if g.get("homeTeamId") is not None else None
        a = cid("T", "team", g["awayTeamId"]) if g.get("awayTeamId") is not None else None
        for tid, pid, name in ((h, g.get("homeTeamId"), g.get("homeTeam")), (a, g.get("awayTeamId"), g.get("awayTeam"))):
            if tid is not None:
                teams.setdefault(tid, {"canonical_team_id": tid, "school": name, "display_name": name,
                                       "source_team_id": str(pid)})
        gid = cid("G", "game", k)
        rows.append({
            "canonical_game_id": gid, "season": PC.PRODUCTION_SEASON, "season_type": g.get("seasonType"),
            "game_type": g.get("gameType"), "tournament": g.get("tournament"), "scheduled_tip_utc": tip,
            "tip_time_quality": tq, "game_date_et": pd.Timestamp(et) if et else None,
            "neutral_site": g.get("neutralSite"), "conference_game": g.get("conferenceGame"),
            "home_team_id": h, "away_team_id": a,
            "home_conference_id": cid("C", "conference", g["homeConferenceId"]) if g.get("homeConferenceId") is not None else None,
            "away_conference_id": cid("C", "conference", g["awayConferenceId"]) if g.get("awayConferenceId") is not None else None,
            "canonical_venue_id": cid("V", "venue", g["venueId"]) if g.get("venueId") is not None else None,
            "home_points": int(g["homePoints"]) if final and g.get("homePoints") is not None else None,
            "away_points": int(g["awayPoints"]) if final and g.get("awayPoints") is not None else None,
            "game_universe": _universe(h, a, di), "has_team_box_score": False, "has_player_box_score": False,
            "source_system": SRC, "source_record_key": f"id={k}"})
        sched.append({"canonical_game_id": gid, "provider_game_id": str(k), "season": PC.PRODUCTION_SEASON,
                      "status": g.get("status"), "start_time": tip, "start_time_tbd": bool(g.get("startTimeTbd")),
                      "tip_time_quality": tq, "game_date_et": pd.Timestamp(et) if et else None, "home_team_id": h,
                      "away_team_id": a, "provider_home_team_id": str(g.get("homeTeamId")),
                      "provider_away_team_id": str(g.get("awayTeamId")), "home_team_name": g.get("homeTeam"),
                      "away_team_name": g.get("awayTeam"), "neutral_site": g.get("neutralSite"),
                      "venue_id": str(g.get("venueId")) if g.get("venueId") is not None else None, "venue": g.get("venue"),
                      "city": g.get("city"), "state": g.get("state"), "source_received_at": _utc(meta["received_at"]),
                      "listed_in_latest_schedule": True, "schedule_as_of": _utc(schedule_as_of)})
    G = pd.DataFrame(rows)
    for c in ("home_points", "away_points"):
        G[c] = G[c].astype("Int64")
    for c in ("tournament", "home_conference_id", "away_conference_id", "canonical_venue_id", "home_team_id", "away_team_id"):
        G[c] = G[c].astype(object)
    return G, pd.DataFrame(sched, columns=SCHEDULE_COLS), pd.DataFrame(list(teams.values()))


# ============================================================================ preseason identity (frozen event_build)
def _scratch(path: Path, proc) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path), config={"threads": 2})
    con.execute("SET TimeZone = 'UTC'")
    con.execute(C.schema_sql())                       # the frozen canonical schema (tables + views), empty
    con.execute(B.ID_MACROS)
    con.execute("SET VARIABLE src = ?", [SRC])
    con.execute("SET VARIABLE ev_src = ?", [SRC])
    con.execute("SET VARIABLE ver = ?", [PC.FEED_VERSION])
    con.execute("SET VARIABLE recv = CAST(? AS TIMESTAMPTZ)", [_utc(proc).isoformat()])
    con.execute("SET VARIABLE proc = CAST(? AS TIMESTAMPTZ)", [_utc(proc).isoformat()])
    return con


def _relax(con, rel: str) -> None:
    """Same columns, same order, no constraints (a seeded copy is a read model, not the canonical table)."""
    con.execute(f"CREATE TABLE {rel}__x AS SELECT * FROM {rel} LIMIT 0")
    con.execute(f"DROP TABLE {rel}")
    con.execute(f"ALTER TABLE {rel}__x RENAME TO {rel.split('.')[1]}")


def _copy(con, rel: str, src: str, where: str = "TRUE") -> None:
    have = {r[0] for r in con.execute(f"DESCRIBE {src}").fetchall()}
    cols = [r[0] for r in con.execute(f"DESCRIBE {rel}").fetchall() if r[0] in have]
    q = ", ".join(f'"{c}"' for c in cols)
    con.execute(f"INSERT INTO {rel} ({q}) SELECT {q} FROM {src} WHERE {where}")


def _seed(con, history_db: Path, origin_season: int) -> None:
    """Frozen-history inputs of the frozen identity rules: every canonical player, the CBBD id anchors, and the ORIGIN
    (2025-26) rosters the portal rule reads (flagship.rosters = core.rosters). Read-only attach; nothing written back."""
    for rel in ("core.players", "metadata.player_id_map", "core.rosters"):
        _relax(con, rel)
    con.execute(f"ATTACH '{history_db}' AS hist (READ_ONLY)")
    _copy(con, "core.players", "hist.core.players")
    if con.execute("""SELECT count(*) FROM duckdb_tables() WHERE database_name = 'hist' AND schema_name = 'metadata'
                      AND table_name = 'player_id_map'""").fetchone()[0]:
        _copy(con, "metadata.player_id_map", "hist.metadata.player_id_map", "provider = 'CBBD'")
    _copy(con, "core.rosters", "hist.flagship.rosters", f"season = {int(origin_season)}")
    con.execute("DETACH hist")


def preseason_identity(history_db: Path, snap_root: Path, work_db: Path, proc) -> tuple[dict, dict]:
    """(increment relations, report) for rosters / players / recruits / transfers of 2026-27."""
    con = _scratch(work_db, proc)
    _seed(con, history_db, PC.PRESEASON_CLASS_YEAR)
    hist_players = {r[0] for r in con.execute("SELECT canonical_player_id FROM core.players").fetchall()}
    E.build_rosters(con, snap_root)
    E._known_players(con)
    E.build_recruiting(con, snap_root)
    # P-2: preseason recruit identity (see module docstring). Only the frozen unique-name candidate is promoted.
    promoted = con.execute(f"""UPDATE core.recruits SET canonical_player_id = candidate_canonical_player_id,
                                   identity_method = '{P2}', identity_confidence = 0.99, needs_manual_review = FALSE
                               WHERE recruit_year = {PC.PRESEASON_CLASS_YEAR} AND canonical_player_id IS NULL
                                 AND identity_method = 'CANDIDATE_COMMITTED_ROSTER_NAME'
                                 AND candidate_canonical_player_id IS NOT NULL""").fetchone()[0]
    s = PC.PRODUCTION_SEASON
    rel = {
        "flagship.rosters": con.execute(f"SELECT * FROM core.rosters WHERE season = {s} ORDER BY ALL").df(),
        "core.recruits": con.execute(f"SELECT * FROM core.recruits WHERE recruit_year = {PC.PRESEASON_CLASS_YEAR} ORDER BY ALL").df(),
        "core.transfers": con.execute(f"SELECT * FROM core.transfers WHERE transfer_year = {PC.PRESEASON_CLASS_YEAR} ORDER BY ALL").df(),
    }
    newp = con.execute("SELECT * FROM core.players ORDER BY canonical_player_id").df()
    rel["core.players"] = newp[~newp["canonical_player_id"].isin(hist_players)].reset_index(drop=True)
    rep = {
        "roster_rows": int(len(rel["flagship.rosters"])),
        "roster_teams_nonempty": int(rel["flagship.rosters"]["canonical_team_id"].nunique()),
        "roster_players_known_before": int(rel["flagship.rosters"]["canonical_player_id"].isin(hist_players).sum()),
        "new_players": int(len(rel["core.players"])),
        "recruits_by_method": rel["core.recruits"]["identity_method"].value_counts().sort_index().to_dict(),
        "recruits_promoted_p2": int(promoted),
        "transfers_by_method": rel["core.transfers"]["identity_method"].value_counts().sort_index().to_dict(),
    }
    con.close()
    return rel, rep


# ============================================================================ P-3: re-issued CBBD athlete id
P3_SQL = """
WITH t AS (SELECT * FROM p3_t WHERE transfer_year = {cls} AND canonical_player_id IS NULL
                                AND candidate_canonical_player_id IS NOT NULL
                                AND origin_team_id IS NOT NULL AND destination_team_id IS NOT NULL),
hr AS (SELECT canonical_player_id, canonical_team_id, CAST(source_athlete_id AS VARCHAR) AS athlete_id, first_name,
              last_name, player_name, height_inches, hometown_city FROM h.flagship.rosters WHERE season = {cls}),
nr AS (SELECT canonical_player_id, canonical_team_id, CAST(source_athlete_id AS VARCHAR) AS athlete_id, first_name,
              last_name, player_name, height_inches, hometown_city FROM p3_r WHERE season = {season}),
c AS (SELECT n.canonical_player_id AS new_player_id, hr.canonical_player_id AS hist_player_id,
             CAST(t.source_transfer_id AS VARCHAR) AS source_transfer_id, n.athlete_id AS new_athlete_id,
             hr.athlete_id AS hist_athlete_id, n.player_name AS name, t.origin_team_id, t.destination_team_id,
             n.height_inches, n.hometown_city, t.first_name, t.last_name
      FROM t
      JOIN hr ON hr.canonical_player_id = t.candidate_canonical_player_id AND hr.canonical_team_id = t.origin_team_id
      JOIN nr n ON n.canonical_team_id = t.destination_team_id
      WHERE hr.first_name = t.first_name AND hr.last_name = t.last_name           -- exact name: portal = origin roster
        AND n.first_name = t.first_name AND n.last_name = t.last_name             --   = destination roster
        AND hr.player_name = n.player_name
        AND hr.height_inches IS NOT NULL AND hr.height_inches = n.height_inches
        AND hr.hometown_city IS NOT NULL AND hr.hometown_city = n.hometown_city
        AND n.canonical_player_id <> hr.canonical_player_id AND n.athlete_id <> hr.athlete_id)
SELECT * FROM c
WHERE (SELECT count(*) FROM nr x WHERE x.canonical_team_id = c.destination_team_id
          AND x.first_name = c.first_name AND x.last_name = c.last_name) = 1           -- unique on the destination roster
  AND (SELECT count(*) FROM hr x WHERE x.canonical_team_id = c.origin_team_id
          AND x.first_name = c.first_name AND x.last_name = c.last_name) = 1           -- unique on the origin roster
  AND (SELECT count(*) FROM p3_t x WHERE x.transfer_year = {cls}
          AND x.first_name = c.first_name AND x.last_name = c.last_name) = 1           -- one portal record of that name
  AND NOT EXISTS (SELECT 1 FROM nr x WHERE x.canonical_player_id = c.hist_player_id)   -- history id on no current roster
  AND NOT EXISTS (SELECT 1 FROM h.core.players x WHERE x.canonical_player_id = c.new_player_id)   -- new id truly new
  AND NOT EXISTS (SELECT 1 FROM h.metadata.player_id_map x WHERE x.provider = 'CBBD'
                     AND x.provider_player_key = c.new_athlete_id)                       -- athlete id unknown to history
"""


def reissued_athlete_links(history_db: Path, rosters: pd.DataFrame, transfers: pd.DataFrame) -> pd.DataFrame:
    """Production identity adaptation P-3. CBBD occasionally issues a transferring player a NEW athleteId (2026 portal:
    4 of 1,585 records), so the frozen roster rule (cid of the athlete id) creates a second canonical player and the
    frozen portal rule leaves the link as CANDIDATE_ORIGIN_ROSTER_NAME (the destination roster cannot corroborate a
    different athlete id). The new id is linked back to the history-bearing canonical player only when EVERY
    deterministic check holds: the CBBD portal record names the player at the origin team (the history player's 2025-26
    roster) and the destination team; the exact first / last / full name agrees on the portal record and both rosters;
    height and hometown agree (non-null); the name is unique on the origin roster, the destination roster and the
    portal; the history id is on no 2026-27 roster; the new id and athlete id are unknown to the frozen history; and the
    pair is one-to-one. No fuzzy matching; anything else stays unlinked. Returns P3_COLS rows (new -> history)."""
    if rosters is None or transfers is None or not len(rosters) or not len(transfers):
        return pd.DataFrame(columns=P3_COLS)
    need_r = {"canonical_player_id", "canonical_team_id", "season", "source_athlete_id", "first_name", "last_name",
              "player_name", "height_inches", "hometown_city"}
    need_t = {"source_transfer_id", "transfer_year", "first_name", "last_name", "canonical_player_id",
              "candidate_canonical_player_id", "origin_team_id", "destination_team_id"}
    if not need_r <= set(rosters.columns) or not need_t <= set(transfers.columns):
        return pd.DataFrame(columns=P3_COLS)
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{history_db}' AS h (READ_ONLY)")
        have = {(s, t, c) for s, t, c in con.execute("""SELECT schema_name, table_name, column_name FROM duckdb_columns()
                                                         WHERE database_name = 'h'""").fetchall()}
        need_h = {("flagship", "rosters", c) for c in need_r} | {("core", "players", "canonical_player_id")} | \
                 {("metadata", "player_id_map", c) for c in ("provider", "provider_player_key")}
        if not need_h <= have:                                  # evidence the rule needs is absent: no link (fail closed)
            return pd.DataFrame(columns=P3_COLS)
        con.register("p3_r", rosters[sorted(need_r)])
        con.register("p3_t", transfers[sorted(need_t)])
        df = con.execute(P3_SQL.format(cls=PC.PRESEASON_CLASS_YEAR, season=PC.PRODUCTION_SEASON)).df()
    finally:
        con.close()
    one = ~df["new_player_id"].duplicated(keep=False) & ~df["hist_player_id"].duplicated(keep=False)   # one-to-one
    return df.loc[one, P3_COLS].sort_values("new_player_id").reset_index(drop=True)


# ============================================================================ completed games (frozen canonical build)
STAGING_COLUMNS = {
    "games": ["id", "season", "season_label", "season_type", "game_type", "tournament", "notes", "start_date",
              "neutral_site", "conference_game", "home_team_id", "away_team_id", "home_conference_id",
              "away_conference_id", "home_seed", "away_seed", "venue_id", "venue", "venue_city", "venue_state",
              "venue_country", "home_points", "away_points", "home_period_points", "away_period_points", "filename"],
    "team_game_stats": ["gameId", "teamId", "opponentId", "season", "isHome", "neutralSite", "gameMinutes",
                        "startTimeTbd", "conference", "opponentConference", "pace", "filename"]
                       + [f"teamStats_{c}" for c in (
                           "points_total", "fieldGoals_made", "fieldGoals_attempted", "twoPointFieldGoals_made",
                           "twoPointFieldGoals_attempted", "threePointFieldGoals_made", "threePointFieldGoals_attempted",
                           "freeThrows_made", "freeThrows_attempted", "rebounds_offensive", "rebounds_defensive",
                           "rebounds_total", "assists", "steals", "blocks", "turnovers_total", "turnovers_teamTotal",
                           "fouls_total", "fouls_technical", "fouls_flagrant", "possessions", "points_inPaint",
                           "points_fastBreak", "points_offTurnovers", "points_largestLead")]
                       + [f"teamStats_points_byPeriod_{i}" for i in range(8)],
    "player_game_stats": ["game_id", "athleteId", "team_id", "opponent_id", "season", "is_home", "name", "position",
                          "starter", "minutes", "points", "fieldGoals_made", "fieldGoals_attempted",
                          "twoPointFieldGoals_made", "twoPointFieldGoals_attempted", "threePointFieldGoals_made",
                          "threePointFieldGoals_attempted", "freeThrows_made", "freeThrows_attempted",
                          "rebounds_offensive", "rebounds_defensive", "rebounds_total", "assists", "steals", "blocks",
                          "turnovers", "fouls", "ejected", "usage", "conference", "opponent_conference", "filename"],
    "player_season_stats": ["season", "teamId", "athleteId", "name", "conference", "filename"],
    "player_season_shooting_stats": ["season", "teamId", "athleteId", "athleteName", "conference", "filename"],
    "team_season_stats": ["season", "teamId", "conference", "filename"],
}


def _stage(con, family: str, rows: list[dict]) -> None:
    cols = list(dict.fromkeys(STAGING_COLUMNS[family] + sorted({c for r in rows for c in r})))
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    con.execute(f"CREATE OR REPLACE TABLE staging.sp_{family} ({', '.join(f'\"{c}\" VARCHAR' for c in cols)})")
    if rows:
        con.executemany(f"INSERT INTO staging.sp_{family} VALUES ({', '.join('?' * len(cols))})",
                        [[r.get(c) for c in cols] for r in rows])


def completed_games(history_db: Path, box: dict, snap_root: Path | None, work_db: Path, proc,
                    extra_anchor_ids=()) -> tuple[dict, pd.DataFrame, dict]:
    """box: {"games": [...], "games/teams": [...], "games/players": [...], "stats/player/season": [...]} CBBD records of
    2026-27 (any status; only final games are kept). snap_root holds substitutions/game payloads (raw_store layout).
    Returns (increment relations, game result overrides, report)."""
    lin = "cbbd_production"
    out = {"games": R.reshape("games", box.get("games", []), lin),
           "team_game_stats": R.reshape("team_game_stats", box.get("games/teams", []), lin),
           "player_game_stats": R.reshape("player_game_stats", box.get("games/players", []), lin),
           "player_season_stats": [r for r in R.reshape("player_season_stats", box.get("stats/player/season", []), lin)
                                   if R.is_di_season_row(r)]}
    rep = {}
    for fam in list(out):
        out[fam], rep[f"{fam}_duplicates_collapsed"] = R.dedupe(fam, out[fam])
    rep["final_only"] = {k: v for k, v in R.final_games_only(out).items() if k != "non_final_games"}
    rep["team_assignment"] = {k: v for k, v in R.validate_team_assignment(out).items()
                              if k in ("repaired", "quarantined", "games_repaired", "games_quarantined")}
    stray = sorted({r.get("season") for f in out.values() for r in f} - {str(PC.PRODUCTION_SEASON)})
    if stray:
        raise FeedUnavailable("POST_CUTOFF_DATA", f"completed-game payloads carry seasons {stray}")
    con = _scratch(work_db, proc)
    for fam in ("games", "team_game_stats", "player_game_stats", "player_season_stats"):
        _stage(con, fam, out[fam])
    _stage(con, "player_season_shooting_stats", [])
    _stage(con, "team_season_stats", [])
    B.build_games(con)
    B.build_team_games(con)
    B.build_players_and_player_games(con)
    B.flag_accounting(con)
    # substitutions: the frozen event_build reader; CBBD athlete anchors = frozen history + 2026-27 rosters/season files
    con.execute(f"ATTACH '{history_db}' AS hist (READ_ONLY)")
    if con.execute("""SELECT count(*) FROM duckdb_tables() WHERE database_name = 'hist' AND schema_name = 'metadata'
                      AND table_name = 'player_id_map'""").fetchone()[0]:
        _copy(con, "metadata.player_id_map", "hist.metadata.player_id_map", """provider = 'CBBD' AND provider_player_key NOT IN
              (SELECT provider_player_key FROM metadata.player_id_map WHERE provider = 'CBBD')""")
    con.execute("DETACH hist")
    if extra_anchor_ids is not None and len(extra_anchor_ids):
        con.register("xa", pd.DataFrame({"k": sorted({str(x) for x in extra_anchor_ids})}))
        con.execute("""INSERT INTO metadata.player_id_map (provider, provider_player_key, canonical_player_id, match_method,
                           match_confidence, needs_manual_review, source_context, created_by_version)
                       SELECT 'CBBD', k, cid('P','athlete', k), 'ANCHOR', 1.0, FALSE, 'production 2026-27 /teams/roster',
                              getvariable('ver') FROM xa
                       WHERE k NOT IN (SELECT provider_player_key FROM metadata.player_id_map WHERE provider = 'CBBD')""")
    E._lineage_vars(con)
    E._game_pit(con)
    E._known_players(con)
    if snap_root is not None:
        E.build_substitutions(con, snap_root)
    s = PC.PRODUCTION_SEASON
    final = con.execute(f"SELECT * FROM core.games WHERE season = {s} AND home_points IS NOT NULL").df()
    keep = "canonical_game_id IN (SELECT canonical_game_id FROM core.games WHERE home_points IS NOT NULL)"
    rel = {"flagship.team_game_outcomes": con.execute(f"SELECT * FROM fact.team_games WHERE season = {s} AND {keep} ORDER BY ALL").df(),
           "flagship.player_game_outcomes": con.execute(f"SELECT * FROM fact.player_games WHERE season = {s} AND {keep} ORDER BY player_game_key").df(),
           "flagship.substitutions": con.execute(f"SELECT * FROM fact.substitutions WHERE season = {s} AND {keep} ORDER BY ALL").df()}
    newp = con.execute("SELECT canonical_player_id, display_name FROM core.players").df()
    con.close()
    tb = rel["flagship.team_game_outcomes"].groupby("canonical_game_id").size()
    pb = rel["flagship.player_game_outcomes"].groupby("canonical_game_id").size()
    ov = final[["canonical_game_id", "scheduled_tip_utc", "tip_time_quality", "game_date_et", "home_points", "away_points",
                "home_period_points", "away_period_points", "overtime_count"]].copy()
    ov["has_team_box_score"] = ov["canonical_game_id"].map(tb).fillna(0).astype(int) == 2
    ov["has_player_box_score"] = ov["canonical_game_id"].map(pb).fillna(0).astype(int) > 0
    rep.update({"final_games": int(len(ov)), "team_box_rows": int(len(rel["flagship.team_game_outcomes"])),
                "player_box_rows": int(len(rel["flagship.player_game_outcomes"])),
                "player_box_identity": rel["flagship.player_game_outcomes"]["identity_status"].value_counts().to_dict()
                if len(rel["flagship.player_game_outcomes"]) else {},
                "substitution_rows": int(len(rel["flagship.substitutions"]))})
    return rel, ov, {**rep, "players_seen": newp}
