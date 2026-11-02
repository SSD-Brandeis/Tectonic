#!/bin/bash
# 2026-10-31 (authors): the corrected Fig. 7 run (run_fig7_corrected.py) on this machine, logging to
# data/revision/fig7-corrected/console.log and writing data/revision/fig7-corrected/DONE when it ends.
cd /home/cc/Tectonic || exit 1
D=data/revision/fig7-corrected
mkdir -p $D
python3 -u run_scripts/run_fig7_corrected.py 2>&1 | tee -a $D/console.log
echo "$(date -u '+%F %T') exit ${PIPESTATUS[0]}" >> $D/DONE
