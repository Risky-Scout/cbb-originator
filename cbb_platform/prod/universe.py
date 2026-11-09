"""Live 2026-27 pregame player universe: roster awareness + scoreability around the FROZEN Stage 3 pool.

The frozen pool (ex.player_game_examples: legal membership evidence at the feature cutoff) is never changed here: the
current-season roster is NOT membership evidence (docs/ROSTER_PIT_POLICY.md; a dated-roster evidence class would be a
feature-semantic change for the architect). The current roster is used for identity and for explicit suppression:

cold_start = TRUE marks a true new college player (frozen class COLD_NO_HISTORY: no prior college box history); the
frozen classes COLD_TRANSFER_IN / COLD_EARLY_SEASON are reported in cold_start_class.

pool players (simulated exactly as the frozen model defines; a suppressed player is recorded, never priced)
    ON_CURRENT_ROSTER                      same canonical id on this team's 2026-27 roster
    ON_CURRENT_ROSTER_PROVIDER_ID_CHANGED  this team's roster lists the same exact name under another provider id
                                           (CBBD re-issued an athlete id; the pool keeps the history-bearing identity)
    ON_OTHER_TEAM_ROSTER   -> IDENTITY_CONFLICT       listed only on another team's 2026-27 roster
    NOT_ON_CURRENT_ROSTER  -> NOT_ON_CURRENT_ROSTER   on no 2026-27 roster of this team
    ROSTER_UNAVAILABLE                     the feed has no roster for this team (fixtures; live: MISSING_ROSTER)
roster players NOT in the pool (roster awareness, never predicted)
    DUPLICATE_PROVIDER_IDENTITY   same exact name as a pool player of this team (the provider-id duplicate of him)
    TRANSFERRED_OUT               an outbound 2026 portal record from this team names him (stale roster listing)
    AMBIGUOUS_PLAYER              a portal / recruiting record names him but its identity is ambiguous / unresolved
    NO_FROZEN_MEMBERSHIP_EVIDENCE JUCO / international / walk-on / non-D-I transfer / other newcomer: no frozen evidence
committed 2026 recruits with no identity (not on the committed roster; CBBD athleteId not yet linked)
    RECRUIT_IDENTITY_UNAVAILABLE

Verified CBBD roster omissions (prod/roster_omissions: rank-1/2 source evidence, override-safety checked, only when
omissions are passed) are read as current-roster rows of their team -- exactly what a CBBD listing would do, nothing
more: a pool player NOT_ON_CURRENT_ROSTER becomes ON_CURRENT_ROSTER_CBBD_OMISSION (scoreable as the frozen model
defines); a verified player outside the frozen pool is listed NO_FROZEN_MEMBERSHIP_EVIDENCE (never predicted). The
frozen pool, its membership evidence and every feature are unchanged; omissions=None -> the output is byte-identical.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from cbb_platform.prod import config as PC

COLUMNS = ["game_id", "team_id", "canonical_player_id", "provider_player_id", "name", "roster_status", "newcomer_class",
           "in_frozen_pool", "pool_evidence", "cold_start_class", "cold_start", "scoreability", "suppression_reason",
           "source", "source_timestamp"]
POOL_SUPPRESS = {"ON_OTHER_TEAM_ROSTER": "IDENTITY_CONFLICT", "NOT_ON_CURRENT_ROSTER": "NOT_ON_CURRENT_ROSTER"}

SQL = """
WITH tg AS (SELECT DISTINCT canonical_game_id AS game_id, canonical_team_id AS team_id FROM fx.ex.player_game_examples),
ros AS (SELECT canonical_player_id, canonical_team_id, source_athlete_id, player_name, first_name, last_name, start_season
        FROM cx.flagship.rosters WHERE season = {season}),
