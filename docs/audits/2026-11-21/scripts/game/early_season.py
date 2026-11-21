"""Does the top-down ratings prior carry an EARLY-SEASON total/margin mean bias? (READ-ONLY research replay)

(1) 2026-27 season-to-date: replay the production ratings rule (sealed prior + production params, refit at each D's
    cutoff on games knowable before it) over every DI_VS_DI final in the promoted state; check the replay reproduces
    the frozen PMF means of the graded games; report residuals by date / games played.
(2) Historical 2023-24..2025-26: the same rule; total/margin bias and z-variance by min-games-played bucket
    (early season <=5 games vs later), i.e. is the current +3.8 total residual an early-season structural effect?
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parent / "pylib"))
from cbb_platform.game import forecast as GF, params as GP, ratings as GR, model as GMod  # noqa: E402
from cbb_platform.game.research import backtest as BT  # noqa: E402
from cbb_platform.walkforward import config as WC  # noqa: E402

gm = GP.game_model()
rp = GR.RatingParams(**{k: gm["ratings"][k] for k in ("tau_days", "lam_off", "lam_def", "lam_pace", "rho_carry", "lam_league")})
v = GMod.VarianceParams(**gm["variance"])
lines = []
P = lambda *a: lines.append(" ".join(str(x) for x in a))  # noqa: E731


def gp_before(g, cut):
    x = g[g["knowable_at"] < cut]
    return pd.concat([x["home"], x["away"]]).value_counts().to_dict()


def walk(g, prior, label):
    rows = []
    for d, day in g.groupby(g["game_date_et"].dt.date):
        cut = pd.Timestamp(WC.refit_cutoff(d))
        day = day[day["tip_utc"] >= cut]
        if not len(day):
            continue
        r = GR.fit(g, cut, prior, rp)
        pr = r.predict(day["home"].to_numpy(), day["away"].to_numpy(), day["neutral"].to_numpy())
        gp = gp_before(g, cut)
        o = day[["game_id", "game_date_et", "home", "away", "neutral", "home_pts", "away_pts", "ot"]].reset_index(drop=True)
        o = pd.concat([o, pr[["exp_home", "exp_away"]]], axis=1)
        o["n"] = [min(gp.get(h, 0), gp.get(a, 0)) for h, a in zip(o.home, o.away)]
        rows.append(o)
    df = pd.concat(rows, ignore_index=True)
    df["mt"], df["mm"] = df.exp_home + df.exp_away, df.exp_home - df.exp_away
    df["rT"] = df.home_pts + df.away_pts - df.mt
    df["rM"] = df.home_pts - df.away_pts - df.mm
    sd = np.array([v.sd(t, n) for t, n in zip(df.mt, df.n)])
    df["sdM"], df["sdT"] = sd[:, 0], sd[:, 1]
    df["label"] = label
    return df


def summ(df, by):
    o = df.groupby(by, observed=True).apply(lambda x: pd.Series({
        "n": len(x), "total_bias": x.rT.mean(), "t_total": x.rT.mean() / (x.rT.std() / np.sqrt(len(x))),
        "total_rmse": np.sqrt((x.rT ** 2).mean()), "zT_sd": (x.rT / x.sdT).std(), "margin_bias": x.rM.mean(),
        "margin_rmse": np.sqrt((x.rM ** 2).mean()), "zM_sd": (x.rM / x.sdM).std(), "ot_rate": (x.ot > 0).mean()}),
        include_groups=False)
    return o.round(3)


# ---------------------------------------------------------------- (1) 2026-27 season to date
inc = OUT / "db" / "increment.duckdb"
cur = GF.season_games(inc, 2027)
pri = GF.prior(2027)
c27 = walk(cur, pri, "2027")
G = pd.read_parquet(OUT / "games.parquet")
chk = G.merge(c27, on="game_id")
P("# (1) 2026-27 replay check vs frozen PMF means: n", len(chk), " max|home diff|",
  round(float((chk.mh - chk.exp_home).abs().max()), 4), " max|away diff|", round(float((chk.ma - chk.exp_away).abs().max()), 4))
c27["wk"] = pd.cut(c27.n, [-1, 1, 3, 5, 99], labels=["n<=1", "2-3", "4-5", "6+"])
P(summ(c27, "wk").to_string())
P("by date"); P(summ(c27, c27.game_date_et.dt.date).to_string())
P("ALL 2027", summ(c27.assign(a=1), "a").to_string())
c27.to_csv(OUT / "replay_2027.csv", index=False)

# ---------------------------------------------------------------- (2) historical early season
db = "data/lockbox_2025_26/canonical/cbb.duckdb"
games = BT.load(db)
prs = BT.prior_chain(games, 2014, 2026, rp)
H = pd.concat([walk(games[games.season == s], prs[s], str(s)) for s in (2024, 2025, 2026)], ignore_index=True)
H["wk"] = pd.cut(H.n, [-1, 1, 3, 5, 99], labels=["n<=1", "2-3", "4-5", "6+"])
P("\n# (2) historical seasons by min games played")
P(summ(H, ["label", "wk"]).to_string())
P(summ(H, "wk").to_string())
# first 3 weeks of season by calendar (comparable to Nov 3-20)
H["nov"] = H.game_date_et.dt.month.eq(11) & H.game_date_et.dt.day.le(20)
P("Nov 1-20 vs rest"); P(summ(H, ["label", "nov"]).to_string())
H.to_csv(OUT / "replay_hist.csv", index=False)
open(OUT / "early_season.txt", "w").write("\n".join(lines))
print("\n".join(lines))
