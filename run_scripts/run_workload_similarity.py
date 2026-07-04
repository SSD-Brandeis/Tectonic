#!/usr/bin/env python3
"""
Workload Similarity Correctness Experiment
==========================================
Reproduces Fig. 8 from the Tectonic paper.

Goal: prove that YCSB and Tectonic generate comparable YCSB Workload A traces
when run on the same RocksDB configuration.

Stages (clearly separated):
  1. WORKLOAD GENERATION  - produce two trace files (.txt) from each generator
  2. WORKLOAD EXECUTION   - replay each trace through the same C++ RocksDB harness
  3. METRIC CAPTURE       - parse per-operation latency (insert, point query, update),
                            cache hit/miss counts, and RocksDB I/O statistics
  4. COMPARISON           - print a side-by-side table and save results to JSON
"""

import os
import sys
import subprocess
import json
import shutil

# ── Paths ─────────────────────────────────────────────────────────────────────
HARNESS_DIR  = "/home/cc/Tectonic/rocksdb-benchmark-harness"
TECTONIC_CLI = "/home/cc/Tectonic/target/release/tectonic-cli"

# C++ harness with STATS enabled (writes per-op latency CSV + perf context)
HARNESS_STATS = f"{HARNESS_DIR}/cmake-build-release-with-stats/rocksdb-benchmark-harness"
ROCKSDB_OPTS  = f"{HARNESS_DIR}/experiments/workload-similarity/rocksdb-options.ini"

# Tectonic spec file – the same one used in the paper's similarity experiment.
# Encodes YCSB A: inserts in load phase; 50% point_queries + 50% updates in run phase.
# Key format: segmented "usertable:user<19-digit numeric>" (identical to YCSB).
# Selection distribution: Beta(alpha=0.15, beta=0.55) which matches YCSB's Zipfian.
TECTONIC_SPEC = f"{HARNESS_DIR}/experiments/workload-similarity/workload-a.spec.json"

# YCSB Java classpath
M2       = os.path.expanduser("~/.m2/repository")
YCSB_DIR = f"{HARNESS_DIR}/vendor/YCSB"
YCSB_CP  = ":".join([
    f"{YCSB_DIR}/file/conf",
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar",
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar",
])

OUT_DIR = "/home/cc/Tectonic/data/workload_similarity_small"
os.makedirs(OUT_DIR, exist_ok=True)
DB_DIR = "/tmp/rocksdb-similarity-test"

# ── Scale ─────────────────────────────────────────────────────────────────────
# Small scale: 50,000 record load + 50,000 operation run (25k reads + 25k updates)
RECORD_COUNT    = 50_000
OPERATION_COUNT = 50_000
TECTONIC_SCALE  = RECORD_COUNT / 1_000_000  # spec base is 1M ops

