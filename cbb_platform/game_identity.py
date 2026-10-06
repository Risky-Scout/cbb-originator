"""Stage 2.1 -- canonical game identity resolution (ONE real contest = ONE trusted canonical game).

Built inside build_canonical, after box facts exist and before any game-derived aggregate:

    resolve(con)                 -> metadata.game_alias_map (explicit row for every game in a candidate pair)
    assert_fact_safety(con)      -> after event facts: no alias/corrupt record may carry facts that would be
                                    duplicated on, or lost from, the trusted game

Raw truth is untouched: core.games keeps every source record. Trusted truth is the view flagship.games
(core.games minus aliases and quarantined records) and metadata.trusted_game_map, through which every flagship
fact view and every game-derived aggregate is routed (schema.sql).

Resolution classes
    CANONICAL                      representative of a duplicate pair (the trusted contest)
    DUPLICATE_ALIAS                another source record of the same contest -> resolved to the representative
    SOURCE_CORRUPT_MISATTRIBUTED   a record that represents no real contest of its listed teams -> excluded
    LEGITIMATE_DISTINCT_GAME       a candidate pair that is two real contests (both kept)
    UNRESOLVED                     never allowed to reach the map: resolve() STOPs instead

Representative priority for TRUE_DUPLICATE pairs (architect, Stage 2.1):
    1 verified team/player box attachment  2 richer event data  3 complete non-zero score  4 smaller source id
Event facts are built after this step, so priority 2 is enforced afterwards by assert_fact_safety: no alias may
carry event facts (if one did, the build stops and the ruling must be revisited).
"""
from __future__ import annotations

import hashlib
import json

import duckdb

from cbb_platform import game_identity_audit as GA

VERSION = "stage2.1-game-identity-1.0.0"
RULING = "ARCHITECT_ADJUDICATION_STAGE_2_1"

# Architect adjudications (Stage 2.1 directive). Points by team school name as in core.teams.
POSSIBLE_RULINGS = {
    frozenset({71900, 71901}): {"Morgan State": 71, "UTSA": 59},
    frozenset({72094, 72095}): {"Holy Cross": 74, "Morgan State": 73},
    frozenset({72671, 72672}): {"Toledo": 82, "Samford": 69},
    frozenset({72723, 72724}): {"Belmont": 74, "Alaska Anchorage": 60},
    frozenset({72765, 72766}): {"South Carolina Upstate": 75, "Utah Valley": 50},
    frozenset({74035, 74036}): {"Boise State": 67, "New Orleans": 51},
}
UNRESOLVED_RULINGS = {
    frozenset({118538, 118539}): {"valid": 118538, "corrupt": 118539, "result": {"USC": 68, "Indiana State": 64},
                                  "corrupt_score": (81, 78)},
}


class GameIdentityStop(RuntimeError):
    """Implementation evidence contradicts an architect ruling (or a pair has no ruling). Never overridden."""


def _record(con, gid: int) -> dict:
    r = con.execute("""
        SELECT g.canonical_game_id, th.school, ta.school, g.home_points, g.away_points, g.season, g.game_date_et,
               (SELECT count(*) FROM fact.team_games t WHERE t.canonical_game_id = g.canonical_game_id),
               (SELECT count(*) FROM fact.player_games p WHERE p.canonical_game_id = g.canonical_game_id)
        FROM core.games g JOIN core.teams th ON th.canonical_team_id = g.home_team_id
        JOIN core.teams ta ON ta.canonical_team_id = g.away_team_id WHERE g.source_record_key = ?""", [f"id={gid}"]).fetchone()
    return {"gid": gid, "cid": r[0], "home": r[1], "away": r[2], "hp": r[3], "ap": r[4], "season": r[5], "date": r[6],
            "team_box": r[7], "player_box": r[8]}


def _priority(rec: dict) -> tuple:
    """Higher sorts first: (box attached, event rows [0 at build time], complete non-zero score, -source id)."""
    complete = rec["hp"] is not None and rec["ap"] is not None and (rec["hp"], rec["ap"]) != (0, 0)
    return (rec["team_box"] + rec["player_box"] > 0, 0, complete, -rec["gid"])


def _points(rec: dict) -> dict:
    return {rec["home"]: rec["hp"], rec["away"]: rec["ap"]}


