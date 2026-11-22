"""Deterministic selection / actionability / market-disagreement audit from frozen evidence (research only; never on the
production decision path; no provider call, no refit, no simulation). Writes only <out_dir>.

    python -B -m cbb_platform.prod.selection_audit audit <ops_root> <out_dir> [--mode prospective|retrospective]
                                                         [--from YYYY-MM-DD] [--to YYYY-MM-DD]
    python -B -m cbb_platform.prod.selection_audit verify-live <ops_root> <out_dir>

On the Droplet run it as cbb-originator against the deployed release with the production state mounted read-only, e.g.
    systemd-run --pipe --wait --uid=cbb-originator --gid=cbb-originator \\
        -p EnvironmentFile=/etc/sports-models/cbb-originator.env -p ReadOnlyPaths=/var/lib/sports-models \\
        -p ReadWritePaths=<out_dir> -p WorkingDirectory=/ -E PYTHONPATH=/opt/sports-models/cbb-originator/current \\
        -E PYTHONDONTWRITEBYTECODE=1 -E OMP_NUM_THREADS=1 -E OPENBLAS_NUM_THREADS=1 \\
        /opt/sports-models/cbb-originator/venv/bin/python -B -m cbb_platform.prod.selection_audit audit \\
        /var/lib/sports-models/cbb-originator/prod/ops <out_dir>

audit, mode prospective (default; the decision-gate population): only frozen player MARKET_EVALUATIONs carrying their
persisted decision-time candidate_offer_set (selection_replay.replayable_evidence: REPLAYABLE_EVIDENCE_START onward);
earlier records are counted NOT_REPLAYABLE and never back-filled. Mode retrospective additionally reconstructs earlier
candidate sets from the persisted PMF + the record's own decision observation, accepted only when they reproduce the
stored best offer exactly (diagnostic; never used by the gate).
Per record: the four rules of selection_replay pick an offer each; settled on the final box score; CLV of the picked
offer from the corrected close evidence (prod.market_exports.closing, the Performance function). The pre-declared
POLICY_CANDIDATE is applied to its rule's picks. ALL_MARKETS (selection-free): one OVER offer per market (the line with
the most two-sided offers, then book key). Game: every GAME_CANDIDATE_OFFERS record must reproduce its stored
GAME_MARKET_EVALUATIONs through prod.game.markets.best_by_market (metrics by market type x side: CBB_Performance).
verify-live: the latest frozen player and game records carry a complete offer set and replay their stored pick.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import sys
from pathlib import Path

from cbb_platform.prod import market_eval as ME, selection_replay as SR

AUDIT_VERSION = "cbb-selection-audit-1.0.0"
PLAYER_REQUIRED = ("book", "line", "side", "american", "decimal", "model_p", "p_push", "ev_gross", "ev_net",
                   "collector_received_at", "quote_id", "status")
GAME_REQUIRED = ("book", "market_type", "side", "american", "decimal", "model_p", "p_push", "ev_gross", "ev_net",
                 "collector_received_at", "quote_id", "status")
GAME_HEADER = ("game_id", "home_team_id", "away_team_id", "forecast_id", "pmf_sha256", "cutoff_utc", "frozen_at",
               "schema", "content_hash")
GAME_MATCH = ("book", "side", "point", "decimal", "american", "model_p", "fair_p", "edge", "ev", "quote_id")


# ---------------------------------------------------------------- pure helpers
def missing_fields(rows: list[dict], required: tuple) -> dict:
    """Required fields absent on ELIGIBLE offers (fair_p / edge may be None only for an unpaired offer)."""
    c = collections.Counter()
    for r in rows:
        if r.get("status") != ME.ELIGIBLE:
            continue
        for k in required:
            if r.get(k) is None:
                c[k] += 1
        if r.get("paired") and (r.get("fair_p") is None or r.get("edge") is None):
            c["fair_p_on_paired"] += 1
    return dict(c)


def books_at(cands: list[dict], c: dict | None) -> int:
    """Distinct books with a two-sided (paired) price at the picked offer's line and side."""
    if c is None:
        return 0
    return len({x["book"] for x in cands if x["line"] == c["line"] and x["side"] == c["side"] and x.get("paired")})


def game_offer_rows(record: dict) -> list[dict]:
    from cbb_platform.prod import game_exports as GE
    return [dict(zip(GE.OFFER_COLS, o)) for o in record.get("offers") or []]


