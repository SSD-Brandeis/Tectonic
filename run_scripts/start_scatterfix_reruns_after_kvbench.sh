#!/bin/bash
# 2026-11-02 (authors): local watcher. Once KV-Bench's generator run for Table 2 has finished on fixedkeyset-2
# (kvbench-generator-table2/DONE, tmux table2-kvbench-gen ended), copies the X-Bench binary 24f2dddf there
# (bin/tectonic-cli-24f2dddf; fixedkeyset-2 cannot reach fixedkeyset-1, where it was built) and starts
# run_scripts/run_scatterfix_reruns.sh in tmux scatterfix-reruns. Usage (local, in tmux): bash run_scripts/start_scatterfix_reruns_after_kvbench.sh
set -u
R=/home/cc/Tectonic
H=fixedkeyset2
G=data/revision/R1.O8-R2.O2_keyset-selection/kvbench-generator-table2
LOG=$R/data/revision/scatterfix-reruns-watcher.log
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"
NEW_SHA=24f2dddfd753e6f38d6c8352b7dd7e36fcb37456d0a6ca5ab73d21ec2ebb9aa4
log() { echo "$(date -u '+%F %T') $*" >> $LOG; }
log "waiting for $G/DONE on $H"
until $SSH $H "test -f $R/$G/DONE && ! tmux has-session -t table2-kvbench-gen 2>/dev/null"; do sleep 120; done
log "KV-Bench run finished: $($SSH $H "cat $R/$G/DONE")"
rsync -a $R/bin/tectonic-cli-24f2dddf $H:$R/bin/ || { log "ERROR: binary copy failed"; exit 1; }
if [ "$($SSH $H "sha256sum < $R/bin/tectonic-cli-24f2dddf" | cut -d' ' -f1)" != "$NEW_SHA" ]; then log "ERROR: sha256 on $H differs"; exit 1; fi
$SSH $H "cd $R && tmux new-session -d -s scatterfix-reruns 'bash run_scripts/run_scatterfix_reruns.sh'" \
  && log "started run_scatterfix_reruns.sh on $H (tmux scatterfix-reruns)" || log "ERROR: start failed"
