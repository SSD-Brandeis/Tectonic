#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 2026-10-31 (authors): starts the corrected Fig. 7 run (run_fig7_corrected.py: YCSB, KV-Bench, X-Bench, 5 trials,
# caches dropped) on fixedkeyset-2 once the direct-I/O mixgraph rerun there has finished, so nothing ever runs
# next to either:
#   1. waits for the local Table 5 rerun to exit and for the direct-I/O watcher to finish its copies
#      (only then may the local disk be read);
#   2. checks that the local YCSB checkout (rocksdb-benchmark-harness/vendor/YCSB, Fig. 7's) is commit 0bedec5
#      without local changes, i.e. the same source as the one built on fixedkeyset-2 from GitHub;
#   3. waits for mixgraph's direct-io-enabled/DONE on fixedkeyset-2 and its tmux session to end;
#   4. checks that fixedkeyset-2 is idle and starts tmux `fig7` there.
# Usage (local, in tmux): bash run_scripts/start_fig7_corrected_after_mixgraph.sh <table5 pid>
set -u
T5_PID=$1
HOST=fixedkeyset2
MG=${REPO_ROOT}/data/revision/R1.O9-R2.O1_mixgraph
WATCHER_LOG=${REPO_ROOT}/data/revision/direct-io-watcher.log
YCSB=${REPO_ROOT}/rocksdb-benchmark-harness/vendor/YCSB
log() { echo "$(date -u '+%F %T') $*"; }

while kill -0 "$T5_PID" 2>/dev/null; do sleep 60; done
log "Table 5 rerun exited"
until tail -1 "$WATCHER_LOG" 2>/dev/null | grep -q " done$"; do sleep 60; done
log "direct-I/O watcher finished its copies"

head=$(git -C $YCSB rev-parse HEAD)
changes=$(git -C $YCSB status --porcelain --untracked-files=no)
if [ "$head" != 0bedec5f8c5584410ebd48d32e54d63498186597 ] || [ -n "$changes" ]; then
    log "local YCSB is $head with changes [$changes]: not the source built on $HOST, not starting"; exit 1
fi
log "local YCSB is 0bedec5 without changes: same source as on $HOST"

until ssh -o BatchMode=yes -o ConnectTimeout=15 $HOST "test -s $MG/direct-io-enabled/DONE && ! tmux has-session -t direct-io 2>/dev/null"; do
    sleep 600
done
log "mixgraph direct-I/O rerun finished on $HOST: $(ssh -o BatchMode=yes $HOST "cat $MG/direct-io-enabled/DONE")"

busy=$(ssh -o BatchMode=yes -o ConnectTimeout=15 $HOST 'pgrep -ax xbench-cli; pgrep -ax load_gen; pgrep -ax java; pgrep -af "^python3 .*run_(keyset|ycsb_derivatives|mixgraph|fig7)"; tmux has-session -t fig7 2>/dev/null && echo "tmux session fig7"; echo REACHED')
[ "$(echo "$busy" | tail -1)" = REACHED ] || { log "$HOST unreachable: not starting"; exit 1; }
busy=$(echo "$busy" | sed '$d')
[ -z "$busy" ] || { log "$HOST is busy: $busy; not starting"; exit 1; }
ssh -o BatchMode=yes $HOST "tmux new-session -d -s fig7 'bash ${REPO_ROOT}/run_scripts/run_fig7_corrected.sh'" \
    && log "started run_fig7_corrected.py on $HOST (tmux fig7)" || log "start failed"
