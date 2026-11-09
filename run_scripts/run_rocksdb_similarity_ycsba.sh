#!/bin/bash
# run_rocksdb_similarity_ycsba.sh
#
# Reproduces the RocksDB workload similarity experiment (YCSB-A vs X-Bench)
# with per-operation individual latency measurements recorded in execution order.
#
# Outputs to: data/rocksdb_similarity_ycsba/
#
# Required binaries (built by setup):
#   rocksdb-benchmark-harness/cmake-build-release/rocksdb-benchmark-harness
#   rocksdb-benchmark-harness/cmake-build-release-with-stats/rocksdb-benchmark-harness
#   target/release/xbench-cli
#   rocksdb-benchmark-harness/vendor/YCSB  (built with mvn)

set -euo pipefail

RUNS=3
OP_COUNT=1000000

# Resolve absolute paths from X-Bench repo root
REPO_ROOT=$(realpath "$(dirname "$0")/..")
HARNESS_DIR="${REPO_ROOT}/rocksdb-benchmark-harness"
EXPERIMENT_PATH="${HARNESS_DIR}/experiments/workload-similarity"
OUT="${REPO_ROOT}/data/rocksdb_similarity_ycsba"
XBENCH_CLI="${REPO_ROOT}/target/release/xbench-cli"
JAVA=/usr/lib/jvm/java-17-openjdk-amd64/bin/java

YCSB_DIR="${HARNESS_DIR}/vendor/YCSB"
YCSB_JAR="${YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar"
YCSB_CORE="${YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar"
YCSB_CONF="${YCSB_DIR}/file/conf"
M2="${HOME}/.m2/repository"
YCSB_CP="${YCSB_CONF}:${YCSB_JAR}:\
${M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar:\
${M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar:\
${M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar:\
${M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar:\
${YCSB_CORE}"

HARNESS_RELEASE="${HARNESS_DIR}/cmake-build-release/rocksdb-benchmark-harness"
HARNESS_STATS="${HARNESS_DIR}/cmake-build-release-with-stats/rocksdb-benchmark-harness"
ROCKSDB_OPTS="${EXPERIMENT_PATH}/rocksdb-options.ini"
WORKLOAD_SPEC="${EXPERIMENT_PATH}/workload-a.spec.json"

# --------------------------------------------------------------------------
# Sanity checks
# --------------------------------------------------------------------------
for bin in "$XBENCH_CLI" "$HARNESS_RELEASE" "$HARNESS_STATS"; do
  if [[ ! -x "$bin" ]]; then
    echo "ERROR: binary not found or not executable: $bin" >&2
    echo "       Run the build steps first (see implementation_plan.md)" >&2
    exit 1
  fi
done
if [[ ! -f "$YCSB_JAR" ]]; then
  echo "ERROR: YCSB JAR not found: $YCSB_JAR" >&2
  echo "       Run: cd rocksdb-benchmark-harness/vendor/YCSB && mvn -pl site.ycsb:file-binding -am clean package -DskipTests" >&2
  exit 1
fi

mkdir -p "${OUT}"

# --------------------------------------------------------------------------
# Workload generators
# --------------------------------------------------------------------------
function generate_xbench_workload() {
  local out_file="$1"
  echo "  [xbench] generating workload -> ${out_file}"
  "${XBENCH_CLI}" generate \
    -w "${WORKLOAD_SPEC}" \
    -o "${out_file}"
}

function generate_ycsb_workload() {
  local out_file="$1"
  local tmp_load="${OUT}/.ycsb-load.part"
  local tmp_run="${OUT}/.ycsb-run.part"

  echo "  [ycsb] generating load phase"
  cd "${YCSB_DIR}"
  "${JAVA}" -cp "${YCSB_CP}" site.ycsb.Client \
    -db site.ycsb.db.FileClient \
    -P workloads/workloada \
    -p "file.output=${tmp_load}" \
    -p recordcount="${OP_COUNT}" \
    -p operationcount="${OP_COUNT}" \
    -load

  echo "  [ycsb] generating run phase"
  "${JAVA}" -cp "${YCSB_CP}" site.ycsb.Client \
    -db site.ycsb.db.FileClient \
    -P workloads/workloada \
    -p "file.output=${tmp_run}" \
    -p recordcount="${OP_COUNT}" \
    -p operationcount="${OP_COUNT}" \
    -t

  cat "${tmp_load}" "${tmp_run}" > "${out_file}"
  rm -f "${tmp_load}" "${tmp_run}"
  cd "${HARNESS_DIR}"
}

