#!/bin/bash
# Local watcher for Fig. 12 rounds 6-10 on fixedkeyset-2 (2026-11-09, started 16:40 UTC on that host). No ssh during
# the first FIRST_WAIT seconds (ssh logins write to sda, the measured device; 10 trials take ~5.3 h), then a check
# every 20 min until DONE; then it fetches the results and database state (not the traces).
D=/home/cc/Tectonic/data/revision/Fig12_ycsb-a-x10
FIRST_WAIT=${FIRST_WAIT:-19200}
r() { ssh -o BatchMode=yes -o ConnectTimeout=20 fixedkeyset2 "$@"; }
sleep "$FIRST_WAIT"
until r "test -f $D/direct-io-wal-off/DONE"; do echo "$(date -u '+%F %T') not done"; sleep 1200; done
rsync -a --exclude rocksdb_data --exclude '*.txt' --exclude '*.part' fixedkeyset2:$D/direct-io-wal-off/ $D/direct-io-wal-off/
rsync -a fixedkeyset2:$D/direct-io-wal-off/db_state/ $D/direct-io-wal-off/db_state/
echo "fetched $(date -u '+%F %T'), DONE=$(cat $D/direct-io-wal-off/DONE)"; tail -n 14 $D/direct-io-wal-off/progress.log