has_ros AS (SELECT DISTINCT canonical_team_id FROM ros),
nm AS (SELECT canonical_player_id, any_value(display_name) AS name FROM cx.core.players GROUP BY 1),
pool AS (
    SELECT x.canonical_game_id AS game_id, x.canonical_team_id AS team_id, x.canonical_player_id, x.pool_evidence,
           x.ev_transfer_preseason, x.ev_recruit_preseason, f.cold_start_class, nm.name
    FROM fx.ex.player_game_examples x JOIN fx.feat.player_features f ON f.player_game_example_id = x.player_game_example_id
    LEFT JOIN nm ON nm.canonical_player_id = x.canonical_player_id),
pool_c AS (
    SELECT p.*,
           (SELECT any_value(r.source_athlete_id) FROM ros r WHERE r.canonical_player_id = p.canonical_player_id
                AND r.canonical_team_id = p.team_id) AS provider_player_id,
           CASE WHEN p.team_id NOT IN (SELECT canonical_team_id FROM has_ros) THEN 'ROSTER_UNAVAILABLE'
                WHEN EXISTS (SELECT 1 FROM ros r WHERE r.canonical_player_id = p.canonical_player_id AND r.canonical_team_id = p.team_id)
                     THEN 'ON_CURRENT_ROSTER'
                WHEN EXISTS (SELECT 1 FROM ros r WHERE r.canonical_team_id = p.team_id AND lower(r.player_name) = lower(p.name))
                     THEN 'ON_CURRENT_ROSTER_PROVIDER_ID_CHANGED'
                WHEN EXISTS (SELECT 1 FROM ros r WHERE r.canonical_player_id = p.canonical_player_id) THEN 'ON_OTHER_TEAM_ROSTER'
                ELSE 'NOT_ON_CURRENT_ROSTER' END AS roster_status,
           CASE WHEN p.ev_transfer_preseason THEN 'TRANSFER' WHEN p.ev_recruit_preseason THEN 'FRESHMAN'
                ELSE 'RETURNING' END AS newcomer_class
    FROM pool p),
ronly AS (
    SELECT t.game_id, t.team_id, r.canonical_player_id, r.source_athlete_id AS provider_player_id, r.player_name AS name,
           r.first_name, r.last_name, r.start_season
    FROM tg t JOIN ros r ON r.canonical_team_id = t.team_id
    WHERE NOT EXISTS (SELECT 1 FROM pool p WHERE p.game_id = t.game_id AND p.team_id = t.team_id
                      AND p.canonical_player_id = r.canonical_player_id)),
ronly_c AS (
    SELECT o.*,
           CASE WHEN EXISTS (SELECT 1 FROM pool p WHERE p.game_id = o.game_id AND p.team_id = o.team_id
                             AND lower(p.name) = lower(o.name)) THEN 'DUPLICATE_PROVIDER_IDENTITY'
                WHEN EXISTS (SELECT 1 FROM cx.core.transfers tr WHERE tr.transfer_year = {cls} AND tr.origin_team_id = o.team_id
                             AND (coalesce(tr.canonical_player_id, tr.candidate_canonical_player_id) = o.canonical_player_id
                                  OR (lower(tr.first_name) = lower(o.first_name) AND lower(tr.last_name) = lower(o.last_name))))
                     THEN 'TRANSFERRED_OUT'
                WHEN EXISTS (SELECT 1 FROM cx.core.transfers tr WHERE tr.transfer_year = {cls} AND tr.destination_team_id = o.team_id
                             AND lower(tr.first_name) = lower(o.first_name) AND lower(tr.last_name) = lower(o.last_name))
                  OR EXISTS (SELECT 1 FROM cx.core.recruits rc WHERE rc.recruit_year = {cls} AND rc.committed_team_id = o.team_id
                             AND lower(rc.name) = lower(o.name) AND rc.canonical_player_id IS NULL)
                     THEN 'AMBIGUOUS_PLAYER'
                ELSE 'NO_FROZEN_MEMBERSHIP_EVIDENCE' END AS suppression_reason,
           CASE WHEN o.start_season = {season} THEN 'OTHER_NEWCOMER'
                WHEN EXISTS (SELECT 1 FROM cx.flagship.rosters h WHERE h.season = {season} - 1
                             AND h.canonical_player_id = o.canonical_player_id AND h.canonical_team_id = o.team_id)
                     THEN 'RETURNING' ELSE 'OTHER_NEWCOMER' END AS newcomer_class
    FROM ronly o),
