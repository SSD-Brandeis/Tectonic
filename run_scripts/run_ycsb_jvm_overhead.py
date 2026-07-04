#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import json

STATS_DIR = "/home/cc/Tectonic/data/ycsb_jvm_overhead"
os.makedirs(STATS_DIR, exist_ok=True)

YCSB_DIR = "/home/cc/Tectonic/rocksdb-benchmark-harness/vendor/YCSB"
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

def get_process_rss(pid):
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0 # MB
    except (FileNotFoundError, ProcessLookupError):
        pass
    return 0.0

def trace_run(cmd, poll_interval=0.010, cwd=None):
    print(f"Executing: {' '.join(cmd)}")
    start_time = time.time()
    proc = subprocess.Popen(cmd, cwd=cwd)

    mem_log = []
    last_poll_time = 0.0

    while True:
        current_time = time.time()
        elapsed = current_time - start_time

        # Poll memory footprint
        if elapsed - last_poll_time >= poll_interval:
            rss = get_process_rss(proc.pid)
            if rss > 0.0:
                mem_log.append((elapsed, rss))
            last_poll_time = elapsed

        # Check exit
        if proc.poll() is not None:
            break

        time.sleep(0.002)

    total_duration = time.time() - start_time
    max_rss = max([m[1] for m in mem_log], default=0.0)
    print(f"Finished. Duration: {total_duration:.2f}s, Max RSS: {max_rss:.2f} MB")

    return {
        "total_duration": total_duration,
        "mem_log": mem_log
    }

def main():
    # 1. Trace JVM Default
    print("\n--- Running JVM Default (Sleep) ---")
    cmd_jvm_def = ["java", "-cp", "/home/cc/Tectonic", "Sleep"]
    res_jvm_def = trace_run(cmd_jvm_def)
    with open(f"{STATS_DIR}/jvm_default.json", "w") as f:
        json.dump(res_jvm_def, f)

    # 2. Trace JVM Small Heap
    print("\n--- Running JVM Small Heap (Sleep) ---")
    cmd_jvm_small = ["java", "-Xms32m", "-Xmx32m", "-cp", "/home/cc/Tectonic", "Sleep"]
    res_jvm_small = trace_run(cmd_jvm_small)
    with open(f"{STATS_DIR}/jvm_small.json", "w") as f:
        json.dump(res_jvm_small, f)

    # Workloads A-F
    workloads = ["A", "B", "C", "D", "E", "F"]
    ycsb_out = "/tmp/ycsb_out.txt"

    # 3. Trace YCSB Default (A-F)
    print("\n=== Running YCSB Default JVM workloads (A-F) ===")
    for w in workloads:
        w_lower = w.lower()
        print(f"\n--- YCSB Default Workload {w} ---")
        
        # Load phase
        if os.path.exists(ycsb_out):
            try: os.remove(ycsb_out)
            except OSError: pass
        load_cmd = [
            "java", "-cp", YCSB_CP, "site.ycsb.Client",
            "-db", "site.ycsb.db.FileClient",
            "-P", f"workloads/workload{w_lower}",
            "-p", "file.output=/tmp/ycsb_out.txt",
            "-p", "recordcount=1000000",
            "-p", "operationcount=1000000",
            "-load"
        ]
        load_res = trace_run(load_cmd, cwd=YCSB_DIR)

        # Run phase
        run_cmd = [
            "java", "-cp", YCSB_CP, "site.ycsb.Client",
            "-db", "site.ycsb.db.FileClient",
            "-P", f"workloads/workload{w_lower}",
            "-p", "file.output=/tmp/ycsb_out.txt",
            "-p", "recordcount=1000000",
            "-p", "operationcount=1000000",
            "-t"
        ]
        run_res = trace_run(run_cmd, cwd=YCSB_DIR)

        trace_data = {
            "load": load_res,
            "run": run_res
        }
        with open(f"{STATS_DIR}/ycsb_default_{w_lower}_trace.json", "w") as f:
            json.dump(trace_data, f)

    # 4. Trace YCSB Constrained JVM (A-F)
    print("\n=== Running YCSB Constrained JVM workloads (A-F) ===")
    for w in workloads:
        w_lower = w.lower()
        print(f"\n--- YCSB Constrained Workload {w} ---")
        
        # Load phase
        if os.path.exists(ycsb_out):
            try: os.remove(ycsb_out)
            except OSError: pass
        load_cmd = [
            "java", "-Xms64m", "-Xmx128m", "-cp", YCSB_CP, "site.ycsb.Client",
            "-db", "site.ycsb.db.FileClient",
            "-P", f"workloads/workload{w_lower}",
            "-p", "file.output=/tmp/ycsb_out.txt",
            "-p", "recordcount=1000000",
            "-p", "operationcount=1000000",
            "-load"
        ]
        load_res = trace_run(load_cmd, cwd=YCSB_DIR)

        # Run phase
        run_cmd = [
            "java", "-Xms64m", "-Xmx128m", "-cp", YCSB_CP, "site.ycsb.Client",
            "-db", "site.ycsb.db.FileClient",
            "-P", f"workloads/workload{w_lower}",
            "-p", "file.output=/tmp/ycsb_out.txt",
            "-p", "recordcount=1000000",
            "-p", "operationcount=1000000",
            "-t"
        ]
        run_res = trace_run(run_cmd, cwd=YCSB_DIR)

        trace_data = {
            "load": load_res,
            "run": run_res
        }
        with open(f"{STATS_DIR}/ycsb_constrained_{w_lower}_trace.json", "w") as f:
            json.dump(trace_data, f)

    if os.path.exists(ycsb_out):
        try: os.remove(ycsb_out)
        except OSError: pass

    print("\n=== YCSB Constrained JVM experiment finished and logged! ===")

if __name__ == "__main__":
    main()
