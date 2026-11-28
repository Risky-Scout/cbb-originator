"""Stage 08, read-only: score every genuine historical pregame PMF (population A) against the frozen evaluation state.

Inputs (a byte-for-byte read-only mirror of the Droplet production evidence, rsync'd 2026-11-28 ~01:25Z):
  MIRROR/forecasts/<D>/F*/{pmfs,game_pmfs,forecast_players}.parquet, snapshot.json      (forecast snapshots)
  MIRROR/ticks/K*/{pmfs,forecast_players|projections}.parquet, tick.json                  (official cutoff ticks)
  MIRROR/state/S20261127T201352Z-8eeab0c20aef/increment.duckdb                            (frozen evaluation state)

Nothing is refit and no model code is imported: the PMFs are scored exactly as persisted.
Checkpointed: one output parquet per source file under WORK/player_rows and WORK/game_rows; existing outputs are skipped.

    s8_extract.py MIRROR WORK
"""
import glob, hashlib, json, os, sys, zlib
import duckdb, numpy as np, pandas as pd

MIRROR, WORK = sys.argv[1], sys.argv[2]
CUT = pd.Timestamp("2026-11-27T20:40:23Z")
EVAL_STATE = "S20261127T201352Z-8eeab0c20aef"
FROZEN_MODEL = "cbb-v2-jm1-final-shadow-1.0.0"
STATS = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "PRA", "PR", "PA", "RA", "STOCKS"]
LOG_FLOOR = 1e-12
os.makedirs(f"{WORK}/player_rows", exist_ok=True)
os.makedirs(f"{WORK}/game_rows", exist_ok=True)

# ------------------------------------------------------------------ frozen evaluation state: outcomes
con = duckdb.connect(f"{MIRROR}/state/{EVAL_STATE}/increment.duckdb", read_only=True, config={"threads": 1})
games = con.execute("""select canonical_game_id game_id, scheduled_tip_utc tip, game_universe, neutral_site, conference_game,
        home_team_id, away_team_id, home_conference_id, away_conference_id, home_points, away_points,
        has_player_box_score, has_team_box_score, game_date_et from flagship.games where season = 2027""").df()
box = con.execute("""select canonical_game_id game_id, canonical_player_id player_id, canonical_team_id team_id, identity_status,
        starter, minutes, points, reb, ast, three_pm, stl, blk, tov from flagship.player_game_outcomes where season = 2027""").df()
con.close()
games["tip"] = pd.to_datetime(games["tip"], utc=True)
games["complete"] = games.home_points.notna() & games.has_player_box_score.fillna(False) & games.has_team_box_score.fillna(False)
games = games[games.tip <= CUT]
games.to_parquet(f"{WORK}/games.parquet", index=False)
box = box[box.game_id.isin(games.loc[games.complete, "game_id"])].copy()
box["PTS"], box["REB"], box["AST"], box["FG3M"] = box.points, box.reb, box.ast, box.three_pm
box["STL"], box["BLK"], box["TOV"] = box.stl, box.blk, box.tov
box["PRA"] = box.PTS + box.REB + box.AST
box["PR"], box["PA"], box["RA"] = box.PTS + box.REB, box.PTS + box.AST, box.REB + box.AST
box["STOCKS"] = box.STL + box.BLK
box["played"] = box.minutes.fillna(0) > 0
# duplicate box lines for one (game, player) would make the join ambiguous: refuse rather than guess
dup = box.duplicated(["game_id", "player_id"], keep=False)
box.loc[dup].to_csv(f"{WORK}/box_duplicates.csv", index=False)
box = box[~dup]
box.to_parquet(f"{WORK}/box.parquet", index=False)
tipmap = games.set_index("game_id")["tip"]
complete = set(games.loc[games.complete, "game_id"])
bx = box.set_index(["game_id", "player_id"])


def rng_for(key: str):
    return np.random.default_rng(int(hashlib.blake2b(key.encode(), digest_size=8).hexdigest(), 16) % (2**63))


