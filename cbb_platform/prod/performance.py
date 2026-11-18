"""CBB_Performance: cumulative market evaluation of every frozen pre-cutoff CBB Originator market prediction.
REPORTING ONLY (no refit, scoring, simulation or provider call).

Scope: every MARKET_EVALUATION (prod.market_exports: one per game x player x stat with a real pre-cutoff market,
PASS_NO_EDGE rows included), from the FIRST slate date with persisted real pre-cutoff quote evidence (the first date
holding a frozen market evaluation) through the export date. Model-only dates and rows (no real market) never enter a
market metric. Grading, CLV and every metric are the SAME functions Grades uses (prod.market_exports), so a Grades
workbook and the Performance rows of that slate agree exactly.

Canonical dataset (ONE compact file, one row per frozen evaluation; references evidence, never copies PMFs / quotes):
    ops/performance/cbb_market_performance.parquet
FINAL rows (graded or void, close window passed) of an unchanged evaluation are reused; every other row is rebuilt from
evidence. The workbook exports/<D>/CBB_Performance_MM_DD_YYYY.xlsx (+ performance_receipt.json) derives from it.
`verify(..., recompute=True)` rebuilds every row from evidence (no reuse) and requires exact agreement.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from cbb_platform.prod import exports as EX, market_exports as MX

PERFORMANCE_VERSION = "cbb-prod-performance-2.0.0"
DATASET = Path("performance") / "cbb_market_performance.parquet"        # under ops_root
RECEIPT = "performance_receipt.json"
SHEETS = ("Overall", "By Stat", "By Player Role", "Stat x Role", "By Side", "By Selection Tier", "Daily", "Calibration",
          "Selections")
METRICS = ("Predictions", "Graded", "Wins", "Losses", "Pushes", "VOID_DNP", "Win %", "Mean Model Probability",
           "Actual Hit Rate", "Calibration Gap", "Brier Score", "Log Loss", "Mean Edge", "Median Edge", "Mean EV",
           "Units Won", "ROI", "Mean Probability CLV", "Median Probability CLV", "Mean Log-Odds CLV", "Positive CLV %",
           "Exact-Line CLV N", "Line-Moved N", "No-Close N")
BIN_COLS = ("Model Probability Bin", "N", "Mean Predicted Probability", "Actual Hit Rate", "Brier Score")
SEL_COLS = MX.GRADED_COLS


class PerformanceIntegrityError(EX.ExportIntegrityError):
    pass


def _metrics(rows: list[dict]) -> dict:
    m = MX.metrics(rows)
    return {**m, "Predictions": m["N"]}


def evaluation_dates(ops_root: Path, through: str) -> list[str]:
    d = Path(ops_root) / MX.EVAL_DIR
    if not d.exists():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if (p / MX.EVAL_FILE).exists():
            try:
                day = str(pd.Timestamp(p.name).date())
            except ValueError:
                continue
            if EX.PC.PRODUCTION_SEASON_START <= day <= through:
                out.append(day)
    return out


def read_dataset(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    df = pd.read_parquet(path)

    def v(x):
        return None if x is None or (not isinstance(x, str) and pd.isna(x)) else MX._clean(x)
    return [{c: v(r.get(c)) for c in MX.ROW_COLS} for r in df.to_dict(orient="records")]


def _content_sha(rows) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(MX.ROW_COLS)).astype(object)


def build(ops_root: Path, through: str, now=None, reuse: dict | None = None) -> tuple[list[dict], dict]:
    rows, info = [], {"dates": [], "reused": 0, "rebuilt": 0}
    for day in evaluation_dates(ops_root, through):
        r, i = MX.graded_rows(ops_root, day, now, reuse)
        if not r:
            continue
        rows += r
        info["dates"].append(day)
        info["reused"] += i.get("reused", 0)
        info["rebuilt"] += len(r) - i.get("reused", 0)
    keys = [r["eval_key"] for r in rows]
    if len(set(keys)) != len(keys):
        raise PerformanceIntegrityError("duplicate canonical performance keys")
    return sorted(rows, key=lambda r: (r["slate_date"], r["tip_utc"] or "", r["game_id"], r["player_id"], r["stat"])), info


def update(ops_root: Path, through: str, now=None) -> dict:
    ops_root = Path(ops_root)
    now = EX._utc(now) or pd.Timestamp.now(tz="UTC")
    path = ops_root / DATASET
    old = read_dataset(path)
    have = {r["eval_key"]: r for r in old}
    if len(have) != len(old):
        raise PerformanceIntegrityError(f"duplicate eval_key in {path}")
    rows, info = build(ops_root, through, now, have)
    keys = {r["eval_key"] for r in rows}
    vanished = [k for k, r in have.items() if r["slate_date"] <= through and k not in keys]
    if vanished:
        raise PerformanceIntegrityError(f"{len(vanished)} canonical performance row(s) lost their frozen evaluation")
    rows += [r for r in old if r["slate_date"] > through]
    rows = sorted(rows, key=lambda r: (r["slate_date"], r["tip_utc"] or "", r["game_id"], r["player_id"], r["stat"]))
    rewritten = _content_sha(rows) != _content_sha(old) or not path.exists()
    if rewritten:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        _frame(rows).to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, path)
    return {"rows": len(rows), "path": str(path), "rewritten": rewritten, "reused": info["reused"],
            "rebuilt": info["rebuilt"], "dataset_sha256": EX._file_sha(path), "dates": info["dates"]}


def calibration_bins(rows: list[dict]) -> list[list]:
    out = []
    dec = [r for r in rows if r["result"] in ("WIN", "LOSS")]
    for name, _, _ in EX.BANDS:
        b = [r for r in dec if EX.model_band(r["model_probability"]) == name]
        p = [float(r["model_probability"]) for r in b]
        y = [1.0 if r["result"] == "WIN" else 0.0 for r in b]
        out.append([name, len(b), MX._m(p), MX._m(y),
                    None if not b else sum((a - c) ** 2 for a, c in zip(p, y)) / len(b)])
    return out


def tables(rows: list[dict]) -> dict:
    m = _metrics(rows)
    dates = sorted({r["slate_date"] for r in rows})
    scope = [["Start Date (first persisted real pre-cutoff quote evidence)", dates[0] if dates else None],
             ["Through Date", dates[-1] if dates else None],
             ["Rows", "every frozen market evaluation (one per game x player x stat with a real pre-cutoff market; "
                      "PASS_NO_EDGE included); model-only rows excluded"],
             ["Grading", "exact persisted line; VOID_DNP / VOID_NO_GAME / PENDING excluded from Brier, Log Loss, "
                         "win rate and ROI; 1 unit per graded row at the recorded best price; push refunds"],
             ["Close", f"last observation <= min(commence, cutoff), at most {MX.CLOSE_MAX_STALE_MINUTES} min stale"]]
    status = [[s, sum(1 for r in rows if r["result"] in MX.GRADED and r["clv_status"] == s)] for s in MX.CLV_STATUSES]
    fm = MX.METRIC_FORMATS
    daily = []
    by_day: dict = {}
    for r in rows:
        by_day.setdefault(r["slate_date"], []).append(r)
    for d in sorted(by_day):
        mm = _metrics(by_day[d])
        daily.append([d] + [mm[c] for c in METRICS])

    def grp(keys):
        g: dict = {}
        for r in rows:
            g.setdefault(tuple(r[k] for k in keys), []).append(r)
        body = [list(k) + [_metrics(v)[c] for c in METRICS] for k, v in sorted(g.items(), key=lambda x: tuple(map(str, x[0])))]
        return [{"title": None, "columns": tuple(MX.GROUP_LABELS[k] for k in keys) + METRICS, "rows": body, "formats": fm}]
    return {
        "Overall": [{"title": "Overall", "columns": ("Scope",) + METRICS, "rows": [["ALL"] + [m[c] for c in METRICS]],
                     "formats": fm},
                    {"title": "Evaluation Scope", "columns": ("Item", "Value"), "rows": scope},
                    {"title": "CLV Status (graded rows)", "columns": ("CLV Status", "Rows"), "rows": status}],
        "By Stat": grp(("stat",)), "By Player Role": grp(("player_role",)), "Stat x Role": grp(("stat", "player_role")),
        "By Side": grp(("side",)), "By Selection Tier": grp(("tier",)),
        "Daily": [{"title": None, "columns": ("Date",) + METRICS, "rows": daily, "formats": fm}],
        "Calibration": [{"title": None, "columns": BIN_COLS, "rows": calibration_bins(rows), "formats": fm}],
        "Selections": [{"title": None, "columns": SEL_COLS, "rows": MX.graded_table_rows(rows), "formats": fm}]}


def workbook(tbl: dict, path: Path) -> Path:
    return EX.write_xlsx(path, [{"name": s, "blocks": tbl[s], "table_filters": s == "Overall"} for s in SHEETS])


def status_of(rows: list[dict]) -> str:
    if not rows:
        return "NO_MARKET_EVIDENCE_YET"
    graded = sum(1 for r in rows if r["result"] in MX.GRADED)
    return "NOTHING_GRADED_YET" if not graded else ("GRADED" if all(r["result"] != "PENDING" for r in rows)
                                                    else "PARTIALLY_GRADED")


def performance_name(date: str) -> str:
    return EX.file_names(date)["performance"]


def verify(out_dir: Path, date: str, ops_root: Path, recompute: bool = False, now=None) -> dict:
    """Health evidence: workbook readable with the nine tabs, dataset readable, receipt valid (sha256s match). With
    recompute: every row rebuilt from evidence without reuse must equal the dataset exactly (RECOMPUTE_AGREES)."""
    wb = Path(out_dir) / performance_name(date)
    ds = Path(ops_root) / DATASET
    chk = {"WORKBOOK_READABLE": False, "DATASET_READABLE": False, "RECEIPT_VALID": False}
    try:
        chk["WORKBOOK_READABLE"] = tuple(EX.read_xlsx(wb)) == SHEETS
    except Exception:                                                # noqa: BLE001 -- reported, never raised
        pass
    try:
        read_dataset(ds)
        chk["DATASET_READABLE"] = ds.exists()
    except Exception:                                                # noqa: BLE001
        pass
    try:
        rec = json.loads((Path(out_dir) / RECEIPT).read_text())
        chk["RECEIPT_VALID"] = rec.get("through_date") == date and wb.exists() and ds.exists() and \
            rec.get("workbook_sha256") == EX._file_sha(wb) and rec.get("canonical_dataset_sha256") == EX._file_sha(ds)
    except Exception:                                                # noqa: BLE001
        pass
    if recompute:
        fresh, _ = build(ops_root, date, now, None)
        stored = [r for r in read_dataset(ds) if r["slate_date"] <= date]
        chk["RECOMPUTE_AGREES"] = _content_sha(fresh) == _content_sha(stored)
    chk["PERFORMANCE_EVIDENCE"] = "PASS" if all(chk.values()) else "FAIL"
    return chk


def export(date: str, ops_root: Path, out_dir: Path, now=None) -> dict:
    date = str(pd.Timestamp(date).date())
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        ds = update(ops_root, date, now)
        rows = [r for r in read_dataset(Path(ds["path"])) if r["slate_date"] <= date]
    except (EX.ExportIntegrityError, MX.MarketExportIntegrityError) as e:
        return {"status": "ABORTED_INTEGRITY", "missing": str(e)[:400]}
    except (OSError, ValueError, KeyError) as e:
        return {"status": "PERFORMANCE_MISSING_EVIDENCE", "missing": f"{type(e).__name__}: {str(e)[:400]}"}
    tbl = tables(rows)
    path = out_dir / performance_name(date)
    rec_p = out_dir / RECEIPT
    writer = hashlib.sha256(Path(__file__).read_bytes() + Path(MX.__file__).read_bytes()
                            + Path(EX.__file__).read_bytes()).hexdigest()
    content = _content_sha([tbl[s] for s in SHEETS])
    try:
        old = json.loads(rec_p.read_text())
    except (OSError, ValueError):
        old = None
    same = bool(old) and old.get("content_sha256") == content and old.get("writer_sha256") == writer and path.exists() \
        and EX._file_sha(path) == old.get("workbook_sha256")
    if not same:
        workbook(tbl, path)
    m = _metrics(rows)
    dates = sorted({r["slate_date"] for r in rows})
    rec = {"version": PERFORMANCE_VERSION, "file": path.name, "through_date": date, "status": status_of(rows),
           "start_date": dates[0] if dates else None, "evaluations": len(rows), "graded": m["Graded"],
           "void_dnp": m["VOID_DNP"], "pending": m["Pending"], "brier_score": m["Brier Score"], "log_loss": m["Log Loss"],
           "roi": m["ROI"], "units_won": m["Units Won"], "exact_line_clv_n": m["Exact-Line CLV N"],
           "line_moved_n": m["Line-Moved N"], "no_close_n": m["No-Close N"],
           "mean_probability_clv": m["Mean Probability CLV"], "positive_clv_pct": m["Positive CLV %"],
           "canonical_dataset": str(Path(ds["path"])), "canonical_dataset_rows": ds["rows"],
           "canonical_dataset_sha256": ds["dataset_sha256"], "workbook_sha256": EX._file_sha(path),
           "content_sha256": content, "writer_sha256": writer}
    if old != rec:
        tmp = rec_p.with_name(rec_p.name + ".tmp")
        tmp.write_text(json.dumps(rec, sort_keys=True, indent=1, default=str) + "\n")
        os.replace(tmp, rec_p)
    chk = verify(out_dir, date, ops_root)
    return {"status": "WRITTEN" if chk["PERFORMANCE_EVIDENCE"] == "PASS" else "PERFORMANCE_EVIDENCE_INVALID",
            "path": str(path), "rows": len(rows), "rewritten": not same, "PERFORMANCE_STATUS": rec["status"],
            "receipt": str(rec_p), "dataset": {k: ds[k] for k in ("path", "rows", "rewritten", "reused", "rebuilt")},
            "verification": chk, **{k: rec[k] for k in ("start_date", "graded", "brier_score", "log_loss", "roi",
                                                        "exact_line_clv_n", "mean_probability_clv", "positive_clv_pct")}}
