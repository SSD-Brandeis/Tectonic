#!/bin/bash
# Fig. 12 rerun (direct I/O on, WAL off): rounds 6-10 (user, 2026-11-09), on fixedkeyset-2 in tmux `fig12-more`.
# Rounds 1-5 left the two sides with different shares of RocksDB's two compaction patterns (~43 GB vs ~52-55 GB
# compacted for the same trace), so 5 more rounds with the same script, traces, binary and options:
# `run_fig12_direct_io_wal_off.py --stage execute --trials 10` keeps trials 1-5 and runs rounds 6-10, the order
# alternating as before (round 6: X-Bench first).
# The host's package timers (apt-daily, apt-daily-upgrade, man-db, update-notifier-download) wrote to sda during two
# trials of rounds 1-5; they are stopped (not disabled) for this run and started again when it ends.
cd /home/cc/Tectonic
D=data/revision/Fig12_ycsb-a-x10/direct-io-wal-off
TIMERS="apt-daily.timer apt-daily-upgrade.timer man-db.timer update-notifier-download.timer"
echo "$(date '+%F %T') rounds 6-10: start" >> "$D/progress.log"
sudo systemctl stop $TIMERS && echo "$(date '+%F %T') stopped $TIMERS" >> "$D/progress.log"
trap 'sudo systemctl start $TIMERS; echo "$(date "+%F %T") started $TIMERS again" >> "$D/progress.log"' EXIT
rm -f "$D/DONE"
python3 run_scripts/run_fig12_direct_io_wal_off.py --stage execute --trials 10 2>&1 | tee -a "$D/console.log"
rc=${PIPESTATUS[0]}
echo "$(date '+%F %T') rounds 6-10: exit $rc" >> "$D/progress.log"
echo "$rc" > "$D/DONE"
