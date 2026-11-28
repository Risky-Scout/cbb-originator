"""Stage 08, read-only: matched market comparison (population C) at three market horizons.

For every frozen market evaluation (the production decision record):
  * the DECISION forecast is the PMF the evaluation priced (its forecast_id: an official tick K... or a snapshot F...);
  * the model probability of every contract is recomputed from that persisted PMF (validated against the evaluation's own
    model_p where the evaluation stored the same contract);
  * the market probability is the same-book two-way no-vig probability (prod.exports.no_vig convention) of the exact
    (book, line) contract at three horizons:
      OPEN      the first retained observation of that contract before tip,
      DECISION  the evaluation's own observation (player) / the last capture at or before the cutoff (game),
      CLOSE     the last retained observation before tip.
    The model forecast is held fixed (the decision forecast) at every horizon: at OPEN the model holds later information
    than the market, at CLOSE the market holds later information than the model. Labelled, never pooled.
Settlement: player props void if the player has no box line or 0 minutes (the PMF is conditional on playing); pushes are
excluded; probabilities are non-push conditional on both sides. Game markets settle on OT-inclusive final scores.

    s8_market.py MIRROR WORK
"""
import glob, json, os, sys, zlib
import numpy as np, pandas as pd

MIRROR, WORK = sys.argv[1], sys.argv[2]
CUT = pd.Timestamp("2026-11-27T20:40:23Z")
games = pd.read_parquet(f"{WORK}/games.parquet").set_index("game_id")
box = pd.read_parquet(f"{WORK}/box.parquet").set_index(["game_id", "player_id"])
complete = set(games.index[games.complete])
MK2STAT = {"player_points": "PTS", "player_rebounds": "REB", "player_assists": "AST", "player_threes": "FG3M",
           "player_steals": "STL", "player_blocks": "BLK", "player_turnovers": "TOV", "player_points_rebounds_assists": "PRA",
           "player_points_rebounds": "PR", "player_points_assists": "PA", "player_rebounds_assists": "RA",
           "player_blocks_steals": "STOCKS"}


def no_vig(d_side, d_other):
    if d_side is None or d_other is None or not np.isfinite(d_side) or not np.isfinite(d_other) or d_side <= 1 or d_other <= 1:
        return np.nan
    a, b = 1 / d_side, 1 / d_other
    return a / (a + b)


# ============================================================================ player markets
evs = []
for f in sorted(glob.glob(f"{MIRROR}/evaluations/*/market_evaluations.jsonl")):
    for l in open(f):
        x = json.loads(l)
        if x.get("record_type") != "MARKET_EVALUATION":
            continue
        evs.append({k: x.get(k) for k in ["game_id", "player_id", "stat", "forecast_id", "source_kind", "observation_file",
                                          "observation_received_at", "prediction_as_of", "cutoff_utc", "tip_utc", "book", "line",
                                          "side", "model_probability", "fair_probability", "player_role", "expected_minutes",
                                          "slate_date", "engine_id", "model_version", "tier", "preferred_side", "edge"]})
E = pd.DataFrame(evs)
E["dup"] = E.duplicated(["game_id", "player_id", "stat"], keep=False)
E.to_parquet(f"{WORK}/player_evals.parquet", index=False)

# decision PMFs (only the rows the evaluations reference)
pmf_cache = {}


def load_pmfs(fid: str, slate: str):
    if fid in pmf_cache:
        return pmf_cache[fid]
    if fid.startswith("K"):
        p = f"{MIRROR}/ticks/{fid}/pmfs.parquet"
    else:
        c = glob.glob(f"{MIRROR}/forecasts/*/{fid}/pmfs.parquet")
        p = c[0] if c else None
    out = {}
    if p and os.path.exists(p):
        d = pd.read_parquet(p)
        if "probabilities" in d:
            for r in d.itertuples(index=False):
                a = np.zeros(int(r.support_max) + 1)
                a[int(r.support_min):int(r.support_min) + len(r.probabilities)] = r.probabilities
                out[(r.game_id, r.player_id, r.stat)] = (a / a.sum(), r.prediction_as_of)
        else:
            for k, g in d.groupby(["game_id", "player_id", "stat"]):
                a = np.zeros(int(g.outcome.max()) + 1)
                a[g.outcome.to_numpy()] = g.probability.to_numpy()
                out[k] = (a / a.sum(), g.prediction_as_of.iloc[0])
    pmf_cache.clear() if len(pmf_cache) > 40 else None
    pmf_cache[fid] = out
    return out