# ── Helpers ───────────────────────────────────────────────────────────────────
def run(cmd, cwd=None, env=None, capture=False):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(
        cmd, cwd=cwd, env=env,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [ERROR] exit code {result.returncode}", file=sys.stderr)
        if capture and result.stderr:
            print(result.stderr[:2000], file=sys.stderr)
        sys.exit(1)
    return result

def reset_db():
    if os.path.exists(DB_DIR):
        shutil.rmtree(DB_DIR)
    os.makedirs(DB_DIR, exist_ok=True)

# ── Stage 1a: YCSB Workload Generation ───────────────────────────────────────
def generate_ycsb(label):
    print(f"\n{'='*65}")
    print(f" STAGE 1a – Generate YCSB Workload A trace")
    print(f"   Generator       : YCSB FileClient (Java)")
    print(f"   Workload        : workloada (50% reads + 50% updates, Zipfian)")
    print(f"   Record count    : {RECORD_COUNT:,}  (load phase: sequential inserts)")
    print(f"   Operation count : {OPERATION_COUNT:,}  (run phase: reads + updates)")
    print(f"   Output format   : text trace  I<key><val> / P<key> / U<key><val>")
    print(f"{'='*65}")

    load_part = f"{OUT_DIR}/ycsb-{label}-load.part"
    run_part  = f"{OUT_DIR}/ycsb-{label}-run.part"
    trace     = f"{OUT_DIR}/ycsb-{label}.txt"

    # Load phase
    run(["java", "-cp", YCSB_CP, "site.ycsb.Client",
         "-db", "site.ycsb.db.FileClient",
         "-P", "workloads/workloada",
         "-p", f"file.output={load_part}",
         "-p", f"recordcount={RECORD_COUNT}",
         "-p", f"operationcount={OPERATION_COUNT}",
         "-load"], cwd=YCSB_DIR)

    # Run phase
    run(["java", "-cp", YCSB_CP, "site.ycsb.Client",
         "-db", "site.ycsb.db.FileClient",
         "-P", "workloads/workloada",
         "-p", f"file.output={run_part}",
         "-p", f"recordcount={RECORD_COUNT}",
         "-p", f"operationcount={OPERATION_COUNT}",
         "-t"], cwd=YCSB_DIR)

    with open(trace, "w") as out_f:
        for part in [load_part, run_part]:
            with open(part) as in_f:
                shutil.copyfileobj(in_f, out_f)
    os.remove(load_part)
    os.remove(run_part)

    lines = sum(1 for _ in open(trace))
    print(f"  → Trace: {trace}  ({lines:,} lines)")
    return trace

# ── Stage 1b: Tectonic Workload Generation ────────────────────────────────────
def generate_tectonic(label):
    print(f"\n{'='*65}")
    print(f" STAGE 1b – Generate Tectonic Workload A trace")
    print(f"   Generator       : tectonic-cli generate")
    print(f"   Spec file       : {TECTONIC_SPEC}")
    print(f"   Scale factor    : {TECTONIC_SCALE} (= {RECORD_COUNT:,} / 1,000,000)")
    print(f"   Spec encodes    : inserts (load) + point_queries + updates (run),")
    print(f"                     key format usertable:user<19-digit-numeric>,")
    print(f"                     selection: Beta(0.15, 0.55) ≈ YCSB Zipfian(0.99)")
    print(f"   Output format   : text trace  I<key><val> / P<key> / U<key><val>")
    print(f"{'='*65}")

    trace = f"{OUT_DIR}/tectonic-{label}.txt"

    run([TECTONIC_CLI, "generate",
         "-w", TECTONIC_SPEC,
         "-o", trace,
         "-s", str(TECTONIC_SCALE)])

    lines = sum(1 for _ in open(trace))
    print(f"  → Trace: {trace}  ({lines:,} lines)")
    return trace

# ── Inspect trace ─────────────────────────────────────────────────────────────
def inspect_trace(path, name):
    counts = {}
    with open(path) as f:
        for line in f:
            op = line.split(" ", 1)[0]
            counts[op] = counts.get(op, 0) + 1
    print(f"\n  [{name}] Operation breakdown:")
    for op, c in sorted(counts.items()):
        print(f"    {op:<6} : {c:>10,}")
    return counts

# ── Stage 2+3: Execute and capture metrics ────────────────────────────────────
def run_harness(trace, label):
    print(f"\n{'='*65}")
    print(f" STAGE 2+3 – Execute {label} trace through RocksDB harness + capture metrics")
    print(f"   Harness binary  : {HARNESS_STATS}")
    print(f"   RocksDB options : {ROCKSDB_OPTS}")
    print(f"   DB path         : {DB_DIR}  (fresh directory, wiped before run)")
    print(f"   Metric capture  : per-op wall-clock latency (ns) via hrc::now(),")
    print(f"                     sorted then percentile-bucketed on exit.")
    print(f"                     Cache/IO via rocksdb::get_perf_context().")
    print(f"{'='*65}")

    reset_db()
    stats_file   = f"{OUT_DIR}/{label}-stats.txt"
    latency_file = f"{OUT_DIR}/{label}-latency.csv"

    run([HARNESS_STATS, ROCKSDB_OPTS, trace, stats_file, latency_file], cwd=DB_DIR)

    print(f"  → Stats  : {stats_file}")
    print(f"  → Latency: {latency_file}")
    return parse_results(stats_file, latency_file, label)

def parse_results(stats_file, latency_file, label):
    # Parse latency CSV
    latency = {}
    current_op = None
    if os.path.exists(latency_file):
        with open(latency_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if "," not in line:
                    current_op = line
                    latency[current_op] = {}
                else:
                    pct, val = line.split(",", 1)
                    latency[current_op][pct.strip()] = float(val.strip()) / 1000  # ns→µs

    # Parse stats file for cache + IO
    cache_hits = cache_misses = bytes_read = bytes_written = 0
    if os.path.exists(stats_file):
        with open(stats_file) as f:
            for line in f:
                line = line.strip()
                if "block_cache_hit" in line and "=" in line:
                    try: cache_hits = int(line.split("=")[1].strip().split()[0])
                    except: pass
                elif "block_cache_miss" in line and "=" in line:
                    try: cache_misses = int(line.split("=")[1].strip().split()[0])
                    except: pass
                elif line.startswith("bytes_read") and "=" in line:
                    try: bytes_read = int(line.split("=")[1].strip().split()[0])
                    except: pass
                elif line.startswith("bytes_written") and "=" in line:
                    try: bytes_written = int(line.split("=")[1].strip().split()[0])
                    except: pass

    return {"label": label, "latency_us": latency,
            "cache_hits": cache_hits, "cache_misses": cache_misses,
            "bytes_read": bytes_read, "bytes_written": bytes_written}

# ── Stage 4: Comparison ───────────────────────────────────────────────────────
def print_comparison(y, t):
    print(f"\n{'='*65}")
    print(" STAGE 4 – Side-by-side latency comparison (µs)")
    print(f"{'='*65}")

    all_ops = list(dict.fromkeys(list(y["latency_us"]) + list(t["latency_us"])))
    for op in all_ops:
        yop = y["latency_us"].get(op, {})
        top = t["latency_us"].get(op, {})
        if not yop and not top:
            continue
        print(f"\n  {op}")
        print(f"  {'Pct':<8} {'YCSB (µs)':>14} {'Tectonic (µs)':>14}")
        print(f"  {'-'*38}")
        for pct in ["p0", "p25", "p50", "p75", "p95", "p99"]:
            yv = yop.get(pct, 0.0)
            tv = top.get(pct, 0.0)
            print(f"  {pct:<8} {yv:>14.2f} {tv:>14.2f}")

    print(f"\n  {'Metric':<20} {'YCSB':>14} {'Tectonic':>14}")
    print(f"  {'-'*50}")
    for key, label in [("cache_hits","Cache hits"),("cache_misses","Cache misses"),
                        ("bytes_read","Bytes read"),("bytes_written","Bytes written")]:
        print(f"  {label:<20} {y[key]:>14,} {t[key]:>14,}")
    print()

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*65)
    print("  Workload Similarity Correctness Experiment")
    print(f"  Scale: {RECORD_COUNT:,} inserts + {OPERATION_COUNT:,} ops")
    print("="*65)

    # Stage 1: generate workload traces
    ycsb_trace = generate_ycsb("workloada")
    tec_trace  = generate_tectonic("workloada")

    print("\n── Trace inspection (operation counts) ──")
    ycsb_ops = inspect_trace(ycsb_trace, "YCSB")
    tec_ops  = inspect_trace(tec_trace,  "Tectonic")

    # Stage 2+3: execute + capture metrics
    ycsb_res = run_harness(ycsb_trace, "ycsb")
    tec_res  = run_harness(tec_trace,  "tectonic")

    # Stage 4: compare
    print_comparison(ycsb_res, tec_res)

    results = {
        "config": {"record_count": RECORD_COUNT, "operation_count": OPERATION_COUNT,
                   "tectonic_scale": TECTONIC_SCALE, "tectonic_spec": TECTONIC_SPEC,
                   "ycsb_workload": "workloada"},
        "ycsb":     {**ycsb_res, "op_counts": ycsb_ops},
        "tectonic": {**tec_res,  "op_counts": tec_ops},
    }
    out = f"{OUT_DIR}/results.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Full results saved: {out}")

if __name__ == "__main__":
    main()
