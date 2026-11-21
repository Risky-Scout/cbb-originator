import pandas as pd, numpy as np, sys
S=sys.argv[1]
d=pd.read_parquet(S+'/performance/cbb_market_performance.parquet')
g=d[d.result.isin(['WIN','LOSS'])].copy()
g['over']=(g.actual>g.line).astype(int)
g['p_over']=np.where(g.side=='OVER',g.model_probability,1-g.model_probability-g.push_probability.fillna(0))
g['mkt_over']=np.where(g.side=='OVER',g.fair_probability,1-g.fair_probability)
g['res']=g.actual-g.projection
g['proj_minus_line']=g.projection-g.line
def agg(x):
    m=x.dropna(subset=['mkt_over'])
    return pd.Series({'n':len(x),'p_over':x.p_over.mean(),'over_rate':x.over.mean(),'mkt_over(n=%d)'%0:m.mkt_over.mean(),
      'brier_model':((x.p_over-x.over)**2).mean(),'brier_mkt':((m.mkt_over-m.over)**2).mean(),'brier_model_on_mkt':((m.p_over-m.over)**2).mean(),
      'proj':x.projection.mean(),'actual':x.actual.mean(),'resid':x.res.mean(),'resid_se':x.res.std()/np.sqrt(len(x)),'proj-line':x.proj_minus_line.mean()})
pd.set_option('display.width',250)
print(agg(g).round(4).to_string())
for k in ['stat','slate_date','side','player_role']:
    print(g.groupby(k).apply(agg).round(3).to_string())
main=g[g.stat.isin(['PTS','AST','PRA','REB','FG3M'])]
print(main.groupby(['stat','slate_date']).apply(agg)[['n','p_over','over_rate','mkt_over(n=0)','resid','resid_se','proj-line']].round(3).to_string())
print(main.groupby(['stat','side']).apply(agg)[['n','p_over','over_rate','mkt_over(n=0)','resid','resid_se','proj-line']].round(3).to_string())
g.to_parquet(S+'/../player/graded.parquet')
