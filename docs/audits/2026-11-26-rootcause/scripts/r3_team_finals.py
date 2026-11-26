"""Internal season-final team ratings (production game-model parameters, canonical chain) for 2013..2026:
data/qa/large/rootcause_20261126/team_finals.parquet (team, season, off, def, pace, strength = off - def, n_games)."""
import duckdb, numpy as np, pandas as pd
from cbb_platform import config as C
from cbb_platform.game import params as GP, ratings as GR
from cbb_platform.game.research import backtest as BT
gm = GP.game_model()
rp = GR.RatingParams(**{k: gm["ratings"][k] for k in ("tau_days", "lam_off", "lam_def", "lam_pace", "rho_carry", "lam_league", "carry_level", "carry_pace")})
con = duckdb.connect(str(C.PROJECT_ROOT / "data/lockbox_2025_26/canonical/cbb.duckdb"), read_only=True)
hist = GR.games_frame(con, (2013, 2026)); con.close()
pri, rows = None, []
for s in range(gm["ratings"]["prior_chain_first_season"], 2027):
    fin = BT.season_final(hist, s, pri, rp)
    n = pd.concat([hist.loc[hist.season == s, "home"], hist.loc[hist.season == s, "away"]]).value_counts()
    rows.append(pd.DataFrame({"team": fin.teams, "season": s, "off": fin.off, "def": fin.dfn, "pace": fin.pace,
                              "strength": fin.off - fin.dfn, "n_games": [int(n.get(t, 0)) for t in fin.teams]}))
    pri = fin.prior_for(rp.rho_carry, rp.carry_level, rp.carry_pace)
    print(s, len(fin.teams), flush=True)
out = pd.concat(rows, ignore_index=True)
out.to_parquet(C.PROJECT_ROOT / "data/qa/large/rootcause_20261126/team_finals.parquet", index=False)
print(out.groupby("season").strength.describe().round(2).tail(4))
