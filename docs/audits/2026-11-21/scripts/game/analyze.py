"""Residual / variance / selection diagnosis of the graded game markets (READ-ONLY)."""
import glob, json, math
from pathlib import Path
import numpy as np, pandas as pd
from scipy import stats

OUT = Path(__file__).resolve().parent
D = OUT.parent / "data"
R = pd.read_parquet(OUT / "rows.parquet")
G = pd.read_parquet(OUT / "games.parquet")
rep = []
P = lambda *a: rep.append(" ".join(str(x) for x in a))  # noqa: E731

# ------------------------------------------------------------------ market consensus at each game's cutoff (latest capture per book)
q = pd.concat([pd.read_parquet(f) for d in ("2026-11-19", "2026-11-20", "2026-11-21")
               for f in glob.glob(str(D / "game_quotes" / d / "GQ*.parquet"))], ignore_index=True)
q["t"] = pd.to_datetime(q.collector_received_at, utc=True)
cut = R.drop_duplicates("game_id").set_index("game_id")["cutoff"].map(lambda x: pd.Timestamp(x))
mk = {}
stale_rows = []
for gid, x in q.groupby("game_id"):
    if gid not in cut.index:
        continue
    x = x[x.t <= cut[gid]]
    if not len(x):
        continue
    # latest capture per (book, market_type): the book's live board at cutoff
    lastcap = x.groupby(["book", "market_type"]).t.transform("max")
    live = x[x.t == lastcap]
    def med(mt, side):
        y = live[(live.market_type == mt) & (live.side == side)]
        if not len(y):
            return np.nan
        # main line per book = the pair closest to even odds
        y = y.assign(dist=(y.decimal - 1.95).abs()).sort_values("dist").drop_duplicates("book")
        return float(y.point.median())
    mk[gid] = dict(mkt_total=med("GAME_TOTAL", "OVER"), mkt_home_spread=med("SPREAD", "HOME"),
                   mkt_htt=med("HOME_TEAM_TOTAL", "OVER"), mkt_att=med("AWAY_TEAM_TOTAL", "OVER"),
                   live_keys=set(zip(live.book, live.market_type, live.side, live.point.fillna(-9999))))
for r in R.itertuples():
    lk = mk.get(r.game_id, {}).get("live_keys", set())
    stale_rows.append(not ((r.book, r.market_type, r.side, -9999 if pd.isna(r.point) else r.point) in lk))
R["stale_offer"] = stale_rows
M = pd.DataFrame({g: {k: v for k, v in d.items() if k != "live_keys"} for g, d in mk.items()}).T
M.index.name = "game_id"
G = G.merge(M, left_on="game_id", right_index=True, how="left")
G["mkt_margin"] = -G.mkt_home_spread
G["act_T"], G["act_M"] = G.home + G.away, G.home - G.away
G["rT"], G["rM"], G["rH"], G["rA"] = G.act_T - G.mt, G.act_M - G.mm, G.home - G.mh, G.away - G.ma
G["mkt_rT"], G["mkt_rM"] = G.act_T - G.mkt_total, G.act_M - G.mkt_margin
G["model_minus_mkt_T"] = G.mt - G.mkt_total
G["model_minus_mkt_M"] = G.mm - G.mkt_margin
G.to_csv(OUT / "games_residuals.csv", index=False)


def ms(x):
    x = pd.Series(x).dropna()
    n = len(x)
    if n < 2:
        return f"n={n}"
    t = x.mean() / (x.std(ddof=1) / math.sqrt(n))
    return f"n={n:3d} mean={x.mean():+6.2f} sd={x.std(ddof=1):5.2f} t={t:+5.2f}"


P("# Per-GAME residuals (effective sample = unique graded games)")
for k, lab in (("rH", "actual_home-model_home"), ("rA", "actual_away-model_away"), ("rT", "actual_total-model_total"),
               ("rM", "actual_margin-model_margin"), ("mkt_rT", "actual_total-MARKET_total"),
               ("mkt_rM", "actual_margin-MARKET_margin"), ("model_minus_mkt_T", "model_total-market_total"),
               ("model_minus_mkt_M", "model_margin-market_margin")):
    P(f"{lab:32s}", ms(G[k]))
P("RMSE total: model", round(float(np.sqrt((G.rT ** 2).mean())), 2), " market", round(float(np.sqrt((G.mkt_rT ** 2).mean())), 2),
  "| RMSE margin: model", round(float(np.sqrt((G.rM ** 2).mean())), 2), " market", round(float(np.sqrt((G.mkt_rM ** 2).mean())), 2))
P("corr(model-mkt total, actual-mkt total) =", round(G[["model_minus_mkt_T", "mkt_rT"]].corr().iloc[0, 1], 3),
  "  (positive = model total deviations carry signal)")
