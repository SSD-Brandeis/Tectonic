#!/bin/bash
# Local watcher for run_mixgraph_full_scale.sh on fixedkeyset-1 (2026-11-11): checks progress.log every 15 min and
# exits (which notifies) on a failure, or when the steps before execution are done (so they can be checked
# before the multi-day execution), or after 24 h. ssh only reads progress.log.
P=mixgraph-full/data/revision/R1.O9-R2.O1_mixgraph/full-scale/progress.log
for i in $(seq 96); do
  s=$(ssh -o BatchMode=yes -o ConnectTimeout=20 fixedkeyset1 "tail -3 $P" 2>&1)
  if echo "$s" | grep -q "FAILED"; then echo "FAILED"; echo "$s"; exit 0; fi
  if echo "$s" | grep -q -E "start execute|ALL DONE"; then echo "EXECUTION STARTED"; echo "$s"; exit 0; fi
  sleep 900
done
echo "TIMEOUT 24h"; echo "$s"
