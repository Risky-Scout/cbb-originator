#!/bin/bash
# Stage 07 one-shot guarded deploy of 7d160b1 (authorized 2026-11-27: "after the 04:45Z retention pass, only if CI and all
# established deployment safeguards pass"). CI 46078107204 = success (verified before scheduling). Bounded: window W1
# 2026-11-28 04:50-05:40Z, fallback W2 16:50-17:25Z; then gives up. Never lowers a threshold, never stops a timer.
set -uo pipefail
S=7d160b1c7c5a02db834793954924c4ff240a0690; EXPECT_CUR=9f65e7f9196c21a15811560d14c9974cb0bb2d7f
R=/opt/sports-models/cbb-originator; REL=$R/releases; ENV=/etc/sports-models/cbb-originator.env
A=/var/lib/sports-models/cbb-originator/audit/stage7-20261127; LOG=$A/deploy_receipt.jsonl; STATE=/root/cbb-s7-deploy.state
SUMS=$A/candidate_$S.sha256sums; CANCEL=/root/cbb-s7-deploy.CANCEL
MIN_FREE=41339060224          # 38.5 GiB: the established gate, strictest reading
MIN_MEM_KB=4194304            # 4 GiB
rec(){ printf '{"at":"%s","event":"%s","detail":"%s"}\n' "$(date -u +%FT%TZ)" "$1" "$2" >> "$LOG"; }
exec 9>/run/cbb-s7-deploy.lock; flock -n 9 || { rec SKIP "another instance holds the lock"; exit 0; }
[ "${DRYRUN:-0}" != 1 ] && [ -f "$STATE" ] && { rec SKIP "terminal state already recorded: $(cat $STATE)"; exit 0; }
final(){ echo "$1" > "$STATE"; rec "$1" "$2"; exit 0; }
H=$(date -u +%H); [ "${DRYRUN:-0}" = 1 ] && { LOG=/dev/stdout; STATE=/nonexistent/x; DEADLINE=$(($(date -u +%s)+5)); H=DRY; }; [ "$H" = DRY ] || case "$H" in 04|05) DEADLINE=$(date -u -d "today 05:40" +%s);; 16|17) DEADLINE=$(date -u -d "today 17:25" +%s);; *) rec SKIP "outside authorized windows"; exit 0;; esac
LAST=""
while [ "$(date -u +%s)" -lt "$DEADLINE" ]; do
  [ -e "$CANCEL" ] && final CANCELLED "cancel file present"
  CUR=$(basename "$(readlink -f $R/current)")
  [ "$CUR" = "$S" ] && final ALREADY_DEPLOYED "current already $S"
  [ "$CUR" = "$EXPECT_CUR" ] || final ABORT_SUPERSEDED "current is $CUR, expected $EXPECT_CUR: production changed, not racing it"
  OTHER=$(ls -d $REL/*.tmp 2>/dev/null | grep -v "/$S.tmp$" || true)
  [ -z "$OTHER" ] || final ABORT_SUPERSEDED "another staged candidate exists: $OTHER"
  [ -d "$REL/$S.tmp" ] || final ABORT "staged tree missing"
  M=$((10#$(date -u +%M))); why=""
  if ! { [ $M -ge 21 ] && [ $M -le 29 ]; } && ! { [ $M -ge 51 ] && [ $M -le 59 ]; }; then why="within 10 min of a :10/:40 tick"; fi
  if [ -z "$why" ]; then
    for u in $(systemctl list-units --all --plain --no-legend 'cbb-originator-*.service' | awk '{print $1}'); do
      a=$(systemctl show "$u" -p ActiveState --value); case "$a" in inactive|failed) ;; *) why="$u $a";; esac; done
  fi
  FREE=$(df -B1 --output=avail / | tail -1 | tr -d ' '); MEM=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
  [ -z "$why" ] && [ "$FREE" -lt "$MIN_FREE" ] && why="free disk $FREE < $MIN_FREE"
  [ -z "$why" ] && [ "$MEM" -lt "$MIN_MEM_KB" ] && why="MemAvailable ${MEM}kB < 4GiB"
  if [ -z "$why" ] || [ "$H" = DRY ]; then
    if ! (cd "$REL/$S.tmp" && find . -type f -print0 | sort -z | xargs -0 sha256sum | LC_ALL=C sort -k2 | diff -q - <(LC_ALL=C sort -k2 "$SUMS") >/dev/null); then
      final ABORT "staged tree does not match $SUMS"; fi
    [ "$H" = DRY ] && { echo "DRYRUN hash=OK blocker=${why:-none} free=$FREE mem_kB=$MEM minute=$M cur=$CUR"; exit 0; }
    rec PRECONDITIONS_PASS "free=$FREE mem_kB=$MEM minute=$M"
    OUT=$(bash "$REL/$S.tmp/deploy/droplet/ops/cbb-deploy-release" "$S" 2>&1); RC=$?
    rec DEPLOY_RELEASE "rc=$RC $(echo "$OUT" | tr '\n"' ' ' | cut -c1-600)"
    if [ $RC -ne 0 ]; then
      [ "$(basename "$(readlink -f $R/current)")" = "$EXPECT_CUR" ] || final FAILED_NEEDS_OPERATOR "deploy-release rc=$RC and current moved"
      [ -d "$REL/$S" ] && [ ! -d "$REL/$S.tmp" ] && final FAILED_NEEDS_OPERATOR "deploy-release rc=$RC after rename; current unchanged"
      rec RETRY "deploy-release refused rc=$RC (current unchanged)"; sleep 60; continue
    fi
    IU=$(bash "$R/current/deploy/droplet/ops/cbb-install-units" 2>&1); rec INSTALL_UNITS "rc=$? $(echo "$IU" | tr '\n"' ' ' | cut -c1-600)"
    PF=$("$R/current/deploy/droplet/bin/cbb-originator-run" preflight 2>&1); PRC=$?
    rec PREFLIGHT "rc=$PRC $(echo "$PF" | tr '\n"' ' ' | tail -c 600)"
    if [ $PRC -ne 0 ] || ! echo "$PF" | grep -q "PREFLIGHT OK"; then
      cd "$R" && ln -sfn "releases/$EXPECT_CUR" current.new && mv -T current.new current
      echo cae2a027430184c5686eb87d180e6062457359ac > ROLLBACK_RELEASE.new && mv -T ROLLBACK_RELEASE.new ROLLBACK_RELEASE
      sed -i "s/^CBB_EXPECTED_COMMIT=.*/CBB_EXPECTED_COMMIT=$EXPECT_CUR/" "$ENV"
      PF2=$("$R/current/deploy/droplet/bin/cbb-originator-run" preflight 2>&1)
      final ROLLED_BACK "preflight failed on $S; restored $EXPECT_CUR: $(echo "$PF2" | tr '\n"' ' ' | tail -c 300)"
    fi
    RB=$(cat $R/ROLLBACK_RELEASE)
    RBV=$(cd "$REL/$RB" && sha256sum -c --quiet "$REL/$RB.sha256sums" >/dev/null 2>&1 && echo OK || echo MISMATCH)
    rec ROLLBACK_VERIFIED "ROLLBACK_RELEASE=$RB tree=$RBV expected_commit=$(grep ^CBB_EXPECTED_COMMIT= $ENV)"
    PR=$(bash "$R/current/deploy/droplet/ops/cbb-prune-releases" 2>&1); rec PRUNE_PLAN "$(echo "$PR" | tr '\n"' ' ' | cut -c1-600)"
    if [ "$RB" = "$EXPECT_CUR" ] && [ "$RBV" = OK ]; then
      PA=$(bash "$R/current/deploy/droplet/ops/cbb-prune-releases" --apply 2>&1); rec PRUNE_APPLY "$(echo "$PA" | tr '\n"' ' ' | cut -c1-600)"
    else rec PRUNE_SKIPPED "rollback not verified"; fi
    final DEPLOYED "current=$(basename $(readlink -f $R/current)) rollback=$RB free_after=$(df -B1 --output=avail / | tail -1 | tr -d ' ')"
  fi
  [ "$why" != "$LAST" ] && rec WAIT "$why"; LAST="$why"; sleep 30
done
if [ "$H" = 16 ] || [ "$H" = 17 ]; then final NOT_DEPLOYED_WINDOWS_EXHAUSTED "last blocker: $LAST"; fi
rec W1_EXHAUSTED "last blocker: $LAST (fallback window 16:50-17:25Z remains)"
