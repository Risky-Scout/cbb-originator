"""ISOLATED PRE-SEASON REHEARSAL (architect Approach 2, 2026-10-23): ONE real 2025-26 NCAAB event, its REAL historical
The Odds API player-prop payload, pushed through the PRODUCTION quote / pricing / settlement / monitoring components.

NOT production and not on the scheduler's path. The production layer is intentionally 2026-27 only and is NOT changed:
no season guard is widened, odds_acquire.py keeps refusing the historical endpoint, run.daily / slate.build keep their
2027 filters, and nothing here is imported by cbb_platform/prod. This module only COMPOSES production functions in the
order run.daily / shadow_tick use them, on a 2025-26 game rebuilt point-in-time by the validated replay path
(prod.features.build masked at the as-of, target outcomes stripped). Every output goes to an isolated work root
(--work; never data/prod, never the ops-state branch).

    select   pre-registered data-availability rule over the frozen canonical DB (written BEFORE any odds or model output)
    fetch    1 historical events listing + 1 historical event-odds snapshot at the prediction as-of (one-off utility;
             at most 2 events ever, enforced by the work-root request ledger); the key is never logged
    run      frozen model (hash-verified) -> point-in-time features (leakage checks) -> ONE 20,000-draw bank -> real
             payload: quotes.normalize -> matching (strict) -> board freshness -> board.build (pricing, best price)
             -> ledger + side monitor -> odds_feed.validation_checks -> shadow_tick.scoring_evidence / _register
             -> shadow_tick.step_settle (real final box) -> prospective_monitor.write_reports, on an isolated ops root
    cleanup  delete the work root (decrypted / temporary data); the compact report stays in docs/rehearsal

MODEL_PARAMETER_HOLDOUT is FALSE: JM1_FINAL was fit through 2025-26, so this game is in-sample for the parameters. This
validates PIPELINE MECHANICS only; no number here is prospective performance evidence.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests

from cbb_platform import credentials
from cbb_platform.prod import (board as BO, config as PC, features as PF, inputs as IN, ledger as LG, loader as LD,
                               matching as MT, odds_feed as OF, ops_store as OS, pricing as PX, prospective_monitor as PM,
                               quotes as QT, readiness as RD, run as RN, shadow_tick as TK, side_monitor as SD,
                               simulate as SM, slate as SL, universe as UV)

REHEARSAL_VERSION = "cbb-rehearsal-historical-payload-1.0.0"
SEASON = 2026                                   # 2025-26 (in the frozen history; never the production season)
CANONICAL = PC.HISTORY_CANONICAL
HIGH_MAJOR = ("ACC", "Big Ten", "Big 12", "SEC", "Big East")
SELECT_FROM_TIP = "2026-02-10"                  # the validated replay window (tests/test_prod_replay.py)
AS_OF_BEFORE_CUTOFF_MIN = 18                    # the second scheduled attempt (:12 / :42 cadence -> cutoff - 18 min)
MAX_EVENTS = 2
BASE = "https://api.the-odds-api.com"
DOCS = PC.C.PROJECT_ROOT / "docs" / "rehearsal"
LIVE = ("FETCHED", "FETCHED_UNCHANGED", "FAILED", "ATTEMPT_FAILED")


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _iso(t) -> str:
    return _utc(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _guard(work: Path) -> Path:
    w = Path(work).resolve()
    for bad in (PC.PROD_ROOT.resolve(), PC.OPS_ROOT.resolve()):
        if w == bad or bad in w.parents or w in bad.parents:
            raise RuntimeError(f"rehearsal work root {w} overlaps production path {bad}")
    return w


def _j(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, default=str))


# ============================================================================ 1. selection (data availability only)
SELECT_SQL = f"""
WITH hm AS (SELECT canonical_conference_id FROM core.conferences WHERE abbreviation IN {HIGH_MAJOR}),
pb AS (SELECT canonical_game_id, count(*) n, count(DISTINCT canonical_team_id) t,
              count(*) FILTER (WHERE canonical_player_id IS NULL) unidentified
       FROM flagship.player_game_outcomes WHERE season = {SEASON} GROUP BY 1),
tb AS (SELECT canonical_game_id, count(*) n FROM flagship.team_game_outcomes WHERE season = {SEASON} GROUP BY 1),
sb AS (SELECT canonical_game_id, count(*) n FROM flagship.substitutions GROUP BY 1)
SELECT g.canonical_game_id, g.scheduled_tip_utc, g.game_date_et, g.home_team_id, g.away_team_id, g.season_type,
       pb.n AS player_box_rows, sb.n AS substitution_rows
FROM flagship.games g JOIN pb USING (canonical_game_id) JOIN tb USING (canonical_game_id) JOIN sb USING (canonical_game_id)
WHERE g.season = {SEASON} AND g.game_universe = 'DI_VS_DI' AND g.tip_time_quality = 'EXACT'
  AND g.season_type = 'regular' AND NOT coalesce(g.neutral_site, FALSE)
  AND g.scheduled_tip_utc >= TIMESTAMPTZ '{SELECT_FROM_TIP} 00:00:00+00'
  AND g.home_points IS NOT NULL AND g.has_team_box_score AND g.has_player_box_score
  AND g.home_conference_id IN (SELECT * FROM hm) AND g.away_conference_id IN (SELECT * FROM hm)
  AND pb.t = 2 AND pb.unidentified = 0 AND tb.n = 2