def resolve(con: duckdb.DuckDBPyConnection) -> dict:
    df = con.execute(GA.CANDIDATES_SQL + " ORDER BY season, date_a, game_a").df()
    pairs = []
    for r in df.to_dict("records"):
        r = {k: (None if (v is not None and not isinstance(v, (str, list)) and v != v) else v) for k, v in r.items()}
        for k in ("home_pts_a", "away_pts_a", "home_pts_b", "away_pts_b", "team_box_rows_a", "team_box_rows_b",
                  "player_box_rows_a", "player_box_rows_b", "game_a", "game_b"):
            r[k] = None if r[k] is None else int(r[k])
        r["classification"], r["reason"] = GA.classify(r)
        pairs.append(r)

    rows: dict[int, dict] = {}       # source game id -> map row (strongest class wins)
    rank = {"SOURCE_CORRUPT_MISATTRIBUTED": 4, "DUPLICATE_ALIAS": 3, "CANONICAL": 2, "LEGITIMATE_DISTINCT_GAME": 1}

    def put(rec, cls, resolved_cid, rep, method, evidence, conf, partner):
        new = {"provider": "CBBD", "source_game_id": str(rec["gid"]), "source_canonical_game_id": rec["cid"],
               "resolved_canonical_game_id": resolved_cid, "resolution_class": cls, "canonical_representative": rep,
               "adjudication_method": method, "adjudication_evidence": evidence, "confidence": conf,
               "excluded_from_trusted_games": cls in ("DUPLICATE_ALIAS", "SOURCE_CORRUPT_MISATTRIBUTED"),
               "paired_source_game_ids": {partner}, "season": rec["season"]}
        old = rows.get(rec["gid"])
        if old is None or rank[cls] > rank[old["resolution_class"]]:
            if old:
                new["paired_source_game_ids"] |= old["paired_source_game_ids"]
            rows[rec["gid"]] = new
        else:
            old["paired_source_game_ids"].add(partner)

    for p in pairs:
        a, b = _record(con, p["game_a"]), _record(con, p["game_b"])
        key = frozenset({a["gid"], b["gid"]})
        ev = (f"pair {a['gid']}/{b['gid']} {p['season']} {a['date']}: {a['home']} {a['hp']}-{a['ap']} "
              f"{a['away']} (box {a['team_box']}/{a['player_box']}) vs {b['home']} {b['hp']}-{b['ap']} {b['away']} "
              f"(box {b['team_box']}/{b['player_box']}); audit: {p['classification']} ({p['reason']})")
        cls = p["classification"]
        if cls == "LEGITIMATE_DISTINCT_GAME":
            for x, y in ((a, b), (b, a)):
                put(x, "LEGITIMATE_DISTINCT_GAME", x["cid"], False, "RULE_LEGITIMATE_DISTINCT_GAME", ev, 1.0, y["gid"])
        elif cls == "TRUE_DUPLICATE":
            rep, alias = sorted((a, b), key=_priority, reverse=True)
            why = ("box attachment" if _priority(rep)[0] != _priority(alias)[0] else
                   "complete score" if _priority(rep)[2] != _priority(alias)[2] else "smaller source id")
            put(rep, "CANONICAL", rep["cid"], True, f"RULE_TRUE_DUPLICATE_PRIORITY({why})", ev, 1.0, alias["gid"])
            put(alias, "DUPLICATE_ALIAS", rep["cid"], False, f"RULE_TRUE_DUPLICATE_PRIORITY({why})", ev, 1.0, rep["gid"])
        elif cls == "POSSIBLE_DUPLICATE":
            ruling = POSSIBLE_RULINGS.get(key)
            if ruling is None:
                raise GameIdentityStop(f"POSSIBLE_DUPLICATE {sorted(key)} has no architect adjudication")
            rep, alias = sorted((a, b), key=_priority, reverse=True)
            if _points(rep) != ruling:
                raise GameIdentityStop(f"adjudicated result {ruling} contradicts representative {rep['gid']} {_points(rep)}")
            if not (rep["team_box"] and rep["player_box"]) or alias["team_box"] or alias["player_box"]:
                raise GameIdentityStop(f"box evidence for {sorted(key)} does not single out the adjudicated record")
            evr = ev + f"; architect result {ruling} = record {rep['gid']} (box-attached); {alias['gid']} is the orphan"
            put(rep, "CANONICAL", rep["cid"], True, RULING, evr, 1.0, alias["gid"])
            put(alias, "DUPLICATE_ALIAS", rep["cid"], False, RULING, evr, 1.0, rep["gid"])
        else:   # UNRESOLVED
            ruling = UNRESOLVED_RULINGS.get(key)
            if ruling is None:
                raise GameIdentityStop(f"UNRESOLVED pair {sorted(key)} has no architect adjudication")
            valid, bad = (a, b) if a["gid"] == ruling["valid"] else (b, a)
            if _points(valid) != ruling["result"]:
                raise GameIdentityStop(f"adjudicated result {ruling['result']} contradicts record {valid['gid']} {_points(valid)}")
            # the corrupt record's score must be a separately represented real contest -- never a lost game
            hi, lo = ruling["corrupt_score"]
            other = con.execute("""
                SELECT g.source_record_key, th.school, ta.school, g.home_points, g.away_points FROM core.games g
                JOIN core.teams th ON th.canonical_team_id = g.home_team_id JOIN core.teams ta ON ta.canonical_team_id = g.away_team_id
                WHERE g.season = ? AND g.game_date_et = ? AND g.source_record_key <> ? AND g.source_record_key <> ?
                  AND ((g.home_points = ? AND g.away_points = ?) OR (g.home_points = ? AND g.away_points = ?))
                  AND (th.school IN (?, ?) OR ta.school IN (?, ?))""",
                [bad["season"], bad["date"], f"id={bad['gid']}", f"id={valid['gid']}", hi, lo, lo, hi,
                 bad["home"], bad["away"], bad["home"], bad["away"]]).fetchall()
            if not other:
                raise GameIdentityStop(f"excluding {bad['gid']} would remove the only {hi}-{lo} record of its teams that day")
            sep = "; ".join(f"{k} {h} {hp}-{ap} {aw}" for k, h, aw, hp, ap in other)
            evr = (ev + f"; architect: {valid['gid']} is the contest ({ruling['result']}); {bad['gid']}'s {hi}-{lo} score is "
                   f"separately represented by [{sep}] on the same date, so excluding {bad['gid']} removes no real contest")
            put(valid, "CANONICAL", valid["cid"], True, RULING, evr, 1.0, bad["gid"])
            put(bad, "SOURCE_CORRUPT_MISATTRIBUTED", None, False, RULING, evr, 1.0, valid["gid"])

    # no chains / cycles: every alias target is a non-excluded record, and nothing aliases to itself
    by_cid = {r["source_canonical_game_id"]: r for r in rows.values()}
    for r in rows.values():
        if r["resolution_class"] == "DUPLICATE_ALIAS":
            tgt = by_cid.get(r["resolved_canonical_game_id"])
            if r["resolved_canonical_game_id"] == r["source_canonical_game_id"] or (tgt and tgt["excluded_from_trusted_games"]):
                raise GameIdentityStop(f"alias chain or self-alias at source game {r['source_game_id']}")

    out = []
    for gid in sorted(rows):
        r = rows[gid]
        out.append((r["provider"], r["source_game_id"], r["source_canonical_game_id"], r["resolved_canonical_game_id"],
                    r["resolution_class"], r["canonical_representative"], r["adjudication_method"],
                    r["adjudication_evidence"], r["confidence"], r["excluded_from_trusted_games"],
                    ",".join(str(x) for x in sorted(r["paired_source_game_ids"])), r["season"], VERSION))
    con.executemany("INSERT INTO metadata.game_alias_map VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", out)
    digest = hashlib.sha256(json.dumps([o[:11] for o in out], default=str).encode()).hexdigest()
    return {"rows": len(out), "pairs": len(pairs), "resolution_hash": digest}


