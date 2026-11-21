"""Game-clustered significance of the UNDER gap and the UNDER-vs-OVER asymmetry (READ-ONLY)."""
from pathlib import Path
import numpy as np, pandas as pd
OUT = Path(__file__).resolve().parent
gr = pd.read_csv(OUT / "graded_rows_residuals.csv"); tot = pd.read_csv(OUT / "totals_family_rows.csv")
rng = np.random.default_rng(20261121); L = []
def boot(df, stat, B=20000):
    ids = df.game_id.unique(); grp = {g: x for g, x in df.groupby("game_id")}
    v = [stat(pd.concat([grp[g] for g in rng.choice(ids, len(ids))])) for _ in range(B)]
    return np.percentile(v, [2.5, 97.5])
u = gr[gr.side == "UNDER"]
gap = lambda x: x.y.mean() - x.p_recon.mean()
L.append(f"UNDER rows {len(u)} games {u.game_id.nunique()} hit-p {gap(u):+.3f} game-clustered 95% CI {boot(u, gap).round(3).tolist()}")
o = gr[gr.side == "OVER"]
L.append(f"OVER  rows {len(o)} games {o.game_id.nunique()} hit-p {gap(o):+.3f} CI {boot(o, gap).round(3).tolist()}")
mg = lambda x: x.y.mean() - x.fair.mean()
L.append(f"UNDER hit - MARKET fair {mg(u):+.3f} CI {boot(u, mg).round(3).tolist()}  (market also over-predicted UNDER)")
# totals family: actual-line relative to model-line, i.e. does the model's disagreement with the line predict outcome?
t = tot.copy(); t["dis"] = t.model_minus_line; t["out"] = t.act_minus_line
b = np.polyfit(t.dis, t.out, 1)
bs = boot(t, lambda x: np.polyfit(x.dis, x.out, 1)[0], 5000)
L.append(f"totals family: OLS (actual-line) on (model-line): slope {b[0]:+.2f} CI {bs.round(2).tolist()} intercept {b[1]:+.2f}  [slope 1 = model mean informative, 0 = no information]")
# common shock: all evaluated totals rows, actual - line
L.append(f"totals family mean actual-line {t.out.mean():+.2f} CI {boot(t, lambda x: x.out.mean()).round(2).tolist()}")
# UNDER vs OVER game-total picks: actual - line difference
gt = t[t.base == "GAME_TOTAL"]
d = gt[gt.side == "UNDER"].out.mean() - gt[gt.side == "OVER"].out.mean()
L.append(f"GAME_TOTAL: actual-line UNDER-picked {gt[gt.side=='UNDER'].out.mean():+.2f} vs OVER-picked {gt[gt.side=='OVER'].out.mean():+.2f} diff {d:+.2f} (Welch t {d/np.sqrt(gt[gt.side=='UNDER'].out.var()/ (gt.side=='UNDER').sum()+gt[gt.side=='OVER'].out.var()/(gt.side=='OVER').sum()):+.2f})")
# if outcomes are shifted by the per-sample common shock (actual - market mean) do UNDER and OVER calibrate?
G = pd.read_csv(OUT / "games_residuals.csv")
L.append(f"game-level actual-market total {G.mkt_rT.mean():+.2f}; excluding 4 OT games {G[G.ot==0].mkt_rT.mean():+.2f} (n={int((G.ot==0).sum())})")
uo = u.merge(G[["game_id", "ot"]], on="game_id")
L.append(f"UNDER rows in OT games {int((uo.ot>0).sum())}: W-L {int(uo[uo.ot>0].y.sum())}-{int((uo.ot>0).sum()-uo[uo.ot>0].y.sum())}; regulation-only UNDER W-L {int(uo[uo.ot==0].y.sum())}-{int((uo.ot==0).sum()-uo[uo.ot==0].y.sum())} p={uo[uo.ot==0].p_recon.mean():.3f}")
open(OUT / "significance.txt", "w").write("\n".join(L)); print("\n".join(L))
