#!/bin/bash
# usage: s2_run.sh SCRIPT ARGS...   (run as root on the Droplet; transient read-only unit as cbb-originator)
OUT=/var/lib/sports-models/cbb-originator/audit/stage2-20261127
S=$1; shift
mkdir -p $OUT/duckdb_tmp && chown cbb-originator: $OUT/duckdb_tmp
/usr/bin/time -f "WALL %e s  MAXRSS %M KB" systemd-run --quiet --wait --pipe --uid=cbb-originator --nice=10 \
  -p ReadOnlyPaths=/var/lib/sports-models -p ReadWritePaths=$OUT -p MemoryMax=3200M -p IOWeight=50 \
  -E PYTHONDONTWRITEBYTECODE=1 -E OMP_NUM_THREADS=1 -E OPENBLAS_NUM_THREADS=1 -E MKL_NUM_THREADS=1 \
  -E VECLIB_MAXIMUM_THREADS=1 -E NUMEXPR_NUM_THREADS=1 -E CBB_DUCKDB_THREADS=1 -E CBB_DUCKDB_MEMORY_LIMIT=2000MB \
  -E CBB_DUCKDB_HISTORY_MEMORY_LIMIT=700MB -E CBB_DUCKDB_TEMP_DIR=$OUT/duckdb_tmp \
  -E CBB_PROD_ROOT=/var/lib/sports-models/cbb-originator/prod -E CBB_APP_ROOT=/opt/sports-models/cbb-originator/current \
  -E TMPDIR=$OUT /opt/sports-models/cbb-originator/venv/bin/python -B $OUT/$S "$@"
