import pandas as pd, numpy as np, sys
S=sys.argv[1]
g=pd.read_parquet(S+'/../player/graded.parquet')
P=pd.read_parquet(S+'/../player/population.parquet')
gp=g[['game_id','player_id','slate_date']].drop_duplicates().merge(P,on=['game_id','player_id'])
gp=gp[(gp.participation=='PLAYED')&(gp.minutes>0)]
print('market-listed player-games',len(gp))
print('MINUTES: min_mu %.2f actual %.2f ratio %.3f (se of mean diff %.2f)'%(gp.min_mu.mean(),gp.minutes.mean(),gp.minutes.sum()/gp.min_mu.sum(),(gp.minutes-gp.min_mu).std()/np.sqrt(len(gp))))
for s in ['PTS','REB','AST','PRA','FG3M']:
    e=gp['act_'+s]-gp['proj_'+s]
    rr=(gp['act_'+s].sum()/gp.minutes.sum())/(gp['proj_'+s].sum()/gp.min_mu.sum())
    print(f'{s}: ALL-STATS resid on market-listed players {e.mean():+.2f} (se {e.std()/np.sqrt(len(e)):.2f}) ratio {gp["act_"+s].sum()/gp["proj_"+s].sum():.3f} | per-minute rate ratio {rr:.3f}')
print(gp.groupby('evidence_bucket').size().to_dict(), gp.groupby('cold_start_class').size().to_dict(), gp.groupby('role_diagnostic').size().to_dict())
print(gp.groupby('evidence_bucket').apply(lambda x: pd.Series({'n':len(x),'PTS':(x.act_PTS-x.proj_PTS).mean(),'PRA':(x.act_PRA-x.proj_PRA).mean(),'AST':(x.act_AST-x.proj_AST).mean(),'min':(x.minutes-x.min_mu).mean()})).round(2).to_string())
print(gp.groupby('transfer_in').apply(lambda x: pd.Series({'n':len(x),'PTS':(x.act_PTS-x.proj_PTS).mean(),'PRA':(x.act_PRA-x.proj_PRA).mean(),'AST':(x.act_AST-x.proj_AST).mean(),'min':(x.minutes-x.min_mu).mean()})).round(2).to_string())
# compare: TOP5 population NOT market-listed, same dates
pop=P[(P.participation=='PLAYED')&(P.minutes>0)&(P.game_date>='2026-11-17')&(P.role_diagnostic=='TOP5_MINUTES')]
key=set(zip(gp.game_id,gp.player_id)); pop=pop[[ (a,b) not in key for a,b in zip(pop.game_id,pop.player_id)]]
print('TOP5 NOT market-listed 11-17..20 n=%d PTS %+.2f PRA %+.2f AST %+.2f min %+.2f proj_PTS %.1f'%(len(pop),(pop.act_PTS-pop.proj_PTS).mean(),(pop.act_PRA-pop.proj_PRA).mean(),(pop.act_AST-pop.proj_AST).mean(),(pop.minutes-pop.min_mu).mean(),pop.proj_PTS.mean()))
print('market-listed proj_PTS %.1f'%gp.proj_PTS.mean())
# regression actual ~ proj + line on the two-sided graded rows
m=g.dropna(subset=['fair_probability'])
for s in ['PTS','AST','PRA','REB','FG3M']:
    x=m[m.stat==s]; X=np.column_stack([np.ones(len(x)),x.projection,x.line]); b,*_=np.linalg.lstsq(X,x.actual.values,rcond=None)
    r=x.actual.values-X@b; se=np.sqrt(np.diag(np.linalg.inv(X.T@X))*r.var())
    print(f'{s} n={len(x)} actual = {b[0]:.2f} + {b[1]:.2f}(se {se[1]:.2f})*proj + {b[2]:.2f}(se {se[2]:.2f})*line | RMSE proj {np.sqrt(((x.actual-x.projection)**2).mean()):.2f} line {np.sqrt(((x.actual-x.line)**2).mean()):.2f}')
