#!/bin/bash
# 2026-11-02 (authors): Table 2's X-Bench columns again with tectonic-cli 24f2dddf (5b727191 + Scatter::apply computing
# its multiplier once; same output), on fixedkeyset-2, the machine of Table 2's KV-Bench generator column. (Table 3 runs
# on revision-2, run_scripts/run_table3_revision2.sh; the corrected Fig. 7 is not rerun, authors' decision.)
#   0. waits until KV-Bench's generator run for Table 2 (kvbench-generator-table2/, tmux table2-kvbench-gen) has
#      finished, checks that the machine is idle, keeps the old binary as bin/tectonic-cli-<sha8> and installs
#      bin/tectonic-cli-24f2dddf as target/release/tectonic-cli (the path the runners use), checking its sha256;
#   1. Table 2: kvbench-paper-full-fixed-vec-hashset-24f2dddf/ (50 runs), kvbench-iv-0.1x-fixed-vec-hashset-24f2dddf/ (10).
# Each step appends its exit status to DONE in its folder; the log is data/revision/scatterfix-reruns.log.
# Usage (on fixedkeyset-2, in tmux scatterfix-reruns): bash run_scripts/run_scatterfix_reruns.sh
set -u
R=/home/cc/Tectonic
cd $R || exit 1
K=data/revision/R1.O8-R2.O2_keyset-selection
NEW_SHA=24f2dddfd753e6f38d6c8352b7dd7e36fcb37456d0a6ca5ab73d21ec2ebb9aa4
LOG=$R/data/revision/scatterfix-reruns.log
log() { echo "$(date -u '+%F %T') $*" | tee -a $LOG; }

log "waiting for $K/kvbench-generator-table2/DONE and tmux table2-kvbench-gen to end"
until test -f $K/kvbench-generator-table2/DONE && ! tmux has-session -t table2-kvbench-gen 2>/dev/null; do sleep 60; done
log "KV-Bench generator run finished: $(cat $K/kvbench-generator-table2/DONE)"
n=$(ps -eo args | grep -cE '^(/home/cc/Tectonic/target/release/tectonic-cli|/home/cc/KV-WorkloadGenerator/bin/load_gen|java |python3 -u run_scripts/run_)')
if [ "$n" != 0 ]; then log "ERROR: not idle ($n processes); stopped"; exit 1; fi

old=$(sha256sum target/release/tectonic-cli | cut -d' ' -f1)
mkdir -p bin && cp -n target/release/tectonic-cli bin/tectonic-cli-${old:0:8}
if [ "$(sha256sum < bin/tectonic-cli-24f2dddf | cut -d' ' -f1)" != "$NEW_SHA" ]; then log "ERROR: bin/tectonic-cli-24f2dddf sha256 differs; stopped"; exit 1; fi
install -m 775 bin/tectonic-cli-24f2dddf target/release/tectonic-cli
if [ "$(sha256sum < target/release/tectonic-cli | cut -d' ' -f1)" != "$NEW_SHA" ]; then log "ERROR: install failed; stopped"; exit 1; fi
log "installed 24f2dddf as target/release/tectonic-cli (old ${old:0:8} kept as bin/tectonic-cli-${old:0:8})"

for d in kvbench-paper-full-fixed-vec-hashset-24f2dddf kvbench-iv-0.1x-fixed-vec-hashset-24f2dddf; do
  log "start $d"
  python3 -u run_scripts/run_keyset_fixed_queue.py --out-dir $R/$K/$d 2>&1 | tee -a $K/$d/logs/console.$(hostname).out
  s=${PIPESTATUS[0]}; echo "$(date -u '+%F %T') exit $s" >> $K/$d/DONE; log "end $d: exit $s"
done

