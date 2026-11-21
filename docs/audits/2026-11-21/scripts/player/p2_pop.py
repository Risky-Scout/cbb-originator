import pandas as pd, numpy as np, glob, sys
S=sys.argv[1]; T=S+'/ticks/'
STATS=['PTS','REB','AST','FG3M','STL','BLK','TOV','PRA','PR','PA','RA','STOCKS']
P=[]
for f in sorted(glob.glob(T+'*/projections.parquet')):
    x=pd.read_parquet(f); x['tick']=f.split('/')[-2]; P.append(x)
P=pd.concat(P,ignore_index=True)
P['pa']=pd.to_datetime(P.prediction_as_of,utc=True,format='mixed'); P['cut']=pd.to_datetime(P.target_cutoff,utc=True,format='mixed')
P=P[P.pa<=P.cut].sort_values('pa').groupby(['game_id','player_id']).tail(1)
A=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(T+'*/actuals.parquet'))]).drop_duplicates(['game_id','player_id'])
M=P.merge(A,on=['game_id','player_id'],how='inner')
print('proj rows (latest pre-cutoff)',len(P),'with actuals',len(M), M.participation.value_counts().to_dict())
M['played']=(M.participation=='PLAYED')&(M.minutes>0)
M.to_parquet(S+'/../player/population.parquet')
pl=M[M.played].copy()
print('played',len(pl),'priced',pl.priced.sum(), 'dates',pl.game_date.value_counts().sort_index().to_dict())
pl['min_res']=pl.minutes-pl.min_mu
print('\nMINUTES: mean min_mu %.2f actual %.2f resid %.2f se %.2f'%(pl.min_mu.mean(),pl.minutes.mean(),pl.min_res.mean(),pl.min_res.std()/np.sqrt(len(pl))))
def tab(df,by):
    rows=[]
    for k,x in df.groupby(by):
        r={'key':k,'n':len(x),'min_res':x.min_res.mean(),'p_play':x.p_play.mean()}
        for s in ['PTS','REB','AST','PRA','FG3M']:
            e=x['act_'+s]-x['proj_'+s]; r[s+'_res']=e.mean(); r[s+'_t']=e.mean()/(e.std()/np.sqrt(len(e))+1e-9)
            r[s+'_ratio']=x['act_'+s].sum()/x['proj_'+s].sum()
        rows.append(r)
    return pd.DataFrame(rows).round(3)
pd.set_option('display.width',300)
print(tab(pl.assign(all='all'),'all').to_string())
for by in ['game_date','priced','cold_start_class','evidence_bucket','role_diagnostic','transfer_in','first_30_days','distribution_method']:
    print(tab(pl,by).to_string())
# per-minute rate residual: is the per-minute rate or the minutes biased?
for s in ['PTS','AST','REB','PRA']:
    rate_proj=pl['proj_'+s]/pl.min_mu; 
    print(s,'actual/proj %.3f | minutes ratio %.3f | per-min rate ratio (act/actmin vs proj/min_mu, minute-weighted) %.3f'%(pl['act_'+s].sum()/pl['proj_'+s].sum(), pl.minutes.sum()/pl.min_mu.sum(), (pl['act_'+s].sum()/pl.minutes.sum())/((rate_proj*pl.min_mu).sum()/pl.min_mu.sum())))
