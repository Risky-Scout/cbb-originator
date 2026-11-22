"""Selection-rule / market-disagreement / STAT x ROLE x SIDE audit from frozen evidence (2026-11-22 directive §4-§8).

Run ON THE DROPLET with the deployed release, read-only, as cbb-originator (writes only <out_dir>):
    python -B selection_audit.py <ops_root> <out_dir> [first_day] [last_day]

Per frozen player MARKET_EVALUATION (ops/evaluations/<D>/market_evaluations.jsonl):
  * candidate set = its persisted candidate_offer_set when present (frozen after the observability release), else
    RECONSTRUCTED: prod.market_eval.evaluate(persisted PMF, the record's own decision observation file) -- accepted only
    when it reproduces the stored best offer exactly (selection_replay.matches_stored); otherwise the row is reported
    UNREPRODUCIBLE and excluded (never approximated);
  * the four rules of prod.selection_replay pick an offer each; settled on the final box score (VOID / PENDING
    excluded); CLV of the picked offer from the corrected close evidence (prod.market_exports.closing, the same
    function as Performance) with the picked offer's own entry fair probability.
Population ALL_MARKETS (information test, independent of any selection): one OVER offer per market (the line with the
most two-sided books, then book key) -> disagreement = model - fair, outcome; plus an OLS information slope.
"""
import collections, hashlib, json, math, sys
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform.prod import exports as EX, market_eval as ME, market_exports as MX, selection_replay as SR

def context(ops: Path, day: str, e: dict, cache: dict) -> dict:
    """Prediction-time player context of THIS record's forecast (tick projections or snapshot forecast_players)."""
    fid, kind = e.get("forecast_id"), e.get("source_kind")
    if (fid, kind) not in cache:
        base = (ops / "forecasts" / day / str(fid)) if kind == "FORECAST_SNAPSHOT" else ops / "ticks" / str(fid)
        frames = [pd.read_parquet(p) for p in (base / "projections.parquet", base / "forecast_players.parquet")
                  if p.exists()]
        m = {}
        for f in frames:
            for r in f.drop_duplicates("player_id").to_dict(orient="records"):
                m.setdefault(r["player_id"], {}).update({k: v for k, v in r.items() if k not in m.get(r["player_id"], {})})
        cache[(fid, kind)] = m
    return cache[(fid, kind)].get(e["player_id"], {})


def pmf_map(ops: Path, day: str) -> dict:
    games = EX.slate_games(ops, day)
    pmfs: list = []
    EX.distribution_records(ops, EX.RUNS_ROOT, games, {}, day, audit={}, pmf_out=pmfs)
    return {(r["game_id"], r["player_id"], r["stat"]): (np.asarray(r["pmf"], dtype=float), r.get("forecast_id"))
            for r in pmfs}


def market_rows(obs_q: pd.DataFrame, gid, pid, stat) -> pd.DataFrame:
    q = obs_q.assign(stat=obs_q["stat"].astype(object))
    ok = q["canonical_player_id"].map(lambda v: isinstance(v, str)) & q["stat"].map(lambda v: isinstance(v, str)) & \
        (q["canonical_game_id"] == gid)
    q = q[ok]
    return q[(q["canonical_player_id"] == pid) & (q["stat"] == stat)]


def info_slope(rows):
    """OLS of (y - q) on (p - q) over WIN/LOSS rows with a fair price: 1 = model fully informative beyond the market,
    0 = no information beyond the market."""
    g = [(r["model_p"] - r["fair_p"], (1.0 if r["result"] == "WIN" else 0.0) - r["fair_p"]) for r in rows
         if r.get("result") in ("WIN", "LOSS") and r.get("fair_p") is not None]
    if len(g) < 20:
        return {"N": len(g)}
    x, y = np.array([a for a, _ in g]), np.array([b for _, b in g])
    b = float((x * y).sum() / (x * x).sum())
    res = y - b * x
    se = float(math.sqrt((res ** 2).sum() / (len(x) - 1) / (x * x).sum()))
    return {"N": len(g), "slope": b, "se": se, "t": b / se if se else None}