P("corr(model-mkt margin, actual-mkt margin) =", round(G[["model_minus_mkt_M", "mkt_rM"]].corr().iloc[0, 1], 3))
b = np.polyfit(G.dropna(subset=["mkt_total"]).model_minus_mkt_T, G.dropna(subset=["mkt_total"]).mkt_rT, 1)
P("OLS actual-mkt total on model-mkt total: slope", round(b[0], 3), "intercept", round(b[1], 2))

P("\n# Variance: realized residual sd vs PMF sd")
P("total: realized sd(rT)", round(G.rT.std(), 2), " RMS(rT)", round(float(np.sqrt((G.rT ** 2).mean())), 2),
  " mean PMF sdT", round(G.sdT.mean(), 2), " ratio RMS/sd", round(float(np.sqrt((G.rT ** 2).mean()) / G.sdT.mean()), 3))
P("margin: realized sd(rM)", round(G.rM.std(), 2), " RMS(rM)", round(float(np.sqrt((G.rM ** 2).mean())), 2),
  " mean PMF sdM", round(G.sdM.mean(), 2), " ratio", round(float(np.sqrt((G.rM ** 2).mean()) / G.sdM.mean()), 3))
P("z-score total sd (should be 1):", round((G.rT / G.sdT).std(), 3), " margin:", round((G.rM / G.sdM).std(), 3))
for k in ("pit_T", "pit_M", "pit_H", "pit_A"):
    h = np.histogram(G[k], bins=[0, .2, .4, .6, .8, 1.0])[0]
    ks = stats.kstest(G[k], "uniform")
    P(f"PIT {k}: quintile counts {h.tolist()}  mean {G[k].mean():.3f}  KS p={ks.pvalue:.3f}")
P("rho_mt PMF mean", round(G.rho.mean(), 3), " realized corr(rT, rM)", round(G[["rT", "rM"]].corr().iloc[0, 1], 3))
P("chi2 test of total variance (sum z^2, df=n):", round(float(((G.rT / G.sdT) ** 2).sum()), 1), "n", len(G),
  " p(upper)=", round(1 - stats.chi2.cdf(((G.rT / G.sdT) ** 2).sum(), len(G)), 3))

P("\n# Components")
P("OT games", int((G.ot > 0).sum()), "of", len(G), "; OT games residual total", ms(G[G.ot > 0].rT))
P("regulation-only games residual total", ms(G[G.ot == 0].rT))
P("availability delta |dh|+|da| > 0 in", int(((G.avail_dh.abs() + G.avail_da.abs()) > 0).sum()), "games; mean gamma-shift",
  round(float((0.3 * (G.avail_dh + G.avail_da)).mean()), 3))
G["td_total"] = G.top_down_home + G.top_down_away
P("top_down total - PMF total:", ms(G.td_total - G.mt), " (endgame/OT kernel + availability effect on the mean)")
P("actual - top_down total:", ms(G.act_T - G.td_total))
P("games_played_min distribution", G.games_played_min.describe()[["min", "50%", "max"]].to_dict())
P("neutral games", int(G.neutral.sum()), " residual total neutral", ms(G[G.neutral].rT), " non-neutral", ms(G[~G.neutral].rT))
P("conference games", int(G.conf.fillna(False).sum()))
for s in ("2026-11-19", "2026-11-20"):
    P("slate", s, "rT", ms(G[G.slate == s].rT), "| rM", ms(G[G.slate == s].rM))

# ------------------------------------------------------------------ buckets (per game, and per evaluation row)
G["tb"] = pd.cut(G.mt, [0, 135, 145, 155, 400], right=False, labels=["<135", "135-145", "145-155", "155+"])
G["mb"] = pd.cut(G.mm.abs(), [0, 3, 7, 12, 100], right=False, labels=["<3", "3-7", "7-12", "12+"])
P("\n# Per-game residuals by MODEL total bucket")
for k, x in G.groupby("tb", observed=True):
    P(f"{k:8s} rT {ms(x.rT)} | mkt_rT {ms(x.mkt_rT)} | model-mkt {x.model_minus_mkt_T.mean():+.2f}")
P("# Per-game residuals by |model margin| bucket")
for k, x in G.groupby("mb", observed=True):
    P(f"{k:6s} rM {ms(x.rM)} | rT {ms(x.rT)}")

gr = R[R.result.isin(["WIN", "LOSS"])].merge(G[["game_id", "rH", "rA", "rT", "rM", "mkt_total", "model_minus_mkt_T",
                                                  "mkt_htt", "mkt_att", "tb", "mb"]], on="game_id")
