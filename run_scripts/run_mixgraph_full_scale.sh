#!/bin/bash
# 2026-11-11 (user): db_bench mixgraph Prefix_dist at the paper's full scale, 50M keys and 420M queries, as Cao et
# al.'s Appendix A.3 command; the paper's Fig. 16 / Table 6 / Fig. 17 run at 1/10. Same pipeline and harness as the
# 1/10 run (run_mixgraph_fidelity.py with MIXGRAPH_SCALE=1), on fixedkeyset-1, 3 trials, into
# data/revision/R1.O9-R2.O1_mixgraph/full-scale/. Only the two traces the paper uses: the db_bench run with the
# scan-length log (the reference) and X-Bench; no unmodified db_bench rerun, no YCSB.
#
# Disk (fk1: ~128 GB free): the binary db_bench traces are kept, zstd-compressed (sha256 before compression in
# results/dbbench-scanlog.json). Their text form (51+6 GB) is deleted once it is parsed into results/arrays/
# (sha256 and line counts in dbbench-scanlog/text_traces.sha256; trace_analyzer recreates it from the binary trace).
# X-Bench's raw output is deleted once execute/xbench.marked.txt is verified to be it plus the window markers.
#
#   bash run_scripts/run_mixgraph_full_scale.sh     (in tmux mixgraph-full on fixedkeyset-1)
# Resumable: a finished step leaves full-scale/.done.<step> and is skipped on a restart.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd ${REPO_ROOT} || exit 1
export MIXGRAPH_SCALE=1
export XBENCH_CLI=${REPO_ROOT}/bin/xbench-cli-bb407953  # the binary of the 1/10 direct-io-wal-off run
D=data/revision/R1.O9-R2.O1_mixgraph/full-scale
S=$D/dbbench-scanlog
PY="python3 -u run_scripts/run_mixgraph_fidelity.py"
mkdir -p $D
log() { echo "$(date -u '+%F %T') $*" | tee -a $D/progress.log; }
step() {
  local name=$1; shift
  if [ -f $D/.done.$name ]; then log "skip $name (done before)"; return 0; fi
  log "start $name: $*"
  "$@" || { log "FAILED $name (exit $?)"; df -h / | tail -1 | tee -a $D/progress.log; exit 1; }
  touch $D/.done.$name
  log "done $name; disk free $(df -h --output=avail / | tail -1 | tr -d ' ')"
}

if pgrep -f "^(\S*/)?(xbench-cli\S*|tectonic-cli\S*|db_bench) |^(sudo )?python3 (-u )?\S*(run_keyset|run_ycsb_derivatives_fidelity|run_mixgraph_fidelity|run_io_tool_match|run_table5)|^bash \S*run_wal_gap" >/dev/null; then
  log "machine busy: not starting"; pgrep -af "xbench-cli|tectonic-cli|run_keyset|fidelity|db_bench|run_wal_gap"; exit 1
fi
sha256sum $XBENCH_CLI rocksdb-v10.4.2-scanlog/db_bench rocksdb-v10.4.2/trace_analyzer > $D/binaries.sha256
log "binaries: $(tr '\n' ';' < $D/binaries.sha256)"

drop_text() {  # the text traces, once parsed into results/arrays/dbbench.npz
  [ -s $D/results/arrays/dbbench.npz ] || return 1
  (cd $S && for f in fillrandom.txt mixgraph.txt; do echo "$(sha256sum $f | cut -d' ' -f1)  $(wc -l < $f)  $f"; done) > $S/text_traces.sha256 || return 1
  rm $S/fillrandom.txt $S/mixgraph.txt
}
drop_xbench_raw() {  # X-Bench's output, once execute/xbench.marked.txt is verified to be it plus the markers
  local want got
  want=$(python3 -c "import json; print(json.load(open('$D/results/xbench.json'))['trace_sha256'])")
  got=$(grep -v -E '^F[SE] ' $D/execute/xbench.marked.txt | sha256sum | cut -d' ' -f1)
  [ "$want" = "$got" ] || { log "xbench.marked.txt minus markers $got != X-Bench output $want"; return 1; }
  rm $D/xbench/mixgraph.txt
}

step dbbench        $PY --stage dbbench-scanlog
step compress       bash -c "zstd -q -T0 -3 --rm $S/fillrandom.trace && zstd -q -T0 -3 --rm $S/mixgraph.trace"
step characterize   $PY --stage characterize
step drop_text      drop_text
step spec           $PY --stage spec
step xbench         $PY --stage xbench
step distance       $PY --stage distance
step traces         $PY --stage traces
step drop_xbench_raw drop_xbench_raw
step execute        $PY --stage execute --direct-io --disable-wal --trials 3
touch $D/DONE
log "ALL DONE"
