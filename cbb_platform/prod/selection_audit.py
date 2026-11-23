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
stored best offer exactly (diagnostic; never used by the gate; needs MemoryMax=2000M, ~1.4 GB peak per slate -- the
prospective mode loads no PMF and stays small).
Per record: the four rules of selection_replay pick an offer each; settled on the final box score; CLV of the picked
offer from the corrected close evidence (prod.market_exports.closing, the Performance function). The pre-declared
POLICY_CANDIDATE is applied to its rule's picks. ALL_MARKETS (selection-free): one OVER offer per market (the line with
the most two-sided offers, then book key). Game: every GAME_CANDIDATE_OFFERS record must reproduce its stored
GAME_MARKET_EVALUATIONs through prod.game.markets.best_by_market (metrics by market type x side: CBB_Performance).
verify-live: the FIRST (REPLAYABLE_EVIDENCE_START) and the latest live player slates and the latest game slate:
complete offer set, required fields, quote ids unique, quote timestamps parseable and none stale (older than
config.QUOTE_MAX_AGE_SECONDS at the decision observation) or collected after the cutoff / tip / freeze, the stored pick
reproduced exactly by the incumbent max-EV replay, and -- once tipped -- a close quote inside the record's own
entry .. cutoff window (prod.market_exports.closing). Fail-closed: Is Actionable must be False.

    python -B -m cbb_platform.prod.selection_audit daily <ops_root> <out_dir>

daily (the Droplet unit cbb-originator-selection-audit, no Claude session): verify-live, then the prospective audit
INCREMENTALLY by slate -- each slate's replay rows are cached in <out>/slates/<day>.json keyed by the evaluation file's
sha256 and recomputed only while the slate changes or still holds PENDING grades, so one slate's quote evidence is in
memory at a time -- then <out>/reports/<date>.json, <out>/latest.json and one line appended to <out>/history.jsonl.
The gate (selection_replay.GATE_ID) is pinned in <out>/gate_freeze.json on the first run; a later definition hash
that differs is reported GATE_DEFINITION_CHANGED (exit 1), never silently applied.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
import sys
from pathlib import Path

from cbb_platform.prod import market_eval as ME, selection_replay as SR

AUDIT_VERSION = "cbb-selection-audit-1.2.0"   # 1.2.0: every row attributed to its predictive engine
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


def _ts(x):
    import pandas as pd
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    try:
        t = pd.Timestamp(x)
    except (TypeError, ValueError):
        return None
    if t is pd.NaT:
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def offer_timing(e: dict, rows: list[dict]) -> dict:
    """ELIGIBLE offers: quote ids unique, quote timestamps parseable, none stale at the decision observation (production
    freshness, config.QUOTE_MAX_AGE_SECONDS) and none collected after the cutoff, at / after tip or after the freeze."""
    from cbb_platform.prod import config as PC
    dec = _ts(e.get("entry_received_at") or e.get("observation_received_at"))
    cut, tip, fz = _ts(e.get("cutoff_utc")), _ts(e.get("tip_utc")), _ts(e.get("frozen_at"))
    c, ids = collections.Counter(), collections.Counter()
    for r in rows:
        if r.get("status") != ME.ELIGIBLE:
            continue
        ids[r.get("quote_id")] += 1
        rec = _ts(r.get("collector_received_at"))
        if rec is None:
            c["UNPARSEABLE_COLLECTOR_RECEIVED_AT"] += 1
            continue
        if r.get("market_last_update") is not None and _ts(r.get("market_last_update")) is None:
            c["UNPARSEABLE_MARKET_LAST_UPDATE"] += 1
        if dec is not None and (dec - rec).total_seconds() > PC.QUOTE_MAX_AGE_SECONDS:
            c["STALE_OFFER_ELIGIBLE"] += 1
        if (cut is not None and rec > cut) or (tip is not None and rec >= tip):
            c["POST_CUTOFF_OFFER_ELIGIBLE"] += 1
        if fz is not None and rec > fz:
            c["COLLECTED_AFTER_FREEZE"] += 1
    c["DUPLICATE_QUOTE_ID"] = sum(v - 1 for v in ids.values() if v > 1)
    if dec is None:
        c["NO_DECISION_OBSERVATION_TIME"] += 1
    return {k: v for k, v in c.items() if v}


