"""Build the 2027 ratings prior from the canonical chain 2014..2026 exactly as g1_backtest does, optionally with GC3
component carryover; compare to the sealed prior; optionally write a sealed candidate parquet.
    g7_seal_prior.py '<carry json or {}>' [out.parquet]"""
import hashlib, json, sys
import duckdb, numpy as np, pandas as pd
from cbb_platform import config as C
from cbb_platform.game import forecast as GF, params as GP, ratings as GR
from cbb_platform.game.research import backtest as BT
carry = json.loads(sys.argv[1])
if carry:
    def _pf(self, rho, _c=carry):
        s, l = self.off - self.dfn, self.off + self.dfn
        s, l = rho * _c["strength"] * s, rho * _c["level"] * l
        return GR.Ratings(list(self.teams), (s + l) / 2, (l - s) / 2, rho * _c["pace"] * self.pace, self.mu, self.hca, self.pi,
                          {"prior_of": self.meta.get("state_id"), "rho": rho, "carry": _c})
    GR.Ratings.prior_for = _pf
gm = GP.game_model()
rp = GR.RatingParams(**{k: gm["ratings"][k] for k in ("tau_days", "lam_off", "lam_def", "lam_pace", "rho_carry", "lam_league")})
con = duckdb.connect(str(C.PROJECT_ROOT / "data/lockbox_2025_26/canonical/cbb.duckdb"), read_only=True)
hist = GR.games_frame(con, (2013, 2026)); con.close()
chain = BT.prior_chain(hist, gm["ratings"]["prior_chain_first_season"], 2026, rp)
p27 = BT.season_final(hist, 2026, chain[2026], rp).prior_for(rp.rho_carry)
sealed = GF.prior(2027)
ix = {t: i for i, t in enumerate(p27.teams)}
j = [ix[t] for t in sealed.teams]
print("teams", len(p27.teams), len(sealed.teams), "max|d| off %.2e def %.2e pace %.2e" % tuple(float(np.abs(a[j] - b).max()) for a, b in
      ((p27.off, sealed.off), (p27.dfn, sealed.dfn), (p27.pace, sealed.pace))), "league", (p27.mu, p27.hca, p27.pi), (sealed.mu, sealed.hca, sealed.pi))
if len(sys.argv) > 2:
    df = pd.DataFrame({"team_id": p27.teams, "off": p27.off, "def": p27.dfn, "pace": p27.pace})
    df.to_parquet(sys.argv[2], index=False)
    print(json.dumps({"file": sys.argv[2], "sha256": hashlib.sha256(open(sys.argv[2], "rb").read()).hexdigest(), "state_id": p27.state_id(),
                      "league": {"hca": p27.hca, "mu": p27.mu, "pi": p27.pi}, "teams": len(p27.teams)}))
