"""Seal TP1P into the production game model (run only after TP1P gates E1-E4 PASS).
ratings_prior_2027_tp1p.parquet = the sealed GC3 2027 prior with margin strength replaced by the TP1P preseason roster
prediction (level = off + def and pace unchanged), i.e. exactly the 2027 input of the research walk (r9 inject).
game_model.json: ratings_prior["2027"] -> the new file, model_version cbb-game-model-1.2.0, resealed."""
import hashlib, json, os
import numpy as np, pandas as pd
from cbb_platform.game import forecast as GF, params as GP

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
O = os.path.join(ROOT, "data/qa/large/rootcause_20261126")
gm = GP.game_model()
old = gm["ratings_prior"]["2027"]
pri = GF.prior(2027)
TP = pd.read_parquet(f"{O}/tp1p_priors.parquet")
m = dict(zip(TP[TP.season == 2027].team, TP[TP.season == 2027].tp1))
st = np.array([m.get(t, o - d) for t, o, d in zip(pri.teams, pri.off, pri.dfn)])
lv = pri.off + pri.dfn
df = pd.read_parquet(GP.ROOT / old["file"])
assert list(df.team_id) == list(pri.teams)
new = df.copy()
new["off"], new["def"] = (st + lv) / 2, (lv - st) / 2
assert np.allclose(new["off"] + new["def"], df["off"] + df["def"]) and np.array_equal(new["pace"], df["pace"])
fn = "ratings_prior_2027_tp1p.parquet"
new.to_parquet(GP.ROOT / fn, index=False)
sha = hashlib.sha256((GP.ROOT / fn).read_bytes()).hexdigest()
gm2 = {k: v for k, v in gm.items() if k != "params_sha256"}
gm2["model_version"] = "cbb-game-model-1.2.0"
gm2["ratings_prior"] = {**gm["ratings_prior"], "2027": {
    **old, "file": fn, "sha256": sha, "replaces": f"{old['file']} {old['sha256'][:12]} ({old['state_id']})",
    "state_id": "TR-" + sha[:16],
    "strength_source": "TP1P preseason team roster prior (docs/audits/2026-11-26-rootcause/ROOT_CAUSE_PROTOCOL.json "
                       "TP1 / TP1P): ridge on returning / lost / transferred-in PORPAG value, transfer competition change, "
                       "recruit class and GC3 strength x continuity, fit 2016..2026; CBBD API value term (2025-26 stand-in: "
                       "Starter Pack file identical to the API, lockbox-refused for the research client)",
    "teams_tp1p": int(sum(t in m for t in pri.teams))}}
GP.seal_game_model(gm2)
print(fn, sha[:12], "teams", len(pri.teams), "TP1P", gm2["ratings_prior"]["2027"]["teams_tp1p"], GP.game_model()["params_sha256"][:12])
