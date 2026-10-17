"""Pre-Model Integrity Closure v1 -- cluster-level player identity audit, 2013-14 .. 2024-25 (offline; reads a built DB).

    .venv/bin/python -m cbb_platform.identity_cluster_audit            # current DB -> data/qa + docs
    .venv/bin/python -m cbb_platform.identity_cluster_audit --before   # diagnostics only -> data/qa/pre_model_closure/before_v2.3

Supersedes the Stage 3.1 rule "non-overlapping multi-team appearances => CONFIRMED_SINGLE_PLAYER" (REVOKED: chronology is
never positive identity evidence). Every multi-team identity candidate gets an evidence record, a classification and a
confidence from the pure function `classify` (tested adversarially in tests/test_pre_model_closure.py):

    CONFIRMED_SINGLE_PLAYER  one person; the apparent second team is not a team change (positive evidence required)
    CONFIRMED_TRANSFER       one person who really changed teams (independent portal record, or provider roster continuity
                             on both team-seasons outside any whole-team-sized cluster)
    TEAM_ATTRIBUTION_DEFECT  the multi-team appearance is a source team-label defect (mirrored / misfiled box lines, stale
                             roster row, event feed naming a team that did not play)
    SOURCE_ID_COLLISION      one provider id carried by two people (overlap, DOB-age or eligibility impossibility)
    CANONICAL_MAPPING_ERROR  the team identity itself is split (two canonical team ids for one school)
    UNRESOLVED               no positive evidence either way -> quarantined from player-level modeling

Candidate kinds:
    IN_SEASON_MULTI_TEAM   a Starter Pack season-file athleteId on >= 2 teams in one season (source level, stable across
                           remediation) or a canonical player with box rows for >= 2 teams in one season
    CROSS_SEASON_LINK      consecutive box seasons of one canonical player on different teams, or the same team after a
                           >= 3-season gap (team_attribution.LINKS_SQL)
    EVENT_FOREIGN_ACTOR    a PBP actor in a game neither of whose teams he belongs to (box or roster) that season
    ROSTER_MULTI_TEAM      one provider athlete on two team rosters in one season
    PLAYER_IMPOSSIBILITY   eligibility (>= 8 box seasons or a >= 9-season span), DOB-age or overlap impossibility
Clusters: in-season candidates are grouped by (season, team pair, boundary game); cross-season links by (from team-season,
to team-season). A cluster of >= 5 identities (team_attribution.WHOLE_TEAM_CLUSTER_MIN) is whole-team-sized.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict

import duckdb

from cbb_platform import config as C, team_attribution as TA

W = "BETWEEN 2014 AND 2025"
OVERLAP_MIN = 240
OUT_JSON = C.QA / "player_identity_cluster_audit.json"
OUT_CSV = C.QA / "player_identity_cluster_audit.csv"
OUT_CLUSTERS = C.QA / "player_identity_clusters.csv"
OUT_MD = C.PROJECT_ROOT / "docs" / "PLAYER_IDENTITY_CLUSTER_AUDIT.md"
BEFORE_DIR = C.QA / "pre_model_closure" / "before_v2.3"
CLASSES = ("CONFIRMED_SINGLE_PLAYER", "CONFIRMED_TRANSFER", "TEAM_ATTRIBUTION_DEFECT", "SOURCE_ID_COLLISION",
           "CANONICAL_MAPPING_ERROR", "UNRESOLVED")
CONFIDENCE = ("HIGH", "MEDIUM", "LOW")


# ============================================================================ the classifier (pure; evidence -> class)
def classify(ev: dict) -> tuple[str, str, str]:
    """Return (classification, confidence, rationale). Chronology (non-overlap) is NEVER positive evidence: it only appears
    as the absence of a contradiction. Missing keys mean 'signal not available'."""
    kind = ev["kind"]
    if ev.get("overlap_conflict"):
        return "SOURCE_ID_COLLISION", "HIGH", "same identity in two games tipping < 4 h apart (physically impossible)"
    if ev.get("eligibility_conflict"):
        return "SOURCE_ID_COLLISION", "MEDIUM", "career length exceeds any eligibility path (>= 8 box seasons or >= 9-season span)"
    if kind == "PLAYER_IMPOSSIBILITY":
        return "UNRESOLVED", "LOW", "impossibility check fired without a decisive signal"
    if kind == "IN_SEASON_MULTI_TEAM":
        if ev.get("mirror_in_h2h") and ev.get("minority_games_all_h2h"):
            if ev.get("h2h_reconciled"):
                return ("TEAM_ATTRIBUTION_DEFECT", "HIGH", "minority-team appearances exist only in a head-to-head game whose box "
                        "lines are mirrored onto both teams; true-team assignment reconciles both team box lines exactly")
            return ("TEAM_ATTRIBUTION_DEFECT", "MEDIUM", "minority-team appearances exist only in a mirrored head-to-head game "
                    "(reconciliation not established: rows stay unresolved)")
        if ev.get("misfiled_h2h") and ev.get("minority_games_all_h2h"):
            return ("TEAM_ATTRIBUTION_DEFECT", "HIGH" if ev.get("h2h_reconciled") else "MEDIUM",
                    "minority-team appearance is a head-to-head game whose box lines were all filed under one team")
        if ev.get("whole_team_cluster"):
            return ("UNRESOLVED", "LOW", "whole-team-sized in-season cluster without mirror evidence: presumed team-attribution "
                    "defect, quarantined")
        if ev.get("portal_record"):
            return "CONFIRMED_TRANSFER", "HIGH", "independent portal record origin -> destination; no contradiction"
        if ev.get("provider_roster_both") and ev.get("box_teams_after", 0) >= 2:
            return ("CONFIRMED_TRANSFER", "MEDIUM", "provider roster lists the athlete on both teams and box rows exist for both "
                    "(mid-season move); no overlap")
        return "UNRESOLVED", "LOW", "no positive evidence of a real team change"
    if kind == "CROSS_SEASON_LINK":
        if ev.get("profile_conflict"):
            return "SOURCE_ID_COLLISION", "MEDIUM", "provider profile (DOB / height / hometown) conflicts across the two team-seasons"
        if ev.get("portal_record"):
            return ("CONFIRMED_TRANSFER", "HIGH", "independent portal record (normalized name, origin and destination team, "
                    "transfer year inside the gap) plus provider athlete id continuity")
        if ev.get("gap", 1) >= TA.LONG_GAP_SEASONS:
            return ("UNRESOLVED", "LOW", f"{ev.get('gap')}-season gap with no independent record: link quarantined "
                    "(history before the gap is never joined)")
        if ev.get("whole_team_cluster"):
            return ("UNRESOLVED", "LOW", "member of a whole-team-sized cross-season cluster without an independent portal "
                    "record: link quarantined")
        if not ev.get("cross_team"):
            return ("CONFIRMED_SINGLE_PLAYER", "MEDIUM", "same team, provider athlete id and roster continuity")
        if ev.get("roster_from") and ev.get("roster_to"):
            return ("CONFIRMED_TRANSFER", "MEDIUM", "provider athlete id on BOTH team-season rosters with a consistent provider "
                    "profile (provider-asserted, not independent); not part of a whole-team cluster; no contradiction")
        return ("UNRESOLVED", "LOW", "no provider roster continuity on one side and no portal record: link quarantined")
    if kind == "EVENT_FOREIGN_ACTOR":
        if ev.get("event_team_not_in_game"):
            return ("TEAM_ATTRIBUTION_DEFECT", "HIGH", "the game's event feed names a team that did not play it; events "
                    "quarantined from trusted relations")
        if ev.get("team_alias"):
            return ("CANONICAL_MAPPING_ERROR", "HIGH", "actors belong to a team whose games are filed under a second canonical "
                    "team id (team alias); player identity correct, team merge deferred")
        if ev.get("whole_team_cluster"):
            return ("TEAM_ATTRIBUTION_DEFECT", "MEDIUM", "a whole roster of another team appears in this game's PBP")
        return ("UNRESOLVED", "MEDIUM", "isolated foreign PBP actor; player-level attribution blocked by the Stage 3 membership guard")
    if kind == "ROSTER_MULTI_TEAM":
        if ev.get("team_alias") or ev.get("same_school_name"):
            return "CANONICAL_MAPPING_ERROR", "HIGH", "two canonical team ids for one school"
        if ev.get("box_teams_after", 0) == 1 and ev.get("box_on_other_roster_team"):
            return ("TEAM_ATTRIBUTION_DEFECT", "HIGH", "stale roster row: every box row this season is for the other roster "
                    "team; the stale row is quarantined")
        if ev.get("box_teams_after", 0) >= 2:
            return "CONFIRMED_TRANSFER", "MEDIUM", "box rows for both roster teams; provider roster on both"
        return "UNRESOLVED", "LOW", "no box evidence for either roster team"
    raise ValueError(f"unknown candidate kind {kind}")


# ============================================================================ evidence extraction
def _has_table(con, name: str) -> bool:
    s, t = name.split(".")
    return bool(con.execute("SELECT count(*) FROM duckdb_tables() WHERE schema_name = ? AND table_name = ?", [s, t]).fetchone()[0])


def _in_season(con, q) -> list[dict]:
    """Source-level season-file multi-team athleteIds (stable across remediation) + canonical box multi-team."""
    has_ta = _has_table(con, "metadata.team_attribution_resolution")
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE sf AS
    SELECT athleteId AS aid, CAST(CAST(season AS DOUBLE) AS INT) AS season, cid('T','team', teamId) AS team_id, any_value(name) AS name,
           max(TRY_CAST(TRY_CAST(games AS DOUBLE) AS INT)) AS games
    FROM read_csv('{C.STARTER_PACK_DATA}/player_season_stats/*.csv', all_varchar=true, union_by_name=true)
    WHERE TRY_CAST(CAST(season AS DOUBLE) AS INT) {W} GROUP BY 1, 2, 3;
    CREATE OR REPLACE TEMP TABLE sfm AS
    SELECT aid, season, list(team_id ORDER BY games DESC, team_id) AS teams, list(games ORDER BY games DESC, team_id) AS games,
           any_value(name) AS name
    FROM sf WHERE (aid, season) IN (SELECT aid, season FROM sf GROUP BY 1, 2 HAVING count(DISTINCT team_id) > 1) GROUP BY 1, 2;
    """)
    # source box lines (Starter Pack) of head-to-head games: who was filed under which team
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE h2h AS
    SELECT s.aid, s.season, g.canonical_game_id, g.home_team_id, g.away_team_id
    FROM sfm s JOIN core.games g ON g.season = s.season
     AND ((g.home_team_id = s.teams[1] AND g.away_team_id = s.teams[2]) OR (g.home_team_id = s.teams[2] AND g.away_team_id = s.teams[1]));
    CREATE OR REPLACE TEMP TABLE h2h_src AS
    SELECT cid('G','game', game_id) AS canonical_game_id, cid('T','team', team_id) AS team_id, name
    FROM read_csv('{C.STARTER_PACK_DATA}/player_game_stats/*.csv', all_varchar=true, union_by_name=true)
    WHERE cid('G','game', game_id) IN (SELECT canonical_game_id FROM h2h) GROUP BY ALL;
    """)
    out = []
    for aid, season, teams, games, name in q("SELECT aid, season, teams, games, name FROM sfm ORDER BY season, aid"):
        pid = q("SELECT cid('P','athlete', ?)", [aid])[0][0]
        major, minor = teams[0], teams[1]
        hg = [r[0] for r in q("SELECT canonical_game_id FROM h2h WHERE aid = ? AND season = ?", [aid, season])]
        sides = {r[0]: r[1] for r in q("""SELECT canonical_game_id, list(DISTINCT team_id) FROM h2h_src
                                          WHERE canonical_game_id IN (SELECT unnest(?::VARCHAR[])) AND name = ? GROUP BY 1""", [hg, name])}
        mirror = any(len(v) == 2 for v in sides.values())
        misfiled = any(v == [minor] for v in sides.values()) and not mirror
        box_after = q("""SELECT canonical_team_id, count(*) FROM fact.player_games WHERE season = ? AND canonical_player_id = ?
                         GROUP BY 1""", [season, pid])
        rec = None
        if has_ta and hg:
            rec = q(f"""SELECT bool_and(reconciles) FROM metadata.team_attribution_resolution
                        WHERE canonical_game_id IN (SELECT unnest(?::VARCHAR[])) AND defect_class = 'MIRRORED_BOX_LINES'""", [hg])[0][0]
        if hg and rec is None:      # 209587-type misfiled game: reconciled by the Stage 3.1 rule when both team sums match
            rec = q("""SELECT count(*) = 2 FROM fact.team_games t JOIN (SELECT canonical_game_id, canonical_team_id, sum(points) pts
                        FROM fact.player_games GROUP BY 1, 2) p USING (canonical_game_id, canonical_team_id)
                        WHERE t.canonical_game_id IN (SELECT unnest(?::VARCHAR[])) AND t.points = p.pts""", [hg])[0][0]
        onfloor = q("""SELECT list(DISTINCT canonical_team_id) FROM fact.play_on_floor WHERE source_athlete_id = ?
                       AND canonical_game_id IN (SELECT unnest(?::VARCHAR[])) AND canonical_team_id IS NOT NULL""", [aid, hg])[0][0] if hg else None
        portal = q(f"""SELECT count(*) > 0 FROM core.transfers x WHERE x.transfer_year BETWEEN ? - 1 AND ?
                       AND {TA.norm("x.first_name || ' ' || x.last_name")} = {TA.norm('?')}
                       AND (x.origin_team_id, x.destination_team_id) IN ((?, ?), (?, ?))""", [season, season, name, major, minor, minor, major])[0][0]
        ros = {r[0] for r in q("SELECT DISTINCT canonical_team_id FROM core.rosters WHERE source_athlete_id = ? AND season = ?", [aid, season])}
        adjudicated = q("""SELECT count(*) > 0 FROM metadata.player_identity_resolution WHERE provider_athlete_id = ?
                           AND classification = 'SOURCE_ID_COLLISION' AND rule_type = 'SPLIT'""", [aid])[0][0]
        out.append({"kind": "IN_SEASON_MULTI_TEAM", "entity": f"athleteId {aid}", "canonical_player_id": pid, "name": name,
                    "season": season, "teams": [major, minor], "season_file_games": games, "h2h_games": hg,
                    "boundary_game": hg[0] if len(hg) == 1 else (",".join(hg) or None),
                    "mirror_in_h2h": mirror, "misfiled_h2h": misfiled,
                    "minority_games_all_h2h": bool(hg) and games[1] <= len(hg),
                    "h2h_reconciled": bool(rec), "onfloor_team_in_h2h": onfloor,
                    "box_teams_after": len(box_after), "box_rows_after": {t: n for t, n in box_after},
                    "portal_record": bool(portal), "provider_roster_both": {major, minor} <= ros,
                    "overlap_conflict": bool(adjudicated)})
    # canonical box multi-team in one season (after repair this must be empty)
    for pid, season, teams in q(f"""SELECT canonical_player_id, season, list(DISTINCT canonical_team_id) FROM fact.player_games
                                   WHERE canonical_player_id IS NOT NULL AND season {W} GROUP BY 1, 2 HAVING count(DISTINCT canonical_team_id) > 1"""):
        if any(c["canonical_player_id"] == pid and c["season"] == season for c in out):
            continue
        out.append({"kind": "IN_SEASON_MULTI_TEAM", "entity": pid, "canonical_player_id": pid, "season": season, "teams": teams,
                    "source": "CANONICAL_BOX", "box_teams_after": len(teams), "h2h_games": []})
    return out


def _player_flags(con, q) -> dict:
    """Per canonical player: overlap / eligibility / DOB-age contradictions (window players)."""
    flags = defaultdict(dict)
    for pid, n in q(f"""WITH p AS (SELECT p.canonical_player_id, p.canonical_game_id, g.scheduled_tip_utc t FROM fact.player_games p
                       JOIN core.games g USING (canonical_game_id) WHERE p.canonical_player_id IS NOT NULL AND g.season {W})
                       SELECT a.canonical_player_id, count(*) FROM p a JOIN p b USING (canonical_player_id)
                       WHERE a.canonical_game_id < b.canonical_game_id AND abs(date_diff('minute', a.t, b.t)) < {OVERLAP_MIN} GROUP BY 1"""):
        flags[pid]["overlap_conflict"] = n
    for pid, n, a, b in q("""WITH s AS (SELECT canonical_player_id, min(season) a, max(season) b, count(DISTINCT season) n FROM fact.player_games
                             WHERE canonical_player_id IS NOT NULL GROUP BY 1) SELECT canonical_player_id, n, a, b FROM s
                             WHERE b >= 2014 AND (n >= 8 OR b - a >= 8)"""):
        flags[pid]["eligibility_conflict"] = f"{n} box seasons {a}-{b}"
    return flags


def _dob_reliability(q) -> dict:
    """Provider DOB is NOT admitted as decisive identity evidence: measure how often it is impossible for the season."""
    rows = q("""SELECT season, count(*) FROM core.rosters WHERE TRY_CAST(date_of_birth_raw AS DATE) IS NOT NULL
                AND date_diff('year', TRY_CAST(date_of_birth_raw AS DATE), make_date(season - 1, 11, 1)) < 16 GROUP BY 1 ORDER BY 1""")
    top = q("""SELECT date_of_birth_raw, count(*) FROM core.rosters WHERE date_of_birth_raw IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 5""")
    return {"roster_rows_with_dob": q("SELECT count(*) FROM core.rosters WHERE date_of_birth_raw IS NOT NULL")[0][0],
            "roster_rows_age_under_16_at_season_start": sum(n for _, n in rows),
            "by_season": {s: n for s, n in rows}, "most_frequent_dob_values": top}


def _cross_season(con, q, flags) -> list[dict]:
    rows = q(f"""SELECT canonical_player_id, from_season, to_season, from_team_id, to_team_id, name, gap, cross_team, portal_record,
                        roster_from, roster_to, cluster_size, {TA.link_reason_sql()} AS reason
                 FROM ({TA.LINKS_SQL}) WHERE to_season {W} AND (cross_team OR gap >= {TA.LONG_GAP_SEASONS}) ORDER BY ALL""")
    prof = {(p, t, s): (d, h, ht) for p, t, s, d, h, ht in q("""SELECT canonical_player_id, canonical_team_id, season, any_value(date_of_birth_raw),
                                                                   any_value(height_inches), any_value(lower(hometown_city)) FROM core.rosters GROUP BY 1, 2, 3""")}
    out = []
    for pid, fs, ts, ft, tt, name, gap, cross, portal, rf, rt, size, reason in rows:
        a, b = prof.get((pid, ft, fs)), prof.get((pid, tt, ts))
        conflict = bool(a and b and ((a[0] and b[0] and a[0] != b[0]) or (a[1] and b[1] and abs(a[1] - b[1]) > 3)
                                     or (a[2] and b[2] and a[2] != b[2])))
        out.append({"kind": "CROSS_SEASON_LINK", "entity": pid, "canonical_player_id": pid, "name": name, "season": ts,
                    "from_season": fs, "to_season": ts, "teams": [ft, tt], "gap": gap, "cross_team": cross,
                    "portal_record": portal, "roster_from": rf, "roster_to": rt, "cluster_size": size,
                    "whole_team_cluster": bool(cross and size >= TA.WHOLE_TEAM_CLUSTER_MIN), "profile_conflict": conflict,
                    "link_quarantine_reason": reason, **{k: v for k, v in flags.get(pid, {}).items() if k != "overlap_conflict"}})
    return out


def _event_foreign(con, q) -> list[dict]:
    con.execute(f"""
    CREATE OR REPLACE TEMP TABLE mem AS
    SELECT DISTINCT canonical_player_id, season, canonical_team_id FROM fact.player_games WHERE canonical_player_id IS NOT NULL
    UNION SELECT DISTINCT canonical_player_id, season, canonical_team_id FROM core.rosters;
    CREATE OR REPLACE TEMP TABLE fa AS
    WITH a AS (SELECT DISTINCT canonical_game_id, canonical_player_id FROM fact.play_participants WHERE canonical_player_id IS NOT NULL)
    SELECT g.season, a.canonical_game_id, a.canonical_player_id, g.home_team_id, g.away_team_id,
           (SELECT list(DISTINCT m.canonical_team_id) FROM mem m WHERE m.canonical_player_id = a.canonical_player_id AND m.season = g.season) AS member_teams
    FROM a JOIN core.games g USING (canonical_game_id)
    WHERE g.season {W}
      AND NOT EXISTS (SELECT 1 FROM mem m WHERE m.canonical_player_id = a.canonical_player_id AND m.season = g.season
                      AND m.canonical_team_id IN (g.home_team_id, g.away_team_id))
      AND EXISTS (SELECT 1 FROM mem m WHERE m.canonical_player_id = a.canonical_player_id AND m.season = g.season);
    """)
    per_game = {g: n for g, n in q("SELECT canonical_game_id, count(*) FROM fa GROUP BY 1")}
    # team alias: actors who are members of team T appear in >= 3 games under a game team Y that has NO player box rows in
    # those games (T's games filed under a second canonical team id)
    alias = {}
    for y, t, n in q("""WITH x AS (SELECT f.canonical_game_id, unnest(f.member_teams) AS t, y.y
                                   FROM fa f, LATERAL (SELECT unnest([f.home_team_id, f.away_team_id]) AS y) y
                                   WHERE NOT EXISTS (SELECT 1 FROM fact.player_games pg WHERE pg.canonical_game_id = f.canonical_game_id
                                                     AND pg.canonical_team_id = y.y))
                        SELECT y, t, count(DISTINCT canonical_game_id) FROM x GROUP BY 1, 2 HAVING count(DISTINCT canonical_game_id) >= 3"""):
        alias[y] = t
    names = dict(q("SELECT canonical_team_id, school FROM core.teams"))
    ev_bad = {r[0] for r in q("""SELECT DISTINCT canonical_game_id FROM fact.substitutions s JOIN core.games g USING (canonical_game_id)
                                 WHERE s.canonical_team_id NOT IN (g.home_team_id, g.away_team_id)""")}
    out = []
    for season, g, pid, h, a, mt in q("SELECT season, canonical_game_id, canonical_player_id, home_team_id, away_team_id, member_teams FROM fa ORDER BY ALL"):
        is_alias = (any(alias.get(x) in (mt or []) for x in (h, a))
                    or any(names.get(t) in (names.get(h), names.get(a)) for t in (mt or [])))    # same school, two team ids
        out.append({"kind": "EVENT_FOREIGN_ACTOR", "entity": pid, "canonical_player_id": pid, "season": season, "teams": mt,
                    "boundary_game": g, "game_teams": [names.get(h), names.get(a)], "member_teams": [names.get(t) for t in (mt or [])],
                    "team_alias": is_alias, "foreign_actors_in_game": per_game[g],
                    "whole_team_cluster": per_game[g] >= TA.WHOLE_TEAM_CLUSTER_MIN, "event_team_not_in_game": g in ev_bad})
    # 209587-type: whole event feed under a team that did not play (actors may be members of a game team)
    for g, season in q("""SELECT DISTINCT s.canonical_game_id, g.season FROM fact.shots s JOIN core.games g USING (canonical_game_id)
                          WHERE s.canonical_team_id NOT IN (g.home_team_id, g.away_team_id) AND g.season BETWEEN 2014 AND 2025"""):
        out.append({"kind": "EVENT_FOREIGN_ACTOR", "entity": f"game {g}", "canonical_player_id": None, "season": season,
                    "boundary_game": g, "event_team_not_in_game": True, "teams": []})
    return out, {names.get(y): names.get(t) for y, t in alias.items()}


def _roster_multi(con, q) -> list[dict]:
    out = []
    names = dict(q("SELECT canonical_team_id, school FROM core.teams"))
    src = "qa.identity_remediation_quarantine" if _has_table(con, "qa.identity_remediation_quarantine") else None
    rows = q(f"""WITH r AS (SELECT canonical_player_id, canonical_team_id, season, source_athlete_id FROM core.rosters WHERE season {W}
                 {"UNION SELECT canonical_player_id, canonical_team_id, season, NULL FROM " + src + " WHERE table_name = 'core.rosters' AND rule_id = 'STALE_ROSTER_MULTI_TEAM'" if src else ""})
                 SELECT canonical_player_id, season, list(DISTINCT canonical_team_id ORDER BY canonical_team_id) FROM r GROUP BY 1, 2
                 HAVING count(DISTINCT canonical_team_id) > 1""")
    for pid, season, teams in rows:
        box = {t for (t,) in q("SELECT DISTINCT canonical_team_id FROM fact.player_games WHERE canonical_player_id = ? AND season = ?", [pid, season])}
        out.append({"kind": "ROSTER_MULTI_TEAM", "entity": pid, "canonical_player_id": pid, "season": season, "teams": teams,
                    "box_teams_after": len(box), "box_on_other_roster_team": len(box & set(teams)) == 1,
                    "same_school_name": len({names.get(t) for t in teams}) == 1})
    return out


def run(db_path=None, before: bool = False) -> dict:
    con = duckdb.connect(str(db_path or C.DUCKDB_PATH), read_only=True, config={"threads": C.DUCKDB_THREADS})
    con.execute("SET TimeZone = 'UTC'")
    q = lambda sql, p=None: con.execute(sql, p or []).fetchall()
    scanned = q(f"SELECT count(DISTINCT canonical_player_id) FROM fact.player_games WHERE canonical_player_id IS NOT NULL AND season {W}")[0][0]
    flags = _player_flags(con, q)
    dob = _dob_reliability(q)
    cands = _in_season(con, q)
    cands += _cross_season(con, q, flags)
    ev, alias_pairs = _event_foreign(con, q)
    cands += ev
    cands += _roster_multi(con, q)
    for pid, f in flags.items():
        cands.append({"kind": "PLAYER_IMPOSSIBILITY", "entity": pid, "canonical_player_id": pid, "season": None, **f})
    # also report adjudicated collisions whose impossible era is now quarantined (they no longer trip the checks)
    if _has_table(con, "metadata.player_identity_resolution"):
        for rule, aid, pid, ev_txt in q("""SELECT DISTINCT rule_id, provider_athlete_id, from_canonical_player_id, evidence
                                           FROM metadata.player_identity_resolution WHERE classification = 'SOURCE_ID_COLLISION'"""):
            if not any(c["kind"] == "PLAYER_IMPOSSIBILITY" and c["canonical_player_id"] == pid for c in cands):
                cands.append({"kind": "PLAYER_IMPOSSIBILITY", "entity": f"athleteId {aid}", "canonical_player_id": pid, "season": None,
                              "adjudicated_rule": rule, "overlap_conflict": aid in ("31449", "19801"),
                              "eligibility_conflict": aid not in ("31449", "19801"),
                              "evidence_text": ev_txt})
    # in-season clusters
    for c in cands:
        if c["kind"] == "IN_SEASON_MULTI_TEAM":
            c["cluster_key"] = f"{c['season']}|{'/'.join(sorted(c['teams']))}|{c.get('boundary_game')}"
    size = Counter(c["cluster_key"] for c in cands if c["kind"] == "IN_SEASON_MULTI_TEAM")
    for c in cands:
        if c["kind"] == "IN_SEASON_MULTI_TEAM":
            c["cluster_size"] = size[c["cluster_key"]]
            c["whole_team_cluster"] = size[c["cluster_key"]] >= TA.WHOLE_TEAM_CLUSTER_MIN
        if c["kind"] == "CROSS_SEASON_LINK":
            c["cluster_key"] = f"{c['from_season']}>{c['to_season']}|{c['teams'][0]}>{c['teams'][1]}"
    for c in cands:
        c["classification"], c["confidence"], c["rationale"] = classify(c)
    names = dict(q("SELECT canonical_team_id, school FROM core.teams"))
    link_q = q("SELECT count(*) FROM metadata.identity_link_quarantine")[0][0] if _has_table(con, "metadata.identity_link_quarantine") else None
    ta = (q("""SELECT canonical_game_id, season, defect_class, decision, rows_considered, rows_restored, rows_quarantined,
                      home_box_points, home_player_points_after, away_box_points, away_player_points_after, reconciles
               FROM metadata.team_attribution_resolution ORDER BY season, canonical_game_id""")
          if _has_table(con, "metadata.team_attribution_resolution") else [])
    remaining_box_multi = q(f"""SELECT count(*) FROM (SELECT canonical_player_id, season FROM fact.player_games WHERE canonical_player_id IS NOT NULL
                               AND season {W} GROUP BY 1, 2 HAVING count(DISTINCT canonical_team_id) > 1)""")[0][0]
    remaining_sf_multi = q(f"""SELECT count(*) FROM (SELECT canonical_player_id, season FROM fact.player_season_stats WHERE season {W}
                              GROUP BY 1, 2 HAVING count(DISTINCT canonical_team_id) > 1)""")[0][0]
    unreconciled = q(f"""WITH ps AS (SELECT canonical_game_id, canonical_team_id, sum(points) pts FROM fact.player_games GROUP BY 1, 2)
        SELECT count(*) FROM fact.team_games t JOIN ps USING (canonical_game_id, canonical_team_id) JOIN core.games g USING (canonical_game_id)
        WHERE g.season {W} AND t.canonical_game_id IN (SELECT canonical_game_id FROM metadata.team_attribution_resolution
                                                       WHERE decision = 'REMEDIATED_RECONCILED') AND ps.pts <> t.points""")[0][0] if ta else None
    con.close()
    # ------------------------------------------------------------------ summary
    clusters = defaultdict(list)
    for c in cands:
        if c["kind"] in ("IN_SEASON_MULTI_TEAM", "CROSS_SEASON_LINK") and (c["kind"] == "IN_SEASON_MULTI_TEAM" or c.get("cross_team")):
            clusters[c["cluster_key"]].append(c)
    cl_rows = []
    for k, members in clusters.items():
        m0 = members[0]
        cl_rows.append({"cluster_key": k, "kind": m0["kind"], "season": m0["season"],
                        "teams": " / ".join(names.get(t, t) for t in m0["teams"]), "boundary": m0.get("boundary_game") or k.split("|")[0],
                        "size": len(members), "whole_team": len(members) >= TA.WHOLE_TEAM_CLUSTER_MIN,
                        "classes": dict(Counter(x["classification"] for x in members)),
                        "confidence": dict(Counter(x["confidence"] for x in members))})
    cl_rows.sort(key=lambda r: (-r["size"], r["cluster_key"]))
    whole = [r for r in cl_rows if r["whole_team"]]
    whole_accepted_without_evidence = sum(
        1 for c in cands if c.get("whole_team_cluster") and c["classification"] in ("CONFIRMED_TRANSFER", "CONFIRMED_SINGLE_PLAYER")
        and not c.get("portal_record"))
    by_class = Counter(c["classification"] for c in cands)
    multi = [c for c in cands if c["kind"] in ("IN_SEASON_MULTI_TEAM", "CROSS_SEASON_LINK", "ROSTER_MULTI_TEAM", "EVENT_FOREIGN_ACTOR")]
    result = {
        "window": "2013-14 .. 2024-25", "mode": "BEFORE_v2.3_DIAGNOSTIC" if before else "AFTER_REMEDIATION",
        "revoked_rule": "non-overlapping multi-team appearances => CONFIRMED_SINGLE_PLAYER (Stage 3.1) -- chronology is not identity evidence",
        "canonical_players_scanned": scanned, "candidates": len(cands), "multi_team_candidates": len(multi),
        "by_kind": dict(Counter(c["kind"] for c in cands)), "by_class": dict(by_class),
        "by_kind_class": {k: dict(Counter(c["classification"] for c in cands if c["kind"] == k)) for k in sorted({c["kind"] for c in cands})},
        "by_confidence": dict(Counter(c["confidence"] for c in cands)),
        "team_pair_clusters": len(cl_rows), "in_season_clusters": sum(1 for r in cl_rows if r["kind"] == "IN_SEASON_MULTI_TEAM"),
        "cross_season_clusters_size_ge_2": sum(1 for r in cl_rows if r["kind"] == "CROSS_SEASON_LINK" and r["size"] >= 2),
        "whole_team_clusters": len(whole), "whole_team_clusters_list": whole,
        "whole_team_members_accepted_without_positive_evidence": whole_accepted_without_evidence,
        "every_candidate_has_evidence_class_confidence": all(c.get("classification") in CLASSES and c.get("confidence") in CONFIDENCE
                                                             and c.get("rationale") for c in cands),
        "team_alias_pairs": alias_pairs,
        "provider_dob_reliability": dob,
        "team_attribution_resolution": [dict(zip(("game", "season", "defect_class", "decision", "rows_considered", "rows_restored",
                                                  "rows_quarantined", "home_box_pts", "home_player_pts", "away_box_pts", "away_player_pts",
                                                  "reconciles"), r)) for r in ta],
        "remaining_in_season_box_multi_team": remaining_box_multi, "remaining_in_season_season_file_multi_team": remaining_sf_multi,
        "repaired_games_unreconciled": unreconciled, "identity_links_quarantined": link_q,
        "largest_clusters": cl_rows[:25],
    }
    result["classification_hash"] = hashlib.sha256(json.dumps(
        sorted((c["kind"], str(c["entity"]), str(c.get("season")), str(c.get("cluster_key")), c["classification"], c["confidence"]) for c in cands)
    ).encode()).hexdigest()
    out_dir = BEFORE_DIR if before else C.QA
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = ["kind", "entity", "canonical_player_id", "name", "season", "teams", "cluster_key", "cluster_size", "classification",
            "confidence", "rationale"]
    with (out_dir / OUT_CSV.name).open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(keys + ["evidence_json"])
        for c in sorted(cands, key=lambda c: (c["kind"], str(c.get("season")), str(c.get("cluster_key")), str(c["entity"]))):
            ev = {k: v for k, v in c.items() if k not in keys}
            w.writerow([json.dumps(c.get(k), default=str) if isinstance(c.get(k), list) else c.get(k) for k in keys]
                       + [json.dumps(ev, default=str, sort_keys=True)])
    with (out_dir / OUT_CLUSTERS.name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cl_rows[0]))
        w.writeheader()
        w.writerows([{k: (json.dumps(v) if isinstance(v, dict) else v) for k, v in r.items()} for r in cl_rows])
    (out_dir / OUT_JSON.name).write_text(json.dumps(result, indent=1, default=str))
    if not before:
        OUT_MD.write_text(render(result, names))
    return result


def render(r: dict, names: dict) -> str:
    n = lambda t: names.get(t, t)
    L = ["# Player identity cluster audit (Pre-Model Integrity Closure v1)", "",
         "Rendered from `data/qa/player_identity_cluster_audit.json` by `cbb_platform/identity_cluster_audit.py`; every candidate "
         "with its full evidence record is in `data/qa/player_identity_cluster_audit.csv`, clusters in "
         "`data/qa/player_identity_clusters.csv`; the pre-remediation diagnostic run on v2.3 is in "
         "`data/qa/pre_model_closure/before_v2.3/`. Remediation rules: `cbb_platform/team_attribution.py` (applied inside every "
         "canonical build; derived rows only; removed rows preserved in `qa.identity_remediation_quarantine`).", "",
         "**Superseded rule.** The Stage 3.1 rule *non-overlapping multi-team appearances => CONFIRMED_SINGLE_PLAYER* is REVOKED. "
         "Chronology is never positive identity evidence; it only shows the absence of one kind of contradiction. "
         "`docs/PLAYER_IDENTITY_COLLISION_AUDIT.md` is preserved unchanged as a historical record.", "",
         "## Summary", "",
         f"- window {r['window']}; canonical players scanned: **{r['canonical_players_scanned']:,}**",
         f"- candidates: **{r['candidates']:,}** (multi-team: {r['multi_team_candidates']:,}); by kind {r['by_kind']}",
         f"- by classification: {r['by_class']}", f"- by confidence: {r['by_confidence']}",
         f"- team-pair clusters: {r['team_pair_clusters']:,} (in-season {r['in_season_clusters']}, cross-season size >= 2: "
         f"{r['cross_season_clusters_size_ge_2']}); whole-team-sized (>= 5): **{r['whole_team_clusters']}**",
         f"- whole-team cluster members accepted as transfer/single player without positive evidence: "
         f"**{r['whole_team_members_accepted_without_positive_evidence']}**",
         f"- remaining in-season multi-team identities after repair: box {r['remaining_in_season_box_multi_team']}, "
         f"season files {r['remaining_in_season_season_file_multi_team']}; repaired games not reconciling: {r['repaired_games_unreconciled']}",
         f"- cross-season links quarantined (metadata.identity_link_quarantine): {r['identity_links_quarantined']}",
         f"- team-id alias pairs (CANONICAL_MAPPING_ERROR, merge deferred): {r['team_alias_pairs']}",
         f"- provider DOB reliability: {r['provider_dob_reliability']['roster_rows_age_under_16_at_season_start']:,} of "
         f"{r['provider_dob_reliability']['roster_rows_with_dob']:,} roster rows carry a DOB implying age < 16 at season start "
         f"(by season {r['provider_dob_reliability']['by_season']}); DOB is therefore informational only, never decisive",
         f"- classification hash: `{r['classification_hash']}`", "",
         "## Classes per candidate kind", "", "| kind | classes |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in r["by_kind_class"].items()]
    L += ["", "## Whole-team-sized clusters", "", "| cluster | kind | teams | size | classes | confidence |", "|---|---|---|---|---|---|"]
    L += [f"| `{c['cluster_key']}` | {c['kind']} | {c['teams']} | {c['size']} | {c['classes']} | {c['confidence']} |"
          for c in r["whole_team_clusters_list"]]
    L += ["", "## Game-level team-attribution decisions", "",
          "| game | season | defect | decision | rows | restored | quarantined | home box/players | away box/players | reconciles |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {t['game']} | {t['season']} | {t['defect_class']} | {t['decision']} | {t['rows_considered']} | {t['rows_restored']} | "
          f"{t['rows_quarantined']} | {t['home_box_pts']}/{t['home_player_pts']} | {t['away_box_pts']}/{t['away_player_pts']} | {t['reconciles']} |"
          for t in r["team_attribution_resolution"]]
    L += ["", "## Largest clusters", "", "| cluster | kind | teams | size | classes |", "|---|---|---|---|---|"]
    L += [f"| `{c['cluster_key']}` | {c['kind']} | {c['teams']} | {c['size']} | {c['classes']} |" for c in r["largest_clusters"]]
    L += ["", "## Evidence rule", "",
          "Positive evidence admitted (independence noted): independent CBBD portal record matching normalized name + origin + "
          "destination + transfer year (independent of the athlete-id linkage); mirrored / misfiled head-to-head box lines in the "
          "Starter Pack source rows plus exact reconciliation of both teams' PTS/FGM/3PM/FTM; PBP on-floor team of the athlete "
          "in the boundary game (CBBD API, independent of the Starter Pack box file); provider roster continuity (same athlete "
          "id on both team-season rosters -- provider-asserted, NOT independent, admitted only at MEDIUM confidence and never "
          "for a whole-team-sized cluster). Contradictions (decisive): < 4 h overlapping appearances, eligibility-impossible "
          "career length, conflicting provider profile across team-seasons. Provider DOB vs appearance season is recorded but "
          "NOT decisive: it is measured unreliable (summary above). Not admitted as "
          "positive evidence: chronology / non-overlap, exact name equality alone (names are the resolution key itself), "
          "provider roster attributes (DOB, hometown, height) as *confirmation* -- they are attributes of the provider entity and "
          "therefore repeat whatever the provider merged (A.J. Lawson). BDL holds 130 player-game rows locally "
          "(2024-25 sample) and contributes no cluster evidence.", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    b = "--before" in sys.argv
    r = run(before=b)
    print(json.dumps({k: v for k, v in r.items() if k not in ("largest_clusters", "whole_team_clusters_list", "team_attribution_resolution")},
                     indent=1, default=str))
