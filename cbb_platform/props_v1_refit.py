"""Production candidate v1: final development refit through 2024-25 and its serialized scoring artifact.

The FROZEN v1 specification is refit unchanged (no selection of any kind):
    stat / minutes   props_v0.fit(..., train_through=2025): every eligible appearance 2013-14..2024-25, same recency
                     weights (age from 2024-25), ridge penalty, priors, shrinkage, dispersion rules and supports
    participation    props_v0_participation.fit(..., train_seasons=(2024, 2025)): identifiable PLAYED / DNP_CONFIRMED
                     rows of 2023-24 and 2024-25; UNKNOWN_PARTICIPATION and NOT_ON_ROSTER stay excluded
    pricing          props_v1_pricing, unchanged
2025-26 is refused by every loader. 2024-25 is now training data: nothing here is out-of-sample evidence (the frozen
2024-25 validation results in modeling/player_props_v0/*metrics.json remain the promotion evidence).

The artifact (JSON) holds only what scoring needs: the v0 state (minutes preprocessing + coefficients, position prior
rates, shrinkage k, dispersion per target, minutes residual SDs, baseline population means), the participation state
(preprocessing + coefficients), the frozen config (recency, targets, supports), versions and the training range. Player
rates are not stored: by design they are computed at scoring time from that game's point-in-time features.

    .venv/bin/python -m cbb_platform.props_v1_refit    -> modeling/player_props_v1/refit_2024_25_artifact.json
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cbb_platform import lockbox, props_v0 as P, props_v0_participation as Q, props_v1_pricing as V

TRAIN_THROUGH = 2025                                   # 2024-25
PARTICIPATION_SEASONS = (2024, 2025)                   # 2023-24, 2024-25 (the only seasons with confirmed DNPs)
ARTIFACT_PATH = P.CONFIG_PATH.parent.parent / "player_props_v1" / "refit_2024_25_artifact.json"
ARTIFACT_VERSION = "cbb-props-v1-refit-2024-25"


def spec_fingerprint(cfg: dict) -> str:
    """sha256 of every frozen specification element (config + participation inputs/penalty + pricing constants)."""
    spec = {"config": cfg, "participation": {"numeric": Q.NUMERIC, "log1p": sorted(Q.LOG1P), "flags": Q.FLAGS,
                                             "l2_alpha": Q.L2_ALPHA, "labels": Q.LABEL},
            "pricing": {"version": V.PRICING_VERSION, "ladder_tail": V.LADDER_TAIL, "quantiles": V.QUANTILES}}
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


def fit_artifact() -> dict:
    lockbox.check_season(TRAIN_THROUGH)
    if TRAIN_THROUGH != lockbox.LAST_DEVELOPMENT_SEASON:
        raise ValueError("the final development refit trains through the last development season only")
    cfg = P.load_config()
    frame, finals, _ = P.load_frames(cfg)
    v0 = P.fit(frame, finals, cfg, TRAIN_THROUGH)
    part_rows, counts = Q.load(seasons=PARTICIPATION_SEASONS)
    part = Q.fit(part_rows, train_seasons=PARTICIPATION_SEASONS)
    rows = {str(s): {"played": int(((part_rows["season"] == s) & (part_rows["y"] == 1)).sum()),
                     "dnp": int(((part_rows["season"] == s) & (part_rows["y"] == 0)).sum()),
                     "unknown_excluded": counts[str(s)].get("UNKNOWN_PARTICIPATION", 0),
                     "not_on_roster_excluded": counts[str(s)].get("NOT_ON_ROSTER", 0)} for s in PARTICIPATION_SEASONS}
    art = {"artifact_version": ARTIFACT_VERSION, "model_version": V.MODEL_VERSION,
           "dataset_version": cfg["upstream_dataset"], "training_through_season": TRAIN_THROUGH,
           "stat_training_seasons": [int(frame["season"].min()), TRAIN_THROUGH],
           "participation_training_seasons": list(PARTICIPATION_SEASONS), "participation_rows": rows,
           "spec_fingerprint": spec_fingerprint(cfg), "config": cfg,
           "v0_state": v0, "participation_state": part,
           # the frozen scorers read coefficients positionally (dict order = design-matrix column order)
           "coef_order": {"v0": list(v0["coef"]), "participation": list(part["coef"])}}
    art["artifact_fingerprint"] = fingerprint(art)
    return art


def fingerprint(art: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in art.items() if k != "artifact_fingerprint"},
                                     sort_keys=True).encode()).hexdigest()


def dumps(art: dict) -> str:
    # insertion order is preserved (NOT sort_keys): coefficient dicts are positional for the frozen scorers
    return json.dumps(art, indent=1) + "\n"


def save(art: dict, path: Path = ARTIFACT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(art))


def load(path: Path = ARTIFACT_PATH) -> dict:
    art = json.loads(Path(path).read_text())
    if fingerprint(art) != art["artifact_fingerprint"]:
        raise ValueError("artifact fingerprint mismatch")
    if art["spec_fingerprint"] != spec_fingerprint(P.load_config()):
        raise ValueError("artifact was fit under a different specification")
    if list(art["v0_state"]["coef"]) != art["coef_order"]["v0"] or \
            list(art["participation_state"]["coef"]) != art["coef_order"]["participation"]:
        raise ValueError("coefficient order differs from the design-matrix order recorded at fit time")
    if art["training_through_season"] >= lockbox.LOCKBOX_SEASON:
        raise lockbox.LockboxViolation("artifact trained on 2025-26")
    return art


def scoring_model(art: dict) -> dict:
    """The props_v1_pricing.score() model dict from a loaded artifact. Nothing is fit: only the participation
    FEATURE rows are read from the Stage 3 store (labels are not used by scoring)."""
    part_rows, _ = Q.load(seasons=PARTICIPATION_SEASONS)
    return {"cfg": art["config"], "v0": art["v0_state"], "part": art["participation_state"], "part_rows": part_rows}


def score_demo(art: dict) -> "pd.DataFrame":
    """The fixed v1 demonstration games (2024-25; IN-SAMPLE for this refit: a functional check only)."""
    cand = V.load_candidates()
    return V.score(cand[cand["canonical_game_id"].isin(V.sample_games(cand))], scoring_model(art))


if __name__ == "__main__":
    a = fit_artifact()
    save(a)
    print(json.dumps({k: a[k] for k in ("artifact_version", "training_through_season", "participation_training_seasons",
                                        "participation_rows", "spec_fingerprint", "artifact_fingerprint")}, indent=1))
    print("n_train v0", a["v0_state"]["n_train_rows"], "participation", a["participation_state"]["n_train"])
