import pandas as pd, numpy as np, glob, sys
from pathlib import Path
from cbb_platform.prod import pricing as PRC
S=sys.argv[1]; OPS=Path(S)/'pmfsrc'
g=pd.read_parquet(S+'/../player/graded.parquet')
cache={}
def load(fid):
    if fid in cache: return cache[fid]
    fs=glob.glob(f'{OPS}/ticks/{fid}/pmfs.parquet')+glob.glob(f'{OPS}/forecasts/*/{fid}/pmfs.parquet')
    if not fs: cache[fid]=None; return None
    x=pd.read_parquet(fs[0])
    if 'probabilities' not in x.columns:
        from cbb_platform.prod import exports as EX
        x=None
    cache[fid]=x; return x
out=[]
for r in g.itertuples():
    x=load(r.forecast_id)
    if x is None: out.append((r.eval_key,None,None,'NO_PMF_FILE')); continue
    m=x[(x.game_id==r.game_id)&(x.player_id==r.player_id)&(x.stat==r.stat)]
    if len(m)!=1: out.append((r.eval_key,None,None,f'MATCH_{len(m)}')); continue
    pmf=np.asarray(m.probabilities.iloc[0],float); pr=PRC.price_line(pmf,r.line)
    p=pr['p_over'] if r.side=='OVER' else pr['p_under']
    k=np.arange(len(pmf)); out.append((r.eval_key,p,(k*pmf).sum(),'OK'))
o=pd.DataFrame(out,columns=['eval_key','p_re','mu_re','st']); g=g.merge(o,on='eval_key')
print(g.st.value_counts().to_dict())
ok=g[g.st=='OK']; print('n',len(ok),'max |p_re-model_p|',(ok.p_re-ok.model_probability).abs().max(),'max |mu-proj|',(ok.mu_re-ok.projection).abs().max())
print(g[g.st!='OK'][['forecast_id','st']].value_counts().head())
