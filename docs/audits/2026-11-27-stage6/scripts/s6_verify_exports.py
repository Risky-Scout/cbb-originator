"""Stage 06 independent verification of the player-prop probability mathematics and the daily exports.

INDEPENDENT: imports NO cbb_platform module. Pricing, de-vig, EV, best-offer ordering, tiers, settlement, grading and
aggregation are re-derived here from first principles and compared with what production persisted / exported.
Readers: a minimal xlsx reader (zipfile + ElementTree), pandas for parquet / jsonl.

    python -I s6_verify_exports.py <data root> <slate D> <out.json>

<data root> holds: exp<DD-next>/ (export folder D+1: Grades of D), exp<DD>/ (export folder D: Boards + Distributions of D),
eval/<D>/market_evaluations.jsonl, ticks/*/pmfs.parquet + ticks/*/actuals.parquet, forecasts/<D>/*/pmfs.parquet.
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
TOL = 1e-12
STATS = ("PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "PRA", "PR", "PA", "RA", "STOCKS")
COMBOS = {"PRA": ("PTS", "REB", "AST"), "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST"),
          "STOCKS": ("STL", "BLK")}


# ------------------------------------------------------------------ minimal xlsx reader
def xlsx(path):
    out = {}
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        names = [s.get("name") for s in wb.iter(NS + "sheet")]
        for i, n in enumerate(names, 1):
            ws = ET.fromstring(z.read(f"xl/worksheets/sheet{i}.xml"))
            grid = {}
            for c in ws.iter(NS + "c"):
                m = re.match(r"([A-Z]+)(\d+)", c.get("r"))
                col = 0
                for ch in m.group(1):
                    col = col * 26 + ord(ch) - 64
                if c.get("t") == "inlineStr":
                    v = "".join(t.text or "" for t in c.iter(NS + "t"))
                else:
                    vv = c.find(NS + "v")
                    v = None if vv is None else float(vv.text)
                grid.setdefault(int(m.group(2)) - 1, {})[col - 1] = v
            rows = []
            for r in range(max(grid) + 1 if grid else 0):
                d = grid.get(r, {})
                rows.append([d.get(k) for k in range(max(d) + 1)] if d else [])
            out[n] = rows
    return names, out


def table(rows):
    """First non-empty row = header; returns list of dicts (stops at the first blank row)."""
    h = rows[0]
    out = []
    for r in rows[1:]:
        if not r:
            break
        out.append({h[i]: (r[i] if i < len(r) else None) for i in range(len(h))})
    return out


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------ first-principles mathematics
def side_probs(pmf, lo, line, side):
    """P(win), P(lose), P(push) of `side` at `line` for P(X = lo + i) = pmf[i]."""
    x = lo + np.arange(len(pmf))
    over, under, push = pmf[x > line].sum(), pmf[x < line].sum(), pmf[x == line].sum()
    return (over, under, push) if side == "OVER" else (under, over, push)


def dec_from_american(a):
    return 1 + a / 100.0 if a > 0 else 1 + 100.0 / abs(a)


def ev(win, lose, d):
    return win * (d - 1) - lose


def devig(d_side, d_other):
    return (1 / d_side) / (1 / d_side + 1 / d_other)


def role(m):
    if m is None or (isinstance(m, float) and not math.isfinite(m)):
        return "UNKNOWN_ROLE"
    return "CORE_30_PLUS" if m >= 30 else "ROTATION_20_TO_30" if m >= 20 else "LIMITED_UNDER_20" if m > 0 else \
        "NO_EXPECTED_MINUTES"


def pmf_sha(lo, p):
    h = hashlib.sha256()
    h.update(np.array([lo, lo + len(p) - 1], dtype=np.int64).tobytes())
    h.update(np.ascontiguousarray(p, dtype=np.float64).tobytes())
    return h.hexdigest()


def close(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


class Tally:
    def __init__(self):
        self.c = Counter()
        self.ex = defaultdict(list)

    def chk(self, name, ok, example=None):
        self.c[(name, bool(ok))] += 1
        if not ok and len(self.ex[name]) < 5:
            self.ex[name].append(example)

    def report(self):
        names = sorted({k[0] for k in self.c})
        return {n: {"pass": self.c[(n, True)], "fail": self.c[(n, False)],
                    **({"examples": self.ex[n]} if self.ex[n] else {})} for n in names}


def main(root, D, out):
    T = Tally()
    nxt = str((pd.Timestamp(D) + pd.Timedelta(days=1)).date())
    tag = lambda d: pd.Timestamp(d).strftime("%m_%d_%Y")                 # noqa: E731
    expD, expN = os.path.join(root, f"exp{D[-2:]}"), os.path.join(root, f"exp{nxt[-2:]}")
    res = {"slate": D, "independent": "no cbb_platform import"}

    # ---------------------------------------------------------- PMF store: every compact PMF file available
    pmfs = {}
    for f in sorted(glob.glob(os.path.join(root, "ticks", "*", "pmfs.parquet")) +
                    glob.glob(os.path.join(root, "forecasts", D, "*", "pmfs.parquet"))):
        fid = os.path.basename(os.path.dirname(f))
        df = pd.read_parquet(f, columns=["game_id", "player_id", "stat", "support_min", "support_max", "probabilities",
                                         "pmf_sha256", "mean", "method"])
        for r in df.itertuples(index=False):
            pmfs[(fid, r.game_id, r.player_id, r.stat)] = r

    # ---------------------------------------------------------- A. Distributions (export folder D)
    refs = pd.read_parquet(os.path.join(expD, f"CBB_Distributions_{tag(D)}_pmf_refs.parquet"))
    rec = json.load(open(os.path.join(expD, "distributions_receipt.json")))
    T.chk("A0_receipt_pmf_parquet_sha256", sha_file(os.path.join(expD, rec["pmf_file"])) == rec["pmf_parquet_sha256"])
    T.chk("A0_receipt_xlsx_sha256", sha_file(os.path.join(expD, rec["file"])) == rec["xlsx_sha256"])
    T.chk("A0_receipt_rows", rec["rows"] == len(refs), [rec["rows"], len(refs)])
    keys = Counter(zip(refs.game_id, refs.player_id, refs.stat))
    T.chk("A1_unique_game_player_stat", max(keys.values()) == 1)
    per = refs.groupby(["game_id", "player_id"]).stat.apply(lambda s: tuple(sorted(s)))
    for k, v in per.items():
        T.chk("A2_all_12_supported_stats_per_player", v == tuple(sorted(STATS)), [k, v])
    _, dw = xlsx(os.path.join(expD, rec["file"]))
    drows = []
    for n, rows in dw.items():
        if n.startswith("Distributions"):
            drows += table(rows)
    T.chk("A3_summary_rows_eq_refs", len(drows) == len(refs), [len(drows), len(refs)])
    by_sha = {(r["FORECAST_ID"], r["PMF_SHA256"]): r for r in drows}
    tail_mass, mean_gap = [], []
    vec = {}
    for r in refs.itertuples(index=False):
        p = pmfs.get((r.forecast_id, r.game_id, r.player_id, r.stat))
        if p is None:
            T.chk("A4_pmf_located", False, [r.forecast_id, r.game_id, r.player_id, r.stat])
            continue
        T.chk("A4_pmf_located", True)
        v = np.asarray(p.probabilities, dtype=float)
        vec[(r.game_id, r.player_id, r.stat)] = (p.support_min, v, r.forecast_id)
        T.chk("A5_sha256_recomputed", pmf_sha(p.support_min, v) == r.pmf_sha256 == p.pmf_sha256, [r.pmf_sha256[:12]])
        T.chk("A6_mass_one_nonneg", abs(v.sum() - 1) <= 1e-9 and (v >= 0).all(), [r.game_id, r.player_id, r.stat, v.sum()])
        T.chk("A7_support_matches", p.support_max == p.support_min + len(v) - 1)
        x = p.support_min + np.arange(len(v))
        mu = float((x * v).sum())
        mean_gap.append(abs(mu - r.mean))
        tail_mass.append(float(v[-1]))
        s = by_sha.get((r.forecast_id, r.pmf_sha256))
        if s is None:
            T.chk("A8_summary_row_for_pmf", False, [r.forecast_id, r.pmf_sha256[:12]])
            continue
        sd = math.sqrt(max(float(((x - mu) ** 2 * v).sum()), 0))
        cdf = np.cumsum(v)
        q = {f"P{int(k * 100)}": float(x[min(np.searchsorted(cdf, k - 1e-12), len(v) - 1)]) for k in (.10, .25, .50, .75, .90)}
        ok = close(s["MEAN"], r.mean) and close(s["SD"], sd, 1e-9) and s["MEDIAN"] == q["P50"] and \
            all(s[k] == q[k] for k in ("P10", "P25", "P75", "P90"))
        T.chk("A8_summary_stats_recomputed", ok, [r.player_label, r.stat, s["MEAN"], mu, s["SD"], sd, s["MEDIAN"], q["P50"]])
    res["A_pmf_mean_vs_reported_mean"] = {"max_abs": max(mean_gap, default=None),
                                          "p99": float(np.quantile(mean_gap, .99)) if mean_gap else None}
    res["A_tail_point_mass"] = {"max": max(tail_mass, default=None), "n_over_1e-6": int(sum(t > 1e-6 for t in tail_mass))}
    # combos: means of sums == sums of means on the same draws (BANK_DRAWS combos vs base PMFs)
    cg = []
    for (g, pl, st), (lo, v, fid) in vec.items():
        if st in COMBOS and all((g, pl, b) in vec for b in COMBOS[st]):
            m = lambda t: float(((t[0] + np.arange(len(t[1]))) * t[1]).sum())   # noqa: E731
            cg.append(abs(m((lo, v)) - sum(m(vec[(g, pl, b)][:2]) for b in COMBOS[st])))
    res["A_combo_mean_minus_sum_of_base_means"] = {"n": len(cg), "max_abs": max(cg, default=None),
                                                   "median": float(np.median(cg)) if cg else None}

    # ---------------------------------------------------------- B. frozen evaluations of D (the graded evidence)
    ev_rows = [json.loads(l) for l in open(os.path.join(root, "eval", D, "market_evaluations.jsonl"))]
    evs = [e for e in ev_rows if e["record_type"] == "MARKET_EVALUATION"]
    T.chk("B0_unique_eval_keys", len({(e["game_id"], e["player_id"], e["stat"]) for e in evs}) == len(evs))
    res["B_evaluations"] = len(evs)
    order_mismatch = []
    for e in evs:
        p = pmfs.get((e["forecast_id"], e["game_id"], e["player_id"], e["stat"]))
        T.chk("B1_eval_pmf_located", p is not None, [e["forecast_id"], e["player_id"], e["stat"]])
        cands = []
        offers = [o for o in e["candidate_offer_set"] if o[17] == "ELIGIBLE"]
        T.chk("B2_candidate_count", len(offers) == e["candidate_offers"])
        idx = {(o[0], o[1], o[2]): o for o in offers}
        for o in offers:
            book, line, side, am, d = o[0], o[1], o[2], o[3], o[4]
            if am is not None:
                T.chk("B3_decimal_eq_american", abs(dec_from_american(am) - d) < 1e-9, [am, d])
            if p is not None:
                w, l, pu = side_probs(np.asarray(p.probabilities, float), p.support_min, line, side)
                T.chk("B4_pwin_plose_ppush_from_pmf", close(w, o[5], 1e-12) and close(l, o[6], 1e-12) and close(pu, o[7], 1e-12),
                      [e["player"], e["stat"], line, side, w, o[5]])
                T.chk("B4b_integer_line_push_only", (pu == 0) == (line != int(line) or pu == 0))
            w, l, pu = o[5], o[6], o[7]
            mp = w / (w + l)
            T.chk("B5_model_p_nonpush", close(mp, o[8], 1e-12), [mp, o[8]])
            other = idx.get((book, line, "UNDER" if side == "OVER" else "OVER"))
            fair = None if other is None else devig(d, other[4])
            T.chk("B6_same_book_devig", close(fair, o[9], 1e-12), [book, line, side, fair, o[9]])
            T.chk("B7_edge", close(None if fair is None else mp - fair, o[10], 1e-12))
            T.chk("B8_ev_push_aware", close(ev(w, l, d), o[11], 1e-12) and o[11] == o[12], [ev(w, l, d), o[11]])
            cands.append({"book": book, "line": line, "side": side, "d": d, "ev": ev(w, l, d),
                          "edge": None if fair is None else mp - fair, "mp": mp, "w": w, "l": l, "pu": pu, "fair": fair})
        if not cands:
            continue
        # independent best-offer rule: max EV; then max edge (missing last); then max model p; then max decimal;
        # then book asc, line asc, OVER first
        best = sorted(cands, key=lambda c: (-c["ev"], 0 if c["edge"] is not None else 1,
                                            -(c["edge"] or 0), -c["mp"], -c["d"], c["book"], c["line"],
                                            c["side"] != "OVER"))[0]
        ok = (best["book"], best["line"], best["side"]) == (e["book"], e["line"], e["side"]) and close(best["d"], e["decimal_odds"])
        T.chk("B9_best_offer_reproduced", ok, [e["player"], e["stat"], (best["book"], best["line"], best["side"]),
                                              (e["book"], e["line"], e["side"])])
        if not ok:
            order_mismatch.append(e["content_hash"])
        T.chk("B10_entry_fields_eq_best", close(e["model_probability"], best["mp"]) and close(e["ev"], best["ev"]) and
              close(e["fair_probability"], best["fair"]) and close(e["push_probability"], best["pu"]))
        T.chk("B11_one_offer_selected_per_key", True)
        pref = best["side"] if best["ev"] > 0 else "NO_POSITIVE_EV"
        T.chk("B12_preferred_side", pref == e["preferred_side"], [pref, e["preferred_side"]])
        paired_books = len({c["book"] for c in cands if c["line"] == best["line"] and c["fair"] is not None})
        T.chk("B13_books_at_line", paired_books == e["books_at_line"], [paired_books, e["books_at_line"]])
        rel = paired_books >= 2 or (e.get("consensus_line") is not None and float(e["consensus_line"]) == best["line"]
                                    and e.get("consensus_confidence") in ("MEDIUM", "HIGH"))
        T.chk("B14_reliable", rel == e["reliable"], [rel, e["reliable"]])
        screen = pref in ("OVER", "UNDER") and best["ev"] >= .03 and best["edge"] is not None and best["edge"] >= .03 \
            and best["mp"] >= .54 and rel
        tier = "PASS_NO_EDGE" if not screen else "65_PLUS" if best["mp"] >= .65 else "60_PLUS" if best["mp"] >= .60 else "ACTIONABLE"
        T.chk("B15_screen_and_tier", screen == e["screen_pass"] and tier == e["tier"], [screen, tier, e["tier"]])
        T.chk("B16_never_actionable_unvalidated", e["actionable"] is False)
        T.chk("B17_role_from_prediction_minutes", role(e.get("expected_minutes")) == e["player_role"],
              [e.get("expected_minutes"), e["player_role"]])
        cut, aso, fro = pd.Timestamp(e["cutoff_utc"]), pd.Timestamp(e["prediction_as_of"]), pd.Timestamp(e["frozen_at"])
        rec_at = pd.Timestamp(e["quote_received_at"])
        T.chk("B18_pit_prediction_and_quote_before_cutoff", aso < cut and rec_at < cut and fro >= cut,
              [e["player"], str(aso), str(rec_at), str(cut)])
        if p is not None:
            T.chk("B19_line_inside_support", best["line"] <= p.support_max - 0.5, [best["line"], p.support_max])
    res["B_order_mismatch_hashes"] = order_mismatch[:10]

    # ---------------------------------------------------------- C. settlement + Grades workbook (export folder D+1)
    gp = os.path.join(expN, f"CBB_Grades_{tag(D)}.xlsx")
    grec = json.load(open(os.path.join(expN, "grades_receipt.json")))
    T.chk("C0_grades_receipt_xlsx_sha256", sha_file(gp) == grec["xlsx_sha256"] and grec["graded_slate_date"] == D)
    gnames, gw = xlsx(gp)
    res["C_grades_sheet_order"] = gnames
    sec = gw[gnames[1]] if len(gnames) > 1 else []
    titles = [r[0] for r in sec if len(r) == 1]
    T.chk("C9_second_worksheet_success_by_stat_and_role",
          any("BY STAT" in str(t) for t in titles) and any("BY PLAYER ROLE" in str(t) for t in titles)
          and "POPULATIONS" in titles, [gnames[1] if len(gnames) > 1 else None])
    act = {}
    for f in sorted(glob.glob(os.path.join(root, "ticks", "*", "actuals.parquet"))):
        a = pd.read_parquet(f)
        for r in a.to_dict(orient="records"):
            k = (r["game_id"], r["player_id"])
            if k in act:
                same = act[k]["participation"] == r["participation"] and all(
                    (pd.isna(act[k][c]) and pd.isna(r[c])) or act[k][c] == r[c] for c in r if c.startswith("act_") or c == "minutes")
                T.chk("C1_settlement_rows_consistent_across_ticks", same, [k])
            else:
                act[k] = r
    gr = table(gw[gnames[0]])
    T.chk("C2_graded_rows_eq_evaluations", len(gr) == len(evs), [len(gr), len(evs)])
    by_hash = {e["content_hash"]: e for e in evs}
    mine = []
    for g in gr:
        e = by_hash.get(g["Evaluation Hash"])
        T.chk("C3_graded_row_traces_to_frozen_eval", e is not None)
        if e is None:
            continue
        T.chk("C4_graded_row_fields_eq_eval", g["Side"] == e["side"] and g["Line"] == e["line"] and g["Book"] == e["book"]
              and close(g["Model Probability"], e["model_probability"]) and close(g["EV"], e["ev"]) and
              g["Reporting Tier"] == e["tier"])
        a = act.get((e["game_id"], e["player_id"]))
        if a is None:
            res_, units, y = "PENDING", None, None
        elif a["participation"] == "GAME_NOT_PLAYED":
            res_, units, y = "VOID_NO_GAME", None, None
        elif a["participation"] != "PLAYED" or not a["minutes"] or pd.isna(a["minutes"]):
            res_, units, y = "VOID_DNP", None, None
        else:
            y = a[f"act_{e['stat']}"]
            if e["stat"] in COMBOS:
                T.chk("C5_combo_actual_eq_sum", y == sum(a[f"act_{b}"] for b in COMBOS[e["stat"]]), [e["stat"], y])
            res_ = "PUSH" if y == e["line"] else "WIN" if (y > e["line"]) == (e["side"] == "OVER") else "LOSS"
            units = {"WIN": e["decimal_odds"] - 1, "LOSS": -1.0, "PUSH": 0.0}[res_]
        T.chk("C6_result_independent", res_ == g["Result"], [e["player"], e["stat"], e["line"], e["side"], y, res_, g["Result"]])
        T.chk("C7_units_eq_decimal_minus_one", (units is None and g["Units"] is None) or
              (units is not None and g["Units"] is not None and abs(units - g["Units"]) < 1e-9),
              [res_, units, g["Units"], e["american_odds"], e["decimal_odds"]])
        mine.append({**g, "_res": res_, "_units": units})

    def metrics(rows):
        dec = [r for r in rows if r["_res"] in ("WIN", "LOSS")]
        grd = [r for r in rows if r["_res"] in ("WIN", "LOSS", "PUSH")]
        w = sum(r["_res"] == "WIN" for r in dec)
        p = np.array([r["Model Probability"] for r in dec], float)
        y = np.array([r["_res"] == "WIN" for r in dec], float)
        pc = np.clip(p, 1e-15, 1 - 1e-15)
        u = sum(r["_units"] for r in grd)
        return {"N": len(rows), "Graded": len(grd), "Wins": w, "Losses": len(dec) - w,
                "Pushes": sum(r["_res"] == "PUSH" for r in rows), "VOID_DNP": sum(r["_res"] == "VOID_DNP" for r in rows),
                "Hit Rate": w / len(grd) if grd else None,
                "Brier Score": float(np.mean((p - y) ** 2)) if dec else None,
                "Log Loss": float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc))) if dec else None,
                "Units Won": u if grd else None, "ROI": u / len(dec) if dec else None}

    for sheet, key in (("By Stat", ("Stat",)), ("By Player Role", ("Player Role",)), ("Stat x Role", ("Stat", "Player Role"))):
        if sheet not in gw:
            T.chk(f"C8_{sheet}_present", False)
            continue
        got = {tuple(r[k] for k in key): r for r in table(gw[sheet])}
        grp = defaultdict(list)
        for r in mine:
            grp[tuple(r[k] for k in key)].append(r)
        for k, rows in grp.items():
            m, g = metrics(rows), got.get(k)
            ok = g is not None and all(close(m[c], g.get(c), 1e-9) for c in m)
            T.chk(f"C8_{sheet}_metrics_recomputed", ok, [k, m, None if g is None else {c: g.get(c) for c in m}])
    allm = metrics(mine)
    res["C_all_rows_metrics"] = allm
    res["C_population"] = {"market_matched_graded_rows": len(mine),
                           "preferred_side_positive_ev": sum(r["Preferred Side"] in ("OVER", "UNDER") for r in mine),
                           "no_positive_ev_rows": sum(r["Preferred Side"] == "NO_POSITIVE_EV" for r in mine),
                           "screen_pass": sum(r["Reporting Tier"] != "PASS_NO_EDGE" for r in mine),
                           "official_eligible_selections": sum(r["Is Actionable"] == "YES" for r in mine)}

    # ---------------------------------------------------------- D. Boards of D (export folder D)
    bp = os.path.join(expD, f"CBB_Boards_{tag(D)}.xlsx")
    brec = json.load(open(os.path.join(expD, "boards_receipt.json")))
    T.chk("D0_boards_receipt_xlsx_sha256", sha_file(bp) == brec["xlsx_sha256"])
    bnames, bw = xlsx(bp)
    res["D_boards_sheet_order"] = bnames
    full = table(bw["Player Props"])
    cov = table(bw["Model Coverage"])
    k = lambda r: (r["Canonical Game ID"], r["Canonical Player ID"], r["Stat"])          # noqa: E731
    allk = Counter(map(k, full + cov))
    T.chk("D1_one_row_per_game_player_stat", max(allk.values()) == 1 and len(allk) == len(refs), [len(allk), len(refs)])
    T.chk("D2_full_board_all_valid_market", all(r["Market Status"] == "VALID_MARKET" for r in full))
    T.chk("D3_coverage_none_valid_market", all(r["Market Status"] != "VALID_MARKET" for r in cov))
    for sheet, tiers, thr in (("Player 60 Percent Plus", ("60_PLUS", "65_PLUS"), .60), ("Player 65 Percent Plus", ("65_PLUS",), .65)):
        s = table(bw[sheet])
        want = [r for r in full if r["Reporting Tier"] in tiers]
        T.chk(f"D4_{sheet}_exact_subset", [k(r) for r in s] == [k(r) for r in want] and s == want, [len(s), len(want)])
        T.chk(f"D5_{sheet}_threshold", all(r["Model Probability"] >= thr and r["Preferred EV"] >= .03 and
                                           r["Probability Edge"] >= .03 for r in s))
        T.chk(f"D6_{sheet}_complete_vs_model_p", all(r["Reporting Tier"] in tiers for r in full
                                                     if r["Reporting Tier"] != "PASS_NO_EDGE" and r["Model Probability"] >= thr))
    by_key = {(e["game_id"], e["player_id"], e["stat"]): e for e in evs}
    for r in full:
        T.chk("D7_tier_consistent_with_row", (r["Reporting Tier"] == "PASS_NO_EDGE") or (
            r["Preferred Side"] in ("OVER", "UNDER") and r["Preferred EV"] >= .03 and r["Probability Edge"] >= .03 and
            r["Model Probability"] >= .54), [r["Player"], r["Stat"]])
        T.chk("D8_is_actionable_NO", r["Is Actionable"] == "NO")
        e = by_key.get(k(r))
        # the Board prices the LATEST pre-cutoff observation, the frozen evaluation the DECISION observation (first one
        # at/after the PMF): they must agree exactly when both used the same PMF and the same quote instant
        if e is not None and r["Forecast ID"] == e["forecast_id"] and r["Best Book"] == e["book"] and \
                r["Best Market Line"] == e["line"] and r["Quote Timestamp"] == e["market_last_update"]:
            T.chk("D9_board_row_eq_frozen_eval_same_forecast_offer", close(r["Model Probability"], e["model_probability"]) and
                  close(r["Preferred EV"], e["ev"]))
    res["D_rows"] = {"full_board": len(full), "model_coverage": len(cov),
                     "60_plus": len(table(bw["Player 60 Percent Plus"])), "65_plus": len(table(bw["Player 65 Percent Plus"]))}
    res["checks"] = T.report()
    res["failed_checks"] = sorted(n for n, v in res["checks"].items() if v["fail"])
    json.dump(res, open(out, "w"), indent=1, default=str)
    print(json.dumps({"failed_checks": res["failed_checks"], "C_grades_sheet_order": gnames,
                      "population": res["C_population"]}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:4])
