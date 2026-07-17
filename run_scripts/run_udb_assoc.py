#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import math
from collections import Counter

def run_cmd(cmd):
    print(f"Running: {' '.join(cmd)}")
    sys.stdout.flush()
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("Error:", res.stderr)
        sys.exit(res.returncode)
    return res.stdout

def main():
    ROOT = "/home/cc/Tectonic"
    DATA_DIR = os.path.join(ROOT, "data/udb_assoc")
    os.makedirs(DATA_DIR, exist_ok=True)
    
    spec_dir = os.path.join(ROOT, "tectonic-specs")
    preload_spec = os.path.join(spec_dir, "udb_assoc_preload.spec.json")
    runtime_spec = os.path.join(spec_dir, "udb_assoc_runtime.spec.json")
    
    # 1. Ensure spec files are generated
    if not os.path.exists(preload_spec) or not os.path.exists(runtime_spec):
        print("Spec files not found. Generating them first...")
        run_cmd([sys.executable, os.path.join(ROOT, "run_scripts/create_udb_assoc_spec.py")])
        
    cli_path = os.path.join(ROOT, "target/release/tectonic-cli")
    db_path = "/tmp/rocksdb_udb_assoc"
    
    # Clean up old DB path to ensure empty baseline state
    if os.path.exists(db_path):
        import shutil
        shutil.rmtree(db_path)
        print(f"Cleaned up existing database at: {db_path}")
        
    # 2. Pre-populate Baseline State (30,000,000 unsorted inserts)
    preload_trace = "/tmp/udb_assoc_preload_trace.txt"
    print("--- STEP 1: Generating 30,000,000 Key Preload Trace (approx. 2-3 mins) ---")
    sys.stdout.flush()
    run_cmd([cli_path, "generate", "-w", preload_spec, "-o", preload_trace])
    
    print("--- STEP 2: Executing Preload writes on RocksDB (approx. 2-3 mins) ---")
    sys.stdout.flush()
    run_cmd([cli_path, "execute", "-i", preload_trace, "-d", "rocksdb", "-p", db_path])
    
    if os.path.exists(preload_trace):
        os.remove(preload_trace)
        
    # 3. Execute Runtime Workload Phase (10,000,000 queries matching diurnal wave)
    runtime_trace = os.path.join(DATA_DIR, "udb_assoc_runtime_trace.txt")
    print("--- STEP 3: Generating 10,000,000 Query Runtime Trace (approx. 1-2 mins) ---")
    sys.stdout.flush()
    run_cmd([cli_path, "generate", "-w", runtime_spec, "-o", runtime_trace])
    
    print("--- STEP 4: Executing 10M operations against RocksDB (approx. 1-2 mins) ---")
    sys.stdout.flush()
    run_cmd([cli_path, "execute", "-i", runtime_trace, "-d", "rocksdb", "-p", db_path])
    
    # 4. Parse the actual database trace log file to extract logical distributions
    print("--- STEP 5: Parsing runtime trace log to gather statistics ---")
    sys.stdout.flush()
    
    group_op_counts = {}
    op_counts = Counter()
    
    all_keys_len = []
    prefix_counts = Counter()
    val_lens = []
    scan_lens = []
    key_access_counts = Counter()
    
    with open(runtime_trace, "r") as f:
        for line in f:
            parts = line.strip().split()
            if not parts:
                continue
            op = parts[0]
            if op == "FS":
                continue
            elif op == "FE":
                group_name = " ".join(parts[2:])
                group_op_counts[group_name] = dict(op_counts)
                op_counts.clear()
                continue
                
            op_counts[op] += 1
            
            if op in ("I", "P", "SC"):
                key = parts[1]
                all_keys_len.append(len(key))
                prefix_counts[key[:3]] += 1
                key_access_counts[key] += 1
                
                if op == "I":
                    val_lens.append(len(parts[2]))
                elif op == "SC":
                    scan_lens.append(int(parts[2]))
                    
    # Downsample datasets to keep results JSON small
    key_len_dist = dict(Counter(all_keys_len))
    prefix_dist = {prefix: count for prefix, count in sorted(prefix_counts.items(), key=lambda x: x[0])}
    
    val_lens.sort()
    step_val = max(1, len(val_lens) // 5000)
    sampled_val_lens = val_lens[::step_val]
    
    scan_lens.sort()
    step_scan = max(1, len(scan_lens) // 5000)
    sampled_scan_lens = scan_lens[::step_scan]
    
    freqs = sorted(key_access_counts.values(), reverse=True)
    total_accesses = sum(freqs)
    if freqs:
        indices = sorted(list(set(int(10 ** (i * math.log10(len(freqs)) / 999)) - 1 for i in range(1000) if len(freqs) > 1)))
        if not indices and len(freqs) == 1:
            indices = [0]
        sampled_key_access = [{"rank": idx + 1, "prob": freqs[idx] / total_accesses} for idx in indices]
    else:
        sampled_key_access = []
        
    # Copy RocksDB info LOG to the data directory in the workspace
    rocksdb_log_src = os.path.join(db_path, "LOG")
    rocksdb_log_dest = os.path.join(DATA_DIR, "rocksdb_execution.log")
    if os.path.exists(rocksdb_log_src):
        import shutil
        shutil.copy(rocksdb_log_src, rocksdb_log_dest)
        print(f"RocksDB execution log successfully saved to: {rocksdb_log_dest}")
        
    # Build results payload with Provenance
    results = {
        "provenance": {
            "runner_script": "run_scripts/run_udb_assoc.py",
            "plot_script": "plot_scripts/plot_udb_assoc.py",
            "data_directory": "data/udb_assoc/",
            "plot_directory": "data/udb_assoc/",
            "artifact_directory": "/home/cc/.gemini/antigravity-ide/brain/eaff35b0-472c-45c6-8218-bff10f70fb23/",
            "command_setup": "./target/release/tectonic-cli execute -i data/udb_assoc/udb_assoc_runtime_trace.txt -d rocksdb -p /tmp/rocksdb_udb_assoc",
            "metric_definitions": {
                "diurnal_ops": "Count of operations (Get/P, Put/I, Seek/SC) in each diurnal phase group",
                "key_lens": "Distribution of key sizes in bytes",
                "prefix_counts": "Frequencies of active table prefixes t00-t29",
                "val_lens": "Sorted sample of generated value sizes in bytes",
                "scan_lens": "Sorted sample of range query iterator scan lengths",
                "key_popularity": "Probability of accessing the r-th most popular key, sorted by popularity rank"
            }
        },
        "diurnal_ops": group_op_counts,
        "key_lens": key_len_dist,
        "prefix_counts": prefix_dist,
        "val_lens": sampled_val_lens,
        "scan_lens": sampled_scan_lens,
        "key_popularity": sampled_key_access
    }
    
    results_path = os.path.join(DATA_DIR, "udb_assoc_results.json")
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results successfully saved with provenance to: {results_path}")

if __name__ == "__main__":
    main()
