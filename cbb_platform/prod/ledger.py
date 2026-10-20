"""Append-only shadow ledger (JSON Lines, hash-chained).

Every record: record_type, body, prev_row_hash, row_hash = sha256(canonical JSON of everything but row_hash).
    RUN         one per run (identity, state, readiness)
    PREDICTION  what was known at prediction time; immutable (a duplicate prediction_id is refused)
    SETTLEMENT  appended later for an existing prediction_id: actual, settlement, Brier, log loss, CRPS, CLV;
                may NOT carry any prediction-time field (the original row is never mutated)
The file is only ever opened in append mode; verify() recomputes the whole chain and fails on any edit, deletion,
reordering or insertion.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from cbb_platform.prod import config as PC

GENESIS = "0" * 64
PREDICTION_FIELDS = ("prediction_id", "run_id", "prediction_as_of", "feature_cutoff", "model_version", "artifact_sha256",
                     "production_manifest", "state_version", "game_id", "game", "player_id", "player", "team_id", "market",
                     "line", "side", "projection", "probability", "push_probability", "p_nonpush", "p_play",
                     "fair_decimal", "fair_american", "best_decimal_odds", "best_american_odds", "sportsbook", "edge",
                     "quote_available_at", "collector_received_at", "readiness_state", "eligibility_state",
                     "suppression_reason", "distribution_method", "pmf_q75", "mcse", "bank_sha256", "data_label")
SETTLEMENT_FIELDS = ("prediction_id", "settled_at", "actual", "settlement", "brier", "log_loss", "crps", "clv",
                     "closing_decimal_odds", "result_source")


class LedgerViolation(RuntimeError):
    pass


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)


def prediction_id(run_id: str, game_id: str, player_id: str, market: str, line, side: str) -> str:
    return hashlib.sha256(f"{run_id}|{game_id}|{player_id}|{market}|{line}|{side}".encode()).hexdigest()


def _records(path: Path):
    if not Path(path).exists():
        return
    with open(path) as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                yield n, json.loads(line)


def verify(path: Path = PC.LEDGER_PATH) -> dict:
    prev, n, preds, setts = GENESIS, 0, set(), 0
    for n, rec in _records(path):
        h = rec.get("row_hash")
        body = {k: v for k, v in rec.items() if k != "row_hash"}
        if rec.get("prev_row_hash") != prev:
            raise LedgerViolation(f"line {n}: chain broken (prev_row_hash)")
        if hashlib.sha256(_canon(body).encode()).hexdigest() != h:
            raise LedgerViolation(f"line {n}: row_hash mismatch (record edited)")
        t = rec.get("record_type")
        if t == "PREDICTION":
            pid = rec["body"]["prediction_id"]
            if pid in preds:
                raise LedgerViolation(f"line {n}: duplicate prediction {pid}")
            preds.add(pid)
        elif t == "SETTLEMENT":
            if rec["body"]["prediction_id"] not in preds:
                raise LedgerViolation(f"line {n}: settlement of an unknown prediction")
            setts += 1
        elif t != "RUN":
            raise LedgerViolation(f"line {n}: unknown record type {t}")
        prev = h
    return {"rows": n, "predictions": len(preds), "settlements": setts, "head": prev, "CHAIN_VALID": True}


def append(records: list[dict], path: Path = PC.LEDGER_PATH) -> dict:
    """Append [{record_type, body}] atomically per line; validates types and immutability first."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = verify(path)
    prev, known = state["head"], set()
    for _, rec in _records(path):
        if rec["record_type"] == "PREDICTION":
            known.add(rec["body"]["prediction_id"])
    lines = []
    for r in records:
        t, body = r["record_type"], r["body"]
        if t == "PREDICTION":
            miss = [k for k in PREDICTION_FIELDS if k not in body]
            if miss:
                raise LedgerViolation(f"prediction missing fields {miss}")
            if body["prediction_id"] in known:
                raise LedgerViolation(f"prediction {body['prediction_id']} already recorded (immutable)")
            known.add(body["prediction_id"])
        elif t == "SETTLEMENT":
            extra = sorted(set(body) - set(SETTLEMENT_FIELDS))
            if extra:
                raise LedgerViolation(f"settlement may not carry prediction-time fields: {extra}")
            if body.get("prediction_id") not in known:
                raise LedgerViolation("settlement of an unknown prediction")
        elif t != "RUN":
            raise LedgerViolation(f"unknown record type {t}")
        rec = {"record_type": t, "body": body, "prev_row_hash": prev}
        rec["row_hash"] = hashlib.sha256(_canon(rec).encode()).hexdigest()
        prev = rec["row_hash"]
        lines.append(_canon(rec) + "\n")
    with open(path, "a") as f:                                    # append only
        f.writelines(lines)
        f.flush()
        os.fsync(f.fileno())
    return {"appended": len(lines), "head": prev}


def predictions(path: Path = PC.LEDGER_PATH) -> list[dict]:
    return [r["body"] for _, r in _records(path) if r["record_type"] == "PREDICTION"]


def settlements(path: Path = PC.LEDGER_PATH) -> list[dict]:
    return [r["body"] for _, r in _records(path) if r["record_type"] == "SETTLEMENT"]
