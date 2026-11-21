"""Walk-forward challenger for the Phase 1B per-minute RATE history blend (research only; nothing deployed).
CURRENT    rate = (H_t + k prior_pos) / (H_min + k),  H = cur + sum_j 0.5^(j/1.0) h_j        (half-life 1 season)
CHALLENGER same formula, half-life hl in GRID (one parameter; prior k / position prior / minutes / dispersion UNCHANGED).
mu' = mu_1B_oos * r'(hl) / r_current (exact reproduction of r_current is verified first).
Folds (true OOS store, fold states through F-1): F2024 development, F2025 selection, F2026 frozen OOS.
Metrics on PLAYED rows: NB log loss, RPS/CRPS, Brier + calibration at the synthetic line floor(mu_cur)+0.5."""
import json, sys, numpy as np, pandas as pd
from pathlib import Path
from cbb_platform import props_v0 as P
from cbb_platform import config as C
from cbb_platform.v2 import marginal as MG
ROOT=C.PROJECT_ROOT; OUT=Path(sys.argv[1])
cfg=json.loads((ROOT/"modeling/player_props_v2/v2_marginal_final_v1ref_through_2025_26.json").read_text())["config"]
TGT={"PTS":"pts","AST":"ast","REB":"reb","FG3M":"three_pm"}
GRID=[1.0,0.75,0.5,0.35,0.25]
oos=pd.read_parquet(ROOT/"modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet")
def pm(mu,phi,kmax):
    return P.pmf_matrix(mu,phi,kmax)            # rows P(0..kmax-1), P(>=kmax): the production PMF construction
res=[]
for F in (2024,2025,2026):
    fr,_=MG.load_frames(cfg,range(F-3,F+1),"candidates"); fr=fr[fr.season==F]
    st=json.loads((ROOT/f"modeling/player_props_v2/states/F{F}_ANNUAL_REFIT_V1REF.json").read_text()); st=st.get("v0_state",st)
    o=oos[(oos.fold_id==f"F{F}")&(oos.y_min>0)]
    d=o.merge(fr,on="player_game_example_id",how="inner",suffixes=("","_f"))
    d["ts"]=pd.to_datetime(d.prediction_cutoff_utc,utc=True); d["day"]=(d.ts-d.ts.min()).dt.days
    d["early"]=d.day<=30
    print(F,"rows",len(d),file=sys.stderr)
    for S,t in TGT.items():
        k=st["prior_k_minutes"][t]; prior=d["pos"].map(st["prior_rate"][t]).to_numpy(float)
        def rate(hl):
            w=[0.5**(j/hl) for j in (1,2,3)]
            Hm=d.cur_min.fillna(0).to_numpy(float)+sum(wj*d[f"h{j}_min"].fillna(0).to_numpy(float) for j,wj in zip((1,2,3),w))
            Hs=d[f"cur_{t}"].fillna(0).to_numpy(float)+sum(wj*d[f"h{j}_{t}"].fillna(0).to_numpy(float) for j,wj in zip((1,2,3),w))
            return (Hs+k*prior)/(Hm+k)
        r0=rate(1.0); mu0=d[f"{S}_mu"].to_numpy(float); phi=float(d[f"{S}_phi"].iloc[0]); y=d[f"y_{S}"].to_numpy(float)
        rep=np.abs(np.maximum(d.min_mu.to_numpy(float)*r0,cfg["mean_floor"])-mu0)
        ok=~np.isnan(y)
        line=np.floor(mu0)+0.5
        for hl in GRID:
            mu=np.maximum(mu0*rate(hl)/r0,cfg["mean_floor"])
            yy=np.nan_to_num(y).astype(int); kmax=int(yy.max())+40
            M=pm(mu,phi,kmax); ll=-np.log(np.maximum(M[np.arange(len(yy)),np.minimum(yy,kmax)],1e-300))
            cdf=np.cumsum(M[:,:kmax],1); ind=(np.arange(kmax)[None,:]>=yy[:,None]).astype(float); rps=((cdf-ind)**2).sum(1)
            pov=1-cdf[np.arange(len(yy)),np.floor(line).astype(int)]; over=(y>line).astype(float)
            del M,cdf,ind
            for seg,m in (("ALL",ok),("EARLY_D0_30",ok&d.early.to_numpy()),("EARLY_TOP_MIN30",ok&d.early.to_numpy()&(d.min_mu.to_numpy()>=30)),("MIN_MU_30PLUS",ok&(d.min_mu.to_numpy()>=30))):
                res.append({"fold":F,"stat":S,"hl":hl,"seg":seg,"n":int(m.sum()),"logloss":ll[m].mean(),"rps":rps[m].mean(),
                            "brier":((pov-over)**2)[m].mean(),"p_over":pov[m].mean(),"over_rate":over[m].mean(),
                            "mean_err":(mu-y)[m].mean(),"max_rate_reproduction_err":float(np.nanmax(rep)) if hl==1.0 else None})
R=pd.DataFrame(res); R.to_csv(OUT/"c1_blend_results.csv",index=False)
pd.set_option("display.width",250)
base=R[R.hl==1.0].set_index(["fold","stat","seg"])
R["d_logloss"]=R.apply(lambda r: r.logloss-base.loc[(r.fold,r.stat,r.seg),"logloss"],axis=1)
R["d_rps"]=R.apply(lambda r: r.rps-base.loc[(r.fold,r.stat,r.seg),"rps"],axis=1)
R["d_brier"]=R.apply(lambda r: r.brier-base.loc[(r.fold,r.stat,r.seg),"brier"],axis=1)
R.to_csv(OUT/"c1_blend_results.csv",index=False)
print(R[R.seg.isin(["ALL","EARLY_D0_30","EARLY_TOP_MIN30"])].pivot_table(index=["seg","stat","hl"],columns="fold",values=["d_logloss","d_rps","mean_err"]).round(5).to_string())
print(base.max_rate_reproduction_err.dropna().groupby(level=[0,1]).max())