unlinked AS (
    SELECT t.game_id, t.team_id, rc.name, rc.source_recruit_id
    FROM tg t JOIN cx.core.recruits rc ON rc.committed_team_id = t.team_id AND rc.recruit_year = {cls}
    WHERE rc.canonical_player_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM ros r WHERE r.canonical_team_id = t.team_id AND lower(r.player_name) = lower(rc.name)))
SELECT game_id, team_id, canonical_player_id, provider_player_id, name, roster_status, newcomer_class, TRUE AS in_frozen_pool,
       pool_evidence, cold_start_class, coalesce(cold_start_class = 'COLD_NO_HISTORY', FALSE) AS cold_start,
       NULL AS suppression_reason
FROM pool_c
UNION ALL
SELECT game_id, team_id, canonical_player_id, provider_player_id, name, 'ON_CURRENT_ROSTER', newcomer_class, FALSE, NULL,
       NULL, NULL, suppression_reason
FROM ronly_c
UNION ALL
SELECT game_id, team_id, NULL, 'recruit:' || source_recruit_id, name, 'NOT_ON_CURRENT_ROSTER', 'FRESHMAN', FALSE, NULL,
       NULL, NULL, 'RECRUIT_IDENTITY_UNAVAILABLE'
FROM unlinked
"""


def _has(con, db: str, rel: str) -> bool:
    s, t = rel.split(".")
    return bool(con.execute(f"""SELECT count(*) FROM duckdb_tables() WHERE database_name = '{db}' AND schema_name = '{s}'
                                AND table_name = '{t}'""").fetchone()[0]
                or con.execute(f"""SELECT count(*) FROM duckdb_views() WHERE database_name = '{db}' AND schema_name = '{s}'
                                   AND view_name = '{t}'""").fetchone()[0])


OMITTED = "ON_CURRENT_ROSTER_CBBD_OMISSION"


def build(features_db: Path, canonical_db: Path, roster_as_of=None, source: str = "CBBD_API_PRODUCTION",
          omissions: pd.DataFrame | None = None, as_of=None) -> pd.DataFrame:
    """The universe table for the run's target games (features_db / canonical_db of prod/features.build).
    omissions / as_of: the verified CBBD roster omission table and the run instant (None -> not applied)."""
    con = duckdb.connect()
    con.execute(f"ATTACH '{features_db}' AS fx (READ_ONLY)")
    con.execute(f"ATTACH '{canonical_db}' AS cx (READ_ONLY)")
    if not _has(con, "cx", "flagship.rosters") or not con.execute(
            f"SELECT count(*) FROM cx.flagship.rosters WHERE season = {PC.PRODUCTION_SEASON}").fetchone()[0]:
        # no current-season roster in the state (fixtures): the frozen pool only, nothing suppressed by roster evidence
        df = con.execute("""SELECT x.canonical_game_id AS game_id, x.canonical_team_id AS team_id, x.canonical_player_id,
                                   NULL AS provider_player_id, NULL AS name, 'ROSTER_UNAVAILABLE' AS roster_status,
                                   CASE WHEN x.ev_transfer_preseason THEN 'TRANSFER' WHEN x.ev_recruit_preseason THEN 'FRESHMAN'
                                        ELSE 'RETURNING' END AS newcomer_class, TRUE AS in_frozen_pool, x.pool_evidence,
                                   f.cold_start_class, coalesce(f.cold_start_class = 'COLD_NO_HISTORY', FALSE) AS cold_start,
                                   NULL AS suppression_reason
                            FROM fx.ex.player_game_examples x JOIN fx.feat.player_features f USING (player_game_example_id)""").df()
    else:
        for rel in ("core.transfers", "core.recruits"):
            if not _has(con, "cx", rel):
                raise RuntimeError(f"universe: {rel} missing from the canonical input")
        df = con.execute(SQL.format(season=PC.PRODUCTION_SEASON, cls=PC.PRESEASON_CLASS_YEAR)).df()
        if omissions is not None and len(omissions):
            df = _apply_omissions(con, df, omissions, as_of)
    con.close()
    df["suppression_reason"] = df["suppression_reason"].astype(object)
    pool = df["in_frozen_pool"].astype(bool)
    df.loc[pool, "suppression_reason"] = df.loc[pool, "roster_status"].map(POOL_SUPPRESS)
    df["suppression_reason"] = df["suppression_reason"].where(df["suppression_reason"].notna(), None)
    df["scoreability"] = ["SCOREABLE" if (p and r is None) else "SUPPRESSED"
                          for p, r in zip(df["in_frozen_pool"], df["suppression_reason"])]
    df["source"] = source
    df["source_timestamp"] = roster_as_of
    return df[COLUMNS].sort_values(["game_id", "team_id", "in_frozen_pool", "canonical_player_id"],
                                   ascending=[True, True, False, True], kind="mergesort").reset_index(drop=True)


def _apply_omissions(con, df: pd.DataFrame, omissions: pd.DataFrame, as_of) -> pd.DataFrame:
    from cbb_platform.prod import roster_omissions as RO
    if as_of is None:
        raise ValueError("universe: omissions need the run instant (as_of)")
    ros = con.execute(f"""SELECT canonical_player_id, canonical_team_id, player_name, first_name, last_name
                          FROM cx.flagship.rosters WHERE season = {PC.PRODUCTION_SEASON}""").df()
    applied, _ = RO.effective(omissions, ros, as_of)
    rr = RO.roster_rows(applied)
    if not len(rr):
        return df
    key = set(zip(rr["canonical_player_id"], rr["canonical_team_id"]))
    pool = df["in_frozen_pool"].astype(bool)
    hit = pool & (df["roster_status"] == "NOT_ON_CURRENT_ROSTER") & pd.Series(
        [(p, t) in key for p, t in zip(df["canonical_player_id"], df["team_id"])], index=df.index)
    df = df.copy()
    df.loc[hit, "roster_status"] = OMITTED
    games = df[["game_id", "team_id"]].drop_duplicates()
    in_pool = set(zip(df.loc[pool, "game_id"], df.loc[pool, "team_id"], df.loc[pool, "canonical_player_id"]))
    extra = []
    for r in rr.itertuples(index=False):
        for g in games[games["team_id"] == r.canonical_team_id]["game_id"]:
            if (g, r.canonical_team_id, r.canonical_player_id) not in in_pool:
                extra.append({"game_id": g, "team_id": r.canonical_team_id, "canonical_player_id": r.canonical_player_id,
                              "provider_player_id": None, "name": r.player_name, "roster_status": OMITTED,
                              "newcomer_class": "OTHER_NEWCOMER", "in_frozen_pool": False, "pool_evidence": None,
                              "cold_start_class": None, "cold_start": None,
                              "suppression_reason": "NO_FROZEN_MEMBERSHIP_EVIDENCE"})
    return pd.concat([df, pd.DataFrame(extra)], ignore_index=True) if extra else df


def suppression_map(universe: pd.DataFrame) -> dict:
    """(game_id, player_id) -> reason for POOL players the roster evidence suppresses (simulated, never priced)."""
    u = universe[universe["in_frozen_pool"].astype(bool) & universe["suppression_reason"].notna()]
    return {(r.game_id, r.canonical_player_id): r.suppression_reason for r in u.itertuples(index=False)}


def summary(universe: pd.DataFrame) -> dict:
    if universe.empty:
        return {"rows": 0}
    pool = universe[universe["in_frozen_pool"].astype(bool)]
    return {"rows": int(len(universe)), "pool_players": int(len(pool)),
            "pool_scoreable": int((pool["scoreability"] == "SCOREABLE").sum()),
            "pool_by_class": pool["newcomer_class"].value_counts().sort_index().to_dict(),
            "pool_cold_start": int(pool["cold_start"].astype(bool).sum()),
            "pool_by_roster_status": pool["roster_status"].value_counts().sort_index().to_dict(),
            "suppressed_by_reason": universe["suppression_reason"].dropna().value_counts().sort_index().to_dict()}
