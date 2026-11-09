#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 2026-10-31 (authors): the execute stage of R1.O7 or of R1.O9-R2.O1 again with RocksDB direct I/O, same traces,
# into direct-io-enabled/ of the experiment, then its report. One experiment per machine:
#   bash run_scripts/run_direct_io_reruns.sh r1o7       (fixedkeyset-1)
#   bash run_scripts/run_direct_io_reruns.sh mixgraph   (fixedkeyset-2)
# Refuses to start if anything else uses the machine (one run per machine).
cd ${REPO_ROOT} || exit 1
case "$1" in
  r1o7) script=run_scripts/run_ycsb_derivatives_fidelity.py; exp=R1.O7_ycsb-derivatives ;;
  mixgraph) script=run_scripts/run_mixgraph_fidelity.py; exp=R1.O9-R2.O1_mixgraph ;;
  *) echo "usage: $0 r1o7|mixgraph"; exit 1 ;;
esac
done_file=data/revision/$exp/direct-io-enabled/DONE
if pgrep -x xbench-cli >/dev/null || pgrep -f "run_keyset_fixed_queue|run_ycsb_derivatives_fidelity|run_mixgraph_fidelity|db_bench" >/dev/null; then
  echo "$(date -u '+%F %T') machine busy: not starting"; pgrep -af "xbench-cli|run_keyset|fidelity|db_bench"; exit 1
fi
python3 -u $script --stage execute --direct-io && python3 -u $script --stage report --direct-io
status=$?
echo "$(date -u '+%F %T') $1 exit $status" >> "$done_file"
