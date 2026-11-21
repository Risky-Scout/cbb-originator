import pandas as pd, numpy as np
x=pd.read_parquet('modeling/v2_oos/oos_forecasts_phase1b_v1ref.parquet')
x=x[(x.y_min>0)].copy()
x['ts']=pd.to_datetime(x.prediction_cutoff_utc,utc=True)
x['start']=x.groupby('forecast_season').ts.transform('min'); x['day']=(x.ts-x.start).dt.days
x['phase']=pd.cut(x.day,[-1,14,30,60,200],labels=['d0-14','d15-30','d31-60','d61+'])
pd.set_option('display.width',250)
def f(g):
    o={'n':len(g),'min_res':(g.y_min-g.min_mu).mean()}
    for s in ['PTS','AST','REB']:
        e=g['y_'+s]-g[s+'_mu']; o[s+'_res']=e.mean(); o[s+'_t']=e.mean()/(e.std()/np.sqrt(len(g))); o[s+'_ratio']=g['y_'+s].sum()/g[s+'_mu'].sum()
        X=np.column_stack([np.ones(len(g)),g[s+'_mu']]); b=np.linalg.lstsq(X,g['y_'+s].values,rcond=None)[0]; o[s+'_slope']=b[1]
    return pd.Series(o)
print(x.groupby(['forecast_season','phase']).apply(f).round(3).to_string())
e=x[x.day<=30]
e['pq']=e.groupby('forecast_season').PTS_mu.transform(lambda v: pd.qcut(v,5,labels=False))
print(e.groupby(['cold_start_class','pq']).apply(f)[['n','PTS_res','PTS_t','PTS_ratio','AST_res','AST_ratio','min_res']].round(3).to_string())
print(e[e.cold_start_class=='WARM'].groupby(['forecast_season','pq']).apply(f)[['n','PTS_res','PTS_t','PTS_ratio','AST_ratio','min_res']].round(3).to_string())
