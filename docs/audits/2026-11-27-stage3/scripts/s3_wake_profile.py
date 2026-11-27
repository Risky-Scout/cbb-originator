"""Stage 3: time the components of an IDLE quote wake on production data, read-only, no provider call.
QR.run(dry_run=True) returns before any session; game_quotes_plan is a pure read (capture NOT called);
exports.daily runs with out_root = a copy of today's export dir (so unchanged workbooks are not rewritten, as in
production) and ops/runs/state read-only; any step that tries to write outside out_root is recorded, not performed."""
import json, os, resource, sys, time, functools, shutil
from pathlib import Path
OUT = Path(sys.argv[1])
APP = Path("/opt/sports-models/cbb-originator/current"); sys.path.insert(0, str(APP))
os.environ.setdefault("CBB_EXPORT_ROOT", str(OUT / "exports"))
S = Path("/var/lib/sports-models/cbb-originator")
from cbb_platform import duckdb_limits as DLM
DLM.install()
import pandas as pd
from cbb_platform.prod import lifecycle as LC, exports as EX, market_exports as MX, quote_refresh as QR, shadow_tick as SH
T, ERR = {}, {}
def timed(mod, name, key, swallow=False, default=None):
    f = getattr(mod, name)
    @functools.wraps(f)
    def w(*a, **k):
        t = time.perf_counter()
        try:
            return f(*a, **k)
        except OSError as e:
            ERR[key] = f"{type(e).__name__}: {str(e)[:160]}"
            if swallow: return default
            raise
        finally:
            T[key] = round(T.get(key, 0) + time.perf_counter() - t, 3)
    setattr(mod, name, w)
for mod, n in [(EX, "slate_games"), (EX, "team_names"), (EX, "distribution_records"), (EX, "player_context"), (EX, "summary_rows"),
               (EX, "pmf_frame"), (EX, "distributions_if_changed"), (EX, "_cutoffs"), (EX, "game_lines"), (EX, "write_if_changed"),
               (EX, "game_grades"), (MX, "board"), (MX, "graded_rows"), (MX, "grades_tables"), (MX, "ingestion_totals")]:
    timed(mod, n, f"{mod.__name__.split('.')[-1]}.{n}")
from cbb_platform.prod import quote_obs as QO
timed(QO, "latest", "quote_obs.latest")
for mod, n, d in [(MX, "freeze", {}), (EX, "_freeze_day", {}), (EX, "_performance", {}), (EX, "public_contract", {})]:
    timed(mod, n, f"{mod.__name__.split('.')[-1]}.{n}", swallow=True, default=d)
life = LC.Life(ops_root=S / "prod/ops", prod_root=S / "prod", export_root=OUT / "exports")
day = LC.et_date(life.now())
src = S / "exports" / day.isoformat()
if src.exists():
    shutil.copytree(src, OUT / "exports" / day.isoformat(), dirs_exist_ok=True)
r0 = resource.getrusage(resource.RUSAGE_SELF); t0 = time.perf_counter()
ctx = SH.Ctx(ops_root=life.ops_root, prod_root=life.prod_root, history_db=LC.HX.resolve(), clock=None)
life.history_db = ctx.history_db
t = time.perf_counter(); qr = QR.run(ctx, dry_run=True); T["A_quote_refresh_dry"] = round(time.perf_counter() - t, 3)
t = time.perf_counter(); gp = LC.game_quotes_plan(life, day, life.now()); T["B_game_quotes_plan"] = round(time.perf_counter() - t, 3)
t = time.perf_counter(); ex = LC.exports_for(life, day); T["C_exports_daily"] = round(time.perf_counter() - t, 3)
r1 = resource.getrusage(resource.RUSAGE_SELF)
out = {"day": str(day), "now": str(life.now()), "quote_refresh": {k: qr.get(k) for k in ("status", "due_games")}, "game_quotes_plan": gp,
       "seconds": T, "write_refused": ERR, "total_s": round(time.perf_counter() - t0, 2),
       "cpu_s": round(r1.ru_utime + r1.ru_stime - r0.ru_utime - r0.ru_stime, 2), "peak_rss_mb": round(r1.ru_maxrss / 1024, 1),
       "exports_status": {k: (v.get("status"), v.get("rewritten")) for k, v in ex.items() if isinstance(v, dict) and "status" in v},
       "quote_rows": ex.get("game_lines", {}).get("quote_rows"), "raw_quote_rows": ex.get("raw_quote_rows")}
shutil.rmtree(OUT / "exports", ignore_errors=True)
(OUT / f"wake_profile_{int(time.time())}.json").write_text(json.dumps(out, indent=1, default=str)); print(json.dumps(out, default=str))
