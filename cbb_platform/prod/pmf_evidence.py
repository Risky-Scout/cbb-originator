"""Durable PMF evidence: the complete marginal PMF each prediction actually used, persisted in the immutable evidence tree.

Before this module, prod.run.daily wrote the PMFs only to runs/<run_id>/market_pmfs.parquet. On a GitHub-hosted runner
that run directory is torn down with the runner, so the Distributions workbook had nothing to read. Now every official
prediction tick (ticks/<tick>/pmfs.parquet) and every forecast snapshot (forecasts/<ET date>/<snapshot>/pmfs.parquet)
persists them beside its other evidence. The PMFs are copied from the run that made the prediction, never recomputed
later and never taken from a newer model.

Long format: one row per (prediction, outcome). Logical keys (directive §13):
    prediction_receipt_id  ledger.prediction_id(run_id, game_id, player_id, stat, None, None): the existing ledger id
                           function at forecast level, i.e. no line and no side
    game_id / player_id / team_id   canonical ids (G-/P-/T-)
    stat                   production market (PTS, REB, ..., PRA)
    prediction_as_of       the run's as-of instant (UTC ISO)
    model_version          the model identity's model_version that produced the PMF
    outcome / probability  integer outcome and its probability (the full support, folded tail point included)
    mean                   the official projection mean of that PMF row
plus run_id, method, game_label and player_label (provenance and labels for the exports).

Integrity: a PMF with a negative / non-finite value, or with mass more than PMF_TOL from 1, is refused; a file is written
once (an existing file with different content is an ExportIntegrityError-style refusal, never overwritten).

Storage (footprint directive, 2026-11-14): the file on disk is the COMPACT canonical form (prod.pmf_compact: one row per
PMF with its exact float64 vector, zstd), not one row per outcome. read() returns the identical long frame (a
deterministic view); files written before the change (long schema) are read as before.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from cbb_platform.prod import ledger as LG, pmf_compact as PCX

PMF_TOL = 1e-9                                   # = prod.pricing.SUM_TOL = prod.simulate.PMF_TOL
FILE = "pmfs.parquet"
COLUMNS = ["prediction_receipt_id", "game_id", "player_id", "team_id", "stat", "prediction_as_of", "model_version",
           "outcome", "probability", "mean", "run_id", "method", "game_label", "player_label"]


class PMFEvidenceError(RuntimeError):
    pass


def receipt_id(run_id: str, game_id: str, player_id: str, stat: str) -> str:
    return LG.prediction_id(run_id, game_id, player_id, stat, None, None)


def _iso(t) -> str:
    t = pd.Timestamp(t)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).isoformat()


def from_run(run_dir: Path, run_id: str, prediction_as_of, model_version: str, game_ids=None) -> pd.DataFrame:
    """Long-format evidence of the priced PMFs a run wrote (runs/<run_id>/market_pmfs.parquet)."""
    src = Path(run_dir) / "market_pmfs.parquet"
    if not src.exists():
        return pd.DataFrame(columns=COLUMNS)
    mp = pd.read_parquet(src)
    if game_ids is not None:
        mp = mp[mp["game_id"].isin(set(game_ids))]
    aso = _iso(prediction_as_of)
    out = []
    for r in mp.sort_values(["game_id", "player_id", "market"]).itertuples(index=False):
        pmf = np.asarray(r.pmf, dtype=float)
        check(pmf, f"{r.game_id} {r.player_id} {r.market}")
        rid = receipt_id(run_id, r.game_id, r.player_id, r.market)
        gl = getattr(r, "game_label", None)
        pl = getattr(r, "player_label", None)
        out.append(pd.DataFrame({"prediction_receipt_id": rid, "game_id": r.game_id, "player_id": r.player_id,
                                 "team_id": r.team_id, "stat": r.market, "prediction_as_of": aso,
                                 "model_version": model_version, "outcome": np.arange(len(pmf), dtype=np.int64),
                                 "probability": pmf, "mean": float(r.projection), "run_id": run_id, "method": r.method,
                                 "game_label": gl if isinstance(gl, str) else r.game_id,
                                 "player_label": pl if isinstance(pl, str) else r.player_id}))
    return pd.concat(out, ignore_index=True)[COLUMNS] if out else pd.DataFrame(columns=COLUMNS)


RUN_FILE = "market_pmfs.parquet"
RUN_REF = "market_pmfs.ref.json"


def run_copy_verified(run_dir: Path, run_id: str, durable: Path) -> tuple[bool, str]:
    """True only when the durable evidence file holds EVERY PMF of runs/<run_id>/market_pmfs.parquet bit for bit (same
    receipt id, support 0..k, identical float64 bytes)."""
    src, durable = Path(run_dir) / RUN_FILE, Path(durable)
    if not src.exists():
        return False, "run PMF file absent"
    if not durable.exists():
        return False, "durable evidence absent"
    have = {r.prediction_receipt_id: (int(r.support_min), np.asarray(r.probabilities, dtype=np.float64))
            for r in read_compact(durable).itertuples(index=False)}
    mp = pd.read_parquet(src, columns=["game_id", "player_id", "market", "pmf"])
    if not len(mp):
        return False, "empty run PMF file"
    for r in mp.itertuples(index=False):
        got = have.get(receipt_id(run_id, r.game_id, r.player_id, r.market))
        want = np.asarray(r.pmf, dtype=np.float64)
        if got is None or got[0] != 0 or got[1].shape != want.shape or got[1].tobytes() != want.tobytes():
            return False, f"{r.game_id} {r.player_id} {r.market} not in {durable} bit for bit"
    return True, f"all {len(mp)} PMFs of {run_id} are in {durable} bit for bit"


def dereference_run(run_dir: Path, run_id: str, durable: Path) -> dict:
    """ONE physical PMF payload per forecast state: once run_copy_verified, runs/<run_id>/market_pmfs.parquet is replaced
    by market_pmfs.ref.json (canonical path, its sha256, PMF count, the replaced file's sha256). Otherwise the run file
    is kept and the reason returned (never a partial reference)."""
    run_dir, durable = Path(run_dir), Path(durable)
    src = run_dir / RUN_FILE
    if not src.exists():
        return {"status": "ABSENT"}
    ok, why = run_copy_verified(run_dir, run_id, durable)
    if not ok:
        return {"status": "KEPT", "reason": why}
    ref = {"run_id": run_id, "format": "REFERENCE", "canonical": str(durable), "canonical_sha256": sha256(durable),
           "pmfs": int(len(pd.read_parquet(src, columns=["game_id"]))), "replaced_bytes": src.stat().st_size,
           "replaced_sha256": sha256(src)}
    tmp = run_dir / (RUN_REF + ".tmp")
    tmp.write_text(json.dumps(ref, indent=1) + "\n")
    os.replace(tmp, run_dir / RUN_REF)
    src.unlink()
    return {"status": "REFERENCED", **ref}


def read_run_pmfs(run_dir: Path) -> pd.DataFrame:
    """The run's PMFs in the market_pmfs.parquet shape (game_id, team_id, player_id, market, method, projection, pmf,
    game_label, player_label), from the run file or, once dereferenced, from its canonical durable file (filtered to
    the run; the canonical sha256 is re-checked). Empty frame when the run priced nothing."""
    run_dir = Path(run_dir)
    if (run_dir / RUN_FILE).exists():
        return pd.read_parquet(run_dir / RUN_FILE)
    ref_p = run_dir / RUN_REF
    if not ref_p.exists():
        return pd.DataFrame(columns=["game_id", "team_id", "player_id", "market", "method", "projection", "pmf"])
    ref = json.loads(ref_p.read_text())
    if sha256(ref["canonical"]) != ref["canonical_sha256"]:
        raise PMFEvidenceError(f"{ref['canonical']}: canonical PMF file changed since {ref_p} was written")
    c = read_compact(Path(ref["canonical"]))
    c = c[c["run_id"].astype(str) == ref["run_id"]]
    return pd.DataFrame({"game_id": c["game_id"].to_numpy(), "team_id": c["team_id"].to_numpy(),
                         "player_id": c["player_id"].to_numpy(), "market": c["stat"].to_numpy(),
                         "method": c["method"].to_numpy(), "projection": c["mean"].to_numpy(),
                         "pmf": [np.asarray(v, dtype=np.float64) for v in c["probabilities"]],
                         "game_label": c["game_label"].to_numpy(), "player_label": c["player_label"].to_numpy()})


def check(pmf: np.ndarray, what: str = "") -> None:
    if not np.isfinite(pmf).all() or (pmf < 0).any():
        raise PMFEvidenceError(f"negative / non-finite PMF {what}")
    if abs(pmf.sum() - 1.0) > PMF_TOL:
        raise PMFEvidenceError(f"PMF mass {pmf.sum()!r} differs from 1 by more than {PMF_TOL} ({what})")


def _bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    PCX.compact(df).to_parquet(buf, index=False, compression="zstd")
    return buf.getvalue()


def write(path: Path, df: pd.DataFrame) -> dict:
    """Write-once. Re-writing identical content is a no-op; different content for an existing file is refused."""
    path = Path(path)
    df = df[COLUMNS].reset_index(drop=True)
    if path.exists():
        old = read(path)
        if not old.equals(df):
            raise PMFEvidenceError(f"{path}: durable PMF evidence is immutable (different content refused)")
        return {"path": str(path), "rows": int(len(df)), "written": False, "sha256": sha256(path)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(_bytes(df))
    tmp.replace(path)
    return {"path": str(path), "rows": int(len(df)), "written": True, "sha256": sha256(path),
            "pmfs": int(df["prediction_receipt_id"].nunique()) if len(df) else 0, "format": "COMPACT"}


def read_compact(path: Path) -> pd.DataFrame:
    """The compact canonical rows (a legacy long-schema file is compacted on read)."""
    df = pd.read_parquet(path)
    if "probabilities" in df.columns:
        miss = [c for c in PCX.PMF_COLUMNS if c not in df.columns]
        if miss:
            raise PMFEvidenceError(f"{path}: missing columns {miss}")
        PCX.verify(df)
        return df[PCX.PMF_COLUMNS].reset_index(drop=True)
    return PCX.compact(_long(df, path))


def _long(df: pd.DataFrame, path) -> pd.DataFrame:
    miss = [c for c in COLUMNS if c not in df.columns]
    if miss:
        raise PMFEvidenceError(f"{path}: missing columns {miss}")
    return df[COLUMNS].reset_index(drop=True)


def read(path: Path) -> pd.DataFrame:
    """The long frame (one row per outcome): the exact expansion of the compact file, or a legacy long file as stored."""
    df = pd.read_parquet(path)
    if "probabilities" in df.columns:
        return read_compact(path).pipe(PCX.expand)[COLUMNS].reset_index(drop=True)
    return _long(df, path)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def distributions(df: pd.DataFrame) -> list[dict]:
    """[{receipt, game_id, player_id, team_id, stat, mean, pmf (np.ndarray), game_label, player_label, ...}] with the
    mass / sign re-checked on read."""
    out = []
    for rid, g in df.sort_values(["prediction_receipt_id", "outcome"]).groupby("prediction_receipt_id", sort=False):
        o = g["outcome"].to_numpy()
        if not (o == np.arange(len(o))).all():
            raise PMFEvidenceError(f"{rid}: outcomes are not the contiguous support 0..{len(o) - 1}")
        pmf = g["probability"].to_numpy(dtype=float)
        f = g.iloc[0]
        check(pmf, f"{f.game_id} {f.player_id} {f.stat}")
        out.append({"prediction_receipt_id": rid, **{k: f[k] for k in ("game_id", "player_id", "team_id", "stat",
                    "prediction_as_of", "model_version", "mean", "run_id", "method", "game_label", "player_label")},
                    "pmf": pmf})
    return out
