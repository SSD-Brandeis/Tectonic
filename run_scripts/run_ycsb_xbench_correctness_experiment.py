#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import shutil
import argparse
import collections

# Paths
TECTONIC_CLI = "/home/cc/Tectonic/target/release/tectonic-cli"
HARNESS_DIR  = "/home/cc/Tectonic/rocksdb-benchmark-harness"
YCSB_DIR     = f"{HARNESS_DIR}/vendor/YCSB"
M2           = "/home/cc/.m2/repository"
TECTONIC_SPEC = "/home/cc/Tectonic/example-specs/ycsb/b.spec.json"

YCSB_CP = ":".join([
    f"{YCSB_DIR}/file/conf",
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar",
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar",
])

DATA_DIR = "/home/cc/Tectonic/data/ycsb_tectonic_correctness"
os.makedirs(DATA_DIR, exist_ok=True)

def run_cmd(cmd, cwd=None):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"  [ERROR] exit={res.returncode}")
        if res.stderr:
            print(res.stderr[:1000])
        sys.exit(1)
    return res

def count_trace_ops(path):
    counts = {"I": 0, "P": 0, "U": 0}
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            op = parts[0]
            if op in counts:
                counts[op] += 1
    return counts

def generate_ycsb(scale, base_scale):
    print(f"\n--- Generating YCSB Workload B (scale={scale}x, base={base_scale}) ---")
    
    inserts = int(base_scale * scale)
    point_queries = int(base_scale * 0.95 * scale)
    updates = int(base_scale * 0.05 * scale)
    execution_ops = point_queries + updates
    
    load_part = os.path.join(DATA_DIR, "ycsb.load.part")
    run_part = os.path.join(DATA_DIR, "ycsb.run.part")
    combined = os.path.join(DATA_DIR, f"ycsb_scale_{scale}.txt")
    
    # 1. Load Phase
    run_cmd(["java", "-cp", YCSB_CP, "site.ycsb.Client",
             "-db", "site.ycsb.db.FileClient",
             "-P", "workloads/workloadb",
             "-p", f"file.output={load_part}",
             "-p", f"recordcount={inserts}",
             "-p", f"operationcount={inserts}",
             "-load"], cwd=YCSB_DIR)
             
    # 2. Run Phase
    run_cmd(["java", "-cp", YCSB_CP, "site.ycsb.Client",
             "-db", "site.ycsb.db.FileClient",
             "-P", "workloads/workloadb",
             "-p", f"file.output={run_part}",
             "-p", f"recordcount={inserts}",
             "-p", f"operationcount={execution_ops}",
             "-t"], cwd=YCSB_DIR)
             
    # Combine
    with open(combined, "w") as out_f:
        for part in [load_part, run_part]:
            if os.path.exists(part):
                with open(part) as inf:
                    shutil.copyfileobj(inf, out_f)
                os.remove(part)
                
    counts = count_trace_ops(combined)
    os.remove(combined) # Clean up to save space
    return counts

def generate_tectonic(scale, base_scale):
    print(f"\n--- Generating Tectonic Workload B (scale={scale}x, base={base_scale}) ---")
    
    # Calculate scale factor relative to 1M spec
    scale_factor = (base_scale * scale) / 1_000_000.0
    out_path = os.path.join(DATA_DIR, f"tectonic_scale_{scale}.txt")
    
    run_cmd([TECTONIC_CLI, "generate",
             "-w", TECTONIC_SPEC,
             "-o", out_path,
             "-s", str(scale_factor)])
             
    counts = count_trace_ops(out_path)
    os.remove(out_path) # Clean up to save space
    return counts

def run_experiment_group(scales, base_scale):
    group_runs = []
    for scale in scales:
        expected_inserts = int(base_scale * scale)
        expected_queries = int(base_scale * 0.95 * scale)
        expected_updates = int(base_scale * 0.05 * scale)
        
        ycsb_counts = generate_ycsb(scale, base_scale)
        tectonic_counts = generate_tectonic(scale, base_scale)
        
        run_data = {
            "scale": scale,
            "expected": {
                "I": expected_inserts,
                "P": expected_queries,
                "U": expected_updates
            },
            "ycsb": ycsb_counts,
            "tectonic": tectonic_counts
        }
        group_runs.append(run_data)
        
        # Log to console
        print(f"Scale {scale}x:")
        print(f"  Expected - Inserts: {expected_inserts}, Queries: {expected_queries}, Updates: {expected_updates}")
        print(f"  YCSB     - Inserts: {ycsb_counts['I']}, Queries: {ycsb_counts['P']}, Updates: {ycsb_counts['U']}")
        print(f"  Tectonic - Inserts: {tectonic_counts['I']}, Queries: {tectonic_counts['P']}, Updates: {tectonic_counts['U']}")
    return group_runs

def main():
    parser = argparse.ArgumentParser(description="Run YCSB vs Tectonic Correctness Experiment")
    parser.add_argument("--base-scale", type=int, default=10000, help="Base scale for 1x in number of operations")
    args = parser.parse_args()
    
    large_scales = [0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
    small_scales = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    
    print("=== Running Large Scale Experiment Group ===")
    large_runs = run_experiment_group(large_scales, args.base_scale)
    
    print("\n=== Running Small Scale Experiment Group ===")
    small_runs = run_experiment_group(small_scales, args.base_scale)
    
    results = {
        "base_scale": args.base_scale,
        "large_scale_runs": large_runs,
        "small_scale_runs": small_runs
    }
        
    results_file = os.path.join(DATA_DIR, "results.json")
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved all results to {results_file}")

if __name__ == "__main__":
    main()
