#!/usr/bin/env python3
"""
YCSB Workload A Similarity Experiment — RocksDB Harness
=========================================================
Stages:
  1. Generate YCSB Workload A trace   → print first 10 lines to log
  2. Generate X-Bench Workload A trace → print first 10 lines to log
  3. Compare the two workloads        → op counts, key format, sample diff
  4. Execute YCSB trace on RocksDB    → via C++ rocksdb-benchmark-harness (stats build)
  5. Execute X-Bench trace on RocksDB → same binary, same options, fresh DB
  6. Compare performance results      → latency percentiles (insert, point query, update)
"""
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])

import os, sys, subprocess, shutil, json, collections

# ── Paths ──────────────────────────────────────────────────────────────────
HARNESS_DIR   = REPO_ROOT + "/rocksdb-benchmark-harness"
XBENCH_CLI  = REPO_ROOT + "/target/release/xbench-cli"
YCSB_DIR      = f"{HARNESS_DIR}/vendor/YCSB"
M2            = "/home/cc/.m2/repository"

# C++ RocksDB benchmark harness (stats-enabled build)
# Accepts: <rocksdb-options> <workload-file> <stats-out> <latency-csv-out>
# Opens DB at ./db relative to cwd; records per-op nanosecond latency via hrc::now()
HARNESS_BIN   = f"{HARNESS_DIR}/cmake-build-release-with-stats/rocksdb-benchmark-harness"
ROCKSDB_OPTS  = f"{HARNESS_DIR}/experiments/workload-similarity/rocksdb-options.ini"

# X-Bench spec: encodes YCSB Workload A (50% point_queries + 50% updates)
# using Beta(0.15, 0.55) key selection ≈ Zipfian(0.99), same key format as YCSB
XBENCH_SPEC = f"{HARNESS_DIR}/experiments/workload-similarity/workload-a.spec.json"

YCSB_CP = ":".join([
    f"{YCSB_DIR}/file/conf",
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar",
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar",
])

OUT_DIR = REPO_ROOT + "/data/workload_similarity_rocksdb"
os.makedirs(OUT_DIR, exist_ok=True)
DB_DIR = "/tmp/rocksdb-similarity"

# ── Scale ──────────────────────────────────────────────────────────────────
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--scale", type=int, default=50_000,
                    help="Number of records (load) and operations (run). Default: 50000")
args = parser.parse_args()
SCALE = args.scale
XBENCH_SCALE_FACTOR = SCALE / 1_000_000.0   # spec base = 1M ops

# ── Helpers ────────────────────────────────────────────────────────────────
SEP = "=" * 65

def banner(stage, title):
    print(f"\n{SEP}")
    print(f"  {stage}: {title}")
    print(SEP)
    sys.stdout.flush()

def run(cmd, cwd=None, capture=False):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    sys.stdout.flush()
    r = subprocess.run(cmd, cwd=cwd,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None,
                       text=True)
    if r.returncode != 0:
        print(f"  [ERROR] exit={r.returncode}", file=sys.stderr)
        if capture and r.stderr:
            print(r.stderr[:1000], file=sys.stderr)
        sys.exit(1)
    return r

def print_workload_sample(path, label, n=10):
    """Print the first n lines of a workload trace to the log."""
    print(f"\n  ── {label}: first {n} lines of {path} ──")
    with open(path) as f:
        for i, line in enumerate(f):
            if i >= n:
                break
            print(f"  {line}", end="")
    print()

def count_ops(path):
    c = collections.Counter()
    with open(path) as f:
        for line in f:
            op = line.split(" ", 1)[0]
            c[op] += 1
    return c

