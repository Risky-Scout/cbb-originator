"""Deterministic upcoming-slate builder (point in time). Never guesses an ambiguous game: every excluded or blocked game
carries an explicit reason.

feature_cutoff = the validated Stage 3 rule (scheduled tip - 1 h for EXACT tips; 00:00 US/Eastern of the game date
otherwise). A game is wager-eligible only if, for both teams, every earlier game whose result would be knowable by the
feature cutoff is already in the state (else the features would not equal the validated definition: STALE_STATE).
"""
from __future__ import annotations

import duckdb
import pandas as pd

from cbb_platform.prod import config as PC

SLATE_COLS = ["canonical_game_id", "provider_game_id", "season", "start_time", "tip_time_quality", "game_date_et",
              "home_team_id", "away_team_id", "neutral_site", "canonical_venue_id", "game_universe",
              "prediction_as_of", "feature_cutoff", "cutoff_rule", "schedule_as_of", "slate_status", "suppression_reasons"]


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def feature_cutoff(tip, tip_quality: str, game_date_et) -> tuple[pd.Timestamp, str]:
    if tip_quality == "EXACT" and pd.notna(tip):
        return _utc(tip) - pd.Timedelta(hours=PC.CUTOFF_OFFSET_HOURS), "TIP_MINUS_1H"
    d = pd.Timestamp(str(pd.Timestamp(game_date_et).date())).tz_localize("America/New_York")
    return d.tz_convert("UTC"), "ET_GAME_DATE_START"


def build(increment_db, prediction_as_of, schedule_as_of, horizon_hours: float = 36.0) -> pd.DataFrame:
    """All 2026-27 scheduled (no result) games with tip in (prediction_as_of, prediction_as_of + horizon]."""
    aso = _utc(prediction_as_of)
    con = duckdb.connect(str(increment_db), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    g = con.execute("SELECT * FROM flagship.games ORDER BY scheduled_tip_utc, canonical_game_id").df()
    has_sched = con.execute("""SELECT count(*) FROM duckdb_tables() WHERE schema_name = 'metadata'
                               AND table_name = 'prod_schedule'""").fetchone()[0]
    sched = con.execute("SELECT * FROM metadata.prod_schedule").df() if has_sched else None
    con.close()
    # provider listing (live feed only): a game no longer listed, or listed as cancelled / postponed, is not scheduled
    listed = subs = None
    if sched is not None:
        st = sched["status"].astype(str).str.strip().str.lower()
        listed = dict(zip(sched["canonical_game_id"], sched["listed_in_latest_schedule"].astype(bool)
                          & ~st.isin(["canceled", "cancelled", "postponed"])))
        if "substitutions_acquired" in sched:
            subs = dict(zip(sched["canonical_game_id"], sched["substitutions_acquired"].astype(bool)))
    g["scheduled_tip_utc"] = pd.to_datetime(g["scheduled_tip_utc"], utc=True)
    up = g[g["home_points"].isna() & (g["season"] == PC.PRODUCTION_SEASON)
           & (g["scheduled_tip_utc"] > aso) & (g["scheduled_tip_utc"] <= aso + pd.Timedelta(hours=horizon_hours))].copy()
    rows = []
    for r in up.itertuples(index=False):
        reasons = []
        if r.season != PC.PRODUCTION_SEASON:
            reasons.append("WRONG_PRODUCTION_SEASON")
        if pd.isna(r.home_team_id) or pd.isna(r.away_team_id):
            reasons += ["UNMATCHED_GAME", "UNKNOWN_TEAM"]
        if r.game_universe == "UNKNOWN":
            reasons.append("MISSING_ROSTER")                     # D-I status of a team not established pregame
        elif r.game_universe != "DI_VS_DI":
            reasons.append("NON_DI_GAME")
        if listed is not None and not listed.get(r.canonical_game_id, False):
            reasons.append("MISSING_SCHEDULE")
        cut, rule = feature_cutoff(r.scheduled_tip_utc, r.tip_time_quality, r.game_date_et)
        if aso > cut:
            reasons.append("PREDICTION_AFTER_CUTOFF")
        # identity: another scheduled game for the same pair on the same ET date (duplicate / doubleheader record)
        same = up[(up["canonical_game_id"] != r.canonical_game_id) & (up["game_date_et"] == r.game_date_et)
                  & (((up["home_team_id"] == r.home_team_id) & (up["away_team_id"] == r.away_team_id))
                     | ((up["home_team_id"] == r.away_team_id) & (up["away_team_id"] == r.home_team_id)))]
        if len(same):
            reasons.append("AMBIGUOUS_GAME")
        # every earlier game of either team knowable by the cutoff must already carry its result
        for team in (r.home_team_id, r.away_team_id):
            prior = g[((g["home_team_id"] == team) | (g["away_team_id"] == team))
                      & (g["scheduled_tip_utc"] < r.scheduled_tip_utc) & (g["canonical_game_id"] != r.canonical_game_id)]
            known = prior["scheduled_tip_utc"] + pd.Timedelta(hours=4) <= cut
            if listed is not None:                               # cancelled / unlisted games never produce a result
                known &= prior["canonical_game_id"].map(lambda x: listed.get(x, False)).astype(bool)
            kp = prior.loc[known]
            # every knowable earlier game must be fully ingested: result, both team boxes, player box (and, on the live
            # feed, its substitution request) -- otherwise the features would not equal the validated definition
            incomplete = kp["home_points"].isna() | ~kp["has_team_box_score"].fillna(False).astype(bool) \
                | ~kp["has_player_box_score"].fillna(False).astype(bool)
            if subs is not None:
                incomplete |= ~kp["canonical_game_id"].map(lambda x: subs.get(x, False)).astype(bool)
            if incomplete.any():
                reasons.append("STALE_STATE")
                break
        rows.append({"canonical_game_id": r.canonical_game_id, "provider_game_id": r.source_record_key,
                     "season": int(r.season), "start_time": r.scheduled_tip_utc, "tip_time_quality": r.tip_time_quality,
                     "game_date_et": r.game_date_et, "home_team_id": r.home_team_id, "away_team_id": r.away_team_id,
                     "neutral_site": bool(r.neutral_site) if pd.notna(r.neutral_site) else False,
                     "canonical_venue_id": r.canonical_venue_id, "game_universe": r.game_universe,
                     "prediction_as_of": aso, "feature_cutoff": cut, "cutoff_rule": rule,
                     "schedule_as_of": _utc(schedule_as_of),
                     "slate_status": "EXCLUDED" if {"NON_DI_GAME", "UNMATCHED_GAME", "AMBIGUOUS_GAME", "WRONG_PRODUCTION_SEASON",
                                                   "MISSING_SCHEDULE", "UNKNOWN_TEAM", "MISSING_ROSTER"} & set(reasons)
                     else "SCORE", "suppression_reasons": sorted(set(reasons))})
    return pd.DataFrame(rows, columns=SLATE_COLS)
