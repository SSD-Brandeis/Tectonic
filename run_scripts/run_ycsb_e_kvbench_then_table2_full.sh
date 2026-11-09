#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 2026-11-02 (authors), on fixedkeyset-2, one after the other:
#   1. the corrected Fig. 7's YCSB-E/KV-Bench job again, 5 trials, with KV-Bench's range-query fix
#      (data/revision/fig7-corrected/kvbench-minimal-fix/load_gen_rq_fixes.patch); the failed records it replaces
#      go to results/failed_replaced.<host>.json;
#   2. Table 2's 50 X-Bench runs with the full-size KVBench specs that follow the KVBench paper
#      (R1.O8-R2.O2_keyset-selection/kvbench-paper-full-fixed-vec-hashset/).
cd ${REPO_ROOT} || exit 1
F=data/revision/fig7-corrected
T=data/revision/R1.O8-R2.O2_keyset-selection/kvbench-paper-full-fixed-vec-hashset
python3 -u run_scripts/run_fig7_corrected.py --only YCSB-E/KV-Bench --redo-failed 2>&1 | tee -a $F/console.log
echo "$(date -u '+%F %T') YCSB-E/KV-Bench rerun exit ${PIPESTATUS[0]}" >> $F/DONE
python3 -u run_scripts/run_keyset_fixed_queue.py --out-dir ${REPO_ROOT}/$T 2>&1 | tee -a $T/logs/console.$(hostname).out
echo "$(date -u '+%F %T') exit ${PIPESTATUS[0]}" >> $T/DONE
