import pandas as pd, numpy as np, sys
S=sys.argv[1]
g=pd.read_parquet(S+'/../player/graded.parquet'); g['hit']=(g.result=='WIN').astype(int); g['side_']=g.side
P=pd.read_parquet(S+'/../player/population.parquet')[['game_id','player_id','role_diagnostic','cold_start_class','evidence_bucket','min_mu','cur_min','team_games']]
g=g.merge(P,on=['game_id','player_id'],how='left')
g['role_']=g.role_diagnostic; m=g.dropna(subset=['fair_probability'])
def dec(x):
    xm=x.dropna(subset=['fair_probability'])
    return pd.Series({'n':len(x),'p_model':x.model_probability.mean(),'hit':x.hit.mean(),'gap':x.hit.mean()-x.model_probability.mean(),'se_hit':np.sqrt(x.hit.mean()*(1-x.hit.mean())/len(x)),
     'n_mkt':len(xm),'p_mkt':xm.fair_probability.mean(),'model-mkt':(xm.model_probability-xm.fair_probability).mean(),'mkt-actual':(xm.fair_probability-xm.hit).mean(),
     'edge':x.edge.mean(),'proj-line':(x.projection-x.line).mean(),'TOP5_share':(x.role_=='TOP5_MINUTES').mean(),'under_share':(x.side_=='UNDER').mean()})
pd.set_option('display.width',300)
print(dec(g).round(3).to_string())
for k in [['role_diagnostic'],['tier'],['stat','side']]:
    t=g.groupby(k).apply(dec); print(t[t.n>=10].round(3).to_string())
# binned by model-market disagreement
m=m.copy(); m['dis']=pd.cut(m.model_probability-m.fair_probability,[-1,0,.05,.10,.15,.2,1])
print(m.groupby('dis').apply(dec).round(3).to_string())
# 11-20 vs 11-19: what drove the larger disagreement
for D in ['2026-11-19','2026-11-20']:
    x=g[g.slate_date==D]; print(D,'stat mix',x.stat.value_counts(normalize=True).round(2).head(6).to_dict(),'| model p>=.65 share %.2f'%(x.model_probability>=.65).mean(), '| abs proj-line %.2f'%(x.projection-x.line).abs().mean(),
      '| books mean %.2f'%x.books_at_line.mean(),'| fair NaN %.2f'%x.fair_probability.isna().mean(), '| cold_start', x.cold_start_class.value_counts(normalize=True).round(2).to_dict())
