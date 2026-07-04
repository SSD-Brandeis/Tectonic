#!/usr/bin/env python3
import os
import json
import re
import time
import subprocess

SPECS_DIR = "/home/cc/Tectonic/example-specs/tectonic"
STATS_DIR = "/home/cc/Tectonic/data/concurrent_experiment"
os.makedirs(STATS_DIR, exist_ok=True)

workload_files = [
    "Tec-1_multi_phase_workload.spec.json",
    "Tec-2_interleave_wl.spec.json",
    "Tec-3_varied_sortedness.spec.json",
    "Tec-4_slowly_shifting.spec.json",
    "Tec-5_abruptly_shifting.spec.json",
    "Tec-6_varied_kv_length.spec.json",
    "Tec-7_hot_key.spec.json"
]

def get_op_count(op):
    val = op.get("op_count", 0)
    if isinstance(val, dict):
        if "uniform" in val:
            return (val["uniform"].get("min", 0) + val["uniform"].get("max", 0)) / 2
        return 0
    return float(val)

def get_num_expr_val(num_expr):
    if isinstance(num_expr, dict):
        if "uniform" in num_expr:
            u = num_expr["uniform"]
            return (float(u.get("min", 0)) + float(u.get("max", 0))) / 2.0
        return 0.0
    return float(num_expr)

def get_expr_len(expr):
    if not expr or not isinstance(expr, dict):
        return 0.0
    if "uniform" in expr:
        u = expr["uniform"]
        if "len" in u:
            return get_num_expr_val(u["len"])
        if "min" in u and "max" in u:
            return (get_num_expr_val(u["min"]) + get_num_expr_val(u["max"])) / 2.0
    elif "hot_range" in expr:
        h = expr["hot_range"]
        if "len" in h:
            return float(h["len"])
    elif "weighted" in expr:
        weighted_list = expr["weighted"]
        total_weight = 0.0
        total_weighted_len = 0.0
        for item in weighted_list:
            weight = float(item.get("weight", 1.0))
            val_expr = item.get("value")
            val_len = get_expr_len(val_expr)
            total_weight += weight
            total_weighted_len += val_len * weight
        if total_weight > 0:
            return total_weighted_len / total_weight
    return 0.0

def calculate_workload_size(spec):
    total_bytes = 0
    for section in spec.get("sections", []):
        for group in section.get("groups", []):
            for op_type in ["inserts", "unique_inserts", "upserts"]:
                if op_type in group:
                    op = group[op_type]
                    count = get_op_count(op)
                    key_len = get_expr_len(op.get("key"))
                    if key_len == 0:
                        key_len = 128.0
                    total_bytes += count * key_len
    return total_bytes / (1024 * 1024) # return in MB

def prepare_merged_spec():
    sections = []
    sizes = {}
    
    for filename in workload_files:
        path = os.path.join(SPECS_DIR, filename)
        workload_name = filename.split('_')[0]
        
        with open(path, "r") as f:
            spec = json.load(f)
            
        # Calculate size before modifying
        sizes[workload_name] = calculate_workload_size(spec)
        
        char_set = spec.get("character_set")
        
        # Merge all groups in the sections of this spec file into a single section
        groups = []
        for sec in spec["sections"]:
            groups.extend(sec["groups"])
            
        merged_section = {
            "name": workload_name,
            "enable_granular_stats": False,
            "groups": groups
        }
        
        if char_set:
            merged_section["defaults"] = {
                "character_set": char_set
            }
            
        sections.append(merged_section)
        
    merged_spec = {
        "$schema": "../workload_schema.json",
        "sections": sections
    }
    
    merged_spec_path = os.path.join(STATS_DIR, "merged.spec.json")
    with open(merged_spec_path, "w") as f:
        json.dump(merged_spec, f, indent=2)
        
    print(f"Created merged spec file at {merged_spec_path}")
    return merged_spec_path, sizes

def run_and_parse(cmd, env):
    print(f"Executing: {' '.join(cmd)}")
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    
    timeline = {}
    pattern = re.compile(
        r'\[Tectonic (Sequential|Parallel) Gen\] Section \d+ \((.*?)\) started at (.*?)s, finished at (.*?)s \(duration: (.*?)s\)'
    )
    
    for line in process.stdout:
        print(line, end="")
        match = pattern.search(line)
        if match:
            section_name = match.group(2)
            timeline[section_name] = {
                "start": float(match.group(3)),
                "end": float(match.group(4)),
                "duration": float(match.group(5))
            }
            
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"Tectonic failed with exit code {process.returncode}")
        
    return timeline

