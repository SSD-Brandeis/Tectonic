#!/bin/bash
# Fig. 12 rerun with direct I/O on and the WAL off (user, 2026-11-04), on fixedkeyset-2 in tmux `fig12`:
# generate both traces, then 5 rounds; writes DONE in direct-io-wal-off/ when it ends.
cd /home/cc/Tectonic
D=data/revision/Fig12_ycsb-a-x10/direct-io-wal-off
mkdir -p "$D"
echo "$(date '+%F %T') start" >> "$D/progress.log"
python3 run_scripts/run_fig12_direct_io_wal_off.py --stage all 2>&1 | tee -a "$D/console.log"
rc=${PIPESTATUS[0]}
echo "$(date '+%F %T') exit $rc" >> "$D/progress.log"
sha256sum bin/tectonic-cli-bb407953 > "$D/binary.sha256"
echo "$rc" > "$D/DONE"