def score_matrix(P: np.ndarray, y: np.ndarray, u: np.ndarray):
    """P: n x K row-stochastic PMFs on support 0..K-1; y integer outcomes (may exceed K-1 -> folded into the last cell)."""
    n, K = P.shape
    F = np.cumsum(P, axis=1)
    k = np.arange(K)
    yc = np.clip(y, 0, K - 1).astype(int)
    crps = ((F - (k[None, :] >= yc[:, None])) ** 2).sum(axis=1)
    py = P[np.arange(n), yc]
    logs = -np.log(np.maximum(py, LOG_FLOOR))
    Fm = np.where(yc > 0, F[np.arange(n), np.maximum(yc - 1, 0)], 0.0)
    pit = Fm + u * py
    q = lambda a: (F >= a - 1e-12).argmax(axis=1)
    lo80, hi80, lo90, hi90, lo98, hi98 = q(.10), q(.90), q(.05), q(.95), q(.01), q(.99)
    return dict(crps=crps, logs=logs, log_floored=py < LOG_FLOOR, pit=pit,
                cov80=(yc >= lo80) & (yc <= hi80), w80=hi80 - lo80, cov90=(yc >= lo90) & (yc <= hi90), w90=hi90 - lo90,
                cov98=(yc >= lo98) & (yc <= hi98), above_q99=yc > hi98, below_q01=yc < lo98, y_beyond_support=y > K - 1)


def pmf_stack(d: pd.DataFrame) -> np.ndarray:
    smax = int(d.support_max.max())
    P = np.zeros((len(d), smax + 1))
    for i, (a, lo) in enumerate(zip(d["probabilities"].to_numpy(), d["support_min"].to_numpy())):
        a = np.asarray(a, dtype=float)
        P[i, int(lo):int(lo) + len(a)] = a
    return P


def player_ctx(dirpath: str) -> pd.DataFrame:
    for name in ("forecast_players.parquet", "projections.parquet"):
        f = f"{dirpath}/{name}"
        if os.path.exists(f):
            c = pd.read_parquet(f)
            keep = [x for x in ["game_id", "player_id", "p_play", "min_mu", "min_sigma", "availability_status", "player_suppression",
                                "cold_start_class", "role_diagnostic", "transfer_in", "evidence_bucket", "first_30_days"] if x in c]
            c = c[keep].drop_duplicates(["game_id", "player_id"])
            c["ctx_source"] = name.split(".")[0]
            return c
    return pd.DataFrame(columns=["game_id", "player_id"])


