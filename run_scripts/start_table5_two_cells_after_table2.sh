#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 2026-11-03 (authors): Table 5's two anomalous cells (Cassandra 2^19, Redis 2^18; YCSB and X-Bench, 5 trials each)
# run again on a free machine: fixedkeyset-2, once Table 2's X-Bench reruns there have finished (fixedkeyset-1 and
# revision-2 run another session's walgap experiment; the local machine is not idle). Local watcher, one step after
# the other, everything logged to data/revision/table5-two-cells-watcher.log:
#   1. waits for both Table 2 rerun DONE files on fixedkeyset-2, its tmux scatterfix-reruns to end, and the local
#      tmux scatterfix-watcher to end; checks that fixedkeyset-2 is idle;
#   2. installs docker.io 29.1.3-0ubuntu3~24.04.2 there (the local machine's version) and adds cc to the docker group;
#   3. pulls the Redis and Cassandra images by digest and checks that their image IDs are the local ones;
#   4. copies the four trace files and checksums.json to the same paths, and the harness scripts;
#   5. starts run_scripts/run_table5_two_cells.py with bin/xbench-cli-fc63df5f (the October Table 5 binary) in tmux
#      table5-two-cells (tmux -L table5): Redis 2^18, then Cassandra 2^19, into data/table-5-revision-two-cell-rerun-2026-11-03/.
# Usage (local, in tmux table5-two-cells-watcher): bash run_scripts/start_table5_two_cells_after_table2.sh
set -u
R=${REPO_ROOT}
H=fixedkeyset2
K=data/revision/R1.O8-R2.O2_keyset-selection
W=data/table-5-revision-multirun/workloads
OUT=data/table-5-revision-two-cell-rerun-2026-11-03
STAGE=/home/cc/table5-two-cells-stage
LOG=$R/data/revision/table5-two-cells-watcher.log
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"
REDIS=redis@sha256:2838d5524559494f6f1cd66e97e76b200d64a633a8614200620755ed395daf32
CASS=cassandra@sha256:b89056c366c4b807380cd9a4ac865ad558c0b4d5aac342f69087d0169ca1ddcd
log() { echo "$(date -u '+%F %T') $*" >> $LOG; }
die() { log "ERROR: $*; stopped"; exit 1; }

log "waiting for Table 2's X-Bench reruns on $H"
until $SSH $H "test -f $R/$K/kvbench-paper-full-fixed-vec-hashset-24f2dddf/DONE && test -f $R/$K/kvbench-iv-0.1x-fixed-vec-hashset-24f2dddf/DONE && ! tmux has-session -t scatterfix-reruns 2>/dev/null" \
      && ! tmux has-session -t scatterfix-watcher 2>/dev/null; do sleep 120; done
log "Table 2 reruns finished: $($SSH $H "cat $R/$K/kvbench-paper-full-fixed-vec-hashset-24f2dddf/DONE $R/$K/kvbench-iv-0.1x-fixed-vec-hashset-24f2dddf/DONE" | tr '\n' ' ')"
n=$($SSH $H "ps -eo args | grep -cE '^(${REPO_ROOT}/target/release/xbench-cli|${REPO_ROOT}/bin/xbench-cli|/home/cc/KV-WorkloadGenerator/bin/load_gen|java |python3 -u run_scripts/run_)'")
[ "$n" = 0 ] || die "$H is not idle ($n processes)"

$SSH $H "sudo apt-get install -y docker.io=29.1.3-0ubuntu3~24.04.2 >/dev/null 2>&1 && sudo usermod -aG docker cc" || die "docker install failed"
log "docker on $H: $($SSH $H 'docker --version; docker info 2>/dev/null | grep -E "Storage Driver|Docker Root|Cgroup Version" | tr "\n" " "')"

# 2026-11-03 01:19: `docker save` of a digest reference wrote no loadable image (docker load imported nothing), so
# the images are pulled by digest; with the containerd image store of both machines the image ID is that digest,
# so equal IDs mean identical images
for img in $REDIS $CASS; do
  want=$(docker image inspect --format '{{.Id}}' $img)
  $SSH $H "docker image inspect $img >/dev/null 2>&1 || timeout 900 docker pull -q $img >/dev/null" || die "docker pull $img failed"
  got=$($SSH $H "docker image inspect --format '{{.Id}}' $img")
  [ "$want" = "$got" ] || die "image $img: id $got on $H, $want locally"
  log "image $img on $H, id $got (the local id)"
done

$SSH $H "mkdir -p $R/$W $R/$OUT"
rsync -a $R/$W/ycsb_workload_a_scale_262144.txt $R/$W/xbench_workload_a_scale_262144.txt \
         $R/$W/ycsb_workload_a_scale_524288.txt $R/$W/xbench_workload_a_scale_524288.txt $R/$W/checksums.json $H:$R/$W/ || die "trace copy failed"
rsync -a $R/run_scripts/run_table5_post_fix_validation.py $R/run_scripts/run_table5_revision_multirun.py \
         $R/run_scripts/run_table5_two_cells.py $H:$R/run_scripts/ || die "script copy failed"
F="run_scripts/run_table5_post_fix_validation.py run_scripts/run_table5_revision_multirun.py run_scripts/run_table5_two_cells.py $W/checksums.json"
[ "$($SSH $H "cd $R && md5sum $F")" = "$(cd $R && md5sum $F)" ] || die "copied files differ"
$SSH $H "sha256sum $R/bin/xbench-cli-fc63df5f" | grep -q '^fc63df5fd595b995' || die "bin/xbench-cli-fc63df5f on $H is not fc63df5f"
log "traces, scripts and binary in place on $H"

C="python3 -u run_scripts/run_table5_two_cells.py --cli $R/bin/xbench-cli-fc63df5f"
# its own tmux server (-L table5): the running tmux server of fixedkeyset-2 predates the docker group
$SSH $H "cd $R && tmux -L table5 new-session -d -s table5-two-cells \"($C --db redis --scales 262144 --out-dir $R/$OUT && $C --db cassandra --scales 524288 --out-dir $R/$OUT) 2>&1 | tee -a $R/$OUT/console.log; echo \\\"\\\$(date -u '+%F %T') exit \\\${PIPESTATUS[0]}\\\" >> $R/$OUT/DONE\"" \
  && log "started table5-two-cells on $H (tmux -L table5)" || die "start failed"
