#!/bin/bash
# usage: s7_run.sh NAME SCRIPT ARGS...  (root on the Droplet). Named transient read-only unit cbb-audit-s7-NAME as cbb-originator;
# MemoryMax, RuntimeMaxSec, all thread vars 1, nice 10; refuses to start if a unit of that name exists or memory is low.
set -u
OUT=/var/lib/sports-models/cbb-originator/audit/stage7-20261127
N=cbb-audit-s7-$1; shift; S=$1; shift
systemctl list-units --all --no-legend "$N.service" | grep -q . && { echo "REFUSE $N exists"; exit 4; }
avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo); [ "$avail" -ge 3145728 ] || { echo "YIELD mem $avail"; exit 3; }
systemd-run --quiet --wait --pipe --unit=$N --uid=cbb-originator --nice=10 \
  -p ReadOnlyPaths=/var/lib/sports-models -p ReadWritePaths=$OUT -p MemoryMax=1500M -p RuntimeMaxSec=${RMS:-2400} -p IOWeight=50 \
  -E PYTHONDONTWRITEBYTECODE=1 -E OMP_NUM_THREADS=1 -E OPENBLAS_NUM_THREADS=1 -E MKL_NUM_THREADS=1 \
  -E VECLIB_MAXIMUM_THREADS=1 -E NUMEXPR_NUM_THREADS=1 -E CBB_DUCKDB_THREADS=1 -E CBB_DUCKDB_MEMORY_LIMIT=800MB \
  -E CBB_DUCKDB_TEMP_DIR=$OUT/duckdb_tmp -E TMPDIR=$OUT \
  -E CBB_PROD_ROOT=/var/lib/sports-models/cbb-originator/prod -E CBB_APP_ROOT=/opt/sports-models/cbb-originator/current \
  -E CBB_SCHEDULER_ROLE=AUDIT_READ_ONLY -E PYTHONPATH=/opt/sports-models/cbb-originator/current \
  -p WorkingDirectory=/ \
  /opt/sports-models/cbb-originator/venv/bin/python -B $OUT/$S "$OUT" "$@"