def process_player_file(f: str, kind: str):
    src = os.path.relpath(os.path.dirname(f), MIRROR)
    out = f"{WORK}/player_rows/{src.replace('/', '__')}.parquet"
    if os.path.exists(out):
        return
    d = pd.read_parquet(f)
    meta = {}
    if kind == "SNAPSHOT":
        s = json.load(open(f"{os.path.dirname(f)}/snapshot.json"))
        meta = {"window": s.get("window"), "code_commit": s.get("code_commit"), "state_version": s.get("state_version"),
                "model_kind": s.get("model_kind")}
    else:
        t = json.load(open(f"{os.path.dirname(f)}/tick.json"))
        meta = {"window": "OFFICIAL_TICK", "code_commit": t.get("source_commit") or t.get("code_commit"),
                "state_version": (t.get("state") or {}).get("version") if isinstance(t.get("state"), dict) else None,
                "model_kind": None}
    if not len(d):
        pd.DataFrame().to_parquet(out)
        return
    if "probabilities" not in d:                                  # legacy long format (outcome, probability) -> stacked
        keys = ["game_id", "player_id", "team_id", "stat", "prediction_as_of", "model_version"]
        d = d.sort_values(keys + ["outcome"])
        first = d.drop_duplicates(keys).copy()
        grp = d.groupby(keys, sort=False)
        first["support_min"] = grp["outcome"].min().to_numpy()
        first["support_max"] = grp["outcome"].max().to_numpy()
        vals = grp.apply(lambda g: (g["outcome"].to_numpy(), g["probability"].to_numpy()), include_groups=False).to_numpy()
        arrs = []
        for (o, p), lo, hi in zip(vals, first["support_min"], first["support_max"]):
            a_ = np.zeros(int(hi) - int(lo) + 1); a_[o - lo] = p; arrs.append(a_)
        first["probabilities"] = arrs
        d = first.drop(columns=["outcome", "probability"])
    for c in ("engine_id", "method"):
        if c not in d:
            d[c] = None
    d["prediction_as_of"] = pd.to_datetime(d["prediction_as_of"], utc=True)
    d = d[d.game_id.isin(complete)].copy()
    d["tip"] = d.game_id.map(tipmap)
    d = d[d.prediction_as_of < d.tip]
    if not len(d):
        pd.DataFrame().to_parquet(out)
        return
    P = pmf_stack(d)
    P = P / P.sum(axis=1, keepdims=True)
    K = P.shape[1]
    k = np.arange(K)
    mean = P @ k
    var = P @ (k ** 2) - mean ** 2
    d = d.reset_index(drop=True)
    j = d[["game_id", "player_id"]].merge(box[["game_id", "player_id", "minutes", "starter"] + STATS], on=["game_id", "player_id"],
                                           how="left", indicator=True)
    assert len(j) == len(d)
    has = (j["_merge"] == "both").to_numpy()
    minutes = j["minutes"].to_numpy(dtype=float)
    starter = j["starter"].to_numpy(dtype=object)
    y = np.full(len(d), -1.0)
    for st in STATS:
        m = (d.stat == st).to_numpy()
        y[m] = j.loc[m, st].fillna(-1).to_numpy(dtype=float)
    played = has & (np.nan_to_num(minutes) > 0) & (y >= 0)
    u = rng_for(src).random(len(d))
    res = pd.DataFrame({"game_id": d.game_id.values, "player_id": d.player_id.values, "team_id": d.team_id.values,
                        "stat": d.stat.values, "prediction_as_of": d.prediction_as_of.values, "tip": d.tip.values,
                        "model_version": d.model_version.values, "engine_id": d.engine_id.values, "method": d.method.values,
                        "source": src, "kind": kind, **meta,
                        "pmf_mean": mean.astype("float32"), "pmf_var": var.astype("float32"), "p0": P[:, 0].astype("float32"),
                        "persisted_mean": d["mean"].values.astype("float32") if "mean" in d else np.nan,
                        "box_line": has, "minutes": minutes, "starter": starter, "played": played, "y": y})
    sc = score_matrix(P, np.where(played, y, 0).astype(int), u)
    for c, v in sc.items():
        v = np.asarray(v)
        if v.dtype == bool:
            res[c] = np.where(played, v, False)
        elif v.dtype.kind in "iu":
            res[c] = np.where(played, v, -1).astype("int16")
        else:
            res[c] = np.where(played, v, np.nan).astype("float32")
    # ---- unconditional (participation x conditional PMF) for every forecast player-game with a recorded p_play
    ctx = player_ctx(os.path.dirname(f))
    res = res.merge(ctx, on=["game_id", "player_id"], how="left")
    pp = res["p_play"].to_numpy(dtype=float) if "p_play" in res else np.full(len(res), np.nan)
    okp = np.isfinite(pp)
    if okp.any():
        Pu = P[okp] * pp[okp, None]
        Pu[:, 0] += 1 - pp[okp]
        yu = np.where(played[okp], y[okp], 0).astype(int)
        su = score_matrix(Pu, yu, rng_for(src + "|U").random(okp.sum()))
        for c in ("crps", "logs", "pit"):
            col = np.full(len(res), np.nan)
            col[okp] = su[c]
            res[f"u_{c}"] = col.astype("float32")
    for c in ("crps", "logs", "pit"):
        if f"u_{c}" not in res:
            res[f"u_{c}"] = np.nan
    res.to_parquet(out, index=False)


def decode_game(row):
    a = np.frombuffer(zlib.decompress(row["pmf_zlib"]), dtype=np.float32).reshape(int(row["rows"]), int(row["cols"])).astype(float)
    return a / a.sum()


def marg_scores(p: np.ndarray, support: np.ndarray, y: float, u: float):
    F = np.cumsum(p)
    j = int(np.searchsorted(support, y))
    inside = 0 <= j < len(support) and support[j] == y
    py = p[j] if inside else 0.0
    Fm = F[j - 1] if j > 0 else 0.0
    crps = float((((F - (support >= y)) ** 2) * np.r_[np.diff(support), 1]).sum())
    q = lambda a: support[int((F >= a - 1e-12).argmax())]
    mean = float(p @ support)
    sd = float(np.sqrt(max(p @ support ** 2 - mean ** 2, 0)))
    return {"mean": mean, "sd": sd, "crps": crps, "logs": -np.log(max(py, LOG_FLOOR)), "pit": Fm + u * py,
            "cov80": q(.10) <= y <= q(.90), "cov90": q(.05) <= y <= q(.95), "cov98": q(.01) <= y <= q(.99),
            "w80": q(.90) - q(.10), "w90": q(.95) - q(.05)}


