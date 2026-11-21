import pandas as pd, numpy as np, sys
S=sys.argv[1]
M=pd.read_parquet(S+'/../player/population.parquet'); pl=M[(M.participation=='PLAYED')&(M.minutes>0)&M.priced].copy()
pl['gd']=pd.to_datetime(pl.game_date)
def ols(y,X):
    X=np.column_stack([np.ones(len(y))]+X); b,*_=np.linalg.lstsq(X,y,rcond=None); r=y-X@b
    se=np.sqrt(np.diag(np.linalg.inv(X.T@X))*r.var()); return b,se
print('Slope of actual on projection (b>1 => projections over-shrunk toward the mean)')
for per in [('all',pl),('11-13..11-20',pl[pl.gd>='2026-11-13']),('11-17..11-20',pl[pl.gd>='2026-11-17'])]:
    for s in ['PTS','REB','AST','PRA','FG3M']:
        b,se=ols(per[1]['act_'+s].values,[per[1]['proj_'+s].values]); print(f'{per[0]:14s} {s:5s} n={len(per[1])} a={b[0]:.3f} b={b[1]:.3f} se={se[1]:.3f}')
# decile of projection
pl2=pl[pl.gd>='2026-11-13']
for s in ['PTS','AST','PRA']:
    pl2['q']=pd.qcut(pl2['proj_'+s],10,labels=False,duplicates='drop')
    t=pl2.groupby('q').apply(lambda x: pd.Series({'n':len(x),'proj':x['proj_'+s].mean(),'act':x['act_'+s].mean(),'res':(x['act_'+s]-x['proj_'+s]).mean(),'se':(x['act_'+s]-x['proj_'+s]).std()/np.sqrt(len(x)),'min_mu':x.min_mu.mean(),'min_act':x.minutes.mean()}))
    print(s,'\n',t.round(2).to_string())
# minutes calibration by min_mu decile
pl2['mq']=pd.qcut(pl2.min_mu,10,labels=False)
print(pl2.groupby('mq').apply(lambda x: pd.Series({'n':len(x),'min_mu':x.min_mu.mean(),'min_act':x.minutes.mean(),'p_play':x.p_play.mean()})).round(2).to_string())
# rate vs minutes decomposition for top decile PTS
for s in ['PTS','PRA','AST']:
    top=pl2[pl2['proj_'+s]>=pl2['proj_'+s].quantile(.8)]
    print(s,'top20%%: n=%d proj %.2f act %.2f | minutes ratio act/mu %.3f | per-min rate ratio %.3f'%(len(top),top['proj_'+s].mean(),top['act_'+s].mean(),top.minutes.sum()/top.min_mu.sum(),(top['act_'+s].sum()/top.minutes.sum())/(top['proj_'+s].sum()/top.min_mu.sum())))