ORDER BY g.scheduled_tip_utc, g.canonical_game_id
LIMIT {MAX_EVENTS}
"""


def select(work: Path) -> dict:
    """Pre-registered rule (written before any odds request or model output): the EARLIEST 2025-26 regular-season,
    D-I vs D-I, non-neutral game with an EXACT tip on or after the validated replay window start, both teams in a
    high-major conference (the provider's player-prop coverage proxy), team + player box scores for both teams, a
    substitution feed and every box-score player canonically identified. Ordered by tip, then canonical id. The
    second row is the ONLY allowed fallback (used only if the first event's payload has no player-prop market)."""
    work = _guard(work)
    con = duckdb.connect(str(CANONICAL), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    rows = con.execute(SELECT_SQL).df()
    names = con.execute("SELECT canonical_team_id, any_value(display_name) n FROM core.teams GROUP BY 1").df() \
        .set_index("canonical_team_id")["n"].to_dict()
    con.close()
    if not len(rows):
        raise RuntimeError("no game satisfies the pre-registered selection rule")
    out = {"rehearsal_version": REHEARSAL_VERSION, "selected_at": datetime.now(timezone.utc).isoformat(),
           "rule": select.__doc__.split("\n\n")[0].replace("\n    ", " "), "rule_sql_sha256": _sha(SELECT_SQL.encode()),
           "selection_basis": "DATA AVAILABILITY / INTEGRATION COVERAGE ONLY (no odds, no model output inspected)",
           "candidates": [{"rank": i + 1, "canonical_game_id": r.canonical_game_id, "tip_utc": _iso(r.scheduled_tip_utc),
                           "game_date_et": str(pd.Timestamp(r.game_date_et).date()),
                           "game": f"{names.get(r.away_team_id)} @ {names.get(r.home_team_id)}",
                           "player_box_rows": int(r.player_box_rows), "substitution_rows": int(r.substitution_rows)}
                          for i, r in enumerate(rows.itertuples(index=False))]}
    _j(work / "selection.json", out)
    return out


def _game(gid: str) -> dict:
    con = duckdb.connect(str(CANONICAL), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    g = con.execute("SELECT * FROM flagship.games WHERE canonical_game_id = ?", [gid]).df().iloc[0].to_dict()
    con.close()
    cut, rule = SL.feature_cutoff(_utc(g["scheduled_tip_utc"]), g["tip_time_quality"], g["game_date_et"])
    return {**g, "scheduled_tip_utc": _utc(g["scheduled_tip_utc"]), "cutoff": cut, "cutoff_rule": rule,
            "as_of": cut - pd.Timedelta(minutes=AS_OF_BEFORE_CUTOFF_MIN)}


# ============================================================================ 2. one-off historical acquisition
def _ledger(work: Path) -> list[dict]:
    return OS.read_jsonl(Path(work) / "odds_historical_requests.jsonl")


def _get(work: Path, kind: str, path: str, params: dict) -> dict:
    """One historical request. The key is sent only in the query string and never reaches params / ledger / errors."""
    key = credentials.get_strict("ODDS_API_KEY")
    t = datetime.now(timezone.utc)
    try:
        r = requests.get(BASE + path, params={**params, "apiKey": key}, timeout=30)
    except requests.RequestException as e:
        raise RuntimeError(f"historical {kind} request failed ({type(e).__name__})") from None
    body = r.content
    h = {k: r.headers.get(k) for k in ("x-requests-remaining", "x-requests-used", "x-requests-last")}
    ent = {"kind": kind, "endpoint": path, "params": params, "requested_at": t.isoformat(), "http_status": r.status_code,
           "response_sha256": _sha(body), "response_bytes": len(body), "credits_charged": int(h["x-requests-last"] or 0),
           "quota_remaining": int(h["x-requests-remaining"]) if h["x-requests-remaining"] else None,
           "quota_used": int(h["x-requests-used"]) if h["x-requests-used"] else None}
    raw = Path(work) / "raw" / f"{kind}_{t.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(body)
    ent["raw_path"] = str(raw.relative_to(work))
    OS.append_jsonl(Path(work) / "odds_historical_requests.jsonl", [ent])
    if r.status_code != 200:
        raise RuntimeError(f"historical {kind} HTTP {r.status_code}")
    return {**ent, "body": json.loads(body)}


def fetch(work: Path, rank: int = 1) -> dict:
    """Historical events listing at the as-of (strict production event matching), then ONE historical event-odds
    snapshot at the as-of for the documented NCAAB base markets, region us (the collector's request shape)."""
    work = _guard(work)
    sel = json.loads((work / "selection.json").read_text())
    done = {e["params"].get("_event_rank") for e in _ledger(work) if e["kind"] == "event_odds"}
    if rank in done:
        raise RuntimeError(f"event rank {rank} already acquired: never re-bought")
    if len(done) >= MAX_EVENTS or rank > MAX_EVENTS:
        raise RuntimeError(f"at most {MAX_EVENTS} historical events may be queried")
    if rank == 2:
        f = json.loads((work / "fetch_1.json").read_text())
        if f["prop_markets_returned"]:
            raise RuntimeError("fallback refused: the first event returned player-prop markets")
    c = sel["candidates"][rank - 1]
    g = _game(c["canonical_game_id"])
    tip, aso = g["scheduled_tip_utc"], g["as_of"]
    sport = f"/v4/historical/sports/{QT.SPORT_KEY}"
    ev = _get(work, "events", f"{sport}/events", {"date": _iso(aso), "dateFormat": "iso",
                                                 "commenceTimeFrom": _iso(tip - pd.Timedelta(hours=3)),
                                                 "commenceTimeTo": _iso(tip + pd.Timedelta(hours=3))})
    events = pd.DataFrame([{"provider_event_id": e.get("id"), "provider_home_team": e.get("home_team"),
                            "provider_away_team": e.get("away_team"), "provider_commence_time": _utc(e.get("commence_time"))}
                           for e in ev["body"].get("data") or []], columns=OF.EVENT_COLS)
    teams, _ = RN._names(CANONICAL)
    tidx = MT.team_index(teams, None)
    mev = OF.match_events(events, _slate(g), tidx)
    hit = mev[mev["canonical_game_id"] == g["canonical_game_id"]]
    out = {"rank": rank, "canonical_game_id": g["canonical_game_id"], "as_of": aso.isoformat(),
           "events_snapshot_timestamp": ev["body"].get("timestamp"), "events_listed": int(len(events)),
           "events_credits": ev["credits_charged"],
           "event_match": mev.assign(provider_commence_time=mev["provider_commence_time"].astype(str)).to_dict(orient="records")}
    if len(hit) != 1:
        out.update({"status": "EVENT_NOT_RESOLVED", "prop_markets_returned": []})
        _j(work / f"fetch_{rank}.json", out)
        return out
    eid = hit["provider_event_id"].iat[0]
    od = _get(work, "event_odds", f"{sport}/events/{eid}/odds",
              {"date": _iso(aso), "regions": PC.ODDS_REGIONS, "markets": ",".join(QT.REQUEST_MARKETS),
               "oddsFormat": "american", "dateFormat": "iso", "includeLinks": "true", "includeSids": "true"})
    led = _ledger(work)                                    # tag the ledger row with the rank (not sent to the provider)
    led[-1]["params"]["_event_rank"] = rank
    (work / "odds_historical_requests.jsonl").write_text("".join(OS.canon(e) + "\n" for e in led))
    body = od["body"].get("data") or {}
    snap = od["body"].get("timestamp")
    books = body.get("bookmakers") or []
    returned = sorted({m.get("key") for b in books for m in b.get("markets") or []})
    coll = work / "collections" / f"{eid}_{_utc(snap).strftime('%Y%m%dT%H%M%SZ')}.json"
    _j(coll, {"label": "HISTORICAL REHEARSAL The Odds API event odds (2025-26 NCAAB; NOT live)",
              "provider": QT.PROVIDER, "collector_received_at": snap, "request_sha256": od["response_sha256"],
              "fetched_at_wallclock": od["requested_at"],
              "events": [{**body, "collector_received_at": snap}]})
    out.update({"status": "FETCHED", "event_id": eid, "snapshot_timestamp": snap,
                "previous_timestamp": od["body"].get("previous_timestamp"), "next_timestamp": od["body"].get("next_timestamp"),
                "bookmakers": [b.get("key") for b in books], "prop_markets_returned": [k for k in returned if k in QT.KEY_TO_STAT],
                "unsupported_keys_returned": [k for k in returned if k not in QT.KEY_TO_STAT],
                "event_odds_credits": od["credits_charged"], "quota_remaining": od["quota_remaining"],
                "response_sha256": od["response_sha256"], "collection": str(coll.relative_to(work))})
    _j(work / f"fetch_{rank}.json", out)
    return out


# ============================================================================ 3. pipeline (production components)
def _slate(g: dict) -> pd.DataFrame:
    """The slate row run.daily would hold for this game (slate.SLATE_COLS; slate.build itself only reads 2026-27)."""
    return pd.DataFrame([{"canonical_game_id": g["canonical_game_id"], "provider_game_id": g["source_record_key"],
                          "season": int(g["season"]), "start_time": g["scheduled_tip_utc"],
                          "tip_time_quality": g["tip_time_quality"], "game_date_et": g["game_date_et"],
                          "home_team_id": g["home_team_id"], "away_team_id": g["away_team_id"],
                          "neutral_site": bool(g["neutral_site"]) if pd.notna(g["neutral_site"]) else False,
                          "canonical_venue_id": g["canonical_venue_id"], "game_universe": g["game_universe"],
                          "prediction_as_of": g["as_of"], "feature_cutoff": g["cutoff"], "cutoff_rule": g["cutoff_rule"],
                          "schedule_as_of": g["as_of"], "slate_status": "SCORE", "suppression_reasons": []}],
                        columns=SL.SLATE_COLS)


def leakage_checks(cdb: Path, gid: str, aso, cand: pd.DataFrame, cutoff) -> dict:
    """The as-of canonical the features were built from holds nothing about the target game's outcome and nothing
    that became knowable after the as-of."""
    con = duckdb.connect(str(cdb), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    q = lambda s: con.execute(s, [gid]).fetchone()[0]               # noqa: E731
    a = TK._iso(aso)
    c = {"target_result_masked": q("SELECT count(*) FROM flagship.games WHERE canonical_game_id = ? AND (home_points IS NOT NULL OR away_points IS NOT NULL OR has_player_box_score OR has_team_box_score)") == 0,
         "target_player_box_absent": q("SELECT count(*) FROM flagship.player_game_outcomes WHERE canonical_game_id = ?") == 0,
         "target_team_box_absent": q("SELECT count(*) FROM flagship.team_game_outcomes WHERE canonical_game_id = ?") == 0,
         "target_substitutions_absent": q("SELECT count(*) FROM flagship.substitutions WHERE canonical_game_id = ?") == 0,
         "no_player_outcome_after_as_of": con.execute(f"SELECT count(*) FROM flagship.player_game_outcomes WHERE historical_knowable_at > TIMESTAMPTZ '{a}'").fetchone()[0] == 0,
         "no_team_outcome_after_as_of": con.execute(f"SELECT count(*) FROM flagship.team_game_outcomes WHERE historical_knowable_at > TIMESTAMPTZ '{a}'").fetchone()[0] == 0,
         "no_result_tipped_after_as_of": con.execute(f"SELECT count(*) FROM flagship.games WHERE home_points IS NOT NULL AND scheduled_tip_utc >= TIMESTAMPTZ '{a}'").fetchone()[0] == 0,
         "feature_cutoff_is_tip_minus_1h": bool(len(cand)) and bool((pd.to_datetime(cand["prediction_cutoff_utc"], utc=True) == cutoff).all()),
         "candidate_season_is_target": bool(len(cand)) and bool((cand["season"] == SEASON).all())}
    con.close()
    return {"pass": all(c.values()), **c}


def _pricing_checks(priced: list[dict], dist: pd.DataFrame, sims: int) -> dict:
    """Re-derive every priced row from its own pmf (pricing.price_line definitions: P(over)=P(Y>line), P(push)=P(Y=line))."""
    pm = {(r.game_id, r.player_id, r.market): r.pmf for r in dist.itertuples(index=False)}
    bad, n = [], 0
    for p in priced:
        if p.get("line") is None or p.get("probability") is None:
            continue
        n += 1
        pmf = np.asarray(pm[(p["game_id"], p["player_id"], p["market"])], dtype=float)
        k = np.arange(len(pmf))
        over, push = float(pmf[k > p["line"]].sum()), float(pmf[k == p["line"]].sum())
        under = 1.0 - over - push
        win = over if p["side"] == "OVER" else under
        lose = under if p["side"] == "OVER" else over
        ok = abs(p["probability"] - win) < 1e-9 and abs(p["push_probability"] - push) < 1e-9 \
            and abs(p["lose_probability"] - lose) < 1e-9 and abs(win + lose + push - 1) < 1e-9
        if p.get("fair_decimal") is not None and win + lose > 0:
            ok &= abs(p["fair_decimal"] - (win + lose) / win) < 1e-6 if win > 0 else True
        if p.get("best_decimal_odds") is not None:
            ok &= abs(p["edge"] - (win * (p["best_decimal_odds"] - 1) - lose)) < 1e-9
        if not ok:
            bad.append({k2: p.get(k2) for k2 in ("player", "market", "line", "side", "probability", "fair_decimal", "edge")})
    return {"pass": n > 0 and not bad, "rows_checked": n, "mismatches": bad[:10]}


def _hash_tree(paths) -> dict:
    out = {}
    for p in paths:
        p = Path(p)
        if p.exists():
            b = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
            out[p.name] = _sha(b)
    return out


def run(work: Path, rank: int = 1, sims: int = PC.SIMS) -> dict:
    work = _guard(work)
    sel = json.loads((work / "selection.json").read_text())
    f = json.loads((work / f"fetch_{rank}.json").read_text())
    g = _game(sel["candidates"][rank - 1]["canonical_game_id"])
    gid, aso, tip = g["canonical_game_id"], g["as_of"], g["scheduled_tip_utc"]
    ops, prod = work / "ops", work / "prod"
    rep: dict = {"rehearsal_version": REHEARSAL_VERSION, "canonical_game_id": gid, "prediction_as_of": aso.isoformat(),
                 "target_cutoff": g["cutoff"].isoformat(), "scheduled_tip": tip.isoformat(), "fetch": f,
                 "model_parameter_holdout": False,
                 "model_parameter_note": "JM1_FINAL was fit through 2025-26: this game is in-sample for the parameters; "
                                         "pipeline mechanics only, never performance evidence"}
    # ---------------------------------------------------------------- frozen model (hash-verified, never refit)
    ident = LD.verify()
    model = LD.load()
    rep["frozen_model"] = {"gate": "PASS", "artifact_sha256": ident["artifact_sha256"],
                           "frozen_commit": model.identity["frozen_commit"]}
    # ---------------------------------------------------------------- point-in-time features (validated replay path)
    tid = "K" + aso.strftime("%Y%m%dT%H%M%SZ") + "-rehearsal"
    run_id = "R" + aso.strftime("%Y%m%dT%H%M%SZ") + "-rehearsal"
    run_dir = prod / "runs" / run_id
    tdir = ops / "ticks" / tid
    tdir.mkdir(parents=True, exist_ok=True)
    fr = PF.build(run_dir / "features", [gid], SEASON, aso, model.marginal_cfg, CANONICAL, None)
    cdb = run_dir / "features" / "canonical_asof.duckdb"
    cand = fr["cand"]
    rep["point_in_time"] = leakage_checks(cdb, gid, aso, cand, g["cutoff"])
    teams, players = RN._names(cdb)
    universe = UV.build(fr["features_db"], cdb, None, "FROZEN_CANONICAL_REHEARSAL")
    universe.to_csv(run_dir / "player_universe.csv", index=False)
    roster_sup = UV.suppression_map(universe)
    tgames = SD.pregame_team_games(fr["features_db"], [gid])
    # ---------------------------------------------------------------- model inputs -> ONE joint bank (run.daily order)
    rows = IN.marginal_rows(cand, model.marginal_cfg, model.v0_state, model.participation_state)
    envd = IN.environment(fr["team"], model.jm1["struct"]["env"])
    rows, _ = IN.apply_availability(rows, None)
    rows.drop(columns=["_scored"]).to_parquet(run_dir / "model_inputs.parquet", index=False)
    tname = teams.set_index("canonical_team_id")["display_name"].to_dict()
    pname = players.set_index("canonical_player_id")["display_name"].to_dict()
    label = f"{tname.get(g['away_team_id'])} @ {tname.get(g['home_team_id'])} {pd.Timestamp(g['game_date_et']).date()}"
    gp = rows[rows["game_id"] == gid]
    bank = SM.simulate_game(model.jm1, gid, gp, envd, sims)
    same = SM.same_draw_check(bank)
    md = SM.market_distributions(bank)
    md["game_label"], md["player_label"] = label, md["player_id"].map(lambda p: pname.get(p, p))
    md["bank_sha256"], md["player_suppression"] = bank["bank_sha256"], ""
    rs = md["player_id"].map(lambda p: roster_sup.get((gid, p), ""))
    md.loc[rs != "", "method"] = "NOT_PRICED"
    md["player_suppression"] = rs
    dists = [md]
    for r in gp[gp["player_suppression"] != ""].itertuples(index=False):
        dists.append(pd.DataFrame([{"game_id": gid, "team_id": r.team_id, "player_id": r.player_id, "market": m,
                                    "pmf": np.array([1.0]), "projection": float("nan"), "bank_mean": float("nan"),
                                    "method": "NOT_PRICED", "p_play": float(r.p_play), "game_label": label,
                                    "player_label": pname.get(r.player_id, r.player_id), "bank_sha256": bank["bank_sha256"],
                                    "player_suppression": r.player_suppression} for m in PC.MARKETS]))
    dist = pd.concat(dists, ignore_index=True)
    dist["game_reasons"] = [[] for _ in range(len(dist))]
    cands = gp[["game_id", "team_id", "player_id"]].assign(display_name=gp["player_id"].map(lambda p: pname.get(p)))
    banks = {gid: {"bank_sha256": bank["bank_sha256"], "priced_players": int(len(bank["rows"])),
                   "corr_min_eig": float(bank["corr_min_eig"]), "jitter": float(bank["jitter"])}}
    rep["joint_bank"] = {"sims": sims, "banks_drawn": 1, "bank_sha256": bank["bank_sha256"], "same_draw_check": bool(same),
                         "players_in_bank": int(len(bank["rows"])),
                         "markets_from_bank": sorted(md["market"].unique().tolist()),
                         "distinct_bank_hashes_in_distributions": int(dist["bank_sha256"].nunique())}
    # ---------------------------------------------------------------- REAL payload -> normalizer -> strict matching
    coll = work / f["collection"]
    raw = QT.load_collected(coll)
    quotes = QT.typed(raw)
    tidx = MT.team_index(teams, None)
    slate = _slate(g)
    quotes = MT.match_games(quotes, slate, tidx)
    quotes = MT.match_players(quotes, cands, None)
    quotes = BO.quote_freshness(quotes, aso, {gid: tip})
    quotes.to_csv(run_dir / "quotes_normalized.csv", index=False)
    lst = {"events": pd.DataFrame([{"provider_event_id": f["event_id"], "provider_home_team": raw["provider_home_team"].iat[0],
                                    "provider_away_team": raw["provider_away_team"].iat[0],
                                    "provider_commence_time": raw["provider_commence_time"].iat[0]}]),
           "window": (tip - pd.Timedelta(hours=3), tip + pd.Timedelta(hours=3)),
           "received_at": _utc(f["events_snapshot_timestamp"]), "sha256": None}
    cov = OF.coverage(slate, quotes, lst, tidx)
    run_reasons: list = []
    meta = {"run_id": run_id, "prediction_as_of": aso, "model_version": model.identity["model_version"], "sims": sims,
            "run_reasons": run_reasons, "no_quote_reason": cov["row_reason"]}
    dist["game_reasons"] = dist["game_id"].map(lambda x: sorted(set(cov["game_reasons"].get(x, []))))
    board, priced = BO.build(dist, quotes, meta)
    board.to_csv(run_dir / "board.csv", index=False)
    # ---------------------------------------------------------------- ledger (run.daily record bodies) + side monitor
    sl = slate.set_index("canonical_game_id")
    recs = [{"record_type": "RUN", "body": {"run_id": run_id, "prediction_as_of": aso, "model_identity": model.identity,
                                            "state_version": "REHEARSAL_FROZEN_CANONICAL_2025_26_ASOF",
                                            "readiness_state": RD.evaluate(run_reasons)["readiness_state"],
                                            "run_reasons": run_reasons, "data_label": "HISTORICAL_REHEARSAL (NOT live)",
                                            "banks": banks, "validated_prediction_cutoff": PC.VALIDATED_PREDICTION_CUTOFF,
                                            "wagering_enabled": PC.WAGERING_ENABLED}}]
    for p in priced:
        recs.append({"record_type": "PREDICTION", "body": {
            "prediction_id": LG.prediction_id(run_id, p["game_id"], p["player_id"], p["market"], p["line"], p["side"]),
            "run_id": run_id, "prediction_as_of": aso, "feature_cutoff": sl.loc[p["game_id"], "feature_cutoff"],
            "model_version": model.identity["model_version"], "artifact_sha256": model.identity["artifact_sha256"],
            "production_manifest": model.identity["production_manifest"],
            "state_version": "REHEARSAL_FROZEN_CANONICAL_2025_26_ASOF",
            "game_id": p["game_id"], "game": p["game"], "player_id": p["player_id"], "player": p["player"],
            "team_id": p["team_id"], "market": p["market"], "line": p["line"], "side": p["side"],
            "projection": None if pd.isna(p["projection"]) else p["projection"], "probability": p.get("probability"),
            "push_probability": p.get("push_probability"), "p_nonpush": p.get("p_nonpush"), "p_play": p["p_play"],
            "fair_decimal": p.get("fair_decimal"), "fair_american": p.get("fair_american"),
            "best_decimal_odds": p.get("best_decimal_odds"), "best_american_odds": p.get("best_american_odds"),
            "sportsbook": p.get("sportsbook"), "edge": p.get("edge"), "quote_available_at": p.get("quote_available_at"),
            "collector_received_at": p.get("collector_received_at"), "readiness_state": p["readiness_state"],
            "eligibility_state": p["eligibility_state"], "suppression_reason": p["suppression_reason"],
            "distribution_method": p["distribution_method"], "pmf_q75": p["pmf_q75"], "mcse": p.get("mcse"),
            "bank_sha256": p["bank_sha256"], "data_label": "HISTORICAL_REHEARSAL (NOT live)"}})
    led = tdir / "shadow_ledger.jsonl"
    rep["ledger_append"] = LG.append(recs, led)
    rep["ledger_verify"] = LG.verify(led)
    expo = SD.exposure_frame(rows, tgames)
    sides = SD.quote_side_records(dist, quotes, expo, {**meta, "data_label": "HISTORICAL_REHEARSAL (NOT live)",
                                                       "game_dates": sl["game_date_et"].astype(str).to_dict()})
    rep["side_monitor_append"] = SD.append([{"record_type": "QUOTE_SIDE", "body": b} for b in sides], tdir / "side_monitor.jsonl")
    # ---------------------------------------------------------------- production real-event implementation checks
    vc = OF.validation_checks([coll], run_dir, led)
    rep["validation_checks"] = vc
    rep["pricing_checks"] = _pricing_checks(priced, dist, sims)
    q = quotes[quotes["stat"].notna()]
    rep["quotes"] = {"rows": int(len(quotes)), "prop_rows": int(len(q)),
                     "by_status": quotes.groupby(["quote_status", "suppression_reason"]).size().rename("n").reset_index().to_dict(orient="records"),
                     "books": sorted(quotes["sportsbook"].unique().tolist()),
                     "markets": sorted(q["market_key"].unique().tolist()),
                     "with_deep_link": int((q["deep_link"].fillna("") != "").sum()),
                     "unmatched_players": sorted(q.loc[q["suppression_reason"] == "UNMATCHED_PLAYER", "provider_player_name"].unique().tolist()),
                     "ambiguous_players": sorted(q.loc[q["suppression_reason"] == "AMBIGUOUS_PLAYER", "provider_player_name"].unique().tolist()),
                     "game_match": sorted(quotes["canonical_game_id"].dropna().unique().tolist())}
    # ---------------------------------------------------------------- tick-format evidence (shadow_tick, isolated ops root)
    ctx = TK.Ctx(ops_root=ops, prod_root=prod, history_db=CANONICAL, clock=lambda: aso, sims=sims)
    OS.write_json(ops / "OPERATION.json", {"operation": "ISOLATED HISTORICAL REHEARSAL (2025-26; NOT the 2026-27 shadow)",
                                           "operation_started_at": aso.isoformat(), "frozen_commit": PC.FROZEN_COMMIT})
    OS.append_jsonl(ops / "registry" / "quote_snapshots.jsonl",
                    [{"event_id": f["event_id"], "game_id": gid, "status": "FETCHED_HISTORICAL", "tick_id": tid,
                      "received_at": f["snapshot_timestamp"], "response_sha256": f["response_sha256"],
                      "credits": f["event_odds_credits"], "bookmakers": len(f["bookmakers"]),
                      "markets_returned": len(f["prop_markets_returned"]), "collection": coll.name}])
    timing = {gid: {"scheduled_tip": TK._iso(tip), "target_cutoff": TK._iso(g["cutoff"]), "cutoff_rule": g["cutoff_rule"],
                    "prediction_as_of": aso.isoformat(), "executed_at": datetime.now(timezone.utc).isoformat(),
                    "timing_deviation_seconds": float((g["cutoff"] - aso).total_seconds()),
                    "timing_note": "prediction made BEFORE the cutoff by timing_deviation_seconds"}}
    sch = _season_schedule()
    s = {"run_dir": str(run_dir), "game_reasons": {gid: []}, "predictions": len(recs) - 1}
    ev = TK.scoring_evidence(ctx, tdir, s, timing, sch)
    rep["evidence"] = ev
    srow = namedtuple("S", "canonical_game_id scheduled_tip_utc cutoff")(gid, tip, g["cutoff"])
    tick = {"tick_id": tid}
    TK._register(ctx, gid, "PREDICTED", srow, tick, extra={**timing[gid], "run_id": run_id, "bank_sha256": bank["bank_sha256"],
                                                          "game_reasons": [], "readiness_state": "SHADOW_ONLY"})
    OS.write_json(tdir / "tick.json", {**tick, "rehearsal": True, "prediction_as_of": aso.isoformat()})
    OS.chain_append(ops, tid)
    proj = pd.read_parquet(tdir / "projections.parquet")
    rep["joint_bank"]["projection_rows_bank_hashes"] = int(proj["bank_sha256"].nunique())
    pre_files = [tdir / n for n in ("shadow_ledger.jsonl.gz", "projections.parquet", "predictions_quoted.parquet",
                                    "quote_sides.parquet", "side_monitor.jsonl.gz")]
    pre_hash = _hash_tree(pre_files)
    rep["pregame_hashes"] = pre_hash
    # ---------------------------------------------------------------- postgame: real final box -> production settlement
    con = duckdb.connect(str(CANONICAL), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    known = _utc(con.execute("SELECT max(historical_knowable_at) FROM flagship.player_game_outcomes WHERE canonical_game_id = ?",
                             [gid]).fetchone()[0])
    con.close()
    settle_at = known + pd.Timedelta(minutes=30)
    rep["outcome_chronology"] = {"outcome_knowable_at": known.isoformat(), "prediction_as_of": aso.isoformat(),
                                 "outcome_after_prediction": bool(known > aso), "settled_at": settle_at.isoformat()}
    ctx.clock = lambda: settle_at
    sdir = ops / "ticks" / ("K" + settle_at.strftime("%Y%m%dT%H%M%SZ") + "-rehearsal")
    sdir.mkdir(parents=True, exist_ok=True)
    st = {"increment_db": CANONICAL, "manifest": {"state_version": "REHEARSAL_FROZEN_CANONICAL_2025_26_FINAL"}}
    t2 = {"tick_id": sdir.name}
    TK.step_settle(ctx, {"settle": [(gid, "FINAL")]}, st, t2, sdir)
    OS.write_json(sdir / "tick.json", {**t2, "rehearsal": True})
    OS.chain_append(ops, sdir.name)
    rep["settlement"] = t2["settlement"]
    rep["settlement_checks"] = settlement_checks(sdir, tdir, CANONICAL, gid)
    chk = work / "ledger_after_settlement.jsonl"                    # the gzipped pregame ledger, re-verified as a chain
    chk.write_bytes(gzip.decompress((tdir / "shadow_ledger.jsonl.gz").read_bytes()))
    lv = LG.verify(chk)
    after = _hash_tree(pre_files)
    rep["pregame_immutable"] = {"pass": after == pre_hash and bool(lv.get("CHAIN_VALID")),
                                "ledger_chain_after_settlement": lv, "after": after}
    # second settlement attempt: the production plan's filter (settled_games) excludes it
    rep["settlement_idempotence"] = {"game_in_settled_registry": gid in TK.settled_games(ctx),
                                     "settlement_registry_rows": len(OS.read_jsonl(ops / "registry" / "settlements.jsonl"))}
    # ---------------------------------------------------------------- reports (prospective_monitor, isolated ops root)
    now = (settle_at.tz_convert("America/New_York").normalize() + pd.Timedelta(days=1)).tz_convert("UTC") + pd.Timedelta(hours=13)
    OS.mirror_out(ops, prod)
    r1 = PM.write_reports(ops, now, aso)
    d1 = _hash_tree(sorted((ops / "reports").rglob("*.*")))
    r2 = PM.write_reports(ops, now, aso)
    d2 = _hash_tree(sorted((ops / "reports").rglob("*.*")))
    cum = json.loads((ops / "reports" / "cumulative.json").read_text())
    rep["reports"] = {"first": r1, "second_identical": d1 == d2, "files": sorted(d1),
                      "chain": OS.verify_chain(ops)}
    rep["monitor_checks"] = monitor_checks(cum, ops)
    _j(work / f"run_{rank}.json", rep)
    return rep


def _season_schedule() -> pd.DataFrame:
    con = duckdb.connect(str(CANONICAL), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    df = con.execute(f"""SELECT canonical_game_id, game_universe, game_date_et FROM flagship.games
                         WHERE season = {SEASON}""").df()
    con.close()
    return df


def settlement_checks(sdir: Path, tdir: Path, cdb: Path, gid: str) -> dict:
    """Independent re-derivation of every settled row from the real final box (outcome, side result, DNP -> NO_ACTION,
    Brier / log loss at the model's no-push probability)."""
    con = duckdb.connect(str(cdb), read_only=True)
    box = con.execute(f"""SELECT canonical_player_id AS player_id, minutes::DOUBLE AS minutes,
                                 {", ".join(f"{c}::DOUBLE AS {s}" for s, c in TK.STAT_COLS.items())}
                          FROM flagship.player_game_outcomes WHERE canonical_game_id = ?""", [gid]).df().set_index("player_id")
    con.close()
    with gzip.open(sdir / "settlements.jsonl.gz", "rt") as fh:
        st = [json.loads(l)["body"] for l in fh if l.strip()]
    bad, kinds = [], {}
    for b in st:
        pid = b["player_id"]
        played = pid in box.index and (box.loc[pid, "minutes"] or 0) > 0
        y = None if not played else float(sum(box.loc[pid, k] for k in PC.COMBOS.get(b["market"], (b["market"],))))
        exp_out = "NO_ACTION" if y is None else ("PUSH" if y == b["line"] else ("OVER" if y > b["line"] else "UNDER"))
        exp_res = "NO_ACTION" if y is None else ("PUSH" if exp_out == "PUSH" else ("WIN" if exp_out == b["side"] else "LOSS"))
        ok = b["outcome"] == exp_out and b["side_result"] == exp_res and (b["actual"] == y)
        if y is None:
            ok &= b["participation"] == "DNP" and b["brier"] is None and b["log_loss"] is None
        elif exp_res in ("WIN", "LOSS") and b["predicted_probability_nonpush"] is not None:
            w = 1.0 if exp_res == "WIN" else 0.0
            pn = b["predicted_probability_nonpush"]
            ok &= abs(b["brier"] - (pn - w) ** 2) < 1e-12 and abs(b["log_loss"] + np.log(max(pn if w else 1 - pn, 1e-12))) < 1e-9
        kinds[exp_out] = kinds.get(exp_out, 0) + 1
        if not ok:
            bad.append({k: b.get(k) for k in ("player_id", "market", "line", "side", "actual", "outcome", "side_result")})
    act_p = pd.read_parquet(sdir / "actuals.parquet")
    dnp = act_p[act_p["participation"] == "DNP"]
    dnp_zero = int(dnp[[f"act_{m}" for m in PC.MARKETS]].notna().any(axis=1).sum())
    ss = pd.read_parquet(sdir / "side_settlements.parquet") if (sdir / "side_settlements.parquet").exists() else pd.DataFrame()
    return {"pass": bool(st) and not bad and dnp_zero == 0, "settled_quoted_predictions": len(st), "outcomes": kinds,
            "mismatches": bad[:10], "players_settled": int(len(act_p)),
            "participation": act_p["participation"].value_counts().to_dict(),
            "dnp_players_with_numeric_actual": dnp_zero,
            "dnp_quoted_rows_no_action": int(sum(1 for b in st if b["participation"] == "DNP" and b["outcome"] == "NO_ACTION")),
            "side_settlements": int(len(ss)),
            "side_results": ss["result"].value_counts().to_dict() if len(ss) else {}}


def monitor_checks(cum: dict, ops: Path) -> dict:
    """Settled rows reached each monitor block, and the market benchmark equals an independent recomputation from the
    evidence files (no-vig per book on two-sided lines, model P(over | no push), realized over / under)."""
    ev = PM.load(ops)
    qs, act = ev["quote_sides"], ev["actuals"]
    out = {"blocks_present": sorted(k for k in cum if isinstance(cum[k], dict))}
    key = ["game_id", "player_id", "market", "line"]
    two = qs.pivot_table(index=key + ["sportsbook"], columns="side", values="book_decimal_odds", aggfunc="max").dropna()
    io, iu = 1 / two["OVER"], 1 / two["UNDER"]
    nv = (io / (io + iu)).groupby(level=key).mean().rename("mkt").reset_index()
    lt = qs.sort_values("record_id").drop_duplicates(key)[key + ["p_over", "p_push"]].merge(nv, on=key)
    a = act.set_index(["game_id", "player_id"])
    lt["actual"] = [a.loc[(g, p), f"act_{m}"] if (g, p) in a.index and a.loc[(g, p), "participation"] == "PLAYED" else np.nan
                    for g, p, m in zip(lt["game_id"], lt["player_id"], lt["market"])]
    lt = lt[lt["actual"].notna() & (lt["actual"] != lt["line"])]
    y = (lt["actual"] > lt["line"]).astype(float)
    pm = lt["p_over"] / (1 - lt["p_push"])
    ind = {"lines": int(len(lt)), "model_brier": float(((pm - y) ** 2).mean()) if len(lt) else None,
           "market_brier": float(((lt["mkt"] - y) ** 2).mean()) if len(lt) else None,
           "model_log_loss": float((-np.log(np.where(y == 1, pm, 1 - pm).clip(1e-12))).mean()) if len(lt) else None,
           "market_log_loss": float((-np.log(np.where(y == 1, lt["mkt"], 1 - lt["mkt"]).clip(1e-12))).mean()) if len(lt) else None}
    mb = (cum.get("market_benchmark") or {}).get("all") or {}
    rep = {k: (mb.get(k) or {}).get("value") if isinstance(mb.get(k), dict) else mb.get(k)
           for k in ("lines", "model_brier", "market_brier", "model_log_loss", "market_log_loss")}
    agree = rep.get("lines") == ind["lines"] and all(
        rep.get(k) is not None and ind[k] is not None and abs(rep[k] - ind[k]) < 1e-9
        for k in ("model_brier", "market_brier", "model_log_loss", "market_log_loss")) if ind["lines"] else False
    out["market_benchmark"] = {"pass": bool(agree), "report": rep, "independent": ind}
    # each monitor's N against an independent count of the stratum's played players (PTS) from the evidence files
    pr = ev["projections"].merge(act[["game_id", "player_id", "participation"]], on=["game_id", "player_id"], how="left")
    played = pr[pr["participation"] == "PLAYED"]
    strata = {"high_exposure_pts": (cum["high_exposure_pts"], played["high_exposure"].fillna(False).astype(bool)),
              "center_pts.C": (cum["center_pts"]["C"], played["center"].fillna(False).astype(bool)),
              "center_pts.non_C": (cum["center_pts"]["non_C"], ~played["center"].fillna(False).astype(bool)),
              "early_season.first_30_days.PTS": (cum["early_season"]["first_30_days"]["PTS"],
                                                 played["first_30_days"].fillna(False).astype(bool)),
              "early_season.team_games_le_2.PTS": (cum["early_season"]["team_games_le_2"]["PTS"],
                                                   played["opening_season"].fillna(False).astype(bool)),
              "transfer_cold_start.COLD_TRANSFER_IN.PTS": (cum["transfer_cold_start"]["COLD_TRANSFER_IN"]["PTS"],
                                                           played["cold_start_class"] == "COLD_TRANSFER_IN"),
              "transfer_cold_start.COLD_NO_HISTORY.PTS": (cum["transfer_cold_start"]["COLD_NO_HISTORY"]["PTS"],
                                                          played["cold_start_class"] == "COLD_NO_HISTORY")}
    for b in ("CUR_0", "CUR_0_100", "CUR_100_300", "CUR_300PLUS"):
        strata[f"evidence_buckets_pts.{b}"] = (cum["evidence_buckets_pts"][b], played["evidence_bucket"] == b)
    mon = {}
    for name, (blk, mask) in strata.items():
        n_rep = int((blk.get("projection") or {}).get("n") or 0)
        n_ind = int(mask.sum())
        mon[name] = {"status": ("PASS" if n_rep == n_ind else "FAIL") if n_ind else ("NOT_APPLICABLE" if n_rep == 0 else "FAIL"),
                     "played_n_report": n_rep, "played_n_independent": n_ind,
                     "settled_lines": int((blk.get("quoted_lines") or {}).get("settled_lines") or 0)}
    sides = cum["over_under_sides"]["overall"]["counters"]
    ss = ev["side_settlements"]
    mon["over_under_sides"] = {"status": "PASS" if sides.get("opportunities") == qs.drop_duplicates(
        ["game_id", "player_id", "market", "line", "sportsbook"]).shape[0] and len(ss) == len(qs) else "FAIL",
        "opportunities_report": sides.get("opportunities"), "quote_sides": int(len(qs)), "side_settlements": int(len(ss))}
    out["monitors"] = mon
    out["pass"] = out["market_benchmark"]["pass"] and all(v["status"] != "FAIL" for v in mon.values())
    return out


def monitors(work: Path, rank: int = 1) -> dict:
    """Re-check the monitors / benchmark on the existing isolated ops root (no pipeline re-run, no ledger append)."""
    work = _guard(work)
    rp = work / f"run_{rank}.json"
    rep = json.loads(rp.read_text())
    cum = json.loads((work / "ops" / "reports" / "cumulative.json").read_text())
    rep["monitor_checks"] = monitor_checks(cum, work / "ops")
    _j(rp, rep)
    return rep["monitor_checks"]


# ============================================================================ cleanup
def cleanup(work: Path, keep: Path) -> dict:
    """Write the compact report (+ normalized evidence, hashes, request metadata) to `keep`, then delete the work root."""
    work = _guard(work)
    keep = Path(keep)
    keep.mkdir(parents=True, exist_ok=True)
    for n in ("selection.json", "fetch_1.json", "fetch_2.json", "run_1.json", "run_2.json"):
        if (work / n).exists():
            shutil.copyfile(work / n, keep / n)
    led = _ledger(work)
    (keep / "odds_historical_requests.jsonl").write_text("".join(OS.canon({k: v for k, v in e.items() if k != "raw_path"}) + "\n"
                                                                for e in led))
    for p in (work / "ops" / "ticks").glob("*/quotes_normalized.csv.gz"):
        shutil.copyfile(p, keep / "quotes_normalized.csv.gz")
    for p in (work / "prod" / "runs").glob("*/board.csv"):
        OS.gzip_file(p, keep / "board.csv.gz")
    for n in ("cumulative.json", "cumulative.md"):
        if (work / "ops" / "reports" / n).exists():
            shutil.copyfile(work / "ops" / "reports" / n, keep / f"report_{n}")
    shutil.rmtree(work)
    return {"kept": sorted(p.name for p in keep.iterdir()), "work_root_deleted": not work.exists()}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.rehearsal.historical_payload")
    ap.add_argument("cmd", choices=("select", "fetch", "run", "monitors", "cleanup"))
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--rank", type=int, default=1)
    ap.add_argument("--sims", type=int, default=PC.SIMS)
    ap.add_argument("--keep", type=Path, default=DOCS)
    a = ap.parse_args(argv)
    out = {"select": lambda: select(a.work), "fetch": lambda: fetch(a.work, a.rank),
           "run": lambda: run(a.work, a.rank, a.sims),
           "monitors": lambda: monitors(a.work, a.rank), "cleanup": lambda: cleanup(a.work, a.keep)}[a.cmd]()
    print(json.dumps(out, indent=1, default=str)[:20000])


if __name__ == "__main__":
    main()
