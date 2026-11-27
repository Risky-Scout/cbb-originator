#!/bin/bash
set -u
for spec in "S20261102T184529Z-90adc5bbc413|2026-11-02 19:00:00.000000+00|1" "S20261102T184529Z-90adc5bbc413|2026-11-02 19:00:00.000000+00|0"             "S20261115T171248Z-202dfaa66b64|2026-11-15 17:30:00.000000+00|1"             "S20261123T124420Z-11e53b4731b0|2026-11-23 15:00:00.000000+00|1" "S20261123T124420Z-11e53b4731b0|2026-11-23 15:00:00.000000+00|0"             "S20261109T221253Z-40fc8c960ea8|2026-11-10 23:00:00.000000+00|1" "S20261109T221253Z-40fc8c960ea8|2026-11-10 23:00:00.000000+00|0"             "S20261126T020455Z-9dd708fefdad|2026-11-26 04:30:00.000000+00|1"; do
  IFS="|" read sv asof cl <<< "$spec"
  avail=$(awk "/MemAvailable/{print \$2}" /proc/meminfo)
  if [ "$avail" -lt 4194304 ]; then echo "YIELD mem $avail"; exit 3; fi
  /root/s2_run.sh s2_bench.py /var/lib/sports-models/cbb-originator/audit/stage2-20261127 "$sv" "$asof" "$cl" 2>&1 | grep -v "^WALL"
done
echo BENCH_DONE
