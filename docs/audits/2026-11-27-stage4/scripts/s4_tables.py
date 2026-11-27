import sys, duckdb
from cbb_platform.prod import state as ST, history_extract as HX
st = ST.current()
print("state", st["pointer"])
con = duckdb.connect(str(st["increment_db"]), read_only=True)
for s, t in con.execute("select table_schema, table_name from information_schema.tables order by 1,2").fetchall():
    cols = [c[0] for c in con.execute(f"describe {s}.{t}").fetchall()]
    print(s, t, con.execute(f"select count(*) from {s}.{t}").fetchone()[0], cols)
print("history", HX.resolve() if hasattr(HX, "resolve") else None)
