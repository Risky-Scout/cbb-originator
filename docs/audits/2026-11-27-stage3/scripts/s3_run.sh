#!/bin/bash
# usage: s3_run.sh SCRIPT ARGS...  (root on the Droplet; transient read-only unit as cbb-originator; no host tuning)
set -u
OUT=/var/lib/sports-models/cbb-originator/audit/stage3-20261127
S=$1; shift
avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo); [ "$avail" -ge 4194304 ] || { echo "YIELD mem $avail"; exit 3; }
free=$(df -BG --output=avail / | tail -1 | tr -dc 0-9); [ "$free" -ge 39 ] || { echo "YIELD disk ${free}G"; exit 3; }
systemd-run --quiet --wait --pipe --uid=cbb-originator --nice=10 \
  -p ReadOnlyPaths=/var/lib/sports-models -p ReadWritePaths=$OUT -p MemoryMax=3200M -p IOWeight=50 \
  -E PYTHONDONTWRITEBYTECODE=1 -E OMP_NUM_THREADS=1 -E OPENBLAS_NUM_THREADS=1 -E MKL_NUM_THREADS=1 \
  -E VECLIB_MAXIMUM_THREADS=1 -E NUMEXPR_NUM_THREADS=1 -E CBB_DUCKDB_THREADS=1 -E CBB_DUCKDB_MEMORY_LIMIT=2000MB \
  -E CBB_DUCKDB_HISTORY_MEMORY_LIMIT=700MB -E CBB_DUCKDB_TEMP_DIR=$OUT/duckdb_tmp -E TMPDIR=$OUT \
  -E CBB_PROD_ROOT=/var/lib/sports-models/cbb-originator/prod -E CBB_APP_ROOT=/opt/sports-models/cbb-originator/current \
  -E CBB_GAME_MODEL=SHADOW -E CBB_HISTORY_SOURCE=CANDIDATE -E CBB_HISTORY_CANDIDATE=/var/lib/sports-models/cbb-originator/history/cbb_production_history_minimal.duckdb -E CBB_CODE_COMMIT=9f65e7f9196c21a15811560d14c9974cb0bb2d7f -E CBB_SCHEDULER_ROLE=AUDIT_READ_ONLY \
  -p WorkingDirectory=/opt/sports-models/cbb-originator/current \
  /opt/sports-models/cbb-originator/venv/bin/python -B $OUT/$S "$OUT" "$@"