def reset_db():
    if os.path.exists(DB_DIR):
        shutil.rmtree(DB_DIR)
    os.makedirs(DB_DIR, exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — Generate YCSB Workload A trace
# ══════════════════════════════════════════════════════════════════════════════
banner("STAGE 1", f"Generate YCSB Workload A  (scale={SCALE:,})")
print(f"  Generator     : YCSB FileClient (Java site.ycsb.Client)")
print(f"  Workload spec : workloads/workloada")
print(f"  Configuration : recordcount={SCALE:,}, operationcount={SCALE:,}")
print(f"  Load phase    : {SCALE:,} sequential inserts  (I usertable:user<key> <value>)")
print(f"  Run phase     : {SCALE:,} ops  — 50% reads (P) + 50% updates (U), Zipfian distribution")
print(f"  Key format    : usertable:user<19-digit-numeric>")
print(f"  Output file   : {OUT_DIR}/ycsb-workload-a.txt")

ycsb_load_part = f"{OUT_DIR}/ycsb-load.part"
ycsb_run_part  = f"{OUT_DIR}/ycsb-run.part"
YCSB_TRACE     = f"{OUT_DIR}/ycsb-workload-a.txt"

print(f"\n  [1a] Load phase — inserting {SCALE:,} records:")
run(["java", "-cp", YCSB_CP, "site.ycsb.Client",
     "-db", "site.ycsb.db.FileClient",
     "-P", "workloads/workloada",
     "-p", f"file.output={ycsb_load_part}",
     "-p", f"recordcount={SCALE}",
     "-p", f"operationcount={SCALE}",
     "-load"], cwd=YCSB_DIR)

print(f"\n  [1b] Run phase — {SCALE:,} reads + updates (Zipfian):")
run(["java", "-cp", YCSB_CP, "site.ycsb.Client",
     "-db", "site.ycsb.db.FileClient",
     "-P", "workloads/workloada",
     "-p", f"file.output={ycsb_run_part}",
     "-p", f"recordcount={SCALE}",
     "-p", f"operationcount={SCALE}",
     "-t"], cwd=YCSB_DIR)

# Concatenate load + run into single trace
with open(YCSB_TRACE, "w") as out_f:
    for part in [ycsb_load_part, ycsb_run_part]:
        with open(part) as inf:
            shutil.copyfileobj(inf, out_f)
os.remove(ycsb_load_part)
os.remove(ycsb_run_part)

ycsb_lines = sum(1 for _ in open(YCSB_TRACE))
print(f"\n  → YCSB trace written: {YCSB_TRACE}  ({ycsb_lines:,} total lines)")
print_workload_sample(YCSB_TRACE, "YCSB trace")

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — Generate X-Bench Workload A trace
# ══════════════════════════════════════════════════════════════════════════════
banner("STAGE 2", f"Generate X-Bench Workload A  (scale={SCALE:,})")
print(f"  Generator     : xbench-cli generate")
print(f"  Spec file     : {XBENCH_SPEC}")
print(f"  Scale factor  : {XBENCH_SCALE_FACTOR}  (= {SCALE:,} / 1,000,000)")
print(f"  Load phase    : {SCALE:,} inserts — key: usertable:user<19-digit-numeric>")
print(f"  Run phase     : {SCALE//2:,} point_queries + {SCALE//2:,} updates")
print(f"                  key selection: Beta(alpha=0.15, beta=0.55) ≈ Zipfian(0.99)")
print(f"  Output file   : {OUT_DIR}/xbench-workload-a.txt")

XBENCH_TRACE = f"{OUT_DIR}/xbench-workload-a.txt"

run([XBENCH_CLI, "generate",
     "-w", XBENCH_SPEC,
     "-o", XBENCH_TRACE,
     "-s", str(XBENCH_SCALE_FACTOR)])

x_lines = sum(1 for _ in open(XBENCH_TRACE))
print(f"\n  → X-Bench trace written: {XBENCH_TRACE}  ({x_lines:,} total lines)")
print_workload_sample(XBENCH_TRACE, "X-Bench trace")

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — Compare the two workloads
# ══════════════════════════════════════════════════════════════════════════════
banner("STAGE 3", "Compare YCSB vs X-Bench workload traces")

ycsb_ops = count_ops(YCSB_TRACE)
x_ops  = count_ops(XBENCH_TRACE)

print(f"\n  {'Operation':<12} {'YCSB':>12} {'X-Bench':>12}  meaning")
print(f"  {'-'*55}")
print(f"  {'I (insert)':<12} {ycsb_ops.get('I',0):>12,} {x_ops.get('I',0):>12,}  db->Put (load phase)")
print(f"  {'P (point Q)':<12} {ycsb_ops.get('P',0):>12,} {x_ops.get('P',0):>12,}  db->Get (valid key, Zipfian)")
print(f"  {'U (update)':<12} {ycsb_ops.get('U',0):>12,} {x_ops.get('U',0):>12,}  db->Put (existing key)")
print(f"  {'Total':<12} {ycsb_lines:>12,} {x_lines:>12,}")

# Key format check
print(f"\n  Key format verification (first insert key from each trace):")
def first_key(path):
    with open(path) as f:
        for line in f:
            if line.startswith("I "):
                return line.split(" ", 2)[1]
    return "?"
print(f"    YCSB     : {first_key(YCSB_TRACE)}")
print(f"    X-Bench : {first_key(XBENCH_TRACE)}")
print(f"\n  Value size check (first insert value length from each trace):")
def first_val_len(path):
    with open(path) as f:
        for line in f:
            if line.startswith("I "):
                parts = line.split(" ", 2)
                return len(parts[2].rstrip()) if len(parts) == 3 else 0
    return 0
print(f"    YCSB     : {first_val_len(YCSB_TRACE)} bytes")
print(f"    X-Bench : {first_val_len(XBENCH_TRACE)} bytes")

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — Execute YCSB trace on RocksDB (C++ harness)
# ══════════════════════════════════════════════════════════════════════════════
banner("STAGE 4", "Execute YCSB trace on RocksDB  [harness: stats build]")
print(f"  Binary     : {HARNESS_BIN}")
print(f"  DB options : {ROCKSDB_OPTS}")
print(f"  DB path    : {DB_DIR}/db  (freshly created)")
print(f"  Workload   : {YCSB_TRACE}")
print(f"  Metric capture:")
print(f"    • Per-op wall-clock latency via hrc::now() (nanoseconds)")
print(f"    • Sorted on exit → percentile buckets written to CSV")
print(f"    • RocksDB perf context + iostats context → stats text file")

reset_db()
YCSB_STATS   = f"{OUT_DIR}/ycsb-stats.txt"
YCSB_LATENCY = f"{OUT_DIR}/ycsb-latency.csv"
run([HARNESS_BIN, ROCKSDB_OPTS, YCSB_TRACE, YCSB_STATS, YCSB_LATENCY], cwd=DB_DIR)
print(f"\n  → Stats written  : {YCSB_STATS}")
print(f"  → Latency written: {YCSB_LATENCY}")
print(f"\n  Raw latency CSV (YCSB):")
with open(YCSB_LATENCY) as f:
    for line in f:
        print(f"    {line}", end="")

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — Execute X-Bench trace on RocksDB (same harness, fresh DB)
# ══════════════════════════════════════════════════════════════════════════════
banner("STAGE 5", "Execute X-Bench trace on RocksDB  [same harness, fresh DB]")
print(f"  Binary     : {HARNESS_BIN}  (identical)")
print(f"  DB options : {ROCKSDB_OPTS}  (identical)")
print(f"  DB path    : {DB_DIR}/db  (wiped and recreated)")
print(f"  Workload   : {XBENCH_TRACE}")

reset_db()
X_STATS   = f"{OUT_DIR}/xbench-stats.txt"
X_LATENCY = f"{OUT_DIR}/xbench-latency.csv"
run([HARNESS_BIN, ROCKSDB_OPTS, XBENCH_TRACE, X_STATS, X_LATENCY], cwd=DB_DIR)
print(f"\n  → Stats written  : {X_STATS}")
print(f"  → Latency written: {X_LATENCY}")
print(f"\n  Raw latency CSV (X-Bench):")
with open(X_LATENCY) as f:
    for line in f:
        print(f"    {line}", end="")

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 6 — Compare performance
# ══════════════════════════════════════════════════════════════════════════════
banner("STAGE 6", "Performance comparison: YCSB vs X-Bench on RocksDB")

def parse_latency_csv(path):
    """Returns dict: {op_name: {pct: value_us}}. Values stored in ns, converted to µs."""
    result = {}
    current = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if "," not in line:
                current = line
                result[current] = {}
            else:
                pct, val = line.split(",", 1)
                result[current][pct.strip()] = float(val.strip()) / 1000.0  # ns → µs
    return result

ycsb_lat = parse_latency_csv(YCSB_LATENCY)
x_lat  = parse_latency_csv(X_LATENCY)

PCTS = ["p0", "p25", "p50", "p75", "p95", "p99"]

all_ops = list(dict.fromkeys(list(ycsb_lat) + list(x_lat)))
for op in all_ops:
    y = ycsb_lat.get(op, {})
    t = x_lat.get(op, {})
    print(f"\n  {op}")
    print(f"  {'percentile':<10} {'YCSB (µs)':>14} {'X-Bench (µs)':>14}")
    print(f"  {'-'*40}")
    for pct in PCTS:
        yv = y.get(pct, 0.0)
        tv = t.get(pct, 0.0)
        print(f"  {pct:<10} {yv:>14.2f} {tv:>14.2f}")

# Save results JSON
results = {
    "scale": SCALE,
    "xbench_spec": XBENCH_SPEC,
    "ycsb_trace":  YCSB_TRACE,
    "xbench_trace": XBENCH_TRACE,
    "op_counts": {
        "ycsb":     {k: v for k,v in ycsb_ops.items()},
        "xbench": {k: v for k,v in x_ops.items()},
    },
    "latency_us": {
        "ycsb":     ycsb_lat,
        "xbench": x_lat,
    },
}
results_path = f"{OUT_DIR}/results.json"
with open(results_path, "w") as f:
    json.dump(results, f, indent=2)
print(f"\n  ✓ Full results saved to: {results_path}")
