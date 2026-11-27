import os, sys, tempfile
from pathlib import Path
import pandas as pd
from cbb_platform.prod import lifecycle as LC, quote_refresh as QR
t = Path(tempfile.mkdtemp()); prod = t / "cbb-originator/prod"; (prod / "ops").mkdir(parents=True)
m = prod.parent / "locks/cbb-originator.writer.priority"; m.parent.mkdir(parents=True)
m.write_text(f"pid={os.getpid()}\nrefresh_id=R-live\nwindow=18:00\n")
n = {"exports": 0}
QR.run = lambda ctx, dry_run=False: {"status": "NOTHING_DUE", "requests": 0, "credits": 0}
LC.game_quotes_capture = lambda life, day: None
def ex(life, day): n["exports"] += 1; return {}
LC.exports_for = ex
now = pd.Timestamp("2026-11-26T23:00:05Z")
LC.quotes(LC.Life(ops_root=prod / "ops", prod_root=prod, history_db=t / "h.duckdb", clock=lambda: now))
print("live waiting refresh -> export rebuilds by this wake:", n["exports"], "(expected 0)", "OLD_PATH_DEFECT" if n["exports"] else "NEW_PATH_OK")
