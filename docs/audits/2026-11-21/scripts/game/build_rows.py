"""Independent reconstruction of every frozen game-market evaluation (READ-ONLY on copies of production evidence).

Output: rows.parquet (one row per evaluation) + games.parquet (one row per game PMF used) + recon summary.
The probability code below is written from the documented market contract, NOT imported from cbb_platform.game.
"""
import glob, gzip, json, math, re, sys, zlib, hashlib
from pathlib import Path
import numpy as np, pandas as pd, duckdb

S = Path(__file__).resolve().parent.parent
D = S / "data"
OUT = S / "game"
DB = OUT / "db" / "increment.duckdb"
DAYS = ["2026-11-19", "2026-11-20", "2026-11-21"]

# ------------------------------------------------------------------ evaluations
ev = []
for d in DAYS:
    for l in open(D / "evaluations" / d / "game_market_evaluations.jsonl"):
        e = json.loads(l)
        if e.get("kind") == "GAME_MARKET_EVALUATION":
            ev.append(e)
ev = pd.DataFrame(ev)
print("evaluations", len(ev), ev.slate_date.value_counts().to_dict())

# ------------------------------------------------------------------ PMFs (exact forecast_id x game_id)
pm = []
for d in DAYS:
    for f in glob.glob(str(D / "forecasts" / d / "*" / "game_pmfs.parquet")):
        pm.append(pd.read_parquet(f))
pm = pd.concat(pm, ignore_index=True)
pmk = pm.set_index(["forecast_id", "game_id"])


def decode(r):
    a = np.frombuffer(zlib.decompress(r["pmf_zlib"]), dtype=np.float32).reshape(int(r["rows"]), int(r["cols"]))
    sha_bytes_ok = None
    p = a.astype(np.float64)
    p = p / p.sum()
    return p, int(r["h_min"]), int(r["a_min"])


def margs(p, h0, a0):
    H = np.arange(p.shape[0]) + h0
    A = np.arange(p.shape[1]) + a0
    hh, aa = np.meshgrid(H, A, indexing="ij")
    out = {}
    for k, v in (("H", hh), ("A", aa), ("T", hh + aa), ("M", hh - aa)):
        s = pd.Series(p.ravel()).groupby(v.ravel()).sum()
        out[k] = s
    return out, hh, aa


def side_p(m, mt, side, pt):
    """(win, lose, push) from first principles."""
    base = mt.replace("ALT_", "")
    if base == "MONEYLINE":
        M = m["M"]
        hw, aw = M[M.index > 0].sum(), M[M.index < 0].sum()
        tie = M[M.index == 0].sum()
        w = hw if side == "HOME" else aw
        return w, 1 - w - tie, tie
    if base == "SPREAD":
        M = m["M"]
        v = M.index.values + pt if side == "HOME" else -M.index.values + pt   # side margin + point
        w, ps = M.values[v > 0].sum(), M.values[v == 0].sum()
        return w, 1 - w - ps, ps
    k = {"GAME_TOTAL": "T", "HOME_TEAM_TOTAL": "H", "AWAY_TEAM_TOTAL": "A"}[base]
    X = m[k]
    v = (X.index.values - pt) * (1 if side == "OVER" else -1)
    w, ps = X.values[v > 0].sum(), X.values[v == 0].sum()
    return w, 1 - w - ps, ps


def cdf_mid(series, x):
    """randomized-PIT midpoint for a discrete distribution."""
    return series[series.index < x].sum() + 0.5 * series.get(x, 0.0)


# ------------------------------------------------------------------ finals (promoted state, independent of grades workbook)
con = duckdb.connect(str(DB), read_only=True)
fin = con.execute("""
  select g.canonical_game_id game_id, g.home_team_id, g.away_team_id, g.home_points, g.away_points, g.neutral_site,
         g.conference_game, h.points h_box, a.points a_box, h.is_home h_is_home, a.is_home a_is_home,
         coalesce(h.overtime_count,0) ot, h.fta h_fta, a.fta a_fta, h.pf h_pf, a.pf a_pf, h.points_by_period hpp,
         a.points_by_period app
  from flagship.games g
  left join flagship.team_game_outcomes h on h.canonical_game_id=g.canonical_game_id and h.canonical_team_id=g.home_team_id
  left join flagship.team_game_outcomes a on a.canonical_game_id=g.canonical_game_id and a.canonical_team_id=g.away_team_id
  where g.season=2027""").df()
names = {a: (b, c) for a, b, c in con.execute("select canonical_team_id, school, display_name from core.teams").fetchall()}
json.dump(names, open(OUT / "team_names.json", "w"))
fin = fin.set_index("game_id")

