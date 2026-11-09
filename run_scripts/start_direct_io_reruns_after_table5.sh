#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 2026-10-31 (authors): starts the direct-I/O reruns once the local Table 5 rerun has finished.
# The traces exist only on the local disk; reading them while Table 5 runs would disturb its device I/O
# counters, so nothing is copied before its process has exited. Then, one machine at a time:
#   R1.O7 (traces, inputs)    -> fixedkeyset-1, start `run_direct_io_reruns.sh r1o7`
#   mixgraph (traces, inputs) -> fixedkeyset-2, start `run_direct_io_reruns.sh mixgraph`
# A machine is checked to be idle before anything is copied to it (the copy writes to its disk), the copy is
# verified by checksum, and its run starts only after its copy is complete.
# Usage (local, in tmux): bash run_scripts/start_direct_io_reruns_after_table5.sh <table5 pid>
set -u
T5_PID=$1
R=${REPO_ROOT}/data/revision
O7=$R/R1.O7_ycsb-derivatives
MG=$R/R1.O9-R2.O1_mixgraph
log() { echo "$(date -u '+%F %T') $*"; }

idle() {  # $1 ssh alias: reachable, and nothing else running there
    local out
    out=$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$1" 'pgrep -ax xbench-cli; pgrep -ax db_bench; pgrep -af "^python3 .*run_(keyset|ycsb_derivatives|mixgraph)"; tmux has-session -t direct-io 2>/dev/null && echo "tmux session direct-io"; echo REACHED')
    [ "$(echo "$out" | tail -1)" = REACHED ] || { log "$1 unreachable"; return 1; }
    out=$(echo "$out" | sed '$d')
    [ -z "$out" ] || { log "$1 is busy: $out"; return 1; }
}

copy() {  # $1 ssh alias, $2 local dir, $3 remote dir, rest: rsync options; then verify by checksum
    local host=$1 src=$2 dst=$3; shift 3
    ssh -o BatchMode=yes "$host" "mkdir -p $dst" || return 1
    rsync -a "$@" "$src/" "$host:$dst/" || return 1
    local diff
    diff=$(rsync -an --checksum --itemize-changes "$@" "$src/" "$host:$dst/" | grep -v '^\.d' ) || true
    [ -z "$diff" ] || { log "copy of $src differs: $diff"; return 1; }
    log "copied and verified $src -> $host"
}

start() {  # $1 ssh alias, $2 r1o7|mixgraph, $3 experiment dir
    ssh -o BatchMode=yes "$1" "mkdir -p $3/direct-io-enabled && tmux new-session -d -s direct-io 'bash ${REPO_ROOT}/run_scripts/run_direct_io_reruns.sh $2 2>&1 | tee -a $3/direct-io-enabled/console.log'" || return 1
    sleep 60
    ssh -o BatchMode=yes "$1" "tmux has-session -t direct-io && pgrep -ax xbench-cli | cut -c1-200" \
        && log "started $2 on $1" || { log "$2 did not start on $1"; return 1; }
}

while kill -0 "$T5_PID" 2>/dev/null; do sleep 60; done
log "Table 5 rerun (pid $T5_PID) exited"
if pgrep -f "^python3 run_scripts/run_table5" >/dev/null || pgrep -x xbench-cli >/dev/null; then
    log "a Table 5 process is still running locally: not copying"; exit 1
fi

idle fixedkeyset1 \
  && copy fixedkeyset1 $O7/workloads $O7/workloads --exclude generation/ \
  && copy fixedkeyset1 $O7/results $O7/results --exclude execution.json --exclude order_check.json --exclude summary.json \
  && idle fixedkeyset1 && start fixedkeyset1 r1o7 $O7 \
  || log "R1.O7 on fixedkeyset-1 not started"

idle fixedkeyset2 \
  && copy fixedkeyset2 $MG/execute $MG/execute \
  && copy fixedkeyset2 $MG/results $MG/results --exclude arrays/ --exclude execution.json \
  && idle fixedkeyset2 && start fixedkeyset2 mixgraph $MG \
  || log "mixgraph on fixedkeyset-2 not started"
log "done"
