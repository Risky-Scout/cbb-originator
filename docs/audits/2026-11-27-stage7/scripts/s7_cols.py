import sys, glob, duckdb
V = "/var/lib/sports-models/cbb-originator/prod/state/versions"
d = sorted(glob.glob(f"{V}/S2026*"))[-1]
con = duckdb.connect(f"{d}/increment.duckdb", read_only=True, config={"threads": 1, "memory_limit": "300MB"})
for s, t in con.execute("select table_schema, table_name from information_schema.tables order by 1,2").fetchall():
    cols = [c[0] for c in con.execute(f"select column_name from information_schema.columns where table_schema='{s}' and table_name='{t}'").fetchall()]
    n = con.execute(f'select count(*) from "{s}"."{t}"').fetchone()[0]
    print(f"{s}.{t} n={n}: {cols}")
