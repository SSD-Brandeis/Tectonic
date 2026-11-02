#!/bin/bash
# 2026-11-02 (authors): Table 3 on revision-2, every column with the same machine and X-Bench binary 24f2dddf:
#   0. checks that revision-2 is idle, keeps its binary as bin/tectonic-cli-<sha8>, installs bin/tectonic-cli-24f2dddf
#      as target/release/tectonic-cli (the path the runners use), checking its sha256;
#   1. X-Bench, Algorithm 1 vs. EmptyKeySet, YCSB A-F, 5 trials each (ycsb-fixed-empty-24f2dddf/, queue/revision-2.txt);
#   2. YCSB's generator, A-F, 5 trials (ycsb-generator-table3/).
# Each step appends its exit status to DONE in its folder. Usage (on revision-2, in tmux table3-revision2).
set -u
R=/home/cc/Tectonic
cd $R || exit 1
K=data/revision/R1.O8-R2.O2_keyset-selection
NEW_SHA=24f2dddfd753e6f38d6c8352b7dd7e36fcb37456d0a6ca5ab73d21ec2ebb9aa4
n=$(ps -eo args | grep -cE '^(/home/cc/Tectonic/target/release/tectonic-cli|java |python3 -u run_scripts/run_|bash /home/cc/diag)')
if [ "$n" != 0 ]; then echo "ERROR: not idle ($n processes)"; exit 1; fi
old=$(sha256sum target/release/tectonic-cli | cut -d' ' -f1)
mkdir -p bin && cp -n target/release/tectonic-cli bin/tectonic-cli-${old:0:8}
if [ "$(sha256sum < bin/tectonic-cli-24f2dddf | cut -d' ' -f1)" != "$NEW_SHA" ]; then echo "ERROR: bin/tectonic-cli-24f2dddf sha256 differs"; exit 1; fi
install -m 775 bin/tectonic-cli-24f2dddf target/release/tectonic-cli
if [ "$(sha256sum < target/release/tectonic-cli | cut -d' ' -f1)" != "$NEW_SHA" ]; then echo "ERROR: install failed"; exit 1; fi
echo "$(date -u '+%F %T') installed 24f2dddf (old ${old:0:8} kept as bin/tectonic-cli-${old:0:8})"
X=$K/ycsb-fixed-empty-24f2dddf
python3 -u run_scripts/run_keyset_fixed_queue.py --out-dir $R/$X 2>&1 | tee -a $X/logs/console.$(hostname).out
echo "$(date -u '+%F %T') exit ${PIPESTATUS[0]}" >> $X/DONE
Y=$K/ycsb-generator-table3
python3 -u run_scripts/run_fig7_corrected.py --only YCSB-A/YCSB YCSB-B/YCSB YCSB-C/YCSB YCSB-D/YCSB YCSB-E/YCSB YCSB-F/YCSB \
  --out-dir $R/$Y 2>&1 | tee -a $Y/logs/console.$(hostname).out
echo "$(date -u '+%F %T') exit ${PIPESTATUS[0]}" >> $Y/DONE