def verify_player_record(e: dict) -> dict:
    rows = [dict(zip(ME.OFFER_COLS, r)) for r in e.get("candidate_offer_set") or []]
    miss = missing_fields(rows, PLAYER_REQUIRED)
    timing = offer_timing(e, rows)
    ok = SR.matches_stored(e, SR.offers(e))
    persisted = (bool(rows) and not miss and not timing and e.get("candidate_offer_schema") == ME.OFFER_SET_SCHEMA
                 and e.get("actionable") is False)
    return {"key": f"{e['game_id']}|{e['player_id']}|{e['stat']}", "frozen_at": e.get("frozen_at"),
            "schema": e.get("candidate_offer_schema"), "offers": len(rows),
            "status_counts": dict(collections.Counter(r["status"].split(":")[0] for r in rows)),
            "missing_offer_fields": miss, "timing_defects": timing, "replay_matches_stored": ok,
            "actionable": e.get("actionable"), "PERSISTENCE_PASS": persisted, "REPLAY_PASS": ok,
            "PASS": persisted and ok}


def close_lineage(obs, e: dict, now) -> dict:
    """After tip: the stored pick's close comes from the same game's quote observations inside the record's own
    entry .. min(tip, cutoff) window (prod.market_exports.closing); NO_CLOSE_QUOTE is a lineage failure."""
    from cbb_platform.prod import market_exports as MX
    tip, cut = _ts(e.get("tip_utc")), _ts(e.get("cutoff_utc"))
    end = min([t for t in (tip, cut) if t is not None], default=None)
    if end is None or now < (tip or end):
        return {"status": "PENDING_TIP"}
    cl = MX.closing(obs, {**e, "entry_fair_probability": e.get("fair_probability")})
    ts, start = _ts(cl.get("close_ts")), _ts(e.get("entry_received_at") or e.get("prediction_as_of"))
    ok = (cl["clv_status"] != "NO_CLOSE_QUOTE" and ts is not None and ts <= end and (start is None or ts >= start))
    return {"status": "PASS" if ok else "FAIL", "clv_status": cl["clv_status"], "close_ts": cl.get("close_ts")}


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
        frames.clear()                 # records are visited game by game: keep one decision observation in memory
        frames[f] = obs.frame(ob)
    q = frames[f].assign(stat=frames[f]["stat"].astype(object))
    q = q[(q["canonical_game_id"] == e["game_id"]) & (q["canonical_player_id"] == e["player_id"])
          & (q["stat"] == e["stat"])]
    return SR.offers({"candidate_offer_set": ME.evaluate(p, q if len(q) else pd.DataFrame())["offer_set"]})


def _pmf_map(ops: Path, day: str, keys) -> dict:
    """Retrospective mode only. prod.exports.distribution_records over the whole slate peaks at ~1.4 GB (per-game calls
    are ~30 s each): run with MemoryMax=2000M outside the morning / heavy windows."""
    import numpy as np
    from cbb_platform.prod import exports as EX
    pmfs: list = []
    EX.distribution_records(ops, EX.RUNS_ROOT, EX.slate_games(ops, day), {}, day, audit={}, pmf_out=pmfs)
    keys = set(keys)
    out = {(r["game_id"], r["player_id"], r["stat"]): np.asarray(r["pmf"], dtype=float) for r in pmfs
           if (r["game_id"], r["player_id"], r["stat"]) in keys}
    del pmfs
    return out


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


def _release(*objs) -> None:
    import gc
    for o in objs:
        if isinstance(o, dict):
            o.clear()
    gc.collect()


