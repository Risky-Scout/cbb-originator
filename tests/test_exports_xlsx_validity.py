"""Excel-valid export workbooks (prod/exports.py write_xlsx / distributions_workbook).

2026-11-10 production defect: CBB_Distributions_11_10_2026.xlsx held 1,399,564 PMF rows on ONE sheet. SpreadsheetML allows
1,048,576 rows per worksheet, so Excel's repair deleted the _xlnm._FilterDatabase defined name ('Distributions'!$A$1:
$G$1399565) and every cell of rows 1,048,577+. The writer now splits the Distributions across sheets ("Distributions",
"Distributions 2", ...) at distribution boundaries and refuses to write any sheet beyond the Excel limits.

TEST fixtures only; the row limit is patched small so no million-row workbook is built. openpyxl (test-only dependency,
requirements-test.lock.txt, required when CI is set) is the independent reader.
"""
import hashlib
import math
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
import pytest

from cbb_platform import config as C
from cbb_platform.prod import config as PC, exports as EX
from tests.test_prod_exports import _game, _pmf, _run, build_tree

try:
    import openpyxl
except ImportError:                                                # CI installs requirements-test.lock.txt: required there
    if os.environ.get("CI"):
        raise
    openpyxl = None
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REF = re.compile(r"^\$?([A-Z]{1,3})\$?(\d+)$")


def _colnum(s):
    return sum((ord(ch) - 64) * 26 ** k for k, ch in enumerate(reversed(s)))


def _dist_rows(n_dist, k=7):
    """n_dist distributions of k outcomes each, in export row shape [GAME, TEAM, PLAYER, PROP, MEAN, OUTCOME, P]."""
    rows = []
    for d in range(n_dist):
        p = np.full(k, 1.0 / k)
        rows += [[f"Away @ Home G{d // 3}", "Team", f"Player {d}", "PTS", 2.5 + d, o, float(p[o])] for o in range(k)]
    return rows


def _key(r):
    return (r[0], r[2], r[3])


def _structural(path: Path) -> dict:
    """Validate every part; return {sheet name: data rows (header excluded)}."""
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        for n in z.namelist():
            if n.endswith((".xml", ".rels")):
                ET.fromstring(z.read(n))                           # every part parses
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        sheets = [s.get("name") for s in wb.find("m:sheets", NS)]
        assert len(sheets) == len(set(sheets)) and all(len(s) <= 31 for s in sheets)
        names = [(d.get("name"), d.get("localSheetId")) for d in wb.iter(f"{{{NS['m']}}}definedName")]
        assert len(names) == len(set(names))                       # no duplicate defined name per scope
        for d in wb.iter(f"{{{NS['m']}}}definedName"):
            sid = int(d.get("localSheetId"))
            assert 0 <= sid < len(sheets)
            sheet, rng = d.text.rsplit("!", 1)
            assert sheet.strip("'").replace("''", "'") == sheets[sid]
            for part in rng.split(":"):
                m = REF.match(part)
                assert m and int(m.group(2)) <= EX.EXCEL_MAX_ROWS and _colnum(m.group(1)) <= EX.EXCEL_MAX_COLS, d.text
        out = {}
        for i, name in enumerate(sheets, 1):
            ws = ET.fromstring(z.read(f"xl/worksheets/sheet{i}.xml"))
            seen = set()
            for c in ws.iter(f"{{{NS['m']}}}c"):
                m = REF.match(c.get("r"))
                assert m and int(m.group(2)) <= EX.EXCEL_MAX_ROWS and c.get("r") not in seen
                seen.add(c.get("r"))
                v = c.find("m:v", NS)
                if v is not None:
                    assert math.isfinite(float(v.text)), (c.get("r"), v.text)   # no NaN / INF numeric cell
            for af in ws.iter(f"{{{NS['m']}}}autoFilter"):
                assert int(REF.match(af.get("ref").split(":")[1]).group(2)) <= EX.EXCEL_MAX_ROWS
            out[name] = len(ws.find("m:sheetData", NS)) - 1
    return out


