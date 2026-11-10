#!/bin/bash
# Fig. 12 at 1x (YCSB-A, 1M records, 1M operations), direct I/O on, WAL off, 5 rounds (user, 2026-11-10), on
# fixedkeyset-2 in tmux `fig12-x1`: generate both traces, then 5 rounds; DONE in direct-io-wal-off/ at the end.
# The host's package timers are stopped for the run and started again when it ends (as rounds 6-10 of the 10x run).
cd /home/cc/Tectonic
export FIG12_SCALE=1
D=data/revision/Fig12_ycsb-a-x1/direct-io-wal-off
mkdir -p "$D"
TIMERS="apt-daily.timer apt-daily-upgrade.timer man-db.timer update-notifier-download.timer"
echo "$(date '+%F %T') start" >> "$D/progress.log"
sudo systemctl stop $TIMERS && echo "$(date '+%F %T') stopped $TIMERS" >> "$D/progress.log"
trap 'sudo systemctl start $TIMERS; echo "$(date "+%F %T") started $TIMERS again" >> "$D/progress.log"' EXIT
python3 run_scripts/run_fig12_direct_io_wal_off.py --stage all 2>&1 | tee -a "$D/console.log"
rc=${PIPESTATUS[0]}
echo "$(date '+%F %T') exit $rc" >> "$D/progress.log"
sha256sum bin/tectonic-cli-bb407953 > "$D/binary.sha256"
echo "$rc" > "$D/DONE"
