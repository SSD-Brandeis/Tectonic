#!/bin/bash
# Local watcher for the Fig. 12 direct-I/O + WAL-off run on fixedkeyset-2 (2026-11-04). ssh is free while the
# traces are generated (nothing is measured); during the trials it checks rarely (ssh logins write to sda):
# once after FIRST_WAIT, then it sleeps until the end estimated from the trials done so far.
D=/home/cc/Tectonic/data/revision/Fig12_ycsb-a-x10
P=$D/direct-io-wal-off/progress.log
FIRST_WAIT=${FIRST_WAIT:-14400}
r() { ssh -o ConnectTimeout=20 fixedkeyset2 "$@"; }
while ! r "grep -q 'generated xbench\|exit' $P"; do sleep 120; done
r "cat $P"; echo "generation done $(date -u '+%F %T')"
sleep "$FIRST_WAIT"
while ! r "test -f $D/direct-io-wal-off/DONE"; do
  est=$(r "cat $P" | python3 -c '
import sys, datetime as dt
L = [l.split(" ", 2) for l in sys.stdin if " round " in l or "generated xbench" in l]
t = [dt.datetime.fromisoformat(a + " " + b) for a, b, _ in L]
done = len(t) - 1
if done < 1: print(1800); sys.exit()
per = (t[-1] - t[0]).total_seconds() / done
left = per * (10 - done) - (dt.datetime.now() - t[-1]).total_seconds()
print(int(max(left, 0) + 600))')
  echo "$(date -u '+%F %T') not done; next check in ${est}s"; sleep "$est"
done
rsync -a --exclude rocksdb_data --exclude '*.txt' --exclude '*.part' fixedkeyset2:$D/ $D/
rsync -a fixedkeyset2:$D/direct-io-wal-off/db_state/ $D/direct-io-wal-off/db_state/
echo "fetched $(date -u '+%F %T')"; cat $P