def verify_game_record(record: dict, stored: list[dict]) -> dict:
    """A GAME_CANDIDATE_OFFERS record: header + offer completeness, content hash, PMF hash agreement, and the
    production game rule (best_by_market over ELIGIBLE offers) reproducing every stored evaluation of the game."""
    from cbb_platform.game import markets as GM
    from cbb_platform.prod import game_exports as GE
    rows = game_offer_rows(record)
    best = GM.best_by_market([{**r, "ev": r["ev_net"]} for r in rows if r["status"] == ME.ELIGIBLE])
    by = {e["market_type"]: e for e in stored}
    match = {mt: all(best.get(mt, {}).get(k) == e.get(k) for k in GAME_MATCH) for mt, e in sorted(by.items())}
    return {"game_id": record.get("game_id"), "frozen_at": record.get("frozen_at"), "offers": len(rows),
            "status_counts": dict(collections.Counter(r["status"] for r in rows)),
            "market_types": sorted({r["market_type"] for r in rows}),
            "missing_header": [k for k in GAME_HEADER if not record.get(k)],
            "missing_offer_fields": missing_fields(rows, GAME_REQUIRED),
            "content_hash_ok": GE._hash({k: v for k, v in record.items() if k != "content_hash"})
            == record.get("content_hash"),
            "pmf_hash_ok": all(e.get("pmf_sha256") == record.get("pmf_sha256") for e in stored),
            "replay_matches_stored": match,
            "PASS": bool(rows) and bool(match) and all(match.values())
            and not [k for k in GAME_HEADER if not record.get(k)] and not missing_fields(rows, GAME_REQUIRED)
            and all(e.get("pmf_sha256") == record.get("pmf_sha256") for e in stored)
            and GE._hash({k: v for k, v in record.items() if k != "content_hash"}) == record.get("content_hash")}


def verify_player_record(e: dict) -> dict:
    rows = [dict(zip(ME.OFFER_COLS, r)) for r in e.get("candidate_offer_set") or []]
    miss = missing_fields(rows, PLAYER_REQUIRED)
    ok = SR.matches_stored(e, SR.offers(e))
    return {"key": f"{e['game_id']}|{e['player_id']}|{e['stat']}", "frozen_at": e.get("frozen_at"),
            "schema": e.get("candidate_offer_schema"), "offers": len(rows),
            "status_counts": dict(collections.Counter(r["status"].split(":")[0] for r in rows)),
            "missing_offer_fields": miss, "replay_matches_stored": ok, "actionable": e.get("actionable"),
            "PASS": bool(rows) and not miss and ok and e.get("candidate_offer_schema") == ME.OFFER_SET_SCHEMA
            and e.get("actionable") is False}


def info_slope(rows: list[dict]) -> dict:
    """OLS of (y - q) on (p - q) over WIN/LOSS rows with a fair price: 1 = model fully informative beyond the market,
    0 = no information beyond the market."""
    g = [(r["model_p"] - r["fair_p"], (1.0 if r["result"] == "WIN" else 0.0) - r["fair_p"]) for r in rows
         if r.get("result") in ("WIN", "LOSS") and r.get("fair_p") is not None]
    if len(g) < 20:
        return {"N": len(g)}
    sxx = sum(x * x for x, _ in g)
    if sxx <= 0:
        return {"N": len(g)}
    b = sum(x * y for x, y in g) / sxx
    se = math.sqrt(sum((y - b * x) ** 2 for x, y in g) / (len(g) - 1) / sxx)
    return {"N": len(g), "slope": b, "se": se, "t": b / se if se else None}


