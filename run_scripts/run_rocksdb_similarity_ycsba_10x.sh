#!/bin/bash
# run_rocksdb_similarity_ycsba_10x.sh
#
# 10x-scale version of the RocksDB workload similarity experiment (YCSB-A vs
# X-Bench) at 10x the base op counts (10M insert / 5M point_query / 5M update
# = 20M total ops per system), using the same beta-distribution
# workload-a.spec.json as the 1x sanity check, scaled via tectonic-cli's -s
# flag.
#
# Single combined pass per generator: one workload generation, one
# stats-enabled harness invocation wrapped in iostat, capturing throughput
# (disk read/write bytes over time), per-op latency, RocksDB internal stats,
# and the RocksDB LOG file all from the same run.
#
# Outputs to: data/rocksdb_similarity_ycsba_10x/
#
# Required binaries (built by setup):
#   rocksdb-benchmark-harness/cmake-build-release-with-stats/rocksdb-benchmark-harness
#   target/release/tectonic-cli
#   rocksdb-benchmark-harness/vendor/YCSB  (built with mvn)

set -euo pipefail

OP_COUNT=10000000
TECTONIC_SCALE=10

# Resolve absolute paths from Tectonic repo root
REPO_ROOT=$(realpath "$(dirname "$0")/..")
HARNESS_DIR="${REPO_ROOT}/rocksdb-benchmark-harness"
EXPERIMENT_PATH="${HARNESS_DIR}/experiments/workload-similarity"
OUT="${REPO_ROOT}/data/rocksdb_similarity_ycsba_10x"
TECTONIC_CLI="${REPO_ROOT}/target/release/tectonic-cli"
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

HARNESS_STATS="${HARNESS_DIR}/cmake-build-release-with-stats/rocksdb-benchmark-harness"
ROCKSDB_OPTS="${EXPERIMENT_PATH}/rocksdb-options.ini"
WORKLOAD_SPEC="${EXPERIMENT_PATH}/workload-a.spec.json"

# --------------------------------------------------------------------------
# Sanity checks
# --------------------------------------------------------------------------
for bin in "$TECTONIC_CLI" "$HARNESS_STATS"; do
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
function generate_tectonic_workload() {
  local out_file="$1"
  echo "  [tectonic] generating workload (scale ${TECTONIC_SCALE}x) -> ${out_file}"
  "${TECTONIC_CLI}" generate \
    -w "${WORKLOAD_SPEC}" \
    -s "${TECTONIC_SCALE}" \
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
# Single combined pass per generator: iostat + stats + latency + LOG
# --------------------------------------------------------------------------
cd "${HARNESS_DIR}"

echo "===== [Tectonic] generate + run (combined) ====="
generate_tectonic_workload /tmp/tec-workload-a-10x.txt
cp /tmp/tec-workload-a-10x.txt "${OUT}/tectonic-workload.txt"

iostat -d -c -y 1 -o JSON > "${OUT}/iostat.tectonic.1.json" &
IOSTAT_PID=$!
sync && sudo sysctl -w vm.drop_caches=3 2>/dev/null || true

"${HARNESS_STATS}" \
  "${ROCKSDB_OPTS}" \
  /tmp/tec-workload-a-10x.txt \
  "${OUT}/stats.tectonic.1.json" \
  "${OUT}/op-latency.tectonic.1.json" \
  "${OUT}/op-latency-raw.tectonic.1.csv" \
  "${OUT}/rocksdb-LOG.tectonic.1.txt"

kill -INT "${IOSTAT_PID}" 2>/dev/null || true
wait "${IOSTAT_PID}" 2>/dev/null || true
rm -f /tmp/tec-workload-a-10x.txt

echo ""
echo "===== [YCSB] generate + run (combined) ====="
generate_ycsb_workload /tmp/ycsb-workload-a-10x.txt
cp /tmp/ycsb-workload-a-10x.txt "${OUT}/ycsb-workload.txt"

iostat -d -c -y 1 -o JSON > "${OUT}/iostat.ycsb.1.json" &
IOSTAT_PID=$!
sync && sudo sysctl -w vm.drop_caches=3 2>/dev/null || true

"${HARNESS_STATS}" \
  "${ROCKSDB_OPTS}" \
  /tmp/ycsb-workload-a-10x.txt \
  "${OUT}/stats.ycsb.1.json" \
  "${OUT}/op-latency.ycsb.1.json" \
  "${OUT}/op-latency-raw.ycsb.1.csv" \
  "${OUT}/rocksdb-LOG.ycsb.1.txt"

kill -INT "${IOSTAT_PID}" 2>/dev/null || true
wait "${IOSTAT_PID}" 2>/dev/null || true
rm -f /tmp/ycsb-workload-a-10x.txt

# --------------------------------------------------------------------------
# Verification summary
# --------------------------------------------------------------------------
echo ""
echo "===== Verification ====="
for tag in tectonic ycsb; do
  csv="${OUT}/op-latency-raw.${tag}.1.csv"
  if [[ -f "${csv}" ]]; then
    header=$(head -1 "${csv}")
    rows=$(( $(wc -l < "${csv}") - 1 ))
    echo "  ${csv##*/}: header='${header}', rows=${rows}"
  else
    echo "  MISSING: ${csv}" >&2
  fi
done

echo ""
echo "All output files in: ${OUT}"
ls "${OUT}"
