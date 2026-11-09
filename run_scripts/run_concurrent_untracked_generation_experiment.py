#!/usr/bin/env python3
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import os
import sys
import time
import json
import subprocess

XBENCH_CLI = REPO_ROOT + "/target/release/xbench-cli"
YCSB_DIR = REPO_ROOT + "/rocksdb-benchmark-harness/vendor/YCSB"
M2 = "/home/cc/.m2/repository"

YCSB_CP = (
    f"{YCSB_DIR}/file/conf:"
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar:"
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar:"
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar:"
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar:"
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar:"
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar"
)

# Output directory for the experiment
EXP_DIR = REPO_ROOT + "/data/concurrent_untracked_experiment"
os.makedirs(EXP_DIR, exist_ok=True)

# Threads to test
THREADS_LIST = [1] + list(range(2, 62, 2))
ITERATIONS = 1  # Run 1 iteration to speed up execution

def cleanup_files():
    # Remove any temp output files to ensure disk space and clean slate
    temp_files = ["/tmp/ycsb_out.txt", "/tmp/xbench_out.txt", "/tmp/xbench_unique_out.txt"]
    for f in temp_files:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass
    # Clean X-Bench thread output files (xbench_out.txt.0, xbench_out.txt.1, etc.)
    for t in range(64):
        for prefix in ["/tmp/xbench_out.txt", "/tmp/xbench_unique_out.txt"]:
            f = f"{prefix}.{t}"
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

def run_cmd(cmd, env=None, cwd=None):
    # Execute a shell command and return its execution duration
    start = time.perf_counter()
    res = subprocess.run(cmd, env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.perf_counter()
    if res.returncode != 0:
        print(f"Command failed with exit code {res.returncode}: {' '.join(cmd)}")
        return None
    return end - start

def main():
    print("=== Starting Concurrent Operation Generation Experiment ===")
    print(f"Workload: YCSB C (scaled by 10 -> 10,000,000 ops)")
    print(f"Threads: {THREADS_LIST}")
    print(f"Iterations per run: {ITERATIONS}")

    ycsb_results = {}
    xbench_results = {}
    xbench_unique_results = {}

    for threads in THREADS_LIST:
        print(f"\n--- Benchmarking Thread Count: {threads} ---")
        
        # 1. YCSB Latency
        ycsb_times = []
        for it in range(ITERATIONS):
            cleanup_files()
            print(f"  YCSB Iteration {it + 1}/{ITERATIONS}...")
            
            # Load phase
            load_cmd = [
                "java", "-cp", YCSB_CP, "site.ycsb.Client",
                "-db", "site.ycsb.db.FileClient",
                "-P", "workloads/workloadc",
                "-p", "file.output=/tmp/ycsb_out.txt",
                "-p", "recordcount=10000000",
                "-p", "operationcount=10000000",
                "-threads", str(threads),
                "-load"
            ]
            load_time = run_cmd(load_cmd, cwd=YCSB_DIR)
            if load_time is None:
                continue
                
            # Run phase
            run_cmd_list = [
                "java", "-cp", YCSB_CP, "site.ycsb.Client",
                "-db", "site.ycsb.db.FileClient",
                "-P", "workloads/workloadc",
                "-p", "file.output=/tmp/ycsb_out.txt",
                "-p", "recordcount=10000000",
                "-p", "operationcount=10000000",
                "-threads", str(threads),
                "-t"
            ]
            run_time = run_cmd(run_cmd_list, cwd=YCSB_DIR)
            if run_time is None:
                continue
                
            total_time = load_time + run_time
            print(f"    YCSB Load: {load_time:.2f}s, Run: {run_time:.2f}s (Total: {total_time:.2f}s)")
            ycsb_times.append(total_time)
            
        ycsb_results[threads] = min(ycsb_times) if ycsb_times else 0.0
        print(f"  Best YCSB End-to-End Latency: {ycsb_results[threads]:.2f} s")

        # 2. X-Bench Latency
        xbench_times = []
        for it in range(ITERATIONS):
            cleanup_files()
            print(f"  X-Bench Iteration {it + 1}/{ITERATIONS}...")
            
            # X-Bench generate command with XBENCH_PARALLEL_GEN enabled
            env = os.environ.copy()
            env["XBENCH_PARALLEL_GEN"] = "1"
            
            cmd = [
                XBENCH_CLI,
                "generate",
                "-w", REPO_ROOT + "/example-specs/ycsb_untracked/c.spec.json",
                "-o", "/tmp/xbench_out.txt",
                "-s", "10",
                "-t", str(threads)
            ]
            
            dur = run_cmd(cmd, env=env)
            if dur is not None:
                print(f"    X-Bench Generation: {dur:.2f}s")
                xbench_times.append(dur)
                
        xbench_results[threads] = min(xbench_times) if xbench_times else 0.0
        print(f"  Best X-Bench End-to-End Latency: {xbench_results[threads]:.2f} s")

        # 3. X-Bench Unique Latency
        xbench_unique_times = []
        for it in range(ITERATIONS):
            cleanup_files()
            print(f"  X-Bench Unique Iteration {it + 1}/{ITERATIONS}...")
            
            env = os.environ.copy()
            env["XBENCH_PARALLEL_GEN"] = "1"
            
            cmd = [
                XBENCH_CLI,
                "generate",
                "-w", REPO_ROOT + "/example-specs/ycsb-unique/c.spec.json",
                "-o", "/tmp/xbench_unique_out.txt",
                "-s", "10",
                "-t", str(threads)
            ]
            
            dur = run_cmd(cmd, env=env)
            if dur is not None:
                print(f"    X-Bench Unique Generation: {dur:.2f}s")
                xbench_unique_times.append(dur)
                
        xbench_unique_results[threads] = min(xbench_unique_times) if xbench_unique_times else 0.0
        print(f"  Best X-Bench Unique End-to-End Latency: {xbench_unique_results[threads]:.2f} s")

    # Save results to JSON
    data = {
        "threads": THREADS_LIST,
        "ycsb": [ycsb_results[t] for t in THREADS_LIST],
        "xbench": [xbench_results[t] for t in THREADS_LIST],
        "xbench_unique": [xbench_unique_results[t] for t in THREADS_LIST]
    }
    
    with open(f"{EXP_DIR}/results.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nResults saved to {EXP_DIR}/results.json")

    # 3. Plotting (invoked via the separate plotting script)
    plot_script = REPO_ROOT + "/plot_scripts/plot_concurrent_untracked_generation_experiment.py"
    if os.path.exists(plot_script):
        print("\nInvoking separate plotting script...")
        subprocess.run([sys.executable, plot_script])
    else:
        print(f"\nPlotting script not found at {plot_script}")
    
    # Print summary speedup
    print("\nSpeedup Summary (YCSB / X-Bench):")
    for t in THREADS_LIST:
        y_val = ycsb_results[t]
        t_val = xbench_results[t]
        tu_val = xbench_unique_results[t]
        su = y_val / t_val if t_val > 0 else 0.0
        su_u = y_val / tu_val if tu_val > 0 else 0.0
        print(f"  {t} Threads: YCSB = {y_val:.2f}s, X-Bench = {t_val:.2f}s (Speedup = {su:.2f}x), X-Bench Unique = {tu_val:.2f}s (Speedup = {su_u:.2f}x)")

if __name__ == "__main__":
    main()
