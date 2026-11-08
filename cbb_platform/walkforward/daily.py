"""THE MORNING JOB of the 2026-27 daily walk-forward refit, and the scorer entry that uses the ACTIVE daily version.

    morning(D):  SCORE PRIOR PREDICTIONS -> UPDATE MONITORING -> INGEST (state as-of the D 06:00 ET cutoff, eligibility,
                 training rows) -> REFIT (twice: determinism) -> HARD GATES -> ATOMIC PROMOTION (or REFUSED; the last
                 known-good version stays ACTIVE)
    predict(t):  prod.run.daily with the ACTIVE version (its training cutoff must be <= t); provenance + pmfs recorded

Idempotent: the run id is a hash of the cutoff + training-data identity + code commit + config; a rerun on the same
inputs settles nothing twice, writes no new version, leaves ACTIVE unchanged and rewrites identical reports. A day with
no new legal training data creates no version (ACTIVE carries forward). Opening day (2026-11-01) is scored by
OPENING_DAY_BASELINE; the first daily version is created the morning after the first completed 2026-27 games pass
eligibility.

    PYTHONPATH=. .venv/bin/python -m cbb_platform.walkforward.daily morning [--date 2026-11-02]
    PYTHONPATH=. .venv/bin/python -m cbb_platform.walkforward.daily predict --as-of 2026-11-02T22:00:00Z
    PYTHONPATH=. .venv/bin/python -m cbb_platform.walkforward.daily status | register-opening
    PYTHONPATH=. .venv/bin/python -m cbb_platform.walkforward.daily rollback --reason R --approved-by NAME
Run with OMP/OPENBLAS/MKL/VECLIB/NUMEXPR_NUM_THREADS=1 (config.THREAD_ENV); one morning job at a time (file lock).
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import platform
import resource
import shutil
import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from cbb_platform import props_v0 as P, props_v1_refit as R
from cbb_platform.prod import config as PC, run as RN, state as ST
from cbb_platform.v2 import phase2 as P2
from cbb_platform.walkforward import config as WC, corrections as CR, eligibility as EL, fit as WF, gates as GT, monitor as MON
from cbb_platform.walkforward import history_source as HS, store as SO, training_rows as TR


class MorningJobLocked(RuntimeError):
    pass


def _utc(x) -> pd.Timestamp:
    return EL._utc(x)


def _sha(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def _rss_gb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1e9 if sys.platform == "darwin" else r * 1024 / 1e9


def _du(p: Path) -> int:
    return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file()) if Path(p).exists() else 0


@contextlib.contextmanager
def _lock(root: Path):
    p = SO.paths(root)["locks"] / "morning.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        f.close()
        raise MorningJobLocked("another CBB walk-forward morning job holds the lock") from e
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def config_hash(cfg: dict) -> str:
    return _sha({"marginal_config": cfg, "walk_forward": WC.WALK_FORWARD_VERSION, "refit_time": WC.DAILY_REFIT_TIME,
                 "windows": [WC.MARGINAL_FIRST_SEASON, WC.PARTICIPATION_FIRST_SEASON, WC.TEAM_FIRST_SEASON,
                             WC.GLM_FIRST_SEASON, WC.RESID_FIRST_SEASON], "sims": PC.SIMS, "universe": WC.TRAINING_UNIVERSE})


def history_identity() -> dict:
    """The frozen through-2025-26 training stores, identified by the sha256 recorded in the frozen production manifest.
    CONTROL re-checks the original stores' sizes (a full re-hash of the 2.4 GB store would cost minutes on the shared
    machine); CANDIDATE verifies the compact source (sha256 + bytes vs its manifest, extracted from exactly these
    data_versions). history_source.identity."""
    return HS.identity()


def run_identity(cut, state_version: str | None, eligibility: pd.DataFrame, training_identity: dict, code: dict,
                 cfg_hash: str, previous_model: dict | None) -> dict:
    """EVERY material logical input of one morning run. Two runs share a run id (and the second is deduplicated) only
    if all of these are identical: the cutoff, the state version used, every game's eligibility decision + reason, the
    training-data identity (history store ids + current-season frame / feature-row hashes), the code commit (+ dirty
    flag), the config identity and the previous model the candidate chains to (version + manifest hash)."""
    el = eligibility.sort_values("game_id")[["game_id", "decision", "reason"]].astype(str).to_numpy().tolist() if len(eligibility) else []
    return {"training_cutoff": pd.Timestamp(cut).isoformat(), "state_version": state_version,
            "eligibility_sha256": _sha(el), "training_identity_sha256": _sha(training_identity),
            "code_commit": code.get("commit"), "code_dirty": code.get("dirty"), "config_hash": cfg_hash,
            "previous_model": previous_model}


def run_id_of(day: date, ident: dict) -> str:
    return f"WF-{day.strftime('%Y%m%d')}-{_sha(ident)[:16]}"


# ============================================================================ ingest -> eligibility -> training rows
def training_state(day: date, root: Path, state_root: Path, history_db: Path, cfg: dict, log=print) -> dict:
    cut = WC.refit_cutoff(day)
    t0 = time.time()
    mp = SO.paths(root)["manifests"] / "outcome_available_at.json"
    avail = EL.outcome_available_at(state_root, cut, json.loads(mp.read_text()) if mp.exists() else None)
    SO.write_json(mp, avail)
    st = EL.state_asof(state_root, cut)
    out = {"training_cutoff": cut, "state": st, "frames": None, "feature_meta": {}, "trained_games": [], "included": [],
           "label_state_created_at": None, "label_post_as_of_rows": 0, "oos_state_is_opening_marginal": True,
           "state_validation_ok": True, "labels": pd.DataFrame(), "timings": {}}
    if st is None:
        out["eligibility"] = pd.DataFrame(columns=EL.COLUMNS)
        out["identity"] = {"history": history_identity(), "current_season": None}
        return out
    try:
        ST.validate(st["increment_db"], st["source_cutoff"])
    except ST.StateUpdateFailed as e:
        out["state_validation_ok"], out["state_validation_error"] = False, str(e)[:500]
    el = EL.evaluate(st, cut, avail)
    inc = el.loc[el["decision"] == "INCLUDED", "game_id"].tolist()
    out["timings"]["eligibility_s"] = time.time() - t0
    if inc:
        t1 = time.time()
        gc = TR.game_cutoffs(st["increment_db"], inc)
        feats = TR.feature_rows(SO.paths(root)["training"], state_root, gc, history_db, cfg, log=log)
        out["timings"]["feature_update_s"] = time.time() - t1
        meta = feats["meta"]
        trained = sorted(g for g in inc if meta.get(g, {}).get("status") == "BUILT")
        for g in sorted(set(inc) - set(trained)):              # included by the data rule, but no legal pregame row
            el.loc[el["game_id"] == g, ["decision", "reason"]] = ["EXCLUDED", meta.get(g, {}).get("status", "NO_PREGAME_EXAMPLES")]
        out.update(feature_meta=meta, trained_games=trained, included=trained)
        if trained:
            t2 = time.time()
            work = SO.paths(root)["candidates"] / f".labels-{uuid.uuid4().hex[:10]}"
            try:
                lab = TR.label_rows(work, st, cut, trained, history_db, cfg)
            finally:
                shutil.rmtree(work, ignore_errors=True)
            opening = SO.load_version(root, WC.version_id(WC.OPENING_DATE))
            oos_state = {"v0_state": opening.v0_state, "participation_state": opening.participation_state}
            cand = feats["cand"][feats["cand"]["canonical_game_id"].isin(trained)]
            team = feats["team"][feats["team"]["game_id"].isin(trained)]
            fr = TR.assemble(cand, team, lab, cfg, oos_state, WC.OPENING_THROUGH)
            out.update(frames=fr, labels=lab["labels"][lab["labels"]["canonical_game_id"].isin(trained)].reset_index(drop=True),
                       label_state_created_at=st["created_at"].isoformat(),
                       label_post_as_of_rows=int(lab["provenance"]["post_as_of_rows"]),
                       oos_state_is_opening_marginal=opening.identity["artifact_sha256"]["marginal"] == PC.ARTIFACTS["marginal"][1])
            out["timings"]["labels_and_assembly_s"] = time.time() - t2
    out["eligibility"] = el
    cur = None
    if out["frames"] is not None and len(out["frames"]["frame"]):
        cur = {"hashes": out["frames"]["hashes"], "games": out["trained_games"],
               "feature_rows": {g: (out["feature_meta"][g]["cand_sha256"], out["feature_meta"][g]["team_sha256"])
                                for g in out["trained_games"]}}
    out["identity"] = {"history": history_identity(), "current_season": cur}
    return out


# ============================================================================ the candidate version
def serialize(f: dict, cfg: dict, meta: dict) -> dict:
    """Artifact bytes exactly as the frozen writers produce them (props_v1_refit.dumps for the marginal: positional
    coefficient order; phase2._dump format for the structural / dependence artifacts)."""
    marg = WF.marginal_artifact(f, cfg, meta)
    mbytes = R.dumps(marg).encode()
    art, dep = WF.structural_artifacts(f, meta, hashlib.sha256(mbytes).hexdigest())
    dbytes = (json.dumps(dep, indent=1, default=P2._json_default) + "\n").encode()
    art["RESIDUAL_DEPENDENCE_ARTIFACT"] = SO.FILES["residual_j2"]
    art["RESIDUAL_DEPENDENCE_ARTIFACT_SHA256"] = hashlib.sha256(dbytes).hexdigest()
    abytes = (json.dumps(art, indent=1, default=P2._json_default) + "\n").encode()
    return {"marg": marg, "art": art, "dep": dep, "mbytes": mbytes, "abytes": abytes, "dbytes": dbytes}


def build_candidate(root: Path, day: date, ts: dict, cfg: dict, prev_active: str, ident: dict, log=print) -> dict:
    vid = WC.version_id(day)
    cut = ts["training_cutoff"]
    fr = ts["frames"]
    tstate = {"state_version": ts["state"]["state_version"], "state_created_at": ts["state"]["created_at"].isoformat(),
              "games": len(ts["trained_games"]), "frames_sha256": fr["hashes"]}
    meta = {"model_version": vid, "training_cutoff": cut.isoformat(), "training_state": tstate}
    timings, shas, built = {}, [], None
    for k in (1, 2):                                             # determinism: two independent fits
        t = time.time()
        f = WF.fit(cfg, fr, log=lambda s: log(f"fit {k}/2: {s}"))
        x = serialize(f, cfg, meta)
        shas.append(hashlib.sha256(x["mbytes"] + x["abytes"] + x["dbytes"]).hexdigest())
        timings[f"fit_{k}_s"] = time.time() - t
        if built is None:
            built = (f, x)
    f, x = built
    marg, art, mbytes, abytes, dbytes = x["marg"], x["art"], x["mbytes"], x["abytes"], x["dbytes"]
    cand = SO.new_candidate_dir(root, day)
    sha = {"marginal": SO.write_bytes(cand / SO.FILES["marginal"], mbytes),
           "jm1_final": SO.write_bytes(cand / SO.FILES["jm1_final"], abytes),
           "residual_j2": SO.write_bytes(cand / SO.FILES["residual_j2"], dbytes)}
    el = ts["eligibility"]
    inc = el[el["game_id"].isin(ts["trained_games"])]
    latest = inc.sort_values(["scheduled_tip_utc", "game_id"]).iloc[-1]
    frozen = json.loads((PC.MODEL_DIR / "jm1_final_production_manifest_2026_27.json").read_text())
    feature_state = _sha({g: ts["feature_meta"][g]["cand_sha256"] + ts["feature_meta"][g]["team_sha256"]
                          for g in ts["trained_games"]})
    man = {"MODEL_VERSION": vid, "MODEL_KIND": WC.DAILY_KIND, "CREATED_AT": pd.Timestamp.now(tz="UTC").isoformat(),
           "ARCHITECTURE": WC.ARCHITECTURE, "PRODUCTION_SEASON": WC.PRODUCTION_SEASON,
           "TRAINING_CUTOFF": cut.isoformat(), "DATA_CUTOFF": ts["state"]["source_cutoff"].isoformat(),
           "TRAINING_THROUGH_SEASON": f["T"],
           "TRAINING_THROUGH_DATE": str(_utc(latest["scheduled_tip_utc"]).tz_convert(WC.TZ).date()),
           "LATEST_INCLUDED_GAME": {"game_id": latest["game_id"], "scheduled_tip_utc": str(latest["scheduled_tip_utc"])},
           "N_INCLUDED_2026_27_GAMES": len(ts["trained_games"]),
           "N_INCLUDED_2026_27_PLAYER_GAMES": int(len(fr["frame"])),
           "FEATURE_STATE_HASH": feature_state, "MARGINAL_ARTIFACT_HASH": sha["marginal"],
           "STRUCTURAL_ARTIFACT_HASH": sha["jm1_final"], "DISPERSION_HASH": WF.content_sha(art["conditional_dispersion"]),
           "DEPENDENCE_HASH": sha["residual_j2"], "CODE_COMMIT": ident["code"]["commit"], "CODE_DIRTY": ident["code"]["dirty"],
           "CONFIG_HASH": ident["config_hash"], "TRAINING_DATA_HASH": _sha(ts["identity"]),
           "INPUT_IDENTITY_SHA": ident["sha"], "PREVIOUS_MODEL_VERSION": prev_active,
           "STATE_VERSION": ts["state"]["state_version"],
           "artifacts": {k: {"file": SO.FILES[k], "sha256": sha[k]} for k in SO.FILES},
           "supported_stats": frozen["supported_stats"], "supported_combos": frozen["supported_combos"],
           "random_seed_policy": frozen["random_seed_policy"], "known_monitoring_warnings": frozen["known_monitoring_warnings"],
           "recency": {"rule": marg["weight_rule"], "weight_by_season": f["weight_by_season"],
                       "structural_components": "unweighted pooled fits (approved procedure)"},
           "fit": {"T": f["T"], "rows": f["rows"], "resid_seasons": f["params"]["resid_seasons"],
                   "glm_seasons": f["params"]["struct"]["glm"]["seasons"], "env_seasons": f["params"]["struct"]["env"]["seasons"]},
           "deterministic_refit": {"content_sha256": shas, "BIT_IDENTICAL": shas[0] == shas[1]}}
    SO.write_manifest(cand, man)
    ts["eligibility"].to_parquet(cand / "eligibility.parquet", index=False)
    ts["labels"].to_parquet(cand / "labels.parquet", index=False)
    SO.write_json(cand / "training_manifest.json", {
        "summary": EL.summary(ts["eligibility"]), "coverage": fr["coverage"], "frames_sha256": fr["hashes"],
        "feature_builds": {g: {k: ts["feature_meta"][g][k] for k in ("prediction_cutoff_utc", "state_version",
                                                                     "state_created_at", "build", "pit_audit")}
                           for g in ts["trained_games"]}})
    return {"dir": cand, "version": vid, "fit_content_sha": shas, "timings": timings, "manifest": man}


def corrections(root: Path, prev_version: str | None, state: dict, vid: str, state_root: Path, cut) -> list[dict]:
    """Official box changes (player or team rows) of games ALREADY in the previous version's training state. The
    corrected box enters THIS version only (as labels and as later games' history); old versions are never touched."""
    if prev_version is None:
        return []
    pm = SO.manifest(root, prev_version)
    pdir = SO.version_dir(root, SO.day_of(prev_version))
    if pm.get("MODEL_KIND") != WC.DAILY_KIND or not (pdir / "eligibility.parquet").exists():
        return []
    vs = {v["state_version"]: v for v in EL.state_versions(state_root)}
    old_state = vs.get(pm["STATE_VERSION"])
    if old_state is None or old_state["state_version"] == state["state_version"]:
        return []
    pe = pd.read_parquet(pdir / "eligibility.parquet")
    later = [v for v in vs.values() if old_state["created_at"] < v["created_at"] <= _utc(cut)]
    later.sort(key=lambda v: (v["created_at"], v["state_version"]))
    out = []
    for g in sorted(pe.loc[pe["decision"] == "INCLUDED", "game_id"]):
        a, b = CR.read_box(old_state["increment_db"], g), CR.read_box(state["increment_db"], g)
        diff = CR.box_diff(a, b)                                 # material changes only (normalized values)
        if not diff:
            continue
        final = CR.box_identity(b)
        recv = next((v for v in later if CR.box_identity(CR.read_box(v["increment_db"], g)) == final), None)
        out.append({"game_id": g, "CORRECTION_RECEIVED_AT": recv["created_at"].isoformat() if recv else None,
                    "correction_state_version": recv["state_version"] if recv else None, "changes": diff,
                    "ORIGINAL_VALUE": [d["original_value"] for d in diff], "CORRECTED_VALUE": [d["corrected_value"] for d in diff],
                    "FIRST_MODEL_VERSION_USING_CORRECTION": vid, "PREVIOUS_MODEL_VERSION": prev_version})
    return out


# ============================================================================ morning
def morning(day: date, root: Path = WC.STORE_ROOT, state_root: Path = PC.STATE_ROOT,
            history_db: Path | None = None, now=None, log=print) -> dict:
    """history_db None: the selected history source (history_source.history_db: the frozen canonical under CONTROL)."""
    with HS.active():
        return _morning(day, root, state_root, HS.history_db() if history_db is None else history_db, now, log)


def _morning(day: date, root: Path, state_root: Path, history_db: Path, now, log) -> dict:
    now = _utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    cut = WC.refit_cutoff(day)
    t0 = time.time()
    if now < cut:
        return {"DAILY_REFIT_STATUS": "NOT_RUN", "reason": "CUTOFF_NOT_REACHED", "training_cutoff": cut.isoformat()}
    SO.ensure(root)
    SO.register_opening(root)
    rec = {"day": day.isoformat(), "training_cutoff": cut.isoformat(), "started_at": now.isoformat(),
           "threads_ok": WC.threads_ok(), "platform": f"{sys.platform}-{platform.machine()}", "timings": {}}
    with _lock(root):
        prev_active = SO.active(root)["ACTIVE_MODEL_VERSION"]
        cfg = P.load_config()
        # 1. score prior predictions  2. update monitoring (both before any refit)
        t = time.time()
        rec["settlement"] = MON.settle(root, state_root, now)
        rec["monitoring"] = MON.write_report(root, day)
        rec["timings"]["settle_and_monitor_s"] = time.time() - t
        # 3. ingest -> eligibility -> training rows
        t = time.time()
        ts = training_state(day, root, state_root, history_db, cfg, log)
        rec["timings"]["ingestion_s"] = time.time() - t
        rec["timings"].update(ts["timings"])
        prev_inc = []
        pm = SO.manifest(root, prev_active)
        pdir = SO.version_dir(root, SO.day_of(prev_active))
        if (pdir / "eligibility.parquet").exists():
            pe = pd.read_parquet(pdir / "eligibility.parquet")
            prev_inc = pe.loc[pe["decision"] == "INCLUDED", "game_id"].tolist()
        rec["eligibility"] = EL.summary(ts["eligibility"], prev_inc)
        ident = {"code": SO.code_commit(), "config_hash": config_hash(cfg), "training": ts["identity"]}
        ident["sha"] = _sha({"training": ts["identity"], "code": ident["code"]["commit"], "config": ident["config_hash"]})
        vdir = SO.version_dir(root, day)
        chain = (json.loads((vdir / SO.MANIFEST).read_text())["PREVIOUS_MODEL_VERSION"]   # a rerun chains as the first run did
                 if vdir.exists() else prev_active)
        rid = run_identity(cut, ts["state"]["state_version"] if ts["state"] else None, ts["eligibility"], ts["identity"],
                           ident["code"], ident["config_hash"],
                           {"version": chain, "manifest_sha256": SO.active(root)["PRODUCTION_MANIFEST_HASH"]
                            if chain == prev_active else SO.verify_version(root, chain)["PRODUCTION_MANIFEST_HASH"]})
        run_id = run_id_of(day, rid)
        rec.update(run_id=run_id, run_identity=rid, CANDIDATE_MODEL_VERSION=None,
                   ROLLBACK_MODEL_VERSION=(SO.rollback_pointer(root) or {}).get("ROLLBACK_MODEL_VERSION"))
        if ts["identity"]["current_season"] is None:
            rec.update(DAILY_REFIT_STATUS="NO_NEW_DATA", DAILY_MODEL_PROMOTION="NOT_REQUIRED",
                       note="no 2026-27 game passes training eligibility at this cutoff: ACTIVE carries forward")
        elif pm.get("INPUT_IDENTITY_SHA") == ident["sha"]:
            rec.update(DAILY_REFIT_STATUS="NO_NEW_DATA", DAILY_MODEL_PROMOTION="NOT_REQUIRED",
                       note=f"training inputs identical to ACTIVE {prev_active}")
        elif vdir.exists():
            vm = json.loads((vdir / SO.MANIFEST).read_text())
            if vm.get("INPUT_IDENTITY_SHA") == ident["sha"]:
                gates_ok = json.loads((vdir / "gates.json").read_text())["ALL_PASS"] if (vdir / "gates.json").exists() else False
                if gates_ok and prev_active != vm["MODEL_VERSION"]:
                    SO.promote(root, vm["MODEL_VERSION"], "resumed promotion of a gated version", run_id)
                rec.update(DAILY_REFIT_STATUS="PASS" if gates_ok else "FAIL", DAILY_MODEL_PROMOTION="IDEMPOTENT_NO_CHANGE",
                           CANDIDATE_MODEL_VERSION=vm["MODEL_VERSION"])
            else:
                rec.update(DAILY_REFIT_STATUS="FAIL", DAILY_MODEL_PROMOTION="REFUSED",
                           reason="VERSION_EXISTS_WITH_DIFFERENT_INPUTS (versions are immutable)")
        else:
            # 4. refit  5. gates  6. atomic promotion
            t = time.time()
            c = build_candidate(root, day, ts, cfg, prev_active, ident, log)
            rec["timings"]["training_s"] = time.time() - t
            rec["timings"].update(c["timings"])
            rec["CANDIDATE_MODEL_VERSION"] = c["version"]
            t = time.time()
            opening_dir = SO.version_dir(root, WC.OPENING_DATE)
            opening = {"art": json.loads((opening_dir / SO.FILES["jm1_final"]).read_text()),
                       "marg": json.loads((opening_dir / SO.FILES["marginal"]).read_text())}
            gres = GT.evaluate(c["dir"], c["version"], {"training": ts, "training_cutoff": cut, "fit_content_sha": c["fit_content_sha"],
                                                        "opening": opening, "previous_active": prev_active, "root": root}, log)
            rec["timings"]["artifact_validation_s"] = time.time() - t
            SO.write_json(Path(c["dir"]) / "gates.json", gres)
            rec["gates"] = {k: gres[k] for k in ("ALL_PASS", "POINT_IN_TIME_LEGALITY", "FUTURE_DATA_LEAKAGE_DETECTED",
                                                 "TARGET_GAME_FEATURE_LEAKAGE_DETECTED", "TARGET_GAME_OUTCOME_LEAKAGE_DETECTED",
                                                 "failed")}
            if gres["ALL_PASS"]:
                final = SO.finalize(root, c["dir"], day)
                rec["disk_bytes_version"] = _du(final)
                corr = corrections(root, prev_active, ts["state"], c["version"], state_root, cut)
                for x in corr:
                    SO.append_log(SO.paths(root)["manifests"] / "corrections.jsonl", x)
                rec["corrections"] = len(corr)
                SO.promote(root, c["version"], "DAILY_WALK_FORWARD gates PASS", run_id)
                rec.update(DAILY_REFIT_STATUS="PASS", DAILY_MODEL_PROMOTION="PROMOTED")
            else:
                dest = SO.paths(root)["refused"] / f"{day.isoformat()}-{run_id}-{uuid.uuid4().hex[:6]}"
                shutil.move(str(c["dir"]), dest)
                rec.update(DAILY_REFIT_STATUS="FAIL", DAILY_MODEL_PROMOTION="REFUSED", refused_candidate=str(dest))
        a = SO.active(root)
        rec["season_guard"] = WF.guard_state()                   # structural.MAX_SEASON restored to the frozen 2026
        rec.update(ACTIVE_MODEL_VERSION=a["ACTIVE_MODEL_VERSION"],
                   ROLLBACK_MODEL_VERSION=(SO.rollback_pointer(root) or {}).get("ROLLBACK_MODEL_VERSION"),
                   total_s=time.time() - t0, peak_rss_gb=_rss_gb(),
                   disk_bytes_training_cache=_du(SO.paths(root)["training"]))
        runs = SO.read_log(SO.paths(root)["manifests"] / "runs.jsonl")
        if not any(r["run_id"] == run_id for r in runs):          # a run id = its inputs: the first record stands
            SO.append_log(SO.paths(root)["manifests"] / "runs.jsonl", rec)
    return rec


# ============================================================================ scoring with the ACTIVE version
def active_for(root: Path, as_of) -> "SO.LD.FrozenModel":
    """The ACTIVE daily version through the explicit scorer model source (fail closed; never trained after as_of)."""
    from cbb_platform.prod import model_source as MS
    return MS.resolve("ACTIVE_DAILY", as_of, root)


def predict(as_of, root: Path = WC.STORE_ROOT, quotes_paths=(), availability=None, **kw) -> dict:
    model = active_for(root, as_of)
    pp = MON._p(root)
    r = RN.daily(as_of, quotes_paths, availability, ledger_path=pp["ledger"], run_root=SO.paths(root)["predictions"] / "runs",
                 side_monitor_path=pp["sides"], model=model, **kw)
    r["provenance"] = MON.record_run(root, r) if r.get("predictions") else {"rows": 0}
    return r


def status(root: Path = WC.STORE_ROOT) -> dict:
    a = SO.active(root)
    return {"ACTIVE": a, "ROLLBACK": SO.rollback_pointer(root), "versions": SO.versions(root),
            "last_runs": SO.read_log(SO.paths(root)["manifests"] / "runs.jsonl")[-3:]}


def main(argv=None) -> None:
    from cbb_platform import duckdb_limits as DLM
    DLM.install()                                    # resource limits only (memory cap + spill); results unchanged
    ap = argparse.ArgumentParser(prog="cbb_platform.walkforward.daily")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("morning")
    m.add_argument("--date", default=None)
    p = sub.add_parser("predict")
    p.add_argument("--as-of", required=True)
    p.add_argument("--quotes", nargs="*", default=[])
    sn = sub.add_parser("snapshot", help="near-tip (T-10m) quote snapshot of due priced games; quotes only, no rescoring")
    sn.add_argument("--now", default=None)
    sub.add_parser("status")
    sub.add_parser("register-opening")
    rb = sub.add_parser("rollback")
    rb.add_argument("--reason", required=True)
    rb.add_argument("--approved-by", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "morning":
        day = date.fromisoformat(a.date) if a.date else datetime.now(WC.TZ).date()
        out = morning(day)
    elif a.cmd == "predict":
        out = predict(a.as_of, quotes_paths=a.quotes)
    elif a.cmd == "snapshot":
        from cbb_platform.prod import odds_acquire as OA, odds_feed as OF
        from cbb_platform.walkforward import near_tip as NT
        now = pd.Timestamp(a.now) if a.now else pd.Timestamp.now(tz="UTC")
        sess = OA.Session("near_tip_snapshot", event_commence=OF.event_commence_resolver(as_of=now),
                          clock=lambda: now.to_pydatetime())
        out = NT.snapshot(WC.STORE_ROOT, now, sess)
    elif a.cmd == "status":
        out = status()
    elif a.cmd == "register-opening":
        out = SO.register_opening()
    else:
        out = SO.rollback(WC.STORE_ROOT, a.reason, a.approved_by)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
