"""TRAINING FRESHNESS: the production model of ET date D must be trained on every eligible completed game through D-1.

Receipt / gate layer only. Nothing here fits, selects, weights or changes a feature, a label, a PMF or a price; it reads
the durable artifacts the walk-forward already writes (version manifest, eligibility.parquet, the ACTIVE pointer, the
forecast index) and states whether the model serving D is fresh.

    EXPECTED_TRAINING_THROUGH       = D - 1 (ET calendar day)
    ACTIVE_MODEL_TRAINING_THROUGH   = D - 1 when the ACTIVE version was fit on D's training cutoff (walkforward.config
                                      .refit_cutoff(D)), or when D's morning run certified that D's legal training inputs
                                      are IDENTICAL to the ACTIVE version's (NO_NEW_DATA carry-forward receipt);
                                      otherwise (ET date of the ACTIVE version's training cutoff) - 1, i.e. stale.

PREVIOUS-DAY PARTITION. Every D-1 game in the training scope (Stage 3 D-I vs D-I universe, played) is classified
EXACTLY ONCE, with its game id persisted:
    TRAINING_INCLUDED              in the candidate's training rows
    UNRECONCILED_FINAL             final, box complete, player / team / game points disagree      (deferred)
    INCOMPLETE_FINAL               final, but result / box / identity / pregame example missing   (deferred)
    DATA_NOT_AVAILABLE_AT_CUTOFF   not final, not knowable, or no state version at the cutoff     (deferred)
Out-of-scope D-1 games (NOT_IN_TRAINING_UNIVERSE, NOT_PLAYED) are counted separately so the totals reconcile:
    PREVIOUS_DAY_ALL_GAMES = PREVIOUS_DAY_FINAL_GAMES (in scope) + PREVIOUS_DAY_OUT_OF_SCOPE
    PREVIOUS_DAY_FINAL_GAMES = INCLUDED + UNRECONCILED_FINAL + INCOMPLETE_FINAL + DATA_NOT_AVAILABLE_AT_CUTOFF
A reason the classifier does not know is UNCLASSIFIED and fails freshness (never a silent omission). Deferred games are
re-evaluated at every later cutoff (walkforward.eligibility); they are ACCEPTED data gaps, never backfilled.

GAME-MODEL CONTRACT (the future game add-on). joint_contract() is the single rule the GameJointPMF must pass before
it is written: the player state and the team-rating state must BOTH be through D-1. A component that does not exist
yet is NOT_APPLICABLE; a component that exists and is stale fails the contract.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from cbb_platform.walkforward import config as WC

FRESHNESS_VERSION = "cbb-training-freshness-1.0.0"
TRAINING_INCLUDED = "TRAINING_INCLUDED"
UNRECONCILED_FINAL = "UNRECONCILED_FINAL"
INCOMPLETE_FINAL = "INCOMPLETE_FINAL"
DATA_NOT_AVAILABLE_AT_CUTOFF = "DATA_NOT_AVAILABLE_AT_CUTOFF"
UNCLASSIFIED = "UNCLASSIFIED"
CLASSES = (TRAINING_INCLUDED, UNRECONCILED_FINAL, INCOMPLETE_FINAL, DATA_NOT_AVAILABLE_AT_CUTOFF)
EXCLUSION_CLASSES = CLASSES[1:]
# eligibility.evaluate reasons + the training_rows feature-build statuses written into `reason` by daily.training_state
CLASS_OF_REASON = {
    "": TRAINING_INCLUDED,
    "BOX_UNRECONCILED": UNRECONCILED_FINAL,
    "FINAL_DATA_INCOMPLETE_AT_CUTOFF": INCOMPLETE_FINAL,
    "BOX_INCOMPLETE": INCOMPLETE_FINAL,
    "IDENTITY_UNRESOLVED": INCOMPLETE_FINAL,
    "NO_PREGAME_EXAMPLES": INCOMPLETE_FINAL,
    "NOT_FINAL_AT_CUTOFF": DATA_NOT_AVAILABLE_AT_CUTOFF,
    "STATUS_NOT_FINAL": DATA_NOT_AVAILABLE_AT_CUTOFF,
    "NOT_KNOWABLE_AT_CUTOFF": DATA_NOT_AVAILABLE_AT_CUTOFF,
    "NO_STATE_AT_CUTOFF": DATA_NOT_AVAILABLE_AT_CUTOFF,
}
OUT_OF_SCOPE_REASONS = ("NOT_IN_TRAINING_UNIVERSE", "NOT_PLAYED")
ACCEPTED_DATA_GAP_CLASSES = EXCLUSION_CLASSES          # explicit, persisted, deferred to the next cutoff
NO_NEW_GAMES = "NO_NEW_GAMES"
STALE_TRAINING_MODEL = "STALE_TRAINING_MODEL"
STALE_FORECAST_MODEL = "STALE_FORECAST_MODEL"
FULL_SLATE_WINDOWS = ("INITIAL", "REFIT_RETRY")        # the snapshots that forecast D's whole eligible slate
PASS, FAIL, NOT_APPLICABLE, NOT_EVALUATED = "PASS", "FAIL", "NOT_APPLICABLE", "NOT_EVALUATED"


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def expected_training_through(day: date) -> date:
    return day - timedelta(days=1)


def through_of_cutoff(cutoff) -> date | None:
    """A model fit at training cutoff T (D 06:00 ET) has consumed the legal state through ET date D - 1."""
    if cutoff is None or (isinstance(cutoff, float) and pd.isna(cutoff)):
        return None
    return _utc(cutoff).tz_convert(WC.TZ).date() - timedelta(days=1)


# ============================================================================ previous-day partition
def classify_prior_day(el: pd.DataFrame, day: date) -> dict:
    """The D-1 partition (module docstring) from a training eligibility table (eligibility.COLUMNS)."""
    prior = expected_training_through(day)
    out = {"PREVIOUS_DAY_DATE": prior.isoformat(), "PREVIOUS_DAY_ALL_GAMES": 0, "PREVIOUS_DAY_OUT_OF_SCOPE": 0,
           "PREVIOUS_DAY_FINAL_GAMES": 0, "PREVIOUS_DAY_INCLUDED_GAMES": 0, "PREVIOUS_DAY_INCLUDED_GAME_IDS": [],
           "PREVIOUS_DAY_EXCLUDED_GAMES": 0, "PREVIOUS_DAY_EXCLUDED_GAME_IDS": {c: [] for c in EXCLUSION_CLASSES},
           "PREVIOUS_DAY_EXCLUSION_REASONS": {}, "PREVIOUS_DAY_CLASS_COUNTS": {c: 0 for c in CLASSES},
           "PREVIOUS_DAY_UNCLASSIFIED_GAME_IDS": [], "PREVIOUS_DAY_OUT_OF_SCOPE_REASONS": {}}
    if el is None or not len(el):
        out.update(PREVIOUS_DAY_STATUS=NO_NEW_GAMES, COUNTERS_RECONCILE=True)
        return out
    tip_et = pd.to_datetime(el["scheduled_tip_utc"], utc=True).dt.tz_convert(WC.TZ).dt.date
    g = el[tip_et == prior]
    if g["game_id"].duplicated().any():
        raise ValueError(f"duplicate D-1 eligibility rows: {sorted(g.loc[g['game_id'].duplicated(), 'game_id'])}")
    reason = g["reason"].fillna("").astype(str)
    oos = reason.isin(OUT_OF_SCOPE_REASONS) | (g["game_universe"] != WC.TRAINING_UNIVERSE)
    scope = g[~oos]
    cls = scope["reason"].fillna("").astype(str).map(lambda r: CLASS_OF_REASON.get(r, UNCLASSIFIED))
    cls[(scope["decision"] == "INCLUDED") & (cls != TRAINING_INCLUDED)] = UNCLASSIFIED   # decision / reason disagree
    cls[(scope["decision"] != "INCLUDED") & (cls == TRAINING_INCLUDED)] = UNCLASSIFIED
    counts = {c: int((cls == c).sum()) for c in CLASSES}
    excl = scope[cls.isin(EXCLUSION_CLASSES)]
    out.update(
        PREVIOUS_DAY_ALL_GAMES=int(len(g)), PREVIOUS_DAY_OUT_OF_SCOPE=int(oos.sum()),
        PREVIOUS_DAY_OUT_OF_SCOPE_REASONS={k: int(v) for k, v in reason[oos].replace("", "NOT_IN_TRAINING_UNIVERSE")
                                           .value_counts().sort_index().items()},
        PREVIOUS_DAY_FINAL_GAMES=int(len(scope)), PREVIOUS_DAY_CLASS_COUNTS=counts,
        PREVIOUS_DAY_INCLUDED_GAMES=counts[TRAINING_INCLUDED],
        PREVIOUS_DAY_INCLUDED_GAME_IDS=sorted(scope.loc[cls == TRAINING_INCLUDED, "game_id"]),
        PREVIOUS_DAY_EXCLUDED_GAMES=int(len(excl)),
        PREVIOUS_DAY_EXCLUDED_GAME_IDS={c: sorted(scope.loc[cls == c, "game_id"]) for c in EXCLUSION_CLASSES},
        PREVIOUS_DAY_EXCLUSION_REASONS={f"{c}:{r}": int(n) for (c, r), n in
                                        pd.DataFrame({"c": cls[cls.isin(EXCLUSION_CLASSES)],
                                                      "r": excl["reason"].astype(str)}).groupby(["c", "r"]).size().items()},
        PREVIOUS_DAY_EXCLUSIONS=[{"game_id": r.game_id, "class": c, "reason": str(r.reason)}
                                 for r, c in zip(excl.itertuples(index=False), cls[cls.isin(EXCLUSION_CLASSES)])],
        PREVIOUS_DAY_UNCLASSIFIED_GAME_IDS=sorted(scope.loc[cls == UNCLASSIFIED, "game_id"]))
    ok = (out["PREVIOUS_DAY_ALL_GAMES"] == out["PREVIOUS_DAY_FINAL_GAMES"] + out["PREVIOUS_DAY_OUT_OF_SCOPE"]
          and out["PREVIOUS_DAY_FINAL_GAMES"] == sum(counts.values()) + len(out["PREVIOUS_DAY_UNCLASSIFIED_GAME_IDS"])
          and out["PREVIOUS_DAY_EXCLUDED_GAMES"] == sum(counts[c] for c in EXCLUSION_CLASSES))
    out["COUNTERS_RECONCILE"] = bool(ok)
    out["PREVIOUS_DAY_STATUS"] = (UNCLASSIFIED if out["PREVIOUS_DAY_UNCLASSIFIED_GAME_IDS"] or not ok else
                                  NO_NEW_GAMES if not len(scope) else
                                  "COMPLETE" if not len(excl) else "ACCEPTED_DATA_GAPS")
    return out


# ============================================================================ candidate gate (before any fit)
def candidate_inputs_check(ts: dict, state_root: Path, day: date) -> dict:
    """STALE-INPUT gate of a morning candidate (walkforward.daily): refuses a candidate whose training inputs are not
    the complete legal state at D's cutoff. PASS requires: the training cutoff is D's; the state used is THE latest
    version as-of that cutoff (a stale cached state / history would differ); every eligibility-INCLUDED game is in the
    trained set and has labels; every D-1 game is classified."""
    from cbb_platform.walkforward import eligibility as EL
    cut = WC.refit_cutoff(day)
    checks, detail = {}, {}
    checks["CUTOFF_IS_D"] = ts.get("training_cutoff") is not None and _utc(ts["training_cutoff"]) == cut
    latest = EL.state_asof(state_root, cut)
    used = (ts.get("state") or {}).get("state_version")
    detail["STATE_USED"], detail["STATE_LATEST_AS_OF_CUTOFF"] = used, (latest or {}).get("state_version")
    checks["STATE_IS_LATEST_AS_OF_CUTOFF"] = latest is not None and used == latest["state_version"]
    el = ts.get("eligibility")
    inc = set(el.loc[el["decision"] == "INCLUDED", "game_id"]) if el is not None and len(el) else set()
    trained = set(ts.get("trained_games") or [])
    lab = ts.get("labels")
    labeled = set(lab["canonical_game_id"]) if lab is not None and len(lab) and "canonical_game_id" in lab else set()
    detail["INCLUDED_NOT_TRAINED"] = sorted(inc - trained)
    detail["TRAINED_NOT_INCLUDED"] = sorted(trained - inc)
    detail["TRAINED_WITHOUT_LABELS"] = sorted(trained - labeled)
    checks["TRAINED_EQUALS_INCLUDED"] = inc == trained
    checks["EVERY_TRAINED_GAME_LABELED"] = not detail["TRAINED_WITHOUT_LABELS"]
    pd_ = classify_prior_day(el, day)
    detail["PREVIOUS_DAY_STATUS"] = pd_["PREVIOUS_DAY_STATUS"]
    checks["PREVIOUS_DAY_CLASSIFIED"] = pd_["PREVIOUS_DAY_STATUS"] != UNCLASSIFIED
    checks["PREVIOUS_DAY_INCLUDED_TRAINED"] = set(pd_["PREVIOUS_DAY_INCLUDED_GAME_IDS"]) <= trained
    return {"PASS": all(checks.values()), "checks": checks, **detail, "previous_day": pd_}


# ============================================================================ the freshness manifest (per morning run)
def receipt_path(root: Path, day: date) -> Path:
    return Path(root) / "manifests" / "freshness" / f"{day.isoformat()}.json"


def _write_json_atomic(p: Path, body: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".{p.name}.{uuid.uuid4().hex}"
    tmp.write_text(json.dumps(body, indent=1, sort_keys=True, default=str) + "\n")
    os.replace(tmp, p)


def manifest_fields(man: dict, prior: dict, through: date | None, basis: str) -> dict:
    latest = (man.get("LATEST_INCLUDED_GAME") or {}).get("scheduled_tip_utc")
    fit_rows = ((man.get("fit") or {}).get("rows") or {}).get("frame")
    return {"MODEL_VERSION": man.get("MODEL_VERSION"), "TRAINING_CUTOFF": man.get("TRAINING_CUTOFF"),
            "TRAINING_THROUGH_DATE": through.isoformat() if through else None, "TRAINING_THROUGH_BASIS": basis,
            "LATEST_GAME_DATE_INCLUDED": str(_utc(latest).tz_convert(WC.TZ).date()) if latest else None,
            **{k: prior.get(k) for k in ("PREVIOUS_DAY_DATE", "PREVIOUS_DAY_FINAL_GAMES", "PREVIOUS_DAY_INCLUDED_GAMES",
                                         "PREVIOUS_DAY_EXCLUDED_GAMES", "PREVIOUS_DAY_EXCLUDED_GAME_IDS",
                                         "PREVIOUS_DAY_EXCLUSION_REASONS", "PREVIOUS_DAY_STATUS")},
            "N_TRAIN_ROWS": {"current_season_player_games": man.get("N_INCLUDED_2026_27_PLAYER_GAMES"),
                             "current_season_games": man.get("N_INCLUDED_2026_27_GAMES"), "fit_frame_rows": fit_rows},
            "SOURCE_STATE_VERSION": man.get("STATE_VERSION"), "SOURCE_HISTORY_HASH": man.get("TRAINING_DATA_HASH")}


def write_run_receipt(root: Path, day: date, rec: dict, ts: dict, active_manifest: dict) -> dict:
    """Written by every morning run of D (PASS / NO_NEW_DATA / REFUSED / FAIL): the D-1 partition at D's cutoff, the
    decision, and the freshness fields of the version that is ACTIVE after the run. Latest run of D wins (atomic)."""
    prior = classify_prior_day(ts.get("eligibility"), day)
    certified = (rec.get("DAILY_REFIT_STATUS") == "NO_NEW_DATA"
                 and active_manifest.get("MODEL_VERSION") == rec.get("ACTIVE_MODEL_VERSION"))
    if _utc(active_manifest.get("TRAINING_CUTOFF") or "1970-01-01") == WC.refit_cutoff(day):
        through, basis = expected_training_through(day), "ACTIVE_FIT_AT_D_CUTOFF"
    elif certified:
        through, basis = expected_training_through(day), "CERTIFIED_NO_NEW_DATA_AT_D_CUTOFF"
    else:
        through, basis = through_of_cutoff(active_manifest.get("TRAINING_CUTOFF")), "ACTIVE_FIT_AT_EARLIER_CUTOFF"
    body = {"FRESHNESS_VERSION": FRESHNESS_VERSION, "DAY": day.isoformat(), "RUN_ID": rec.get("run_id"),
            "D_TRAINING_CUTOFF": WC.refit_cutoff(day).isoformat(), "DAILY_REFIT_STATUS": rec.get("DAILY_REFIT_STATUS"),
            "DAILY_MODEL_PROMOTION": rec.get("DAILY_MODEL_PROMOTION"),
            "D_STATE_VERSION": (ts.get("state") or {}).get("state_version"),
            "CANDIDATE_INPUTS_CHECK": rec.get("candidate_inputs_check"),
            **manifest_fields(active_manifest, prior, through, basis),
            "PREVIOUS_DAY": prior, "written_at": pd.Timestamp.now(tz="UTC").isoformat()}
    _write_json_atomic(receipt_path(root, day), body)
    return body


# ============================================================================ evaluation (health)
def _forecasts(ops_root: Path | None, day: date) -> list[dict]:
    if ops_root is None:
        return []
    p = Path(ops_root) / "forecasts" / "index.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if str(r.get("slate_date") or "")[:10] == day.isoformat() and r.get("model_version"):
            out.append(r)
    return out


def evaluate(model_root: Path, day: date, ops_root: Path | None = None) -> dict:
    """TRAINING_FRESHNESS of ET date D from durable artifacts alone (the model store + the forecast index)."""
    from cbb_platform.walkforward import store as SO
    root = Path(model_root)
    exp = expected_training_through(day)
    out = {"TRAINING_FRESHNESS": NOT_EVALUATED, "EXPECTED_TRAINING_THROUGH": exp.isoformat(),
           "ACTIVE_MODEL_TRAINING_THROUGH": None, "ACTIVE_MODEL_VERSION": None, "FRESHNESS_VERSION": FRESHNESS_VERSION}
    act = SO.active(root) if (root / "active").exists() else None
    if act is None:
        return {**out, "reason": "NO_MODEL_STORE" if not root.exists() else "NO_ACTIVE_MODEL"}
    vid = act["ACTIVE_MODEL_VERSION"]
    try:
        man = SO.manifest(root, vid)
    except (OSError, ValueError, KeyError) as e:
        return {**out, "TRAINING_FRESHNESS": FAIL, "ACTIVE_MODEL_VERSION": vid, "ACTIVE_BLOCKER": STALE_TRAINING_MODEL,
                "reason": f"ACTIVE_MANIFEST_UNREADABLE {type(e).__name__}"}
    rcpt = json.loads(receipt_path(root, day).read_text()) if receipt_path(root, day).exists() else None
    vdir = SO.version_dir(root, SO.day_of(vid))
    if _utc(man.get("TRAINING_CUTOFF") or "1970-01-01") == WC.refit_cutoff(day):
        through, basis = exp, "ACTIVE_FIT_AT_D_CUTOFF"
        prior = (classify_prior_day(pd.read_parquet(vdir / "eligibility.parquet"), day)
                 if (vdir / "eligibility.parquet").exists() else (rcpt or {}).get("PREVIOUS_DAY"))
    elif rcpt and rcpt.get("TRAINING_THROUGH_BASIS") == "CERTIFIED_NO_NEW_DATA_AT_D_CUTOFF" \
            and rcpt.get("MODEL_VERSION") == vid:
        through, basis, prior = exp, rcpt["TRAINING_THROUGH_BASIS"], rcpt.get("PREVIOUS_DAY")
    else:
        through, basis = through_of_cutoff(man.get("TRAINING_CUTOFF")), "ACTIVE_FIT_AT_EARLIER_CUTOFF"
        prior = (rcpt or {}).get("PREVIOUS_DAY")
    fields = manifest_fields(man, prior or {}, through, basis)
    unacc = []
    if prior:
        unacc = list(prior.get("PREVIOUS_DAY_UNCLASSIFIED_GAME_IDS") or [])
        if not prior.get("COUNTERS_RECONCILE", True):
            unacc.append("COUNTERS_DO_NOT_RECONCILE")
    if through is not None and through > exp:              # a past ET date judged against a later ACTIVE version
        return {**out, **fields, "TRAINING_FRESHNESS": NOT_APPLICABLE, "ACTIVE_MODEL_VERSION": vid,
                "ACTIVE_MODEL_TRAINING_THROUGH": fields["TRAINING_THROUGH_DATE"], "reason": "PAST_DAY"}
    fresh = through == exp
    snaps = [s for s in _forecasts(ops_root, day)
             if any(str(s.get("window") or "").startswith(w) for w in FULL_SLATE_WINDOWS)]
    last_full = snaps[-1] if snaps else None
    fc_ok = last_full is None or (fresh and last_full.get("model_version") == vid)
    status = PASS if fresh and not unacc and fc_ok else FAIL
    blocker = (STALE_TRAINING_MODEL if not fresh or unacc else STALE_FORECAST_MODEL if not fc_ok else None)
    lag = bool(prior and prior.get("PREVIOUS_DAY_FINAL_GAMES") and not prior.get("PREVIOUS_DAY_INCLUDED_GAMES"))
    return {**out, **fields, "TRAINING_FRESHNESS": status, "ACTIVE_MODEL_VERSION": vid,
            "TRAINING_DATA_LAG": lag,          # every in-scope D-1 game deferred (accepted, but never silent)
            "ACTIVE_MODEL_TRAINING_THROUGH": fields["TRAINING_THROUGH_DATE"], "FRESH": fresh,
            "UNACCEPTED_GAPS": unacc, "PREVIOUS_DAY_CLASSIFICATION_RECORDED": prior is not None,
            "FULL_SLATE_FORECAST": ({k: last_full.get(k) for k in ("snapshot_id", "window", "model_version", "executed_at")}
                                    if last_full else None),
            "FULL_SLATE_FORECAST_ON_FRESH_MODEL": None if last_full is None else fc_ok,
            "ACTIVE_BLOCKER": blocker, "RUN_RECEIPT": (rcpt or {}).get("RUN_ID")}


# ============================================================================ the game-model contract (future add-on)
def joint_contract(day: date, player_training_through, team_rating_through=None) -> dict:
    """GameJointPMF for D may be written only when BOTH states are through D-1 (a component that does not exist yet:
    None -> NOT_APPLICABLE). Never stale team ratings with fresh player forecasts, or the reverse."""
    exp = expected_training_through(day).isoformat()
    def st(x):
        return NOT_APPLICABLE if x is None else (PASS if str(x)[:10] == exp else FAIL)
    p, t = st(player_training_through), st(team_rating_through)
    ok = p == PASS and t in (PASS, NOT_APPLICABLE)
    return {"EXPECTED_TRAINING_THROUGH": exp, "PLAYER_STATE": p, "TEAM_RATING_STATE": t,
            "PLAYER_TRAINING_THROUGH": None if player_training_through is None else str(player_training_through)[:10],
            "TEAM_RATING_TRAINING_THROUGH": None if team_rating_through is None else str(team_rating_through)[:10],
            "GAME_PMF_ALLOWED": ok, "PASS": ok}