def _day_rows(ops: Path, day: str, evals: dict, mode: str) -> dict:
    """Replay rows of one slate (one slate's quote evidence in memory at a time)."""
    from cbb_platform.prod import market_exports as MX
    picks, allm, pol, recon, graded = [], [], [], collections.Counter(), False
    if mode != "retrospective" and not any(e.get("candidate_offer_set") for e in evals.values()):
        recon["NOT_REPLAYABLE_BEFORE_START"] += len(evals)          # nothing persisted: no quote evidence loaded
        return {"picks": picks, "allm": allm, "pol": pol, "recon": dict(recon), "graded": graded}
    obs, act = MX.Observations(ops, day), MX.actuals(ops, {k[0] for k in evals})
    need = [k for k, e in evals.items() if not e.get("candidate_offer_set")]
    pm = _pmf_map(ops, day, need) if mode == "retrospective" and need else {}
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
        graded = True
        y = g["actual"]
        base = {"slate_date": day, "stat": e["stat"], "source": src,
                "engine_id": MX.ENG.attribute(e.get("engine_id"), e.get("prediction_as_of")),
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
    _release(pm, frames, cache)
    del obs, act
    return {"picks": picks, "allm": allm, "pol": pol, "recon": dict(recon), "graded": graded}


def _report(mode, days, graded_days, ev_window, recon, picks, allm, pol, games) -> dict:
    rep = {"version": AUDIT_VERSION, "mode": mode, "days": days, "graded_slates": sorted(graded_days),
           "evidence_window": ev_window, "records": dict(recon),
           "replay_hash": SR.replay_hash([{k: v for k, v in r.items() if k not in ("roles", "engine_id")} for r in picks]),
           **summarize(picks, allm, pol, len(graded_days)),
           "by_engine": {eid: {"evaluations": sum(1 for r in allm if (r.get("engine_id") or "UNATTRIBUTED") == eid),
                               "selections": sum(1 for r in picks if (r.get("engine_id") or "UNATTRIBUTED") == eid and r.get("picked"))}
                         for eid in sorted({r.get("engine_id") or "UNATTRIBUTED" for r in picks + allm})},
           "game_candidate_replay": {d: g for d, g in games.items() if g}}
    if mode != "prospective":
        rep["decision_gate"] = {**rep["decision_gate"], "gate": "NOT_APPLICABLE_RETROSPECTIVE",
                                "retrospective_gate_would_be": rep["decision_gate"]["gate"]}
    return rep


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
        d = _day_rows(ops, day, evals, mode)
        picks += d["picks"]
        allm += d["allm"]
        pol += d["pol"]
        recon.update(d["recon"])
        if d["graded"]:
            graded_days.add(day)
    rep = _report(mode, days, graded_days, ev_window, recon, picks, allm, pol, games)
    (out / "selection_audit.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    return rep


def _status(results: list[bool] | None, pending: str = "PENDING_FIRST_FREEZE") -> str:
    if not results:
        return pending
    return "PASS" if all(results) else "FAIL"


def verify_live(ops: Path, out: Path, now=None) -> dict:
    """LIVE_PLAYER_* on the first (REPLAYABLE_EVIDENCE_START) and the latest live player slates, LIVE_GAME_* on the
    latest game slate. Only production records frozen with a persisted offer set count (never a reconstruction)."""
    import pandas as pd
    from cbb_platform.prod import game_exports as GE, market_exports as MX
    ops, out = Path(ops), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC") if now is None else _ts(now)
    win = SR.replayable_evidence(ops)
    rep: dict = {"version": AUDIT_VERSION, "verified_at": now.isoformat(), "evidence_window": win,
                 "player": {}, "game": None}
    dates = win["replayable_slate_dates"]
    pers, repl, lin = [], [], []
    for d in sorted({dates[0], dates[-1]}) if dates else []:
        evals, _ = MX.load_evaluations(MX.eval_path(ops, d))
        live = sorted((e for e in evals.values() if e.get("candidate_offer_set")),
                      key=lambda e: (e["frozen_at"], MX.eval_key(e)))
        res = [verify_player_record(e) for e in live]
        obs = MX.Observations(ops, d)
        cl = [close_lineage(obs, e, now) for e in live]
        del obs
        pers += [r["PERSISTENCE_PASS"] for r in res]
        repl += [r["REPLAY_PASS"] for r in res]
        lin += [c["status"] == "PASS" for c in cl if c["status"] != "PENDING_TIP"]
        rep["player"][d] = {"records": len(res), "persistence_pass": sum(r["PERSISTENCE_PASS"] for r in res),
                            "replay_pass": sum(r["REPLAY_PASS"] for r in res),
                            "close_lineage": dict(collections.Counter(c["status"] for c in cl)),
                            "clv_status": dict(collections.Counter(c.get("clv_status") for c in cl
                                                                   if c.get("clv_status"))),
                            "first": {**res[0], "close": cl[0]} if res else None,
                            "failures": [r for r in res if not r["PASS"]][:10],
                            "close_failures": [{"key": r["key"], **c} for r, c in zip(res, cl)
                                               if c["status"] == "FAIL"][:10]}
    for d in sorted((p.parent.name for p in (ops / "evaluations").glob(f"*/{GE.EVAL_FILE}")), reverse=True):
        g = _game_check(ops, d)
        if g:
            rep["game"] = {"slate_date": d, **g}
            break
    rep["FIRST_LIVE_PLAYER_FREEZE"] = win["replayable_evidence_start"] or "PENDING"
    rep["LIVE_PLAYER_CANDIDATE_PERSISTENCE"] = _status(pers)
    rep["LIVE_PLAYER_SELECTION_REPLAY"] = _status(repl)
    rep["LIVE_PLAYER_CLOSE_LINEAGE"] = _status(lin, "PENDING_TIP" if pers else "PENDING_FIRST_FREEZE")
    rep["LIVE_GAME_CANDIDATE_PERSISTENCE"] = _status([rep["game"]["pass"] == rep["game"]["records"]]
                                                     if rep["game"] else None)
    both = [rep["LIVE_PLAYER_CANDIDATE_PERSISTENCE"], rep["LIVE_GAME_CANDIDATE_PERSISTENCE"]]
    rep["LIVE_CANDIDATE_PERSISTENCE"] = "FAIL" if "FAIL" in both else (
        "PASS" if both == ["PASS", "PASS"] else "PENDING_FIRST_FREEZE")
    rep["LIVE_SELECTION_REPLAY"] = rep["LIVE_PLAYER_SELECTION_REPLAY"]
    (out / "verify_live.json").write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    return rep


LIVE_KEYS = ("FIRST_LIVE_PLAYER_FREEZE", "LIVE_PLAYER_CANDIDATE_PERSISTENCE", "LIVE_PLAYER_SELECTION_REPLAY",
             "LIVE_PLAYER_CLOSE_LINEAGE", "LIVE_GAME_CANDIDATE_PERSISTENCE", "LIVE_CANDIDATE_PERSISTENCE")


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def _write(p: Path, obj) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))
    os.replace(tmp, p)


