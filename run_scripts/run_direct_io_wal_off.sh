#!/bin/bash
# 2026-11-03 (user): the direct-I/O reruns again with the WAL off too (`-c direct_io=1;disable_wal=1`): the same
# traces, sides, harness and machine as direct-io-enabled/, into direct-io-wal-off/ of the experiment. With the WAL
# off, iostat adds up to RocksDB's counters (data/revision/R1.O7-R1.O9_direct-io_iostat-vs-rocksdb-stats/).
#   bash run_scripts/run_direct_io_wal_off.sh r1o7       (fixedkeyset-1)
#   bash run_scripts/run_direct_io_wal_off.sh mixgraph   (fixedkeyset-2)
# Phase 1: trial 1 of every side (X-Bench and its comparison), then PHASE1_DONE. The script then waits for
# CONTINUE (created after the results were fetched: no ssh traffic on sda while trials run; at most 2 h), and
# phase 2 adds trials 2-5. Then the report and DONE.
# Binary: bin/tectonic-cli-bb407953 (the one of the WAL gap matrix): direct_io and disable_wal; the binary of
# direct-io-enabled/ (5b727191) predates disable_wal and ignores it.
# Refuses to start if anything else uses the machine (one run per machine).
cd /home/cc/Tectonic || exit 1
case "$1" in
  r1o7) script=run_scripts/run_ycsb_derivatives_fidelity.py; exp=R1.O7_ycsb-derivatives ;;
  mixgraph) script=run_scripts/run_mixgraph_fidelity.py; exp=R1.O9-R2.O1_mixgraph ;;
  *) echo "usage: $0 r1o7|mixgraph"; exit 1 ;;
esac
dir=data/revision/$exp/direct-io-wal-off
mkdir -p $dir
export TECTONIC_CLI=/home/cc/Tectonic/bin/tectonic-cli-bb407953
log() { echo "$(date -u '+%F %T') $*" | tee -a $dir/progress.log; }
# a process whose command line starts with an executor, db_bench or an experiment script (not a shell that merely
# names one, e.g. a finished tmux session waiting at `read`)
if pgrep -f "^(\S*/)?(tectonic-cli\S*|db_bench) |^(sudo )?python3 (-u )?\S*(run_keyset|run_ycsb_derivatives_fidelity|run_mixgraph_fidelity|run_io_tool_match|run_table5)|^bash \S*run_wal_gap" >/dev/null; then
  log "machine busy: not starting"; pgrep -af "tectonic-cli|run_keyset|fidelity|db_bench|run_wal_gap"; exit 1
fi
sha256sum $TECTONIC_CLI > $dir/binary.sha256
log "phase 1: trial 1 of every side ($TECTONIC_CLI)"
python3 -u $script --stage execute --direct-io --disable-wal --trials 1 || { log "phase 1 exit $?"; exit 1; }
log "phase 1 done"; touch $dir/PHASE1_DONE
for _ in $(seq 120); do [ -f $dir/CONTINUE ] && break; sleep 60; done
log "phase 2: trials 2-5 ($( [ -f $dir/CONTINUE ] && echo CONTINUE || echo 'no CONTINUE after 2 h' ))"
python3 -u $script --stage execute --direct-io --disable-wal --trials 5 || { log "phase 2 exit $?"; exit 1; }
log "phase 2 done"
python3 -u $script --stage report --direct-io --disable-wal || log "report exit $? (redo locally)"
touch $dir/DONE
log "ALL DONE"
