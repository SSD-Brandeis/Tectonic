#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 2026-11-03: local helper for run_direct_io_wal_off.sh. Waits until <start-at> (UTC; no ssh while the first
# trials run: an ssh login writes to sda, which iostat measures), then checks every 10 min for PHASE1_DONE. Then it
# copies direct-io-wal-off/ (without the database) here and creates CONTINUE, which starts trials 2-5.
# Usage: fetch_wal_off_phase1.sh <ssh-host> <experiment dir under data/revision> <start-at, e.g. 18:15>
set -u
HOST=$1 EXP=$2 START=$3
D=${REPO_ROOT}/data/revision/$EXP/direct-io-wal-off
log() { echo "$(date -u '+%F %T') $HOST $*"; }
now=$(date -u +%s); at=$(date -u -d "$START" +%s); [ $at -gt $now ] && sleep $((at - now))
until ssh -o BatchMode=yes -o ConnectTimeout=20 $HOST "test -f $D/PHASE1_DONE"; do
  if ssh -o BatchMode=yes -o ConnectTimeout=20 $HOST "grep -q 'phase 1 exit' $D/progress.log" 2>/dev/null; then
    log "phase 1 FAILED:"; ssh $HOST "tail -5 $D/progress.log"; exit 1
  fi
  sleep 600
done
log "phase 1 done; copying"
mkdir -p $D
rsync -a --exclude rocksdb_data $HOST:$D/ $D/ || { log "copy failed"; exit 1; }
ssh -o BatchMode=yes $HOST "touch $D/CONTINUE" && log "CONTINUE set: trials 2-5 start"
ssh -o BatchMode=yes $HOST "cat $D/progress.log"
