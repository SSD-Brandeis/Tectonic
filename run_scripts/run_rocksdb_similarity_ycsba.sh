#!/bin/bash
set -e

# ================= CONFIGURATION VARIABLES =================
# Modify these to run different workloads or change the scale
WORKLOAD_SPEC="/home/cc/Tectonic/rocksdb-benchmark-harness/experiments/workload-similarity/workload-a.spec.json"
YCSB_WORKLOAD_NAME="workloada"
OP_COUNT=1000000  # 1M inserts for load, 1M operations for execution (total 2M ops)
RUNS=3            # Number of benchmark runs
# ==========================================================

STATS_DIR="/home/cc/Tectonic/data/rocksdb_similarity_ycsba"
mkdir -p "$STATS_DIR"

# Clean up previous stats files if any to avoid mixing results
rm -f "$STATS_DIR"/iostat.*.json "$STATS_DIR"/stats.*.json "$STATS_DIR"/op-latency.*.json "$STATS_DIR"/*-workload.txt

# Clean up previous workload files if any to avoid append issues
rm -f /tmp/tec-workload-a.txt /tmp/ycsb-workload-a*

echo "=== Step 1: Compiling Tectonic ==="
cd /home/cc/Tectonic
cargo build --release

echo "=== Step 2: Compiling rocksdb-benchmark-harness ==="
cd /home/cc/Tectonic/rocksdb-benchmark-harness
cmake --build cmake-build-release --target rocksdb-benchmark-harness -- -j"$(nproc)"
cmake --build cmake-build-release-with-stats --target rocksdb-benchmark-harness -- -j"$(nproc)"

echo "=== Step 3: Generating workloads ==="
# Calculate dynamic Tectonic scale factor
SCALE=$(python3 -c "print($OP_COUNT / 1000000)")
echo "Tectonic scale factor: $SCALE"

# Tectonic generate
/home/cc/Tectonic/target/release/tectonic-cli generate \
  -w "$WORKLOAD_SPEC" \
  -s "$SCALE" \
  -o "/tmp/tec-workload-a.txt"

# YCSB generate
YCSB_DIR="/home/cc/Tectonic/rocksdb-benchmark-harness/vendor/YCSB"
M2="/home/cc/.m2/repository"
YCSB_CP="${YCSB_DIR}/file/conf:${YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar:${M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar:${M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar:${M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar:${M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar:${YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar"

echo "Running YCSB load ($OP_COUNT operations)..."
java -cp "$YCSB_CP" site.ycsb.Client \
  -db site.ycsb.db.FileClient \
  -P "$YCSB_DIR/workloads/$YCSB_WORKLOAD_NAME" \
  -p "file.output=/tmp/ycsb-workload-a.1.part" \
  -p recordcount="$OP_COUNT" \
  -p operationcount="$OP_COUNT" \
  -load

echo "Running YCSB run ($OP_COUNT operations)..."
java -cp "$YCSB_CP" site.ycsb.Client \
  -db site.ycsb.db.FileClient \
  -P "$YCSB_DIR/workloads/$YCSB_WORKLOAD_NAME" \
  -p "file.output=/tmp/ycsb-workload-a.2.part" \
  -p recordcount="$OP_COUNT" \
  -p operationcount="$OP_COUNT" \
  -t

cat /tmp/ycsb-workload-a.1.part /tmp/ycsb-workload-a.2.part > /tmp/ycsb-workload-a.txt
rm -f /tmp/ycsb-workload-a.1.part /tmp/ycsb-workload-a.2.part

echo "=== Step 4: Running Standard (I/O) benchmarks with iostat ==="
for i in $(seq 1 "$RUNS"); do
  echo "--- Run $i / $RUNS (Tectonic Standard) ---"
  iostat -d -c -y 1 sda -o JSON > "$STATS_DIR/iostat.tectonic.$i.json" &
  IOSTAT_PID=$!
  sync
  sudo sysctl -w vm.drop_caches=3
  ./cmake-build-release/rocksdb-benchmark-harness \
    ./experiments/workload-similarity/rocksdb-options.ini \
    /tmp/tec-workload-a.txt
  kill -INT "$IOSTAT_PID" || true
  sleep 2

  echo "--- Run $i / $RUNS (YCSB Standard) ---"
  iostat -d -c -y 1 sda -o JSON > "$STATS_DIR/iostat.ycsb.$i.json" &
  IOSTAT_PID=$!
  sync
  sudo sysctl -w vm.drop_caches=3
  ./cmake-build-release/rocksdb-benchmark-harness \
    ./experiments/workload-similarity/rocksdb-options.ini \
    /tmp/ycsb-workload-a.txt
  kill -INT "$IOSTAT_PID" || true
  sleep 2
done

echo "=== Step 5: Running Stats-enabled (Latency & DB stats) benchmarks ==="
for i in $(seq 1 "$RUNS"); do
  echo "--- Run $i / $RUNS (Tectonic Stats) ---"
  sync
  sudo sysctl -w vm.drop_caches=3
  ./cmake-build-release-with-stats/rocksdb-benchmark-harness \
    ./experiments/workload-similarity/rocksdb-options.ini \
    /tmp/tec-workload-a.txt \
    "$STATS_DIR/stats.tectonic.$i.json" \
    "$STATS_DIR/op-latency.tectonic.$i.json"
  sleep 2

  echo "--- Run $i / $RUNS (YCSB Stats) ---"
  sync
  sudo sysctl -w vm.drop_caches=3
  ./cmake-build-release-with-stats/rocksdb-benchmark-harness \
    ./experiments/workload-similarity/rocksdb-options.ini \
    /tmp/ycsb-workload-a.txt \
    "$STATS_DIR/stats.ycsb.$i.json" \
    "$STATS_DIR/op-latency.ycsb.$i.json"
  sleep 2
done

echo "=== Saving workload files to stats directory ==="
cp /tmp/tec-workload-a.txt "$STATS_DIR/tectonic-workload.txt"
cp /tmp/ycsb-workload-a.txt "$STATS_DIR/ycsb-workload.txt"
rm -f /tmp/tec-workload-a.txt
rm -f /tmp/ycsb-workload-a.txt

echo "=== Experiment finished! All stats saved to $STATS_DIR ==="
