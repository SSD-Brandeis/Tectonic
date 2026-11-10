#!/bin/bash
# Fig. 12 with direct I/O on, the WAL off, and RocksDB as the paper's Fig. 12 harness had it otherwise
# (max_write_buffer_number=2, no compression; user, 2026-11-10). Usage: run_fig12_paper_options_remote.sh <scale> <binary>
# On fixedkeyset-2 in tmux `fig12-paper-x<scale>`: 5 rounds on the existing traces of Fig12_ycsb-a-x<scale>/workloads/;
# DONE in direct-io-wal-off-paper-options/ at the end. The host's package timers are stopped during the run.
set -u
SCALE=$1; BIN=$2
cd /home/cc/Tectonic
export FIG12_SCALE=$SCALE FIG12_PAPER_OPTIONS=1 TECTONIC_CLI=$BIN
D=data/revision/Fig12_ycsb-a-x$SCALE/direct-io-wal-off-paper-options
mkdir -p "$D"
TIMERS="apt-daily.timer apt-daily-upgrade.timer man-db.timer update-notifier-download.timer"
echo "$(date '+%F %T') start: scale $SCALE, $BIN" >> "$D/progress.log"
sudo systemctl stop $TIMERS && echo "$(date '+%F %T') stopped $TIMERS" >> "$D/progress.log"
trap 'sudo systemctl start $TIMERS; echo "$(date "+%F %T") started $TIMERS again" >> "$D/progress.log"' EXIT
sha256sum "$BIN" > "$D/binary.sha256"
python3 run_scripts/run_fig12_direct_io_wal_off.py --stage execute 2>&1 | tee -a "$D/console.log"
rc=${PIPESTATUS[0]}
echo "$(date '+%F %T') exit $rc" >> "$D/progress.log"
echo "$rc" > "$D/DONE"
