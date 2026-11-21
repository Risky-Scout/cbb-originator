import pandas as pd, numpy as np, glob, sys
from pathlib import Path
from cbb_platform.prod import exports as EX, pricing as PRC
S=sys.argv[1]; OPS=Path(S)/'pmfsrc'
refs=pd.concat([pd.read_parquet(f).assign(export_date=f.split('/')[-2]) for f in sorted(glob.glob(S+'/exports/2026-11-*/*pmf_refs.parquet')) if f.split('/')[-2]>='2026-11-13'])
refs=refs.drop_duplicates(['game_id','player_id','stat','pmf_sha256'])
R=EX.resolve_pmf_refs(refs,OPS)
A=pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(S+'/ticks/*/actuals.parquet'))]).drop_duplicates(['game_id','player_id'])
P=pd.read_parquet(S+'/../player/population.parquet')[['game_id','player_id','min_mu','p_play','role_diagnostic','cold_start_class','evidence_bucket','game_date']]
rows=[]
for r in R.itertuples(index=False):
    pmf=np.asarray(r.probabilities,float); k=np.arange(len(pmf)); mu=(k*pmf).sum(); var=((k-mu)**2*pmf).sum()
    rows.append((r.game_id,r.player_id,r.stat,r.export_date,r.pmf_sha256,mu,var,pmf))
D=pd.DataFrame(rows,columns=['game_id','player_id','stat','export_date','pmf_sha256','mu','var','pmf'])
D=D.merge(A,on=['game_id','player_id']).merge(P,on=['game_id','player_id'],how='left')
D=D[(D.participation=='PLAYED')&(D.minutes>0)].copy()
D['y']=[getattr(r,'act_'+r.stat) if False else None for r in D.itertuples()] if False else D.apply(lambda r: r['act_'+r['stat']],axis=1)
D=D.dropna(subset=['y'])
rng=np.random.default_rng(0)
def pit(pmf,y):
    y=int(y); c=np.cumsum(pmf); lo=c[y-1] if y>=1 and y-1<len(c) else (0 if y<1 else 1.0); hi=c[y] if y<len(c) else 1.0
    return lo+rng.random()*(hi-lo)
D['pit']=[pit(p,y) for p,y in zip(D.pmf,D.y)]
D['res']=D.y-D.mu; D['z2']=D.res**2/D['var']
def cov(pmf,y,a):
    c=np.cumsum(pmf); lo=np.searchsorted(c,(1-a)/2); hi=np.searchsorted(c,1-(1-a)/2); return lo<=y<=hi
D['in50']=[cov(p,y,.5) for p,y in zip(D.pmf,D.y)]; D['in80']=[cov(p,y,.8) for p,y in zip(D.pmf,D.y)]
pd.set_option('display.width',300)
def summ(x):
    h=np.histogram(x.pit,bins=10,range=(0,1))[0]/len(x)
    return pd.Series({'n':len(x),'mu':x.mu.mean(),'y':x.y.mean(),'res':x.res.mean(),'sd_pmf':np.sqrt(x['var'].mean()),'sd_real':x.res.std(),'var_ratio':(x.res**2).mean()/x['var'].mean(),
      'pit_mean':x.pit.mean(),'pit<.1':h[0],'pit>.9':h[-1],'pit.4-.6':h[4]+h[5],'cov50':x.in50.mean(),'cov80':x.in80.mean()})
print(D.groupby('stat').apply(summ).round(3).to_string())
for s in ['PTS','AST','PRA']:
    print(s); print(D[D.stat==s].groupby('role_diagnostic').apply(summ).round(3).to_string())
    print(D[D.stat==s].groupby('export_date').apply(summ).round(3).to_string())
D.drop(columns=['pmf']).to_parquet(S+'/../player/pmf_pop.parquet')
D[['game_id','player_id','stat','pmf_sha256','pmf']].to_pickle(S+'/../player/pmf_vectors.pkl')