def summarize(picks: list[dict], allm: list[dict], policy_rows: list[dict], slates: int) -> dict:
    """Every table of the report + the decision gate, from replay rows (pure)."""
    tab = SR.group_metrics
    act = [r for r in picks if r["active"]]
    rules = {ru: SR.metrics([r for r in act if r["rule"] == ru]) for ru in SR.RULES}
    pol = SR.metrics(policy_rows)
    srs = {ru: tab([r for r in picks if r["rule"] == ru and r["picked"]],
                   lambda r: [(r["stat"], x, r["side"]) for x in r["roles"]]) for ru in SR.RULES}
    over_cells = tab(allm, lambda r: [(r["stat"], x) for x in r["roles"]])
    defects = SR.localized_defects(over_cells)
    gate = SR.decision_gate(rules, pol, slates, defects)
    return {
        "rules": rules,
        "rules_all_picks_incl_inactive": {ru: SR.metrics([r for r in picks if r["rule"] == ru and r["picked"]])
                                          for ru in SR.RULES},
        "rule_x_stat": tab(act, lambda r: [(r["rule"], r["stat"])]),
        "rule_x_role": tab(act, lambda r: [(r["rule"], x) for x in r["roles"]]),
        "rule_x_side": tab(act, lambda r: [(r["rule"], r["side"])]),
        "rule_x_prob": tab(act, lambda r: [(r["rule"], r["prob_bucket"])]),
        "rule_x_disagreement": tab(act, lambda r: [(r["rule"], r["disagreement_bucket"])]),
        "rule_x_clv_status": dict(collections.Counter(f"{r['rule']} | {r.get('clv_status')}" for r in act)),
        "stat_role_side": srs,
        "stat_role_side_flags": {ru: {k: SR.cell_flag(m) for k, m in v.items()} for ru, v in srs.items()},
        "all_markets_total": SR.metrics(allm),
        "all_markets_disagreement": tab(allm, lambda r: [(r["disagreement_bucket"],)]),
        "all_markets_stat_role": over_cells,
        "model_beats_market": sorted(
            k for k, m in {**{f"ALL_MARKETS | {k}": m for k, m in tab(allm, lambda r: [(r["disagreement_bucket"],)])
                              .items()},
                           **{f"RULE | {k}": m for k, m in tab(act, lambda r: [(r["rule"], r["disagreement_bucket"])])
                              .items()}}.items()
            if m.get("brier_diff_vs_market") is not None and m.get("brier_diff_se") is not None
            and m["brier_diff_vs_market"] + SR.GATE_Z * m["brier_diff_se"] < 0),
        "info_slope_all_markets": info_slope(allm),
        "info_slope_by_rule": {ru: info_slope([r for r in act if r["rule"] == ru]) for ru in SR.RULES},
        "policy_candidate": {"policy": SR.POLICY_CANDIDATE, "metrics": pol},
        "decision_gate": gate,
        "actionability_recommendation": (
            "PROMOTION_REVIEW: a pre-declared rule/policy validated; promotion still requires explicit approval"
            if gate["gate"] == "SELECTION_POLICY_VALIDATED" else
            "KEEP_FAIL_CLOSED: Is Actionable = NO for every player prop (ACTIONABILITY_VALIDATED = False)"),
        "not_derivable_roles": list(SR.ROLES_NOT_IN_FROZEN_EVIDENCE)}


# ---------------------------------------------------------------- evidence readers (Droplet)
def _context(ops: Path, day: str, e: dict, cache: dict) -> dict:
    import pandas as pd
    fid, kind = e.get("forecast_id"), e.get("source_kind")
    if (fid, kind) not in cache:
        base = (ops / "forecasts" / day / str(fid)) if kind == "FORECAST_SNAPSHOT" else ops / "ticks" / str(fid)
        m: dict = {}
        for p in (base / "projections.parquet", base / "forecast_players.parquet"):
            if p.exists():
                for r in pd.read_parquet(p).drop_duplicates("player_id").to_dict(orient="records"):
                    d = m.setdefault(r["player_id"], {})
                    d.update({k: v for k, v in r.items() if k not in d})
        cache[(fid, kind)] = m
    return cache[(fid, kind)].get(e["player_id"], {})


def _reconstruct(ops: Path, day: str, e: dict, obs, pm: dict, frames: dict):
    import pandas as pd
    p = pm.get((e["game_id"], e["player_id"], e["stat"]))
    ob = next((o for o in obs.of_game(e["game_id"]) if o.get("file") == e.get("observation_file")), None)
    if p is None or ob is None:
        return None
    f = e["observation_file"]
    if f not in frames:
        frames[f] = obs.frame(ob)
    q = frames[f].assign(stat=frames[f]["stat"].astype(object))
    q = q[(q["canonical_game_id"] == e["game_id"]) & (q["canonical_player_id"] == e["player_id"])
          & (q["stat"] == e["stat"])]
    return SR.offers({"candidate_offer_set": ME.evaluate(p, q if len(q) else pd.DataFrame())["offer_set"]})


