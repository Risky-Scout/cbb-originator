"""Prospective (APPROXIMATE) re-price of the live graded PTS/AST rows under the selected half-life challenger.
Approximation: rate ratio r(hl)/r(1) from PIT-reconstructed season-to-date + prior-season finals and the F2026 fold
state's k / position prior; PMF replaced by a moment-matched NB with mean scaled by the ratio. Indicative only."""
import json, sys, numpy as np, pandas as pd, pickle
from cbb_platform import config as C, props_v0 as P
S=sys.argv[1]; PL=S+'/player/'
st=json.loads((C.PROJECT_ROOT/"modeling/player_props_v2/states/F2026_ANNUAL_REFIT_V1REF.json").read_text()); st=st.get("v0_state",st)
R=pd.read_parquet(PL+'mkt_rate_components.parquet'); g=pd.read_parquet(PL+'graded.parquet')
pos=R.position_group.map(lambda p: p if p in P.POS_GROUPS else 'U')
def ratio(t,col,hl):
    k=st["prior_k_minutes"][t]; pr=pos.map(st["prior_rate"][t]).to_numpy(float)
    def r(h):
        w=[0.5**(j/h) for j in (1,2,3)]
        Hm=R.c_min+sum(wj*R[f'h{j}_min'] for j,wj in zip((1,2,3),w)); Hs=R['c_'+col]+sum(wj*R[f'h{j}_'+col] for j,wj in zip((1,2,3),w))
        return (Hs+k*pr)/(Hm+k)
    return (r(hl)/r(1.0)).to_numpy()
R['ratio_PTS']=ratio('pts','pts',0.35); R['ratio_AST']=ratio('ast','ast',0.5)
out=[]
for s in ['PTS','AST']:
    x=g[g.stat==s].merge(R[['game_id','player_id','ratio_'+s]],on=['game_id','player_id'])
    pm=pd.read_parquet(PL+'pmf_pop.parquet')  # population PMF moments (official refs)
    for r in x.itertuples():
        mu=r.projection; rr=getattr(r,'ratio_'+s)
        # variance from the graded PMF's own moments isn't stored here; use population var/mean relation of this stat
        out.append({'stat':s,'side':r.side,'line':r.line,'actual':r.actual,'mu':mu,'mu_new':mu*rr,'p_side':r.model_probability,'fair':r.fair_probability,'result':r.result})
O=pd.DataFrame(out)
pop=pd.read_parquet(PL+'pmf_pop.parquet')
for s in ['PTS','AST']:
    z=pop[pop.stat==s]; phi=max(((z['var']-z.mu)/z.mu**2).median(),1e-6)
    m=O.stat==s
    def pside(mu,line,side):
        M=P.pmf_matrix(np.array([mu]),phi,200)[0]; c=np.cumsum(M[:200]); fl=int(np.floor(line))
        pu=c[fl] if line!=fl else c[fl-1]; po=1-c[fl]
        return po if side=='OVER' else pu
    O.loc[m,'p_old_nb']=[pside(a,b,c) for a,b,c in zip(O[m].mu,O[m].line,O[m].side)]
    O.loc[m,'p_new_nb']=[pside(a,b,c) for a,b,c in zip(O[m].mu_new,O[m].line,O[m].side)]
O['hit']=(O.result=='WIN').astype(int)
pd.set_option('display.width',200)
print(O.groupby(['stat','side']).apply(lambda x: pd.Series({'n':len(x),'mean_ratio':(x.mu_new/x.mu).mean(),'p_prod':x.p_side.mean(),'p_old_nb':x.p_old_nb.mean(),'p_new_nb':x.p_new_nb.mean(),'hit':x.hit.mean(),'fair':x.fair.mean(),
   'brier_old':((x.p_old_nb-x.hit)**2).mean(),'brier_new':((x.p_new_nb-x.hit)**2).mean()})).round(3).to_string())