def p_over(a: np.ndarray, line: float):
    k = np.arange(len(a))
    over, push = a[k > line].sum(), a[k == line].sum()
    return over / (1 - push) if push < 1 else np.nan, push


# all retained quote observations, paired per (file, book, player, stat, line)
obs = []
for f in sorted(glob.glob(f"{MIRROR}/quotes/*/observations/*.parquet")):
    d = pd.read_parquet(f, columns=["sportsbook", "market_key", "line", "side", "decimal_odds", "collector_received_at",
                                    "canonical_game_id", "canonical_player_id", "quote_status"])
    d = d[(d.quote_status == "VALID") & d.canonical_player_id.notna() & d.canonical_game_id.isin(complete)]
    if not len(d):
        continue
    d["file"] = os.path.relpath(f, f"{MIRROR}/quotes")
    o = d[d.side == "OVER"].merge(d[d.side == "UNDER"][["file", "sportsbook", "market_key", "line", "canonical_player_id", "decimal_odds"]],
                                  on=["file", "sportsbook", "market_key", "line", "canonical_player_id"], suffixes=("", "_u"))
    o["fair_over"] = [no_vig(a, b) for a, b in zip(o.decimal_odds, o.decimal_odds_u)]
    obs.append(o[["file", "canonical_game_id", "canonical_player_id", "sportsbook", "market_key", "line", "collector_received_at",
                  "decimal_odds", "decimal_odds_u", "fair_over"]])
O = pd.concat(obs, ignore_index=True)
O["stat"] = O.market_key.map(MK2STAT)
O = O.rename(columns={"canonical_game_id": "game_id", "canonical_player_id": "player_id", "sportsbook": "book"})
O["collector_received_at"] = pd.to_datetime(O.collector_received_at, utc=True)
O["tip"] = O.game_id.map(games["tip"])
O = O[O.collector_received_at < O.tip].drop_duplicates(["file", "game_id", "player_id", "stat", "book", "line"])
O.to_parquet(f"{WORK}/player_quote_pairs.parquet", index=False)

rows = []
Eu = E[E.game_id.isin(complete)].drop_duplicates(["game_id", "player_id", "stat"], keep="first")
Og = O.groupby(["game_id", "player_id", "stat"])
for e in Eu.itertuples(index=False):
    key = (e.game_id, e.player_id, e.stat)
    if key not in Og.groups:
        continue
    q = Og.get_group(key)
    pm = load_pmfs(e.forecast_id, e.slate_date).get(key)
    if pm is None:
        rows.append({"game_id": e.game_id, "player_id": e.player_id, "stat": e.stat, "status": "DECISION_PMF_NOT_FOUND"})
        continue
    a, pas = pm
    dec_file = e.observation_file
    has_box = key[:2] in box.index
    mins = box.loc[key[:2], "minutes"] if has_box else np.nan
    y = box.loc[key[:2], e.stat] if has_box else np.nan
    for (book, line), c in q.groupby(["book", "line"]):
        c = c.sort_values("collector_received_at")
        mp, push = p_over(a, line)
        dec = c[c.file.str.endswith(os.path.basename(dec_file))] if isinstance(dec_file, str) else c.iloc[0:0]
        rec = {"game_id": e.game_id, "player_id": e.player_id, "stat": e.stat, "book": book, "line": line,
               "forecast_id": e.forecast_id, "source_kind": e.source_kind, "decision_obs_at": e.observation_received_at,
               "player_role": e.player_role, "expected_minutes": e.expected_minutes, "slate_date": e.slate_date,
               "model_version": e.model_version, "engine_id": e.engine_id, "model_p_over": mp, "model_p_push": push,
               "eval_book": e.book, "eval_line": e.line, "eval_side": e.side, "eval_model_p": e.model_probability,
               "eval_fair_p": e.fair_probability, "minutes": mins, "y": y, "tip": c.tip.iloc[0],
               "open_at": c.collector_received_at.iloc[0], "open_fair": c.fair_over.iloc[0],
               "close_at": c.collector_received_at.iloc[-1], "close_fair": c.fair_over.iloc[-1],
               "dec_fair": dec.fair_over.iloc[0] if len(dec) else np.nan, "n_obs": len(c), "status": "OK"}
        rows.append(rec)
