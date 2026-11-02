#!/bin/bash
# 2026-11-02 (authors): Table 2's X-Bench runs again with the KVBench I-V specs that follow the KVBench paper at 1/20
# (run_scripts/make_kvbench_paper_specs.py), on fixedkeyset-2 once the corrected Fig. 7 there has finished, so that
# nothing runs next to it:
#   1. waits for data/revision/fig7-corrected/DONE on fixedkeyset-2 and its tmux session `fig7` to end;
#   2. checks that fixedkeyset-2 is idle (no tectonic-cli, KV-Bench load_gen, java or experiment script);
#   3. copies run_keyset_fixed_queue.py and the new folder (its queue) there, checks the specs are the local ones;
#   4. starts run_keyset_fixed_queue.py --out-dir <folder> in tmux `keyset-kvbench-paper`.
# Usage (local, in tmux): bash run_scripts/start_table2_paper_after_fig7.sh
set -u
R=/home/cc/Tectonic
EXP=data/revision/R1.O8-R2.O2_keyset-selection/kvbench-paper-1-20-fixed-vec-hashset
LOG=$R/data/revision/table2-paper-watcher.log
H=fixedkeyset2
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"
log() { echo "$(date -u '+%F %T') $*" >> $LOG; }

log "waiting for the corrected Fig. 7 on $H"
until $SSH $H "test -f $R/data/revision/fig7-corrected/DONE && ! tmux has-session -t fig7 2>/dev/null"; do sleep 300; done
log "Fig. 7 finished: $($SSH $H "cat $R/data/revision/fig7-corrected/DONE")"

n=$($SSH $H "ps -eo args | grep -cE '^(/home/cc/Tectonic/target/release/tectonic-cli|/home/cc/KV-WorkloadGenerator/bin/load_gen|java |python3 -u run_scripts/run_)'")
if [ "$n" != 0 ]; then log "ERROR: $H is not idle ($n processes); not started"; exit 1; fi

rsync -a $R/run_scripts/run_keyset_fixed_queue.py $H:$R/run_scripts/ && rsync -a $R/$EXP/ $H:$R/$EXP/ || { log "ERROR: copy failed"; exit 1; }
for w in i ii iii iv v; do
  f=example-specs/kvbench/$w.spec.json
  if [ "$(md5sum < $R/$f)" != "$($SSH $H "md5sum < $R/$f")" ]; then log "ERROR: $f differs on $H; not started"; exit 1; fi
done
$SSH $H "cd $R && tmux new-session -d -s keyset-kvbench-paper \"python3 -u run_scripts/run_keyset_fixed_queue.py --out-dir $R/$EXP 2>&1 | tee -a $R/$EXP/logs/console.\$(hostname).out\"" \
  && log "started run_keyset_fixed_queue.py on $H (tmux keyset-kvbench-paper)" || log "ERROR: start failed"
