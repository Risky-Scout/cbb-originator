"""One-off authorised repair (2026-11-21 directive, Phase 4) of the player Performance closing / CLV fields lost to the
Observations.contracts collision (fixed in 42d11a7, deployed in 111e621).

Run ON THE DROPLET with the deployed release, as cbb-originator, holding the CBB writer lock:
    python -B rebuild_player_close.py <ops_root> <out_dir> [--apply]

Every row is recomputed from persisted evidence (frozen evaluations, quote observations, actuals) by the SAME production
function (market_exports.graded_rows, no reuse). The repair is accepted only if, for every row:
  * every field outside CLV_FIELDS is byte-identical to the stored row (forecast, PMF-derived probability, line, side,
    book, odds, result, units ... never change), and
  * the row set (eval_keys) is identical.
Only CLV_FIELDS may change. Before writing: a receipt with the sha256 of the stored dataset, the affected eval_keys and a
per-row hash; the stored parquet is kept as a backup next to the dataset. Dry run unless --apply."""
import hashlib, json, os, shutil, sys
from pathlib import Path

import pandas as pd

from cbb_platform.prod import exports as EX, market_exports as MX, performance as PF

CLV_FIELDS = ("close_ts", "close_book", "close_line", "close_fair_probability", "probability_clv", "log_odds_clv",
              "raw_line_movement", "directional_line_movement", "clv_status")


def rowhash(r: dict) -> str:
    return hashlib.sha256(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()


def main():
    ops, out = Path(sys.argv[1]), Path(sys.argv[2]); apply = "--apply" in sys.argv
    out.mkdir(parents=True, exist_ok=True)
    path = ops / PF.DATASET
    old = PF.read_dataset(path)
    through = max(r["slate_date"] for r in old)
    now = pd.Timestamp.now(tz="UTC")
    new, info = PF.build(ops, through, now, reuse=None)
    o, n = {r["eval_key"]: r for r in old}, {r["eval_key"]: r for r in new}
    if set(o) != set(n):
        raise SystemExit(f"ABORT: row set differs (old {len(o)}, new {len(n)}, only-old {len(set(o)-set(n))}, "
                         f"only-new {len(set(n)-set(o))})")
    bad, changed = [], []
    for k in sorted(o):
        a, b = o[k], n[k]
        diff = [c for c in MX.ROW_COLS if json.dumps(a.get(c), default=str) != json.dumps(b.get(c), default=str)]
        non_clv = [c for c in diff if c not in CLV_FIELDS and c != "close_window_closed"]
        if non_clv:
            bad.append({"eval_key": k, "fields": non_clv, "old": {c: a.get(c) for c in non_clv},
                        "new": {c: b.get(c) for c in non_clv}})
        if any(c in CLV_FIELDS for c in diff):
            changed.append(k)
    if bad:
        (out / "rebuild_ABORT_non_clv_differences.json").write_text(json.dumps(bad[:200], indent=1, default=str))
        raise SystemExit(f"ABORT: {len(bad)} row(s) differ outside the CLV fields (see rebuild_ABORT_*.json)")
    affected = [k for k in sorted(o) if o[k]["clv_status"] == "NO_CLOSE_QUOTE"]
    before = pd.Series([o[k]["clv_status"] for k in sorted(o)]).value_counts().to_dict()
    after = pd.Series([n[k]["clv_status"] for k in sorted(n)]).value_counts().to_dict()
    aff_after = pd.Series([n[k]["clv_status"] for k in affected]).value_counts().to_dict()
    unrec = [{"eval_key": k, "reason": n[k]["clv_status"], "slate_date": n[k]["slate_date"], "book": n[k]["book"],
              "line": n[k]["line"], "side": n[k]["side"]} for k in affected if n[k]["clv_status"] not in MX.CLV_COMPUTED
             and n[k]["clv_status"] != "LINE_MOVED"]
    other_changed = [k for k in changed if k not in set(affected)]
    rec = {"kind": "PLAYER_CLOSE_REBUILD", "at": now.isoformat(), "applied": apply, "dataset": str(path),
           "dataset_sha256_before": EX._file_sha(path), "rows": len(old), "through": through,
           "AFFECTED_ROWS": len(affected), "NO_CLOSE_BEFORE": before.get("NO_CLOSE_QUOTE", 0),
           "NO_CLOSE_AFTER": after.get("NO_CLOSE_QUOTE", 0),
           "affected_after": aff_after, "EXACT_LINE_AFTER": aff_after.get("EXACT_LINE", 0),
           "SAME_LINE_NEAREST_AFTER": aff_after.get("SAME_LINE_NEAREST_CLOSE", 0),
           "LINE_MOVED_AFTER": aff_after.get("LINE_MOVED", 0), "UNRECOVERABLE_AFTER": len(unrec),
           "unrecoverable": unrec, "clv_status_before": before, "clv_status_after": after,
           "rows_with_clv_change": len(changed),
           "previously_closed_rows_with_changed_close": len(other_changed),
           "previously_closed_changed_status": {f"{o[k]['clv_status']}->{n[k]['clv_status']}": 0 for k in other_changed},
           "non_clv_differences": 0,
           "affected_row_hashes_before": {k: rowhash(o[k]) for k in affected},
           "affected_set_sha256": hashlib.sha256("\n".join(affected).encode()).hexdigest()}
    for k in other_changed:
        rec["previously_closed_changed_status"][f"{o[k]['clv_status']}->{n[k]['clv_status']}"] += 1
    if apply:
        bak = path.with_name(path.name + f".pre-close-rebuild-{now.strftime('%Y%m%dT%H%M%SZ')}")
        shutil.copy2(path, bak)
        rows = sorted(new, key=lambda r: (r["slate_date"], r["tip_utc"] or "", r["game_id"], r["player_id"], r["stat"]))
        tmp = path.with_name(path.name + ".tmp")
        PF._frame(rows).to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, path)
        rec.update(backup=str(bak), dataset_sha256_after=EX._file_sha(path),
                   affected_row_hashes_after={k: rowhash(n[k]) for k in affected})
        chk = PF.read_dataset(path)
        if len(chk) != len(old):
            raise SystemExit("ABORT after write: row count changed (backup kept)")
    (out / ("rebuild_receipt.json" if apply else "rebuild_dryrun.json")).write_text(json.dumps(rec, indent=1, default=str))
    print(json.dumps({k: v for k, v in rec.items() if not k.startswith("affected_row_hashes") and k != "unrecoverable"},
                     indent=1, default=str))


if __name__ == "__main__":
    main()
