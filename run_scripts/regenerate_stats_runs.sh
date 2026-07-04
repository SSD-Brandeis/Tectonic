#!/bin/bash
set -e

STATS_DIR="/home/cc/Tectonic/data/rocksdb_similarity_ycsba"
mkdir -p "$STATS_DIR"

echo "=== Compiling rocksdb-benchmark-harness with stats ==="
cd /home/cc/Tectonic/rocksdb-benchmark-harness
cmake --build cmake-build-release-with-stats --target rocksdb-benchmark-harness -- -j"$(nproc)"

echo "=== Generating workloads ==="
# Tectonic generate
/home/cc/Tectonic/target/release/tectonic-cli generate \
  -w "/home/cc/Tectonic/example-specs/ycsb/a.spec.json" \
  -o "/tmp/tec-workload-a.txt"

# YCSB generate
YCSB_DIR="/home/cc/Tectonic/rocksdb-benchmark-harness/vendor/YCSB"
M2="/home/cc/.m2/repository"
YCSB_CP="${YCSB_DIR}/file/conf:${YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar:${M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar:${M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar:${M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar:${M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar:${YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar"

echo "Running YCSB load..."
java -cp "$YCSB_CP" site.ycsb.Client \
  -db site.ycsb.db.FileClient \
  -P "$YCSB_DIR/workloads/workloada" \
  -p "file.output=/tmp/ycsb-workload-a.1.part" \
  -p recordcount=1000000 \
  -p operationcount=1000000 \
  -load

echo "Running YCSB run..."
java -cp "$YCSB_CP" site.ycsb.Client \
  -db site.ycsb.db.FileClient \
  -P "$YCSB_DIR/workloads/workloada" \
  -p "file.output=/tmp/ycsb-workload-a.2.part" \
  -p recordcount=1000000 \
  -p operationcount=1000000 \
  -t

cat /tmp/ycsb-workload-a.1.part /tmp/ycsb-workload-a.2.part > /tmp/ycsb-workload-a.txt
rm -f /tmp/ycsb-workload-a.1.part /tmp/ycsb-workload-a.2.part

echo "=== Running Stats-enabled benchmarks ==="
RUNS=5
for i in $(seq 1 "$RUNS"); do
  echo "--- Run $i / $RUNS (Tectonic Stats) ---"
  sudo sysctl -w vm.drop_caches=3
  ./cmake-build-release-with-stats/rocksdb-benchmark-harness \
    ./experiments/workload-similarity/rocksdb-options.ini \
    /tmp/tec-workload-a.txt \
    "$STATS_DIR/stats.tectonic.$i.json" \
    "$STATS_DIR/op-latency.tectonic.$i.json"
  sleep 2

  echo "--- Run $i / $RUNS (YCSB Stats) ---"
  sudo sysctl -w vm.drop_caches=3
  ./cmake-build-release-with-stats/rocksdb-benchmark-harness \
    ./experiments/workload-similarity/rocksdb-options.ini \
    /tmp/ycsb-workload-a.txt \
    "$STATS_DIR/stats.ycsb.$i.json" \
    "$STATS_DIR/op-latency.ycsb.$i.json"
  sleep 2
done

echo "=== Cleaning up workload files ==="
rm -f /tmp/tec-workload-a.txt
rm -f /tmp/ycsb-workload-a.txt

echo "=== Finished regenerating stats! ==="