def _pmf_map(ops: Path, day: str) -> dict:
    import numpy as np
    from cbb_platform.prod import exports as EX
    pmfs: list = []
    EX.distribution_records(ops, EX.RUNS_ROOT, EX.slate_games(ops, day), {}, day, audit={}, pmf_out=pmfs)
    return {(r["game_id"], r["player_id"], r["stat"]): np.asarray(r["pmf"], dtype=float) for r in pmfs}


def _game_check(ops: Path, day: str) -> dict:
    from cbb_platform.prod import game_exports as GE
    p = GE.eval_path(ops, day)
    if not p.exists():
        return {}
    L = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    ev: dict = {}
    for e in L:
        if e.get("kind") == "GAME_MARKET_EVALUATION":
            ev.setdefault(e["game_id"], []).append(e)
    res = [verify_game_record(r, ev.get(r["game_id"], [])) for r in L if r.get("kind") == "GAME_CANDIDATE_OFFERS"]
    return {"records": len(res), "pass": sum(r["PASS"] for r in res),
            "fail": [r["game_id"] for r in res if not r["PASS"]][:20]} if res else {}


def audit(ops: Path, out: Path, mode: str = "prospective", lo: str | None = None, hi: str | None = None) -> dict:
    from cbb_platform.prod import market_exports as MX
    ops, out = Path(ops), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    days = sorted(p.parent.name for p in (ops / MX.EVAL_DIR).glob(f"*/{MX.EVAL_FILE}"))
    days = [d for d in days if (lo is None or d >= lo) and (hi is None or d <= hi)]
    ev_window = SR.replayable_evidence(ops)
    picks, allm, pol, recon, graded_days, games = [], [], [], collections.Counter(), set(), {}
    for day in days:
        evals, _ = MX.load_evaluations(MX.eval_path(ops, day))
        games[day] = _game_check(ops, day)
        if not evals:
            continue
        obs, act = MX.Observations(ops, day), MX.actuals(ops, {k[0] for k in evals})
        pm = _pmf_map(ops, day) if mode == "retrospective" and any(
            not e.get("candidate_offer_set") for e in evals.values()) else {}
        frames, cache = {}, {}
        for k in sorted(evals):
            e = evals[k]
            if e.get("candidate_offer_set"):
                cands, src = SR.offers(e), "PERSISTED"
            elif mode != "retrospective":
                recon["NOT_REPLAYABLE_BEFORE_START"] += 1
                continue
            else:
                cands, src = _reconstruct(ops, day, e, obs, pm, frames), "RECONSTRUCTED"
                if cands is None:
                    recon["UNREPRODUCIBLE_MISSING_EVIDENCE"] += 1
                    continue
            if not SR.matches_stored(e, cands):
                recon[f"UNREPRODUCIBLE_{src}"] += 1
                continue
            recon[src] += 1
            g = MX.grade({**e}, act)
            if g["result"] not in MX.GRADED:
                recon["NOT_GRADED_" + str(g["result"])] += 1
                continue
            graded_days.add(day)
            y = g["actual"]
            base = {"slate_date": day, "stat": e["stat"], "source": src,
                    "roles": SR.role_tags(e.get("expected_minutes"), _context(ops, day, e, cache))}
            for r in SR.replay(e, cands, y):
                if r["picked"]:
                    cl = MX.closing(obs, {**e, "book": r["book"], "line": r["line"], "side": r["side"],
                                          "entry_fair_probability": r["fair_p"]})
                    r.update(probability_clv=cl["probability_clv"], clv_status=cl["clv_status"])
                row = {**base, **r}
                picks.append(row)
                if r["rule"] == SR.POLICY_CANDIDATE["rule"] and r["active"]:
                    c = next((x for x in cands if x.get("quote_id") == r["quote_id"] and x["side"] == r["side"]
                              and x["line"] == r["line"] and x["book"] == r["book"]), None)
                    if SR.apply_policy(c and {**c, "stat": e["stat"]}, SR.POLICY_CANDIDATE, books_at(cands, c)):
                        pol.append(row)
            ov = [c for c in cands if c["side"] == "OVER" and c["fair_p"] is not None]
            if ov:
                nb = collections.Counter(c["line"] for c in ov)
                c = min(ov, key=lambda c: (-nb[c["line"]], c["line"], c["book"]))
                res = SR.settle("OVER", c["line"], y)
                cl = MX.closing(obs, {**e, "book": c["book"], "line": c["line"], "side": "OVER",
                                      "entry_fair_probability": c["fair_p"]})
                allm.append({**base, "side": "OVER", "line": c["line"], "book": c["book"], "model_p": c["model_p"],
                             "fair_p": c["fair_p"], "edge": c["edge"], "ev": c["ev"], "decimal": c["decimal"],
                             "result": res, "units": SR.units(res, c["decimal"]),
                             "probability_clv": cl["probability_clv"],
                             "disagreement_bucket": SR.disagreement_bucket(c["edge"]),
                             "prob_bucket": SR.prob_bucket(c["model_p"])})
    rep = {"version": AUDIT_VERSION, "mode": mode, "days": days, "graded_slates": sorted(graded_days),
           "evidence_window": ev_window, "records": dict(recon),
           "replay_hash": SR.replay_hash([{k: v for k, v in r.items() if k != "roles"} for r in picks]),
           **summarize(picks, allm, pol, len(graded_days)),
           "game_candidate_replay": {d: g for d, g in games.items() if g}}
    if mode != "prospective":
        rep["decision_gate"] = {**rep["decision_gate"], "gate": "NOT_APPLICABLE_RETROSPECTIVE",
                                "retrospective_gate_would_be": rep["decision_gate"]["gate"]}
    (out / "selection_audit.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    return rep


def verify_live(ops: Path, out: Path) -> dict:
    """LIVE_CANDIDATE_PERSISTENCE / LIVE_SELECTION_REPLAY on the most recent frozen production records."""
    from cbb_platform.prod import game_exports as GE, market_exports as MX
    ops, out = Path(ops), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rep: dict = {"version": AUDIT_VERSION, "player": None, "game": None}
    for d in sorted((p.parent.name for p in (ops / MX.EVAL_DIR).glob(f"*/{MX.EVAL_FILE}")), reverse=True):
        evals, _ = MX.load_evaluations(MX.eval_path(ops, d))
        live = [e for e in evals.values() if e.get("candidate_offer_set")]
        if live:
            res = [verify_player_record(e) for e in sorted(live, key=lambda e: (e["frozen_at"], MX.eval_key(e)))]
            rep["player"] = {"slate_date": d, "records": len(res), "pass": sum(r["PASS"] for r in res),
                             "first": res[0], "failures": [r for r in res if not r["PASS"]][:10]}
            break
    for d in sorted((p.parent.name for p in (ops / "evaluations").glob(f"*/{GE.EVAL_FILE}")), reverse=True):
        g = _game_check(ops, d)
        if g:
            rep["game"] = {"slate_date": d, **g}
            break
    p_ok = bool(rep["player"]) and rep["player"]["pass"] == rep["player"]["records"]
    g_ok = bool(rep["game"]) and rep["game"]["pass"] == rep["game"]["records"]
    rep["LIVE_CANDIDATE_PERSISTENCE"] = "PASS" if p_ok and g_ok else (
        "PENDING_FIRST_FREEZE" if not rep["player"] or not rep["game"] else "FAIL")
    rep["LIVE_SELECTION_REPLAY"] = rep["LIVE_CANDIDATE_PERSISTENCE"]
    rep["evidence_window"] = SR.replayable_evidence(ops)
    (out / "verify_live.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -B -m cbb_platform.prod.selection_audit")
    ap.add_argument("command", choices=("audit", "verify-live"))
    ap.add_argument("ops_root")
    ap.add_argument("out_dir")
    ap.add_argument("--mode", choices=("prospective", "retrospective"), default="prospective")
    ap.add_argument("--from", dest="lo")
    ap.add_argument("--to", dest="hi")
    a = ap.parse_args(argv)
    if a.command == "verify-live":
        rep = verify_live(Path(a.ops_root), Path(a.out_dir))
        print(json.dumps({k: rep[k] for k in ("LIVE_CANDIDATE_PERSISTENCE", "LIVE_SELECTION_REPLAY",
                                              "evidence_window")}, indent=1, default=str))
        return 1 if rep["LIVE_CANDIDATE_PERSISTENCE"] == "FAIL" else 0
    rep = audit(Path(a.ops_root), Path(a.out_dir), a.mode, a.lo, a.hi)
    print(json.dumps({k: rep[k] for k in ("mode", "evidence_window", "records", "replay_hash", "decision_gate",
                                          "actionability_recommendation")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
