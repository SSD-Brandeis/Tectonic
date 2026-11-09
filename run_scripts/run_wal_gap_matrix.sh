#!/bin/bash
# 2026-11-02: which RocksDB setting closes the gap between iostat (Fig. 12-style panel A) and RocksDB's counters
# (panel C: read = non.last.level + last.level read bytes, write = wal + flush + compaction write bytes) with
# direct I/O on? In the direct-I/O reruns the WAL was on (sync=false): WAL files are deleted after each flush, and
# their pages the kernel had not written back yet never reach the device. Each config below runs every given
# X-Bench trace once on each given device, single-threaded, with direct_io=1 and statistics=1, caches dropped,
# the trace in /dev/shm:
#   wal          WAL on, sync=false (as the direct-I/O reruns)
#   nowal        disable_wal=1
#   bps          wal_bytes_per_sync=1 MiB: the kernel writes the WAL out every 1 MiB in the background
#   archive      wal_ttl_seconds=10^6: obsolete WAL files are moved to archive/ and kept, not deleted
#   bps-archive  both
#   sync         sync=1: every write syncs the WAL (last: the slowest)
# Devices: "loop" = the DB on an ext4 file system of its own (loop device with direct I/O onto a file on sda3, as
# ../data/revision/R1.O7_ycsb-derivatives/direct-io-enabled/diagnostics-2026-11-01/isolated-device), iostat of the
# loop device alone; "sda" = the DB on the root file system, as in the experiments, iostat of sda.
# Per run: iostat (1 s), /proc/diskstats of the device before and after (after `sync` + 2 s), the executor output
# (RocksDB tickers), LOG, OPTIONS, the DB's files and archive/.
# Usage (in tmux): run_wal_gap_matrix.sh <out-dir> <xbench-cli> "<devices>" <workload>=<trace> ...
set -eu
OUT=$1 BIN=$2 DEVICES=$3; shift 3
WORKLOADS=("$@")
CONFIGS=("wal:" "nowal:disable_wal=1" "bps:wal_bytes_per_sync=1048576" "archive:wal_ttl_seconds=1000000"
         "bps-archive:wal_bytes_per_sync=1048576;wal_ttl_seconds=1000000" "sync:sync=1")
MNT=/mnt/walgap-db
mkdir -p $OUT
log() { echo "$(date -u '+%F %T') $*" | tee -a $OUT/progress.log; }
sha256sum $BIN > $OUT/binary.sha256
{ hostname; uname -a; lsblk -o NAME,MODEL,SIZE,LOG-SEC,PHY-SEC /dev/sda; } > $OUT/machine.txt
LOOP=
cleanup() {
  rm -f /dev/shm/walgap_trace.txt
  if [ -n "$LOOP" ]; then rm -rf $MNT/rocksdb_data; sudo umount $MNT || true; sudo losetup -d $LOOP || true; rm -f $OUT/loop.img; fi
}
trap cleanup EXIT
for DEVICE in $DEVICES; do
  if [ $DEVICE = loop ]; then
    fallocate -l 64G $OUT/loop.img
    LOOP=$(sudo losetup -f --show --direct-io=on $OUT/loop.img); DEV=${LOOP#/dev/}
    sudo mkfs.ext4 -q -E lazy_itable_init=0,lazy_journal_init=0 $LOOP
    sudo mkdir -p $MNT; sudo mount $LOOP $MNT; sudo chown $(id -u):$(id -g) $MNT
    DB=$MNT/rocksdb_data
    { losetup -l $LOOP; cat /sys/block/$DEV/queue/logical_block_size; findmnt $MNT; } > $OUT/device-loop.txt
  else
    DEV=sda DB=$OUT/sda_db
  fi
  for W in "${WORKLOADS[@]}"; do
    NAME=${W%%=*} TRACE=${W#*=}
    cp $TRACE /dev/shm/walgap_trace.txt
    for C in "${CONFIGS[@]}"; do
      CFG=${C%%:*} OPTS=${C#*:}
      O=$OUT/$DEVICE/$CFG/$NAME
      if [ -f $O/diskstats.after ]; then continue; fi
      rm -rf $DB; mkdir -p $DB $O
      sudo sh -c "sync; echo 3 > /proc/sys/vm/drop_caches"; sleep 5
      grep -w $DEV /proc/diskstats > $O/diskstats.before; date +%s.%N > $O/t0
      iostat -k -x -d -y $DEV 1 > $O/iostat.txt & IO=$!
      log "$DEVICE $CFG $NAME start (-c statistics=1;direct_io=1${OPTS:+;$OPTS})"
      $BIN execute -i /dev/shm/walgap_trace.txt -d rocksdb -p $DB -t 1 --merge-mode append \
        -c "statistics=1;direct_io=1${OPTS:+;$OPTS}" > $O/cli_raw.log 2> $O/cli_stderr.log || log "  exit $?"
      sync; sleep 2
      kill $IO; wait $IO 2>/dev/null || true
      grep -w $DEV /proc/diskstats > $O/diskstats.after; date +%s.%N > $O/t1
      cp $DB/LOG $O/rocksdb_LOG.txt; cp $DB/OPTIONS-* $O/ 2>/dev/null || true
      ls -la $DB > $O/db_files.txt; ls -la $DB/archive > $O/archive_files.txt 2>/dev/null || true
      log "$DEVICE $CFG $NAME done in $(echo "$(cat $O/t1) - $(cat $O/t0)" | bc) s"
    done
  done
  rm -rf $DB
  if [ $DEVICE = loop ]; then sudo umount $MNT; sudo losetup -d $LOOP; rm -f $OUT/loop.img; LOOP=; fi
done
touch $OUT/DONE
log "ALL DONE"