FACT_TABLES = ("fact.team_games", "fact.player_games", "fact.participation", "fact.plays", "fact.shots",
               "fact.substitutions", "fact.lineup_stats", "fact.play_on_floor", "fact.play_participants")


def assert_fact_safety(con: duckdb.DuckDBPyConnection) -> dict:
    """After ALL facts are built: an excluded record may carry facts only if (a) it is an alias whose survivor
    carries none of that fact (the facts then resolve to the survivor), never (b) facts on a corrupt record
    (they would be silently discarded) or (c) the same fact on alias and survivor (it would be duplicated)."""
    report = {}
    for t in FACT_TABLES:
        rows = con.execute(f"""
            SELECT m.source_game_id, m.resolution_class,
                   (SELECT count(*) FROM {t} f WHERE f.canonical_game_id = m.source_canonical_game_id) AS on_record,
                   (SELECT count(*) FROM {t} f WHERE f.canonical_game_id = m.resolved_canonical_game_id) AS on_survivor
            FROM metadata.game_alias_map m WHERE m.excluded_from_trusted_games""").fetchall()
        moved = 0
        for gid, cls, n_rec, n_surv in rows:
            if n_rec and cls == "SOURCE_CORRUPT_MISATTRIBUTED":
                raise GameIdentityStop(f"{t}: {n_rec} facts on corrupt record {gid} would be discarded")
            if n_rec and n_surv:
                raise GameIdentityStop(f"{t}: facts on both alias {gid} and its survivor would be duplicated")
            moved += n_rec
        report[t] = {"facts_on_excluded_records": moved}
    return report