def main():
    merged_spec_path, workload_sizes = prepare_merged_spec()
    
    print("\nCalculated Workload Sizes:")
    for wl, sz in workload_sizes.items():
        print(f"  {wl}: {sz:.2f} MB")
        
    # Warm-up run
    print("\nPerforming warm-up run...")
    env_warmup = os.environ.copy()
    if "TECTONIC_PARALLEL_GEN" in env_warmup:
        del env_warmup["TECTONIC_PARALLEL_GEN"]
    subprocess.run([
        "/home/cc/Tectonic/target/release/tectonic-cli",
        "generate",
        "-w", os.path.join(SPECS_DIR, "Tec-4_slowly_shifting.spec.json"),
        "-o", "/tmp/tectonic_warmup.txt"
    ], capture_output=True, env=env_warmup)
    
    if os.path.exists("/tmp/tectonic_warmup.txt"):
        os.remove("/tmp/tectonic_warmup.txt")

    all_seq_runs = []
    all_par_runs = []
    
    # Run 3 iterations
    for i in range(3):
        print(f"\n--- Iteration {i+1}/3 ---")
        
        # 1. Sequential Generation
        print("  Running Sequential Generation Baseline...")
        seq_out = f"/tmp/tectonic_seq_out_{i}.txt"
        env_seq = os.environ.copy()
        if "TECTONIC_PARALLEL_GEN" in env_seq:
            del env_seq["TECTONIC_PARALLEL_GEN"]
            
        cmd_seq = [
            "/home/cc/Tectonic/target/release/tectonic-cli",
            "generate",
            "-w", merged_spec_path,
            "-o", seq_out
        ]
        
        start_time = time.perf_counter()
        seq_timeline = run_and_parse(cmd_seq, env_seq)
        seq_total_time = time.perf_counter() - start_time
        print(f"    Sequential Wall-Clock Time: {seq_total_time:.3f} s")
        all_seq_runs.append((seq_total_time, seq_timeline))
        
        if os.path.exists(seq_out):
            os.remove(seq_out)
            
        # 2. Parallel Generation
        print("  Running Parallel Generation (Internal Tectonic Thread Spawning)...")
        par_out = f"/tmp/tectonic_par_out_{i}.txt"
        env_par = os.environ.copy()
        env_par["TECTONIC_PARALLEL_GEN"] = "1"
        
        cmd_par = [
            "/home/cc/Tectonic/target/release/tectonic-cli",
            "generate",
            "-w", merged_spec_path,
            "-o", par_out
        ]
        
        start_time = time.perf_counter()
        par_timeline = run_and_parse(cmd_par, env_par)
        par_total_time = time.perf_counter() - start_time
        print(f"    Parallel Wall-Clock Time:   {par_total_time:.3f} s")
        all_par_runs.append((par_total_time, par_timeline))
        
        if os.path.exists(par_out):
            os.remove(par_out)
            
    # Select best runs
    best_seq_time, best_seq_timeline = min(all_seq_runs, key=lambda x: x[0])
    best_par_time, best_par_timeline = min(all_par_runs, key=lambda x: x[0])
    
    results = {
        "best_seq_total": best_seq_time,
        "best_par_total": best_par_time,
        "seq_runs": [r[0] for r in all_seq_runs],
        "par_runs": [r[0] for r in all_par_runs],
        "seq_timeline": best_seq_timeline,
        "par_timeline": best_par_timeline,
        "workload_sizes": workload_sizes
    }
    
    stats_file = os.path.join(STATS_DIR, "results.json")
    with open(stats_file, 'w') as f:
        json.dump(results, f, indent=2)
        
    print(f"\nAll data saved to {stats_file}")
    print(f"Best Sequential: {best_seq_time:.3f} s")
    print(f"Best Parallel:   {best_par_time:.3f} s")
    print(f"Calculated Speedup: {best_seq_time / best_par_time:.2f}x")

if __name__ == "__main__":
    main()
