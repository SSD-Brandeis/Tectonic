#!/bin/bash
# Local watcher for run_fig12_paper_options_remote.sh on fixedkeyset-2. Usage: fetch_fig12_paper_options.sh <scale>
# No ssh during the first FIRST_WAIT seconds (ssh logins write to sda, the measured device), then a check every
# CHECK seconds until DONE; then it fetches the run folder (not the database).
SCALE=$1
D=/home/cc/Tectonic/data/revision/Fig12_ycsb-a-x$SCALE/direct-io-wal-off-paper-options
FIRST_WAIT=${FIRST_WAIT:-1200}; CHECK=${CHECK:-300}
r() { ssh -o BatchMode=yes -o ConnectTimeout=20 fixedkeyset2 "$@"; }
sleep "$FIRST_WAIT"
until r "test -f $D/DONE"; do echo "$(date -u '+%F %T') not done"; sleep "$CHECK"; done
mkdir -p $D
rsync -a --exclude rocksdb_data fixedkeyset2:$D/ $D/
echo "fetched $(date -u '+%F %T'), DONE=$(cat $D/DONE)"; cat $D/progress.log
