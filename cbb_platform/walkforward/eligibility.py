"""FINALIZED_TRAINING_ELIGIBILITY of 2026-27 games at a daily training cutoff.

Point-in-time source of truth = the immutable production state versions (cbb_platform/prod/state.py). The state used at
cutoff T is the latest promoted version whose created_at AND source_cutoff are <= T (state_asof): every row it holds
reached this machine no later than T, so nothing can be backdated into a fit. The real arrival time of a game's outcome
(outcome_available_at) is the created_at of the FIRST version holding its result, never the canonical HR-1 rule alone.

A game tipped before T enters the training state only if ALL hold (else DEFERRED / EXCLUDED, with one reason):
    universe        Stage 3 D-I vs D-I example universe                         else EXCLUDED NOT_IN_TRAINING_UNIVERSE
    played          not cancelled / forfeited                                    else EXCLUDED NOT_PLAYED
    final           result recorded (the feed records results only at status 'final') and, where the provider
                    schedule snapshot exists, its status is exactly 'final'      else DEFERRED NOT_FINAL_AT_CUTOFF /
                                                                                      STATUS_NOT_FINAL
    knowable        canonical HR-1 knowable_at <= T and outcome_available_at <= T else DEFERRED NOT_KNOWABLE_AT_CUTOFF
    box complete    both team box rows, player rows for both teams, and per team sum(player points) = team box points =
                    game score (2025-26: 5,733 of 5,752 D-I games reconcile)     else DEFERRED BOX_INCOMPLETE /
                                                                                      BOX_UNRECONCILED
    identity        every player box row of both (D-I) teams RESOLVED to a canonical player
                                                                                 else DEFERRED IDENTITY_UNRESOLVED
A DEFERRED game is re-evaluated at every later cutoff and enters the first version whose cutoff sees it pass. Games are
never inferred final from a scheduled end time.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from cbb_platform.prod import features as PF
from cbb_platform.walkforward import config as WC

NOT_PLAYED_STATUSES = ("canceled", "cancelled", "forfeit", "forfeited")
DECISIONS = ("INCLUDED", "DEFERRED", "EXCLUDED")
COLUMNS = ["game_id", "scheduled_tip_utc", "game_universe", "provider_status", "has_result", "knowable_at",
           "outcome_available_at", "outcome_state_version", "box_complete", "box_reconciled", "unresolved_rows",
           "decision", "reason"]


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# ============================================================================ state versions (point in time)
def state_versions(state_root: Path) -> list[dict]:
    out = []
    for m in sorted(Path(state_root, "versions").glob("*/state_manifest.json")):
        if m.parent.name.startswith("."):
            continue
        man = json.loads(m.read_text())
        out.append({"state_version": man["state_version"], "created_at": _utc(man["created_at"]),
                    "source_cutoff": _utc(man["source_cutoff"]), "increment_db": m.parent / "increment.duckdb",
                    "manifest": man})
    return sorted(out, key=lambda v: (v["created_at"], v["state_version"]))


def state_asof(state_root: Path, t) -> dict | None:
    """Latest promoted state version created (and source-cut) at or before t; None when there is none."""
    t = _utc(t)
    ok = [v for v in state_versions(state_root) if v["created_at"] <= t and v["source_cutoff"] <= t]
    return ok[-1] if ok else None


def outcome_available_at(state_root: Path, upto, known: dict | None = None) -> dict:
    """{game_id: {"available_at", "state_version"}} = first version (created <= upto) holding the game's result.
    `known` (the previous run's map) is extended; versions already scanned are not re-read."""
    out = dict(known or {})
    scanned = set(out.get("__scanned__", []))
    for v in state_versions(state_root):
        if v["created_at"] > _utc(upto) or v["state_version"] in scanned:
            continue
        con = duckdb.connect(str(v["increment_db"]), read_only=True)
        gids = [r[0] for r in con.execute("SELECT canonical_game_id FROM flagship.games WHERE home_points IS NOT NULL").fetchall()]
        con.close()
        for g in gids:
            if g not in out:
                out[g] = {"available_at": v["created_at"].isoformat(), "state_version": v["state_version"]}
        scanned.add(v["state_version"])
    out["__scanned__"] = sorted(scanned)
    return out


# ============================================================================ eligibility
def _has(con, rel: str) -> bool:
    s, t = rel.split(".")
    return bool(con.execute(f"SELECT count(*) FROM duckdb_tables() WHERE schema_name = '{s}' AND table_name = '{t}'").fetchone()[0])


def evaluate(state: dict, cutoff, available: dict) -> pd.DataFrame:
    """One row per 2026-27 game of `state` tipped before `cutoff`: decision + reason + the evidence behind it."""
    cut = _utc(cutoff)
    con = duckdb.connect(str(state["increment_db"]), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    sched = ("LEFT JOIN metadata.prod_schedule s USING (canonical_game_id)" if _has(con, "metadata.prod_schedule") else "")
    status = "lower(trim(s.status))" if sched else "NULL"
    g = con.execute(f"""SELECT g.canonical_game_id, g.season, g.scheduled_tip_utc, g.tip_time_quality, g.game_date_et,
            g.home_team_id, g.away_team_id, g.home_points, g.away_points, g.game_universe,
            {status} AS provider_status, {PF.KNOWABLE.format(t='g')} AS knowable_at
        FROM flagship.games g {sched}
        WHERE g.season = {WC.PRODUCTION_SEASON} AND g.scheduled_tip_utc < TIMESTAMPTZ '{cut.isoformat()}'
        ORDER BY g.scheduled_tip_utc, g.canonical_game_id""").df()
    tb = (con.execute("SELECT canonical_game_id, canonical_team_id, points FROM flagship.team_game_outcomes").df()
          if _has(con, "flagship.team_game_outcomes") else pd.DataFrame(columns=["canonical_game_id", "canonical_team_id", "points"]))
    pb = (con.execute("""SELECT canonical_game_id, canonical_team_id, count(*) AS n, sum(points) AS pts,
                                count(*) FILTER (WHERE canonical_player_id IS NULL OR identity_status <> 'RESOLVED') AS unresolved
                         FROM flagship.player_game_outcomes GROUP BY 1, 2""").df()
          if _has(con, "flagship.player_game_outcomes") else
          pd.DataFrame(columns=["canonical_game_id", "canonical_team_id", "n", "pts", "unresolved"]))
    con.close()
    tbi = tb.set_index(["canonical_game_id", "canonical_team_id"])["points"].to_dict()
    pbi = pb.set_index(["canonical_game_id", "canonical_team_id"]).to_dict("index")
    rows = []
    for r in g.itertuples(index=False):
        status = None if pd.isna(r.provider_status) else str(r.provider_status)
        ev = {"game_id": r.canonical_game_id, "scheduled_tip_utc": _utc(r.scheduled_tip_utc), "game_universe": r.game_universe,
              "provider_status": status, "has_result": bool(pd.notna(r.home_points)),
              "knowable_at": _utc(r.knowable_at) if pd.notna(r.knowable_at) else pd.NaT,
              "outcome_available_at": _utc(available[r.canonical_game_id]["available_at"]) if r.canonical_game_id in available else pd.NaT,
              "outcome_state_version": available.get(r.canonical_game_id, {}).get("state_version")}
        teams = ((r.home_team_id, r.home_points), (r.away_team_id, r.away_points))
        box = [(tbi.get((r.canonical_game_id, t)), pbi.get((r.canonical_game_id, t)), p) for t, p in teams]
        ev["box_complete"] = all(tp is not None and pp is not None and pp["n"] > 0 for tp, pp, _ in box)
        ev["box_reconciled"] = ev["box_complete"] and all(
            pd.notna(pp["pts"]) and pd.notna(tp) and pd.notna(p) and int(pp["pts"]) == int(tp) == int(p) for tp, pp, p in box)
        ev["unresolved_rows"] = int(sum(pp["unresolved"] for _, pp, _ in box if pp is not None))
        if r.game_universe != WC.TRAINING_UNIVERSE:
            d = ("EXCLUDED", "NOT_IN_TRAINING_UNIVERSE")
        elif status in NOT_PLAYED_STATUSES:
            d = ("EXCLUDED", "NOT_PLAYED")
        elif not ev["has_result"]:
            d = ("DEFERRED", "NOT_FINAL_AT_CUTOFF")
        elif status is not None and status != WC.FINAL_STATUS:
            d = ("DEFERRED", "STATUS_NOT_FINAL")
        elif pd.isna(ev["knowable_at"]) or ev["knowable_at"] > cut or pd.isna(ev["outcome_available_at"]) \
                or ev["outcome_available_at"] > cut:
            d = ("DEFERRED", "NOT_KNOWABLE_AT_CUTOFF")
        elif not ev["box_complete"]:
            d = ("DEFERRED", "BOX_INCOMPLETE")
        elif not ev["box_reconciled"]:
            d = ("DEFERRED", "BOX_UNRECONCILED")
        elif ev["unresolved_rows"]:
            d = ("DEFERRED", "IDENTITY_UNRESOLVED")
        else:
            d = ("INCLUDED", "")
        rows.append({**ev, "decision": d[0], "reason": d[1]})
    return pd.DataFrame(rows, columns=COLUMNS)


def summary(el: pd.DataFrame, previous_included=()) -> dict:
    """The explicit counts of the daily training manifest (directive section 5)."""
    prev = set(previous_included)
    inc = el[el["decision"] == "INCLUDED"]
    return {"EXPECTED_GAMES": int(len(el)), "FINALIZED_GAMES": int(el["has_result"].sum()) if len(el) else 0,
            "INCLUDED_GAMES": int(len(inc)), "NEWLY_INCLUDED_GAMES": int((~inc["game_id"].isin(prev)).sum()),
            "DEFERRED_GAMES": int((el["decision"] == "DEFERRED").sum()),
            "EXCLUDED_GAMES": int((el["decision"] == "EXCLUDED").sum()),
            "EXCLUSION_REASON": {f"{d}:{r}": int(n) for (d, r), n in
                                 el[el["decision"] != "INCLUDED"].groupby(["decision", "reason"]).size().items()},
            "DEFERRED_GAME_IDS": sorted(el.loc[el["decision"] == "DEFERRED", "game_id"]),
            "NO_LONGER_INCLUDED": sorted(prev - set(inc["game_id"]))}