def main():
    ops, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    days = sorted(p.parent.name for p in (ops / MX.EVAL_DIR).glob(f"*/{MX.EVAL_FILE}"))
    lo = sys.argv[3] if len(sys.argv) > 3 else days[0]; hi = sys.argv[4] if len(sys.argv) > 4 else days[-1]
    days = [d for d in days if lo <= d <= hi]
    picks, allm, recon = [], [], collections.Counter()
    for day in days:
        evals, _ = MX.load_evaluations(MX.eval_path(ops, day))
        if not evals:
            continue
        obs = MX.Observations(ops, day)
        act = MX.actuals(ops, {k[0] for k in evals})
        need_pmf = any(not e.get("candidate_offer_set") for e in evals.values())
        pm = pmf_map(ops, day) if need_pmf else {}
        frames, cache = {}, {}
        for k in sorted(evals):
            e = evals[k]
            if e.get("candidate_offer_set"):
                cands, src = SR.offers(e), "PERSISTED"
            else:
                p = pm.get(k)
                ob = next((o for o in obs.of_game(e["game_id"]) if o.get("file") == e.get("observation_file")), None)
                if p is None or ob is None:
                    recon["UNREPRODUCIBLE_MISSING_EVIDENCE"] += 1
                    continue
                f = e["observation_file"]
                if f not in frames:
                    frames[f] = obs.frame(ob)
                ev = ME.evaluate(p[0], market_rows(frames[f], *k))
                cands, src = SR.offers({"candidate_offer_set": ev["offer_set"]}), "RECONSTRUCTED"
            if not SR.matches_stored(e, cands):
                recon[f"UNREPRODUCIBLE_{src}"] += 1
                continue
            recon[src] += 1
            a = act.get((e["game_id"], e["player_id"]))
            g = MX.grade({**e}, act)
            if g["result"] not in MX.GRADED:
                continue
            y = g["actual"]
            ctx = context(ops, day, e, cache)
            rl = SR.role_tags(e.get("expected_minutes"), ctx)
            base = {"slate_date": day, "stat": e["stat"], "roles": rl, "game_id": e["game_id"],
                    "player_id": e["player_id"]}
            for r in SR.replay(e, cands, y):
                if r["picked"]:
                    cl = MX.closing(obs, {**e, "book": r["book"], "line": r["line"], "side": r["side"],
                                          "entry_fair_probability": r["fair_p"]})
                    r.update(probability_clv=cl["probability_clv"], clv_status=cl["clv_status"])
                picks.append({**base, **r})
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
    rh = SR.replay_hash([{k: v for k, v in r.items() if k != "roles"} for r in picks])
    pd.DataFrame(picks).to_parquet(out / "selection_picks.parquet", index=False)
    pd.DataFrame(allm).to_parquet(out / "all_markets.parquet", index=False)

    tab = SR.group_metrics

    act_picks = [r for r in picks if r["active"]]
    rep = {"days": days, "reconstruction": dict(recon), "replay_hash": rh,
           "rules": tab(act_picks, lambda r: [(r["rule"],)]),
           "rules_all_picks_incl_inactive": tab([r for r in picks if r["picked"]], lambda r: [(r["rule"],)]),
           "rule_x_side": tab(act_picks, lambda r: [(r["rule"], r["side"])]),
           "rule_x_stat": tab(act_picks, lambda r: [(r["rule"], r["stat"])]),
           "rule_x_role": tab(act_picks, lambda r: [(r["rule"], x) for x in r["roles"]]),
           "rule_x_prob": tab(act_picks, lambda r: [(r["rule"], r["prob_bucket"])]),
           "rule_x_disagreement": tab(act_picks, lambda r: [(r["rule"], r["disagreement_bucket"])]),
           "maxev_disagreement": tab([r for r in picks if r["rule"] == "MAX_EV" and r["picked"]],
                                     lambda r: [(r["disagreement_bucket"],)]),
           "maxev_disagreement_x_side": tab([r for r in picks if r["rule"] == "MAX_EV" and r["picked"]],
                                            lambda r: [(r["disagreement_bucket"], r["side"])]),
           "all_markets_disagreement": tab(allm, lambda r: [(r["disagreement_bucket"],)]),
           "all_markets_total": SR.metrics(allm),
           "info_slope_all_markets": info_slope(allm),
           "info_slope_by_rule": {ru: info_slope([r for r in act_picks if r["rule"] == ru]) for ru in SR.RULES},
           "stat_role_side": tab([r for r in picks if r["rule"] == "MAX_EV" and r["picked"]],
                                 lambda r: [(r["stat"], x, r["side"]) for x in r["roles"]]),
           "stat_role_side_flags": {k: SR.cell_flag(m) for k, m in tab(
               [r for r in picks if r["rule"] == "MAX_EV" and r["picked"]],
               lambda r: [(r["stat"], x, r["side"]) for x in r["roles"]]).items()},
           "stat_side": tab([r for r in picks if r["rule"] == "MAX_EV" and r["picked"]],
                            lambda r: [(r["stat"], r["side"])]),
           "not_derivable_roles": list(SR.ROLES_NOT_IN_FROZEN_EVIDENCE)}
    (out / "selection_audit.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: rep[k] for k in ("days", "reconstruction", "replay_hash", "rules", "info_slope_all_markets",
                                          "info_slope_by_rule")}, indent=1, default=str))


if __name__ == "__main__":
    main()