gr["y"] = (gr.result == "WIN").astype(float)
gr["base"] = gr.market_type
P("\n# Per-EVALUATION residuals by market type x side (rows share games: not independent)")
for (mt, sd), x in gr.groupby(["base", "side"]):
    P(f"{mt:16s} {sd:5s} n={len(x):3d} games={x.game_id.nunique():3d} W-L {int(x.y.sum())}-{int(len(x)-x.y.sum())} "
      f"p={x.p_recon.mean():.3f} hit={x.y.mean():.3f} | rH {x.rH.mean():+.2f} rA {x.rA.mean():+.2f} rT {x.rT.mean():+.2f} "
      f"rM {x.rM.mean():+.2f} | stale {x.stale_offer.mean():.2f}")
P("# by total bucket")
for k, x in gr.groupby("tb", observed=True):
    P(f"{k:8s} n={len(x)} games={x.game_id.nunique()} hit={x.y.mean():.3f} p={x.p_recon.mean():.3f} rT {x.rT.mean():+.2f}")

# ------------------------------------------------------------------ totals-family UNDER selection
tot = gr[gr.base.isin(["GAME_TOTAL", "HOME_TEAM_TOTAL", "AWAY_TEAM_TOTAL"])].copy()
tot["model_pt"] = np.where(tot.base == "GAME_TOTAL", tot.mt, np.where(tot.base == "HOME_TEAM_TOTAL", tot.mh, tot.ma))
tot["act_pt"] = np.where(tot.base == "GAME_TOTAL", tot.home + tot.away, np.where(tot.base == "HOME_TEAM_TOTAL", tot.home, tot.away))
tot["model_minus_line"] = tot.model_pt - tot.point
tot["act_minus_line"] = tot.act_pt - tot.point
tot["act_minus_model"] = tot.act_pt - tot.model_pt
P("\n# Totals family (GAME/HOME/AWAY totals) by side: model mean vs line vs actual")
for sd, x in tot.groupby("side"):
    P(f"{sd:5s} n={len(x)} games={x.game_id.nunique()} model-line {x.model_minus_line.mean():+.2f}  actual-line "
      f"{x.act_minus_line.mean():+.2f}  actual-model {x.act_minus_model.mean():+.2f} (t={x.act_minus_model.mean()/(x.act_minus_model.std()/math.sqrt(len(x))):+.2f})"
      f"  hit {x.y.mean():.3f} p {x.p_recon.mean():.3f}")
for (b_, sd), x in tot.groupby(["base", "side"]):
    P(f"  {b_:16s} {sd:5s} n={len(x)} model-line {x.model_minus_line.mean():+.2f} actual-line {x.act_minus_line.mean():+.2f} "
      f"actual-model {x.act_minus_model.mean():+.2f}")

# stale offers
P("\n# Stale offers (point not on the book's latest pre-cutoff board for that market)")
P("all evaluations stale share", round(R.stale_offer.mean(), 3), "; graded", round(gr.stale_offer.mean(), 3))
for k, x in gr.groupby("stale_offer"):
    P(f"stale={k}: n={len(x)} hit={x.y.mean():.3f} p={x.p_recon.mean():.3f} edge={x.edge.mean():.3f}")
for k, x in gr[gr.side == "UNDER"].groupby("stale_offer"):
    P(f"UNDER stale={k}: n={len(x)} W-L {int(x.y.sum())}-{int(len(x)-x.y.sum())} p={x.p_recon.mean():.3f}")

# ------------------------------------------------------------------ proper scores of alternative probability sources
def score(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(((p - y) ** 2).mean()), float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())
x = gr.dropna(subset=["fair"])
P("\n# Proper scores on graded rows with a de-vig pair (n=%d)" % len(x))
P("model", score(x.p_recon.values, x.y.values), " market", score(x.fair.values, x.y.values),
  " 50/50", score(np.full(len(x), .5), x.y.values))
for sd in ("OVER", "UNDER"):
    z = x[x.side == sd]
    P(sd, "model", score(z.p_recon.values, z.y.values), "market", score(z.fair.values, z.y.values), "n", len(z))

# under-selection explanation: market-type rows where model total < line are the UNDER picks; selection effect
P("\n# Selection: the evaluated side is argmax EV across books x lines x sides (best_by_market).")
P("mean edge (model_p - fair) of graded rows", round(x.edge.mean(), 4), " UNDER", round(x[x.side == 'UNDER'].edge.mean(), 4),
  " OVER", round(x[x.side == 'OVER'].edge.mean(), 4))

open(OUT / "analysis.txt", "w").write("\n".join(rep))
gr.to_csv(OUT / "graded_rows_residuals.csv", index=False)
tot.to_csv(OUT / "totals_family_rows.csv", index=False)
print("\n".join(rep))