P = pd.DataFrame(rows)
P["void_dnp"] = ~(P.minutes.fillna(0) > 0)
P["push"] = P.y == P.line
P["over"] = (P.y > P.line).astype(float)
P.to_parquet(f"{WORK}/player_market_contracts.parquet", index=False)
print("player contracts", len(P), P.status.value_counts().to_dict())

# ============================================================================ game markets
gev = []
for f in sorted(glob.glob(f"{MIRROR}/evaluations/*/game_market_evaluations.jsonl")):
    for l in open(f):
        x = json.loads(l)
        if x.get("kind") in ("GAME_MARKET_EVALUATION", "GAME_CANDIDATE_OFFERS"):
            gev.append({k: x.get(k) for k in ["game_id", "kind", "forecast_id", "cutoff_utc", "scheduled_tip_utc", "market_type",
                                              "book", "point", "side", "model_p", "fair_p", "slate_date", "game_model_version"]}
                       | {"offers": x.get("offers")})
GE = pd.DataFrame(gev)
GE.drop(columns=["offers"]).to_parquet(f"{WORK}/game_evals.parquet", index=False)
dec = GE.groupby("game_id").agg(forecast_id=("forecast_id", "first"), cutoff=("cutoff_utc", "first"),
                                n_kinds=("kind", "nunique"), slate=("slate_date", "first"))
# eval-level model_p for validation: (game, market_type, book, side, point) -> model_p
val = {}
for r in GE.itertuples(index=False):
    if r.kind == "GAME_MARKET_EVALUATION":
        val[(r.game_id, r.market_type, r.book, r.side, None if r.point is None else float(r.point))] = r.model_p
    else:
        for o in r.offers or []:
            if o[18] == "ELIGIBLE":
                val[(r.game_id, o[1], o[0], o[2], None if o[3] is None else float(o[3]))] = o[9]

caps = []
for f in sorted(glob.glob(f"{MIRROR}/game_quotes/*/GQ*.parquet")):
    d = pd.read_parquet(f)
    d = d[d.game_id.isin(dec.index)]
    if len(d):
        caps.append(d)
C = pd.concat(caps, ignore_index=True)
C["t"] = pd.to_datetime(C.collector_received_at, utc=True)
C["tip"] = C.game_id.map(games["tip"])
C = C[C.t < C.tip]
C["mt"] = C.market_type
C["pt"] = C.point.astype(float)
# pair: totals / team totals by (capture, book, mt, point) OVER vs UNDER; spreads HOME p vs AWAY -p; ML HOME vs AWAY
def pair(C):
    out = []
    tt = C[C.side.isin(["OVER", "UNDER"])]
    o = tt[tt.side == "OVER"].merge(tt[tt.side == "UNDER"][["capture_id", "book", "mt", "pt", "decimal"]],
                                    on=["capture_id", "book", "mt", "pt"], suffixes=("", "_o"))
    o["side"] = "OVER"
    out.append(o)
    sp = C[C.mt.isin(["SPREAD", "ALT_SPREAD"])].copy()
    h, a = sp[sp.side == "HOME"].copy(), sp[sp.side == "AWAY"].copy()
    a["pt"] = -a["pt"]
    o = h.merge(a[["capture_id", "book", "mt", "pt", "decimal"]], on=["capture_id", "book", "mt", "pt"], suffixes=("", "_o"))
    o["side"] = "HOME"
    out.append(o)
    ml = C[C.mt == "MONEYLINE"]
    o = ml[ml.side == "HOME"].merge(ml[ml.side == "AWAY"][["capture_id", "book", "decimal"]], on=["capture_id", "book"], suffixes=("", "_o"))
    o["side"] = "HOME"
    out.append(o)
    x = pd.concat(out, ignore_index=True)
    x["fair"] = [no_vig(p, q) for p, q in zip(x.decimal, x.decimal_o)]
    return x[["capture_id", "game_id", "book", "mt", "pt", "side", "t", "tip", "fair", "decimal", "decimal_o"]]