# ------------------------------------------------------------------ rows
rows, games = [], {}
for e in ev.to_dict(orient="records"):
    key = (e["forecast_id"], e["game_id"])
    r = pmk.loc[key]
    if isinstance(r, pd.DataFrame):
        r = r.iloc[-1]
    p, h0, a0 = decode(r)
    # production sha256 recomputation is via cbb_platform; here we check the recorded hash is the evaluation's
    sha_match = r["pmf_sha256"] == e["pmf_sha256"]
    m, hh, aa = margs(p, h0, a0)
    w, l, ps = side_p(m, e["market_type"], e["side"], e.get("point"))
    mp = w / (1 - ps) if ps < 1 else 0
    f = fin.loc[e["game_id"]] if e["game_id"] in fin.index else None
    hp = None if f is None or pd.isna(f["home_points"]) else float(f["home_points"])
    ap = None if f is None or pd.isna(f["away_points"]) else float(f["away_points"])
    exp = {k: float((m[k].index.values * m[k].values).sum()) for k in "HATM"}
    sd = {k: float(np.sqrt(((m[k].index.values - exp[k]) ** 2 * m[k].values).sum())) for k in "HATM"}
    if e["game_id"] not in games and hp is not None:
        cov = float(((hh + aa - exp["T"]) * (hh - aa - exp["M"]) * p).sum())
        games[e["game_id"]] = dict(game_id=e["game_id"], slate=e["slate_date"], forecast_id=e["forecast_id"],
            home=hp, away=ap, ot=int(f["ot"]), mh=exp["H"], ma=exp["A"], mt=exp["T"], mm=exp["M"],
            sdT=sd["T"], sdM=sd["M"], sdH=sd["H"], sdA=sd["A"], rho=cov / (sd["T"] * sd["M"]),
            pit_T=cdf_mid(m["T"], hp + ap), pit_M=cdf_mid(m["M"], hp - ap), pit_H=cdf_mid(m["H"], hp),
            pit_A=cdf_mid(m["A"], ap), logp_T=math.log(max(m["T"].get(hp + ap, 1e-12), 1e-12)),
            top_down_home=r["top_down_home"], top_down_away=r["top_down_away"],
            avail_dh=r["availability_delta_home"], avail_da=r["availability_delta_away"],
            S_home=r["S_home"], S_away=r["S_away"], target_sd_total=r["target_sd_total"],
            target_sd_margin=r["target_sd_margin"], games_played_min=r["games_played_min"],
            neutral=bool(r["neutral_site"]), poss=r["expected_possessions"],
            home_known=r["home_known"], away_known=r["away_known"], cutoff=r["cutoff_utc"],
            pred_at=r["prediction_as_of"], h_box=f["h_box"], a_box=f["a_box"], h_is_home=f["h_is_home"],
            conf=f["conference_game"], ids_ok=(f["home_team_id"] == e["home_team_id"]) and (f["away_team_id"] == e["away_team_id"]) and
                 (r["home_team_id"] == e["home_team_id"]) and (r["away_team_id"] == e["away_team_id"]))
    res = None
    if hp is not None:
        base = e["market_type"].replace("ALT_", "")
        mg = hp - ap
        if base == "MONEYLINE":
            res = "WIN" if (mg > 0) == (e["side"] == "HOME") else "LOSS"
        else:
            if base == "SPREAD":
                v = (mg if e["side"] == "HOME" else -mg) + e["point"]
            else:
                sc = {"GAME_TOTAL": hp + ap, "HOME_TEAM_TOTAL": hp, "AWAY_TEAM_TOTAL": ap}[base]
                v = (sc - e["point"]) * (1 if e["side"] == "OVER" else -1)
            res = "WIN" if v > 0 else "PUSH" if v == 0 else "LOSS"
    rows.append(dict(slate=e["slate_date"], game_id=e["game_id"], market_type=e["market_type"], side=e["side"],
        point=e.get("point"), book=e["book"], decimal=e["decimal"], quote_id=e.get("quote_id"),
        recv=e.get("collector_received_at"), cutoff=e["cutoff_utc"], tip=e["scheduled_tip_utc"],
        p_frozen=e["model_p"], push_frozen=e["p_push"], p_recon=mp, push_recon=ps, sha_match=sha_match,
        fair=e.get("fair_p"), edge=e.get("edge"), ev=e["ev"], home=hp, away=ap, result=res,
        mh=exp["H"], ma=exp["A"], mt=exp["T"], mm=exp["M"], sdT=sd["T"], sdM=sd["M"], sdH=sd["H"], sdA=sd["A"],
        home_team_id=e["home_team_id"], away_team_id=e["away_team_id"], forecast_id=e["forecast_id"]))
R = pd.DataFrame(rows)
G = pd.DataFrame(games.values())
R.to_parquet(OUT / "rows.parquet", index=False)
G.to_parquet(OUT / "games.parquet", index=False)
R["dp"] = (R.p_frozen - R.p_recon).abs()
print("max |p_frozen - p_recon| =", R.dp.max(), " max |push diff| =", (R.push_frozen - R.push_recon).abs().max())
print("sha mismatches", (~R.sha_match).sum(), " graded rows", R.result.isin(["WIN", "LOSS", "PUSH"]).sum())
print("games", len(G), "ids_ok", G.ids_ok.all(), "box==game pts", ((G.h_box == G.home) & (G.a_box == G.away)).all(),
      "h_is_home", G.h_is_home.value_counts(dropna=False).to_dict())
