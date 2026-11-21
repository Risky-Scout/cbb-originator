import pandas as pd, numpy as np, sys, glob
S=sys.argv[1]; PL=S+'/../player/'
B=pd.read_parquet(PL+'rates.parquet'); B['av']=pd.to_datetime(B.available_at,utc=True); B['reb']=B.s_orb+B.s_drb
g=pd.read_parquet(PL+'graded.parquet'); Pp=pd.read_parquet(PL+'population.parquet')
gp=g[['game_id','player_id','cutoff_utc']].drop_duplicates(['game_id','player_id']).merge(Pp,on=['game_id','player_id'])
gp=gp[(gp.participation=='PLAYED')&(gp.minutes>0)].copy(); gp['cut']=pd.to_datetime(gp.cutoff_utc,utc=True)
# mu_1B from run model_inputs
MI=pd.concat([pd.read_parquet(f).assign(run_id=f.split('/')[-2]) for f in glob.glob(S+'/runs/*/model_inputs.parquet')])
gp=gp.merge(MI[['run_id','game_id','player_id','PTS_mu','AST_mu','REB_mu','min_mu']].rename(columns={'min_mu':'mi_min_mu'}),on=['run_id','game_id','player_id'],how='left')
print('mu_1B matched',gp.PTS_mu.notna().sum(),'/',len(gp))
fin={s:B[B.season==s].sort_values('av').groupby('canonical_player_id').tail(1).set_index('canonical_player_id') for s in [2026,2025,2024]}
rows=[]
for r in gp.itertuples():
    c=B[(B.canonical_player_id==r.player_id)&(B.season==2027)&(B.av<=r.cut)].sort_values('av').tail(1)
    o={'game_id':r.game_id,'player_id':r.player_id}
    for nm,col in [('min','s_min'),('pts','s_pts'),('ast','s_ast'),('reb','reb')]:
        o['c_'+nm]=float(c[col].iloc[0]) if len(c) else 0.0
        for j,s in enumerate([2026,2025,2024],1):
            f=fin[s]; o[f'h{j}_'+nm]=float(f.loc[r.player_id,col]) if r.player_id in f.index else 0.0
    rows.append(o)
R=gp.merge(pd.DataFrame(rows),on=['game_id','player_id'])
w=[1,.5,.25,.125]
for nm in ['min','pts','ast','reb']:
    R['H_'+nm]=R['c_'+nm]+.5*R['h1_'+nm]+.25*R['h2_'+nm]+.125*R['h3_'+nm]
R['cur_share']=R.c_min/R.H_min
out={}
for st,a,mu in [('pts','act_PTS','PTS_mu'),('ast','act_AST','AST_mu'),('reb','act_REB','REB_mu')]:
    W=R.dropna(subset=[mu]); W=W[(W.c_min>0)&(W.h1_min>0)]
    m=W.minutes.sum()
    out[st]={'n':len(W),'cur_min_share_of_H':W.cur_share.mean(),
      'rate_prior_season(h1)':W['h1_'+st].sum()/W.h1_min.sum(),'rate_current_season_to_date':W['c_'+st].sum()/W.c_min.sum(),
      'rate_hist_blend':W['H_'+st].sum()/W.H_min.sum(),'rate_model_1B(mu/min_mu)':W[mu].sum()/W.mi_min_mu.sum(),
      'rate_model_JM1(proj/min_mu)':W['proj_'+st.upper()].sum()/W.min_mu.sum(),'rate_realized':W[a].sum()/m,
      'share_players_improved(cur>h1)':((W['c_'+st]/W.c_min)>(W['h1_'+st]/W.h1_min)).mean()}
print(pd.DataFrame(out).round(4).to_string())
# within-sample: residual vs (cur - hist) rate gap: is current evidence under-weighted?
W=R.dropna(subset=['PTS_mu']); W=W[(W.c_min>0)&(W.h1_min>0)]
for st,a in [('pts','act_PTS'),('ast','act_AST')]:
    gap=(W['c_'+st]/W.c_min - W['H_'+st]/W.H_min)*W.min_mu; res=W[a]-W['proj_'+st.upper()]
    X=np.column_stack([np.ones(len(W)),gap]); b=np.linalg.lstsq(X,res.values,rcond=None)[0]; e=res.values-X@b; se=np.sqrt(np.diag(np.linalg.inv(X.T@X))*e.var())
    print(st,'resid = %.2f + %.2f (se %.2f) * (cur-hist rate gap x min_mu); mean gap %.2f'%(b[0],b[1],se[1],gap.mean()))
R.to_parquet(PL+'mkt_rate_components.parquet')