CP = pair(C)
CP.to_parquet(f"{WORK}/game_quote_pairs.parquet", index=False)

gp_cache = {}


def game_joint(fid, gid):
    if fid not in gp_cache:
        c = glob.glob(f"{MIRROR}/forecasts/*/{fid}/game_pmfs.parquet")
        gp_cache[fid] = pd.read_parquet(c[0]).set_index("game_id") if c else None
    g = gp_cache[fid]
    if g is None or gid not in g.index:
        return None
    r = g.loc[gid]
    J = np.frombuffer(zlib.decompress(r["pmf_zlib"]), dtype=np.float32).reshape(int(r["rows"]), int(r["cols"])).astype(float)
    J /= J.sum()
    hs = np.arange(J.shape[0]) + int(r["h_min"]); as_ = np.arange(J.shape[1]) + int(r["a_min"])
    H, A = np.meshgrid(hs, as_, indexing="ij")
    return J.ravel(), H.ravel(), A.ravel(), r["prediction_as_of"], r.get("joint_version"), r.get("model_version")


def model_p(jt, mt, side, pt):
    w, H, A = jt[0], jt[1], jt[2]
    if mt == "MONEYLINE":
        win, lose = w[H > A].sum(), w[H < A].sum()
    elif mt in ("SPREAD", "ALT_SPREAD"):
        s = H - A + pt
        win, lose = w[s > 0].sum(), w[s < 0].sum()
    else:
        v = {"GAME_TOTAL": H + A, "ALT_TOTAL": H + A, "HOME_TEAM_TOTAL": H, "ALT_HOME_TEAM_TOTAL": H,
             "AWAY_TEAM_TOTAL": A, "ALT_AWAY_TEAM_TOTAL": A}[mt]
        win, lose = w[v > pt].sum(), w[v < pt].sum()
    return win / (win + lose) if win + lose > 0 else np.nan, 1 - win - lose


grows = []
for (gid, mt, book, pt), c in CP.groupby(["game_id", "mt", "book", "pt"], dropna=False):
    if gid not in dec.index or gid not in complete:
        continue
    jt = game_joint(dec.loc[gid, "forecast_id"], gid)
    if jt is None:
        continue
    c = c.sort_values("t")
    cut = pd.Timestamp(dec.loc[gid, "cutoff"])
    d_ = c[c.t <= cut]
    mp, push = model_p(jt, mt, c.side.iloc[0], pt)
    H, A = games.loc[gid, "home_points"], games.loc[gid, "away_points"]
    if mt == "MONEYLINE":
        res = float(H > A); ps = False
    elif mt in ("SPREAD", "ALT_SPREAD"):
        s = H - A + pt; res = float(s > 0); ps = s == 0
    else:
        v = {"GAME_TOTAL": H + A, "ALT_TOTAL": H + A, "HOME_TEAM_TOTAL": H, "ALT_HOME_TEAM_TOTAL": H,
             "AWAY_TEAM_TOTAL": A, "ALT_AWAY_TEAM_TOTAL": A}[mt]
        res = float(v > pt); ps = v == pt
    vk = (gid, mt, book, c.side.iloc[0], None if pd.isna(pt) else float(pt))
    grows.append({"game_id": gid, "mt": mt, "book": book, "pt": pt, "side": c.side.iloc[0], "model_p": mp, "model_push": push,
                  "eval_model_p": val.get(vk), "forecast_id": dec.loc[gid, "forecast_id"], "forecast_as_of": jt[3],
                  "joint_version": jt[4], "game_model_version": jt[5], "cutoff": cut, "tip": c.tip.iloc[0],
                  "open_at": c.t.iloc[0], "open_fair": c.fair.iloc[0], "close_at": c.t.iloc[-1], "close_fair": c.fair.iloc[-1],
                  "dec_at": d_.t.iloc[-1] if len(d_) else pd.NaT, "dec_fair": d_.fair.iloc[-1] if len(d_) else np.nan,
                  "n_obs": len(c), "result": res, "push": ps, "slate": dec.loc[gid, "slate"],
                  "game_universe": games.loc[gid, "game_universe"]})
G = pd.DataFrame(grows)
G.to_parquet(f"{WORK}/game_market_contracts.parquet", index=False)
print("game contracts", len(G), G.mt.value_counts().to_dict())