def _openpyxl_rows(path: Path) -> dict:
    if openpyxl is None:                                           # local without the test dependency: the module reader
        return {n: s["rows"] for n, s in EX.read_xlsx(path).items()}
    wb = openpyxl.load_workbook(path, read_only=True)
    out = {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
    wb.close()
    return out


def test_distributions_split_across_sheets_within_the_excel_row_limit(monkeypatch, tmp_path):
    """1 valid defined names, 2 openpyxl opens, 4 row count, 5 keys, 6 probabilities -- with the limit forced small."""
    monkeypatch.setattr(EX, "EXCEL_MAX_ROWS", 30)                 # 29 data rows per sheet; 4 distributions of 7 fit
    rows = _dist_rows(10)                                          # 70 rows -> 3 sheets (28 + 28 + 14)
    p = EX.distributions_workbook(rows, tmp_path / "CBB_Distributions_11_10_2026.xlsx")
    counts = _structural(p)
    assert list(counts) == ["Distributions", "Distributions 2", "Distributions 3"]
    assert list(counts.values()) == [28, 28, 14] and sum(counts.values()) == len(rows)
    book = _openpyxl_rows(p)
    got = []
    for name, sheet in book.items():
        assert sheet[0] == list(EX.DIST_COLS)                      # every sheet carries the header
        got += sheet[1:]
    assert len(got) == len(rows)
    assert [_key(r) + (r[5],) for r in got] == [_key(r) + (r[5],) for r in rows]
    assert [r[6] for r in got] == [r[6] for r in rows]             # exact (repr round-trip), no rounding
    assert [r[4] for r in got] == [r[4] for r in rows]
    for sheet in book.values():                                    # no distribution straddles two sheets
        keys = {_key(r) for r in sheet[1:]}
        for k in keys:
            assert sum(1 for r in sheet[1:] if _key(r) == k) == 7
    assert EX.read_xlsx(p)["Distributions 2"]["rows"][0] == list(EX.DIST_COLS)


def test_distribution_sheets_boundaries_and_single_sheet_default():
    rows = _dist_rows(3)
    assert EX.distribution_sheets(rows) == [rows]                  # production limit: one sheet, unchanged
    assert EX.distribution_sheets([]) == [[]]
    parts = EX.distribution_sheets(rows, max_rows=14)
    assert parts == [rows[:14], rows[14:]]
    with pytest.raises(EX.ExportIntegrityError):
        EX.distribution_sheets(rows, max_rows=5)                   # one distribution larger than a sheet: refuse


def test_writer_refuses_a_sheet_beyond_the_excel_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(EX, "EXCEL_MAX_ROWS", 10)
    with pytest.raises(EX.ExportIntegrityError, match="Excel worksheet limit"):
        EX.write_xlsx(tmp_path / "x.xlsx", [{"name": "S", "blocks": [{"title": None, "columns": ("A",),
                                                                      "rows": [[i] for i in range(10)]}]}])
    assert not (tmp_path / "x.xlsx").exists() and not (tmp_path / "x.xlsx.tmp").exists()


def test_non_finite_values_are_blank_cells_never_invalid_numbers(tmp_path):
    """3 NaN / +Inf / -Inf (python and numpy floats) -> empty styled cell (existing export contract)."""
    rows = [["G", "T", "P", "PTS", v, 0, 1.0] for v in (float("nan"), float("inf"), -float("inf"), np.float32("nan"),
                                                        np.float64("inf"))]
    p = EX.write_xlsx(tmp_path / "nf.xlsx", [{"name": EX.DIST_SHEET, "blocks": [{"title": None, "columns": EX.DIST_COLS,
                                                                               "rows": rows}]}])
    _structural(p)
    xml = zipfile.ZipFile(p).read("xl/worksheets/sheet1.xml").decode()
    assert not re.search(r"<v>[^<]*(nan|inf)", xml, re.I)
    assert [r[4] for r in _openpyxl_rows(p)[EX.DIST_SHEET][1:]] == [None] * 5


def test_daily_distributions_workbook_valid_and_matches_source_without_model_change(monkeypatch, tmp_path):
    """End to end through EX.daily: split workbook equals distribution_rows exactly; 7 frozen artifacts unchanged."""
    arts = {k: hashlib.sha256((C.PROJECT_ROOT / p).read_bytes()).hexdigest() for k, (p, _) in PC.ARTIFACTS.items()}
    pm = {"G1": [{"game_id": "G1", "team_id": "T1", "player_id": f"P{i}", "market": "PTS", "method": "X",
                  "projection": 2.0 + i, "pmf": _pmf(2.0 + i, 40).tolist()} for i in range(5)]}
    tree = build_tree(tmp_path, [], [_game("G1", "2026-11-05T00:00:00+00:00")], pmfs=pm)
    src = EX.distribution_rows(tree["ops_root"], tree["runs_root"], EX.slate_games(tree["ops_root"], "2026-11-04"),
                               {}, "2026-11-04")[0]
    src_bytes = (tree["runs_root"] / "R1" / "market_pmfs.parquet").read_bytes()
    monkeypatch.setattr(EX, "EXCEL_MAX_ROWS", 90)                  # 89 data rows/sheet; 40-outcome PMFs, 2 per sheet
    rep = _run(tree)
    assert rep["distributions"]["status"] == "WRITTEN" and rep["distributions"]["rows"] == len(src) > 2 * 89
    p = Path(rep["distributions"]["path"])
    counts = _structural(p)
    assert len(counts) >= 3 and sum(counts.values()) == len(src)
    got = [r for sheet in _openpyxl_rows(p).values() for r in sheet[1:]]
    assert got == [[g, t, pl, pr, m, o, pv] for g, t, pl, pr, m, o, pv in src]
    assert (tree["runs_root"] / "R1" / "market_pmfs.parquet").read_bytes() == src_bytes          # source untouched
    assert {k: hashlib.sha256((C.PROJECT_ROOT / p).read_bytes()).hexdigest() for k, (p, _) in PC.ARTIFACTS.items()} == arts
