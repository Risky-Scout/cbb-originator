"""Daily shadow run (the cheap daily graph). No refit, no historical rebuild, no OOS reconstruction, no dependence refit,
no backtest, one simulation per game shared by every sportsbook.

    state (last-known-good increment) -> slate -> pregame universe + features (frozen Stage 3 SQL, as_of-masked)
    -> frozen model (hash-verified) -> one joint bank per game -> market pmfs -> quotes -> strict matching
    -> pricing -> readiness (ceiling SHADOW_ONLY) -> append-only ledger -> static bundle

    .venv/bin/python -m cbb_platform.prod.run daily --as-of 2026-11-10T16:00:00Z --quotes q.json [--availability a.csv]
    .venv/bin/python -m cbb_platform.prod.run verify-artifacts | verify-ledger [--ledger L] | verify-bundle DIR
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from cbb_platform.prod import (board as BO, bundle as BD, config as PC, features as PF, inputs as IN, ledger as LG,
                               loader as LD, matching as MT, quotes as QT, readiness as RD, simulate as SM, slate as SL,
                               state as ST, universe as UV)

FIXTURE_LABEL = "FIXTURE (synthetic production-format data; NOT live)"


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _names(canonical_db: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect(str(canonical_db), read_only=True)
    teams = con.execute("""SELECT canonical_team_id, any_value(school) AS school, any_value(display_name) AS display_name,
                                  any_value(mascot) AS mascot FROM core.teams GROUP BY 1""").df()
    players = con.execute("""SELECT canonical_player_id, any_value(display_name) AS display_name FROM core.players
                             GROUP BY 1""").df()
    con.close()
    return teams, players


def _last_failure_after(state_root: Path, promoted_at) -> bool:
    log = Path(state_root) / "update_failures.jsonl"
    if not log.exists():
        return False
    last = [json.loads(l) for l in log.read_text().splitlines() if l.strip()][-1]
    return _utc(last["at"]) > _utc(promoted_at)


def _last_sync_failed(sync_log: Path, aso) -> dict | None:
    """The most recent production sync attempt finished by prediction time, when it FAILED (the log is append-only: a
    later success would be a later line) -> PROVIDER_FAILURE; the run continues on the last-known-good state."""
    p = Path(sync_log)
    if not p.exists():
        return None
    lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    lines = [l for l in lines if _utc(l["finished_at"]) <= _utc(aso)]
    return lines[-1] if lines and lines[-1].get("status") == "FAILED" else None


def live_run_reasons(man: dict, pointer: dict, aso, sync_log: Path) -> tuple[list, list]:
    """Run-level blockers of a LIVE (provider) state: roster freshness / presence, the last sync outcome, and quote
    provider coverage (The Odds API NCAAB player props: not yet verified -> every live row stays BLOCKED)."""
    reasons, notes = [], []
    pre = man.get("preseason_evidence", {})
    ras = [man.get("roster_as_of"), (pointer.get("last_sync_extra") or {}).get("roster_as_of")]
    ras = [_utc(x) for x in ras if x and _utc(x) <= aso]
    if not pre.get("roster_2027_acquired") or not ras:
        reasons.append("MISSING_ROSTER")
    elif (aso - max(ras)).total_seconds() / 3600.0 > PC.ROSTER_MAX_AGE_HOURS:
        reasons.append("STALE_ROSTER")
    if man.get("schedule_as_of") and (aso - _utc(pointer.get("last_sync_source_cutoff") or man["schedule_as_of"])
                                      ).total_seconds() / 3600.0 > PC.SCHEDULE_MAX_AGE_HOURS:
        reasons.append("STALE_STATE")
    f = _last_sync_failed(sync_log, aso)
    if f is not None:
        reasons.append("PROVIDER_FAILURE")
        notes.append(f"last production sync FAILED ({f.get('reason')}): running on last-known-good state")
    if not PC.ODDS_PROVIDER_COVERAGE_VERIFIED:
        reasons.append("QUOTE_COVERAGE_UNVERIFIED")
        notes.append("The Odds API NCAAB player-prop coverage not yet verified (no discovery call made)")
    return reasons, notes


def daily(as_of, quotes_paths=(), availability: pd.DataFrame | None = None, state_root: Path = PC.STATE_ROOT,
          history_db: Path = PC.HISTORY_CANONICAL, ledger_path: Path = PC.LEDGER_PATH, run_root: Path = PC.RUNS_ROOT,
          bundle_dir: Path | None = None, team_map: pd.DataFrame | None = None, player_map: pd.DataFrame | None = None,
          horizon_hours: float = 36.0, sims: int = PC.SIMS, artifact_paths: dict | None = None,
          game_ids=None, sync_log: Path = PC.SYNC_LOG) -> dict:
    aso = _utc(as_of)
    run_id = "R" + aso.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = Path(run_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_reasons, notes = [], []
    # ---------------------------------------------------------------- frozen model (hash-verified, never refit)
    try:
        model = LD.load(artifact_paths)
    except LD.ArtifactIntegrityError as e:
        out = {"run_id": run_id, "readiness_state": "BLOCKED", "run_reasons": [e.reason], "error": str(e), "predictions": 0}
        (run_dir / "run_summary.json").write_text(json.dumps(out, indent=1, default=str))
        return out
    ident = model.identity
    # ---------------------------------------------------------------- state (last known good)
    st = ST.current(state_root)
    if st is None:
        out = {"run_id": run_id, "readiness_state": "BLOCKED",
               "run_reasons": ["STATE_UPDATE_FAILED", "LIVE_DATA_FEED_UNAVAILABLE"], "predictions": 0,
               "note": "no promoted 2026-27 state: the current-season data feed has not produced a valid increment"}
        (run_dir / "run_summary.json").write_text(json.dumps(out, indent=1, default=str))
        return out
    man = st["manifest"]
    stl = ST.staleness(man, aso, st.get("pointer"))
    if stl["stale"]:
        run_reasons.append("STALE_STATE")
    if stl["post_cutoff_data"]:
        run_reasons.append("POST_CUTOFF_DATA")
    if _last_failure_after(state_root, json.loads((Path(state_root) / "CURRENT.json").read_text())["promoted_at"]):
        run_reasons.append("STATE_UPDATE_FAILED")
    fixture = str(man.get("provider", {}).get("kind", "")).upper() == "FIXTURE"
    data_label = FIXTURE_LABEL if fixture else "LIVE"
    if fixture and Path(ledger_path).resolve() == PC.LEDGER_PATH.resolve():
        raise RuntimeError("fixture data may never be written to the production shadow ledger")
    live = not fixture
    if live:
        lr, ln = live_run_reasons(man, st.get("pointer", {}), aso, sync_log)
        run_reasons += [r for r in lr if r not in run_reasons]
        notes += ln
    pre = man.get("preseason_evidence", {})
    universe_complete = bool(pre.get("portal_2026_complete")) and bool(pre.get("recruits_2026_complete"))
    # ---------------------------------------------------------------- slate
    slate = SL.build(st["increment_db"], aso, man.get("schedule_as_of") or man["source_cutoff"], horizon_hours)
    if game_ids is not None:                                     # explicit validation subset (never widens the slate)
        slate = slate[slate["canonical_game_id"].isin(set(game_ids))].reset_index(drop=True)
    game_reasons = {r.canonical_game_id: list(r.suppression_reasons) for r in slate.itertuples(index=False)}
    if not universe_complete:
        for g in game_reasons:
            game_reasons[g].append("MISSING_PLAYER_STATE")
        notes.append("preseason membership evidence (portal 2026 / recruits 2026) incomplete: pregame universe incomplete")
    score_ids = slate.loc[slate["slate_status"] == "SCORE", "canonical_game_id"].tolist()
    summary = {"run_id": run_id, "prediction_as_of": aso, "state_version": man["state_version"], "data_label": data_label,
               "state_as_of": (st.get("pointer") or {}).get("last_sync_source_cutoff") or man["source_cutoff"],
               "schedule_as_of": man.get("schedule_as_of"), "roster_as_of": man.get("roster_as_of"),
               "validated_prediction_cutoff": PC.VALIDATED_PREDICTION_CUTOFF,
               "slate_games": int(len(slate)), "scored_games": 0, "model_identity": ident, "notes": notes}
    banks, dists, diag, cand_names = {}, [], [], []
    teams = players = pd.DataFrame()
    if score_ids:
        fr = PF.build(run_dir / "features", score_ids, PC.PRODUCTION_SEASON, aso, model.marginal_cfg, history_db,
                      st["increment_db"])
        summary["feature_provenance"] = fr["provenance"]
        teams, players = _names(run_dir / "features" / "canonical_asof.duckdb")
        universe = UV.build(fr["features_db"], run_dir / "features" / "canonical_asof.duckdb", man.get("roster_as_of"),
                            "FIXTURE" if fixture else "CBBD_API_PRODUCTION")
        universe.to_csv(run_dir / "player_universe.csv", index=False)
        roster_sup = UV.suppression_map(universe)
        summary["player_universe"] = UV.summary(universe)
        cand = fr["cand"]
        if len(cand) and (cand["season"] != PC.PRODUCTION_SEASON).any():
            raise RuntimeError("WRONG_PRODUCTION_SEASON reached the model frame")
        cut = slate.set_index("canonical_game_id")["feature_cutoff"]
        for gid in score_ids:
            gc = cand[cand["canonical_game_id"] == gid]
            if gc.empty:
                game_reasons[gid].append("MISSING_PLAYER_STATE")
                continue
            if (pd.to_datetime(gc["prediction_cutoff_utc"], utc=True) != cut[gid]).any():
                game_reasons[gid].append("FUTURE_LEAKAGE")       # feature cutoff disagrees with the validated rule
        rows = IN.marginal_rows(cand, model.marginal_cfg, model.v0_state, model.participation_state) if len(cand) else pd.DataFrame()
        envd = IN.environment(fr["team"], model.jm1["struct"]["env"]) if len(cand) else {}
        if len(rows):
            rows, aflags = IN.apply_availability(rows, availability)
            for gid, f in aflags.items():
                game_reasons[gid] += f["reasons"]
            rows.drop(columns=["_scored"]).to_parquet(run_dir / "model_inputs.parquet", index=False)
        tname = teams.set_index("canonical_team_id")["display_name"].to_dict()
        pname = players.set_index("canonical_player_id")["display_name"].to_dict()
        for gid in score_ids:
            gp = rows[rows["game_id"] == gid] if len(rows) else pd.DataFrame()
            if gp.empty or "FUTURE_LEAKAGE" in game_reasons[gid]:
                continue
            srow = slate.set_index("canonical_game_id").loc[gid]
            label = (f"{tname.get(srow.away_team_id, srow.away_team_id)} @ {tname.get(srow.home_team_id, srow.home_team_id)} "
                     f"{pd.Timestamp(srow.game_date_et).date()}")
            try:
                bank = SM.simulate_game(model.jm1, gid, gp, envd, sims)
            except SM.ScoringFailure as e:
                game_reasons[gid].append(e.reason)
                continue
            if not SM.same_draw_check(bank):
                game_reasons[gid].append("SCORING_FAILURE")
                continue
            banks[gid] = {"bank_sha256": bank["bank_sha256"], "priced_players": int(len(bank["rows"])),
                          "corr_min_eig": float(bank["corr_min_eig"]), "jitter": float(bank["jitter"])}
            diag.append(SM.same_team_correlation(bank))
            md = SM.market_distributions(bank)
            md["game_label"] = label
            md["player_label"] = md["player_id"].map(lambda p: pname.get(p, p))
            md["bank_sha256"] = bank["bank_sha256"]
            md["player_suppression"] = ""
            rs = md["player_id"].map(lambda p: roster_sup.get((gid, p), ""))
            md.loc[rs != "", "method"] = "NOT_PRICED"            # simulated (frozen pool), never priced
            md["player_suppression"] = rs
            dists.append(md)
            sup = gp[gp["player_suppression"] != ""]
            for r in sup.itertuples(index=False):             # suppressed players: recorded, not priced
                dists.append(pd.DataFrame([{"game_id": gid, "team_id": r.team_id, "player_id": r.player_id, "market": m,
                                            "pmf": np.array([1.0]), "projection": float("nan"), "bank_mean": float("nan"),
                                            "method": "NOT_PRICED", "p_play": float(r.p_play), "game_label": label,
                                            "player_label": pname.get(r.player_id, r.player_id), "bank_sha256": bank["bank_sha256"],
                                            "player_suppression": r.player_suppression} for m in PC.MARKETS]))
            cand_names.append(gp[["game_id", "team_id", "player_id"]].assign(
                display_name=gp["player_id"].map(lambda p: pname.get(p))))
        summary["scored_games"] = len(banks)
    dist = pd.concat(dists, ignore_index=True) if dists else pd.DataFrame(
        columns=["game_id", "team_id", "player_id", "market", "pmf", "projection", "method", "p_play", "game_label",
                 "player_label", "bank_sha256", "player_suppression"])
    dist["game_reasons"] = dist["game_id"].map(lambda g: sorted(set(game_reasons.get(g, []))))
    # ---------------------------------------------------------------- quotes -> strict matching -> freshness
    qs = [QT.load_collected(p) for p in quotes_paths]
    quotes = pd.concat(qs, ignore_index=True) if qs else pd.DataFrame(columns=QT.COLUMNS)
    if len(quotes):
        tidx = MT.team_index(teams, team_map) if len(teams) else {"explicit": {}, "exact": {}}
        quotes = MT.match_games(quotes, slate, tidx)
        cands = pd.concat(cand_names, ignore_index=True) if cand_names else pd.DataFrame(
            columns=["game_id", "team_id", "player_id", "display_name"])
        quotes = MT.match_players(quotes, cands, player_map)
        quotes = BO.quote_freshness(quotes, aso, slate.set_index("canonical_game_id")["start_time"].to_dict())
        quotes.to_csv(run_dir / "quotes_normalized.csv", index=False)
    summary["quotes"] = {"total": int(len(quotes)),
                         "by_status": quotes.groupby(["quote_status", "suppression_reason"]).size().rename("n").reset_index()
                         .to_dict(orient="records") if len(quotes) else []}
    # ---------------------------------------------------------------- price + readiness + ledger + bundle
    run_state = RD.evaluate(run_reasons)
    meta = {"run_id": run_id, "prediction_as_of": aso, "model_version": ident["model_version"], "sims": sims,
            "run_reasons": run_reasons}
    board, priced = BO.build(dist, quotes, meta) if len(dist) else (pd.DataFrame(columns=BO.BOARD_COLS), [])
    sl = slate.set_index("canonical_game_id")
    recs = [{"record_type": "RUN", "body": {"run_id": run_id, "prediction_as_of": aso, "model_identity": ident,
                                            "state_version": man["state_version"], "state_source_cutoff": man["source_cutoff"],
                                            "state_as_of": (st.get("pointer") or {}).get("last_sync_source_cutoff") or man["source_cutoff"],
                                            "schedule_as_of": man.get("schedule_as_of"), "roster_as_of": man.get("roster_as_of"),
                                            "readiness_state": run_state["readiness_state"], "run_reasons": run_reasons,
                                            "data_label": data_label, "banks": banks,
                                            "validated_prediction_cutoff": PC.VALIDATED_PREDICTION_CUTOFF,
                                            "wagering_enabled": PC.WAGERING_ENABLED}}]
    for p in priced:
        recs.append({"record_type": "PREDICTION", "body": {
            "prediction_id": LG.prediction_id(run_id, p["game_id"], p["player_id"], p["market"], p["line"], p["side"]),
            "run_id": run_id, "prediction_as_of": aso, "feature_cutoff": sl.loc[p["game_id"], "feature_cutoff"],
            "model_version": ident["model_version"], "artifact_sha256": ident["artifact_sha256"],
            "production_manifest": ident["production_manifest"], "state_version": man["state_version"],
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
            "bank_sha256": p["bank_sha256"], "data_label": data_label}})
    lg = LG.append(recs, ledger_path)
    proj = dist[["game_id", "game_label", "player_id", "player_label", "market", "projection", "p_play", "method",
                 "player_suppression"]].copy() if len(dist) else pd.DataFrame()
    bmeta = {"run_id": run_id, "model_version": ident["model_version"], "architecture": ident["architecture"],
             "artifact_sha256": ident["artifact_sha256"], "production_manifest": ident["production_manifest"],
             "frozen_commit": ident["frozen_commit"], "frozen_tag": ident["frozen_tag"],
             "state_version": man["state_version"], "prediction_as_of": aso, "readiness_state": run_state["readiness_state"],
             "data_label": data_label, "validated_prediction_cutoff": PC.VALIDATED_PREDICTION_CUTOFF}
    bdir = Path(bundle_dir) if bundle_dir else run_dir / "bundle"
    BD.write(bdir, board, proj, bmeta)
    try:
        bv = BD.verify(bdir)
    except BD.BundleChecksumFailure as e:
        run_reasons.append("BUNDLE_CHECKSUM_FAILURE")
        bv = {"BUNDLE_VALID": False, "error": str(e)}
    (run_dir / "game_diagnostics.json").write_text(json.dumps(diag, default=str))
    board.to_csv(run_dir / "board.csv", index=False)
    row_states = pd.Series([p["readiness_state"] for p in priced]).value_counts().to_dict() if priced else {}
    summary.update({"readiness_state": RD.evaluate(run_reasons)["readiness_state"], "run_reasons": run_reasons,
                    "game_reasons": {g: sorted(set(r)) for g, r in game_reasons.items()}, "banks": banks,
                    "predictions": sum(1 for r in recs if r["record_type"] == "PREDICTION"),
                    "board_rows": int(len(board)), "row_readiness": row_states, "ledger": lg, "bundle": bv,
                    "bundle_dir": str(bdir), "run_dir": str(run_dir), "wagering_enabled": PC.WAGERING_ENABLED})
    (run_dir / "run_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="cbb_platform.prod.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("daily")
    d.add_argument("--as-of", default=datetime.now(timezone.utc).isoformat())
    d.add_argument("--quotes", nargs="*", default=[])
    d.add_argument("--availability")
    d.add_argument("--horizon-hours", type=float, default=36.0)
    sub.add_parser("verify-artifacts")
    v = sub.add_parser("verify-ledger")
    v.add_argument("--ledger", default=str(PC.LEDGER_PATH))
    b = sub.add_parser("verify-bundle")
    b.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd == "daily":
        av = pd.read_csv(a.availability, parse_dates=["received_at"]) if a.availability else None
        out = daily(a.as_of, a.quotes, av, horizon_hours=a.horizon_hours)
    elif a.cmd == "verify-artifacts":
        out = LD.verify()
    elif a.cmd == "verify-ledger":
        out = LG.verify(Path(a.ledger))
    else:
        out = BD.verify(Path(a.dir))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