# --------------------------------------------------------------------------
# Phase 1: I/O throughput runs (iostat, no stats overhead)
# --------------------------------------------------------------------------
echo "===== Phase 1: I/O throughput runs (${RUNS} runs each) ====="
cd "${HARNESS_DIR}"

for i in $(seq 1 "${RUNS}"); do
  echo "--- [X-Bench] iostat run ${i}/${RUNS} ---"
  generate_xbench_workload /tmp/x-workload-a.txt

  iostat -d -c -y 1 -o JSON > "${OUT}/iostat.xbench.${i}.json" &
  IOSTAT_PID=$!
  sync && sudo sysctl -w vm.drop_caches=3 2>/dev/null || true

  "${HARNESS_RELEASE}" "${ROCKSDB_OPTS}" /tmp/x-workload-a.txt "${OUT}/rocksdb-LOG.iostat.xbench.${i}.txt"

  kill -INT "${IOSTAT_PID}" 2>/dev/null || true
  wait "${IOSTAT_PID}" 2>/dev/null || true

  echo "--- [YCSB] iostat run ${i}/${RUNS} ---"
  generate_ycsb_workload /tmp/ycsb-workload-a.txt

  iostat -d -c -y 1 -o JSON > "${OUT}/iostat.ycsb.${i}.json" &
  IOSTAT_PID=$!
  sync && sudo sysctl -w vm.drop_caches=3 2>/dev/null || true

  "${HARNESS_RELEASE}" "${ROCKSDB_OPTS}" /tmp/ycsb-workload-a.txt "${OUT}/rocksdb-LOG.iostat.ycsb.${i}.txt"

  kill -INT "${IOSTAT_PID}" 2>/dev/null || true
  wait "${IOSTAT_PID}" 2>/dev/null || true
done

# --------------------------------------------------------------------------
# Phase 2: Stats-enabled runs (per-op latency collection)
# --------------------------------------------------------------------------
echo ""
echo "===== Phase 2: Stats-enabled runs with per-op latency (${RUNS} runs each) ====="

for i in $(seq 1 "${RUNS}"); do
  echo "--- [X-Bench] stats run ${i}/${RUNS} ---"
  generate_xbench_workload /tmp/x-workload-a.txt
  # Save workload for reproducibility (last run only)
  if [[ "${i}" == "${RUNS}" ]]; then
    cp /tmp/x-workload-a.txt "${OUT}/xbench-workload.txt"
  fi

  sync && sudo sysctl -w vm.drop_caches=3 2>/dev/null || true

  "${HARNESS_STATS}" \
    "${ROCKSDB_OPTS}" \
    /tmp/x-workload-a.txt \
    "${OUT}/stats.xbench.${i}.json" \
    "${OUT}/op-latency.xbench.${i}.json" \
    "${OUT}/op-latency-raw.xbench.${i}.csv" \
    "${OUT}/rocksdb-LOG.xbench.${i}.txt"

  echo "--- [YCSB] stats run ${i}/${RUNS} ---"
  generate_ycsb_workload /tmp/ycsb-workload-a.txt
  if [[ "${i}" == "${RUNS}" ]]; then
    cp /tmp/ycsb-workload-a.txt "${OUT}/ycsb-workload.txt"
  fi

  sync && sudo sysctl -w vm.drop_caches=3 2>/dev/null || true

  "${HARNESS_STATS}" \
    "${ROCKSDB_OPTS}" \
    /tmp/ycsb-workload-a.txt \
    "${OUT}/stats.ycsb.${i}.json" \
    "${OUT}/op-latency.ycsb.${i}.json" \
    "${OUT}/op-latency-raw.ycsb.${i}.csv" \
    "${OUT}/rocksdb-LOG.ycsb.${i}.txt"
done

# --------------------------------------------------------------------------
# Verification summary
# --------------------------------------------------------------------------
echo ""
echo "===== Verification ====="
for i in $(seq 1 "${RUNS}"); do
  for tag in xbench ycsb; do
    csv="${OUT}/op-latency-raw.${tag}.${i}.csv"
    if [[ -f "${csv}" ]]; then
      header=$(head -1 "${csv}")
      rows=$(( $(wc -l < "${csv}") - 1 ))
      echo "  ${csv##*/}: header='${header}', rows=${rows}"
    else
      echo "  MISSING: ${csv}" >&2
    fi
  done
done

echo ""
echo "All output files in: ${OUT}"
ls "${OUT}"