def process_game_file(f: str):
    src = os.path.relpath(os.path.dirname(f), MIRROR)
    out = f"{WORK}/game_rows/{src.replace('/', '__')}.parquet"
    if os.path.exists(out):
        return
    g = pd.read_parquet(f)
    rows = []
    G = games.set_index("game_id")
    rng = rng_for(src + "|G")
    for _, r in g.iterrows():
        gid = r["game_id"]
        if gid not in complete:
            continue
        asof = pd.Timestamp(r["prediction_as_of"])
        if asof >= G.loc[gid, "tip"]:
            continue
        J = decode_game(r)
        hs = np.arange(J.shape[0]) + int(r["h_min"])
        as_ = np.arange(J.shape[1]) + int(r["a_min"])
        H, A = float(G.loc[gid, "home_points"]), float(G.loc[gid, "away_points"])
        ph, pa = J.sum(axis=1), J.sum(axis=0)
        HH, AA = np.meshgrid(hs, as_, indexing="ij")
        tot = (HH + AA).ravel(); mar = (HH - AA).ravel(); w = J.ravel()
        ts = np.arange(tot.min(), tot.max() + 1); pt = np.bincount(tot - tot.min(), weights=w, minlength=len(ts))
        ms = np.arange(mar.min(), mar.max() + 1); pm = np.bincount(mar - mar.min(), weights=w, minlength=len(ms))
        rec = {"game_id": gid, "source": src, "prediction_as_of": asof, "window": r.get("window"), "joint_version": r.get("joint_version"),
               "model_version": r.get("model_version"), "params_sha256": r.get("params_sha256"), "status": r.get("status"),
               "player_engine_id": r.get("player_engine_id"), "home_known": r.get("home_known"), "away_known": r.get("away_known"),
               "games_played_min": r.get("games_played_min"), "neutral_site": r.get("neutral_site"),
               "availability_delta_home": r.get("availability_delta_home"), "availability_delta_away": r.get("availability_delta_away"),
               "H": H, "A": A, "T": H + A, "M": H - A,
               "p_home_win": float(r.get("p_home_win")), "tie_mass": float(r.get("tie_mass")), "rho_mt_model": float(r.get("rho_mt")),
               "pmf_sum_raw": float(np.frombuffer(zlib.decompress(r["pmf_zlib"]), dtype=np.float32).sum())}
        # model covariance of home/away
        mh, ma = float(ph @ hs), float(pa @ as_)
        rec["corr_ha_model"] = float(((HH - mh) * (AA - ma) * J).sum() / np.sqrt((ph @ (hs - mh) ** 2) * (pa @ (as_ - ma) ** 2)))
        for name, (p, sup, y) in {"home": (ph, hs, H), "away": (pa, as_, A), "total": (pt, ts, H + A), "margin": (pm, ms, H - A)}.items():
            for k2, v in marg_scores(p, sup.astype(float), y, rng.random()).items():
                rec[f"{name}_{k2}"] = v
        rows.append(rec)
    pd.DataFrame(rows).to_parquet(out, index=False)


snap = sorted(glob.glob(f"{MIRROR}/forecasts/*/F*/pmfs.parquet"))
ticks = sorted(glob.glob(f"{MIRROR}/ticks/*/pmfs.parquet"))
gp = sorted(glob.glob(f"{MIRROR}/forecasts/*/F*/game_pmfs.parquet"))
for i, f in enumerate(snap):
    process_player_file(f, "SNAPSHOT")
    print("snap", i, len(snap), flush=True) if i % 10 == 0 else None
for i, f in enumerate(ticks):
    process_player_file(f, "TICK")
    print("tick", i, len(ticks), flush=True) if i % 25 == 0 else None
for f in gp:
    process_game_file(f)
print("DONE", len(snap), len(ticks), len(gp))
