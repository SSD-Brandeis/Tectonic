#!/bin/bash
# Local watcher for the Fig. 12 1x run on fixedkeyset-2 (2026-11-10, tmux fig12-x1). No ssh during the first
# FIRST_WAIT seconds (ssh logins write to sda, the measured device), then a check every 5 min until DONE; then it
# fetches the results, database state and generation record (not the traces).
D=/home/cc/Tectonic/data/revision/Fig12_ycsb-a-x1
FIRST_WAIT=${FIRST_WAIT:-2100}
r() { ssh -o BatchMode=yes -o ConnectTimeout=20 fixedkeyset2 "$@"; }
sleep "$FIRST_WAIT"
until r "test -f $D/direct-io-wal-off/DONE"; do echo "$(date -u '+%F %T') not done"; sleep 300; done
mkdir -p $D
rsync -a --exclude rocksdb_data --exclude '*.txt' --exclude '*.part' fixedkeyset2:$D/ $D/
rsync -a fixedkeyset2:$D/direct-io-wal-off/db_state/ $D/direct-io-wal-off/db_state/
echo "fetched $(date -u '+%F %T'), DONE=$(cat $D/direct-io-wal-off/DONE)"; cat $D/direct-io-wal-off/progress.log
