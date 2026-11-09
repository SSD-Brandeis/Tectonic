#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 2026-11-02 (authors): Table 2 with the same operation counts in every column, on fixedkeyset-2, one after the other:
#   1. X-Bench KV-IV at 0.1x, adaptive and fixed VecHashSetKeySet, 5 trials each
#      (R1.O8-R2.O2_keyset-selection/kvbench-iv-0.1x-fixed-vec-hashset/);
#   2. KV-Bench's generator, KV-I, II, III, V at full size and KV-IV at 0.1x, 5 trials
#      (R1.O8-R2.O2_keyset-selection/kvbench-generator-table2/).
# Each step appends its exit status to DONE in its folder. Usage (on fixedkeyset-2, in tmux): bash run_scripts/run_table2_same_scale.sh
cd ${REPO_ROOT} || exit 1
D=data/revision/R1.O8-R2.O2_keyset-selection
X=$D/kvbench-iv-0.1x-fixed-vec-hashset
G=$D/kvbench-generator-table2
python3 -u run_scripts/run_keyset_fixed_queue.py --out-dir ${REPO_ROOT}/$X 2>&1 | tee -a $X/logs/console.$(hostname).out
echo "$(date -u '+%F %T') exit ${PIPESTATUS[0]}" >> $X/DONE
python3 -u run_scripts/run_fig7_corrected.py --only KVBench-I/KV-Bench KVBench-II/KV-Bench KVBench-III/KV-Bench \
  KVBench-IV/KV-Bench KVBench-V/KV-Bench --kv-scales I=1 II=1 III=1 IV=10 V=1 --out-dir ${REPO_ROOT}/$G 2>&1 \
  | tee -a $G/logs/console.$(hostname).out
echo "$(date -u '+%F %T') exit ${PIPESTATUS[0]}" >> $G/DONE