def pin_gate(out: Path, now: str) -> dict:
    """<out>/gate_freeze.json: the gate definition hash pinned on the first daily run; a later different hash is
    GATE_DEFINITION_CHANGED (never applied silently)."""
    p = out / "gate_freeze.json"
    cur = {"gate_id": SR.GATE_ID, "definition_sha256": SR.gate_sha256(), "declared_at": SR.GATE_DECLARED_AT,
           "declared_in_commit": SR.GATE_DECLARED_IN_COMMIT, "definition": SR.gate_definition()}
    if not p.exists():
        _write(p, {**cur, "pinned_at": now})
        return {**cur, "pinned_at": now, "status": "PINNED"}
    pin = json.loads(p.read_text())
    same = pin.get("definition_sha256") == cur["definition_sha256"] and pin.get("gate_id") == cur["gate_id"]
    return {**pin, "status": "UNCHANGED" if same else "GATE_DEFINITION_CHANGED", "running_sha256": SR.gate_sha256()}


def daily(ops: Path, out: Path, now=None) -> dict:
    """Unattended daily accumulation (Droplet unit): verify-live + incremental prospective audit + history line."""
    import pandas as pd
    from cbb_platform.prod import game_exports as GE, market_exports as MX
    ops, out = Path(ops), Path(out)
    now = pd.Timestamp.now(tz="UTC") if now is None else _ts(now)
    for sub in ("slates", "reports"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    gate_pin = pin_gate(out, now.isoformat())
    live = verify_live(ops, out, now)
    win = live["evidence_window"]
    picks, allm, pol, recon, graded_days, games, recomputed = [], [], [], collections.Counter(), set(), {}, []
    days = sorted(p.parent.name for p in (ops / MX.EVAL_DIR).glob(f"*/{MX.EVAL_FILE}"))
    for day in days:
        ep, gp = MX.eval_path(ops, day), GE.eval_path(ops, day)
        key = {"eval_sha256": _sha(ep), "game_sha256": _sha(gp), "version": AUDIT_VERSION,
               "gate_sha256": SR.gate_sha256()}
        cp = out / "slates" / f"{day}.json"
        c = json.loads(cp.read_text()) if cp.exists() else None
        if c is None or c.get("key") != key or not c.get("complete"):
            evals, _ = MX.load_evaluations(ep)
            d = _day_rows(ops, day, evals, "prospective") if evals else {
                "picks": [], "allm": [], "pol": [], "recon": {}, "graded": False}
            del evals
            c = {"key": key, "complete": "NOT_GRADED_PENDING" not in d["recon"], "rows": d,
                 "game": _game_check(ops, day), "computed_at": now.isoformat()}
            _write(cp, c)
            recomputed.append(day)
        d = c["rows"]
        picks += d["picks"]
        allm += d["allm"]
        pol += d["pol"]
        recon.update(d["recon"])
        if d["graded"]:
            graded_days.add(day)
        games[day] = c["game"]
    rep = _report("prospective", days, graded_days, win, recon, picks, allm, pol, games)
    if gate_pin["status"] == "GATE_DEFINITION_CHANGED":
        rep["decision_gate"] = {**rep["decision_gate"], "gate": "GATE_DEFINITION_CHANGED"}
    rep.update(run_at=now.isoformat(), code_commit=os.environ.get("CBB_CODE_COMMIT"), gate_pin=gate_pin,
               recomputed_slates=recomputed, live={k: live[k] for k in LIVE_KEYS},
               actionability_validated=ME.ACTIONABILITY_VALIDATED, actionability_status=ME.ACTIONABILITY_STATUS)
    stamp = now.strftime("%Y-%m-%dT%H%MZ")
    _write(out / "reports" / f"{stamp}.json", rep)
    _write(out / "latest.json", rep)
    act = [r for r in picks if r["active"]]
    head = {"run_at": rep["run_at"], "code_commit": rep["code_commit"], "gate_id": SR.GATE_ID,
            "gate_sha256": SR.gate_sha256(), "gate": rep["decision_gate"]["gate"],
            "replayable_evidence_start": win["replayable_evidence_start"],
            "replayable_records": win["replayable_records"], "replayable_slates": len(win["replayable_slate_dates"]),
            "graded_records": len({(r["slate_date"], r["eval_key"]) for r in act}), "graded_slates": len(graded_days),
            "rules": {ru: {k: m.get(k) for k in ("N", "brier", "market_brier", "log_loss", "market_log_loss",
                                                  "calibration_gap", "mean_clv", "roi")}
                      for ru, m in rep["rules"].items()},
            "actionability_validated": ME.ACTIONABILITY_VALIDATED, **rep["live"]}
    with open(out / "history.jsonl", "a") as f:
        f.write(json.dumps(head, sort_keys=True, default=str) + "\n")
    rep["headline"] = head
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -B -m cbb_platform.prod.selection_audit")
    ap.add_argument("command", choices=("audit", "verify-live", "daily"))
    ap.add_argument("ops_root")
    ap.add_argument("out_dir")
    ap.add_argument("--mode", choices=("prospective", "retrospective"), default="prospective")
    ap.add_argument("--from", dest="lo")
    ap.add_argument("--to", dest="hi")
    a = ap.parse_args(argv)
    if a.command == "verify-live":
        rep = verify_live(Path(a.ops_root), Path(a.out_dir))
        print(json.dumps({k: rep[k] for k in (*LIVE_KEYS, "evidence_window")}, indent=1, default=str))
        return 1 if "FAIL" in {rep[k] for k in LIVE_KEYS} else 0
    if a.command == "daily":
        rep = daily(Path(a.ops_root), Path(a.out_dir))
        print(json.dumps(rep["headline"], indent=1, default=str))
        return 1 if "FAIL" in set(rep["live"].values()) or rep["decision_gate"]["gate"] == "GATE_DEFINITION_CHANGED" \
            else 0
    rep = audit(Path(a.ops_root), Path(a.out_dir), a.mode, a.lo, a.hi)
    print(json.dumps({k: rep[k] for k in ("mode", "evidence_window", "records", "replay_hash", "decision_gate",
                                          "actionability_recommendation")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
