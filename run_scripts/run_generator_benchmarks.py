#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import json

STATS_DIR = "/home/cc/Tectonic/data/generator_comparison"
os.makedirs(STATS_DIR, exist_ok=True)

TECTONIC_CLI = "/home/cc/Tectonic/target/release/tectonic-cli"
KVBENCH_CLI = "/home/cc/KV-WorkloadGenerator/bin/load_gen"
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

def compile_tectonic():
    print("=== Step 1: Compiling Tectonic ===")
    try:
        subprocess.run(["cargo", "build", "--release"], cwd="/home/cc/Tectonic", check=True)
        print("Tectonic compiled successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Compilation failed: {e}")
        sys.exit(1)

def get_process_rss(pid):
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0 # MB
    except (FileNotFoundError, ProcessLookupError):
        pass
    return 0.0

def trace_generator(cmd, out_filepath, target_inserts_x=None, poll_interval=0.010, cwd=None):
    if os.path.exists(out_filepath):
        try:
            os.remove(out_filepath)
        except OSError:
            pass

    print(f"Executing: {' '.join(cmd)}")
    start_time = time.time()
    proc = subprocess.Popen(cmd, cwd=cwd)

    # Wait for the output file to appear or process to terminate
    while not os.path.exists(out_filepath) and proc.poll() is None:
        time.sleep(0.005)

    f = None
    if os.path.exists(out_filepath):
        f = open(out_filepath, "r")

    op_durations = {}
    mem_log = []
    
    last_op_time = 0.0
    last_poll_time = 0.0
    insert_count = 0
    loading_phase_end_time = None

    while True:
        current_time = time.time()
        elapsed = current_time - start_time

        # Read available lines
        if f:
            while True:
                line = f.readline()
                if not line:
                    break
                
                # We have a valid line (operation)
                op_type = line[0] if len(line) > 0 else 'Unknown'
                duration = elapsed - last_op_time
                op_durations[op_type] = op_durations.get(op_type, 0.0) + duration
                last_op_time = elapsed

                if op_type == 'I':
                    insert_count += 1
                    if target_inserts_x is not None and insert_count == target_inserts_x:
                        loading_phase_end_time = elapsed

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

    # Finish reading any remaining lines
    if f:
        while True:
            line = f.readline()
            if not line:
                break
            op_type = line[0] if len(line) > 0 else 'Unknown'
            duration = (time.time() - start_time) - last_op_time
            op_durations[op_type] = op_durations.get(op_type, 0.0) + duration
            last_op_time = (time.time() - start_time)

            if op_type == 'I':
                insert_count += 1
                if target_inserts_x is not None and insert_count == target_inserts_x:
                    loading_phase_end_time = last_op_time
        f.close()

    total_duration = time.time() - start_time
    print(f"Finished. Duration: {total_duration:.2f}s, Max RSS: {max([m[1] for m in mem_log], default=0.0):.2f} MB")

    if os.path.exists(out_filepath):
        try:
            os.remove(out_filepath)
        except OSError:
            pass

    return {
        "total_duration": total_duration,
        "op_durations": op_durations,
        "loading_phase_end_time": loading_phase_end_time,
        "mem_log": mem_log
    }

def run_set2_ycsb():
    print("\n=== Running Set 2: YCSB Workloads (A-F) ===")
    workloads = ["A", "B", "C", "D", "E", "F"]
    
    # 1. YCSB Run (both Load and Run phases)
    for w in workloads:
        w_lower = w.lower()
        print(f"\n--- YCSB Workload {w} ---")
        
        # Load phase
        load_cmd = [
            "java", "-cp", YCSB_CP, "site.ycsb.Client",
            "-db", "site.ycsb.db.FileClient",
            "-P", f"workloads/workload{w_lower}",
            "-p", "file.output=/tmp/ycsb_out.txt",
            "-p", "recordcount=1000000",
            "-p", "operationcount=1000000",
            "-load"
        ]
        load_res = trace_generator(load_cmd, "/tmp/ycsb_out.txt", cwd=YCSB_DIR)
        
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
        run_res = trace_generator(run_cmd, "/tmp/ycsb_out.txt", cwd=YCSB_DIR)
        
        # Save trace
        trace_data = {
            "load": load_res,
            "run": run_res
        }
        with open(f"{STATS_DIR}/ycsb_{w_lower}_trace.json", "w") as f:
            json.dump(trace_data, f)

    # 2. Tectonic Run
    tectonic_specs = {
        "A": "example-specs/ycsb_blind/a.spec.json",
        "B": "example-specs/ycsb_blind/b.spec.json",
        "C": "example-specs/ycsb_blind/c.spec.json",
        "D": "example-specs/ycsb_blind/d.spec.json",
        "E": "example-specs/ycsb_blind/e.spec.json",
        "F": "example-specs/ycsb_blind/f.spec.json",
    }
    for w, spec in tectonic_specs.items():
        w_lower = w.lower()
        print(f"\n--- Tectonic Workload {w} ---")
        cmd = [TECTONIC_CLI, "generate", "-w", spec, "-o", "/tmp/tectonic_out.txt"]
        res = trace_generator(cmd, "/tmp/tectonic_out.txt", target_inserts_x=1000000)
        
        with open(f"{STATS_DIR}/tectonic_{w_lower}_trace.json", "w") as f:
            json.dump(res, f)

    # 3. KVbench Run (A-E, F is missing)
    kvbench_ycsb_args = {
        "A": "-I 1000000 -Q 500000 -U 500000 --UD 3 --ED 3 --entry_size 1050 -L 0.025",
        "B": "-I 1000000 -Q 950000 -U 50000  --UD 3 --ED 3 --entry_size 1050 -L 0.025",
        "C": "-I 1000000 -Q 1000000          --UD 3 --ED 3 --entry_size 1050 -L 0.025",
        "D": "-I 1050000 -Q 950000           --UD 3 --ED 3 --entry_size 1050 -L 0.025",
        "E": "-I 1050000 -S 950000 -Y 0.0001 --YCSB=1 --ED 3 --entry_size 1050 -L 0.025",
    }
    for w in ["A", "B", "C", "D", "E"]:
        w_lower = w.lower()
        print(f"\n--- KVbench Workload {w} ---")
        args_list = kvbench_ycsb_args[w].split()
        cmd = [KVBENCH_CLI] + args_list + ["--OP", "/tmp/kvbench_out.txt"]
        
        # Target inserts x is 1,000,000 (since YCSB Load phase inserts 1M keys)
        res = trace_generator(cmd, "/tmp/kvbench_out.txt", target_inserts_x=1000000)
        
        with open(f"{STATS_DIR}/kvbench_{w_lower}_trace.json", "w") as f:
            json.dump(res, f)

def run_set1_kvbench():
    print("\n=== Running Set 1: KVbench Workloads (I-V) ===")
    
    # KVbench args for I-V
    kvbench_args = {
        "I": "-I 1000000 -Q 1000000 -Z 0.8 --ED 0 --ZD 2 --entry_size 1024",
        "II": "-I 500000 -D 100000 -U 250000 -Q 150000 -Z 1 --ID 0 --UD 0 --ED 0 --ZD 0 --entry_size 1024",
        "III": "-I 1000000 -U 500000 -Q 500000 -Z 0.5 --UD 3 --ED 0 --ZD 0 --entry_size 1024",
        "IV": "-I 1000000 -U 500000 -R 500000 -y 0.000001 --UD 3 --entry_size 1024",
        "V": "-I 950000 -Q 50000 -Z 0 --ID 3 --ED 0 --ZD 0 --entry_size 1024",
    }
    
    # 1. KVbench Run
    for w in ["I", "II", "III", "IV", "V"]:
        w_lower = w.lower()
        print(f"\n--- KVbench Workload {w} ---")
        args_list = kvbench_args[w].split()
        cmd = [KVBENCH_CLI] + args_list + ["--OP", "/tmp/kvbench_out.txt"]
        
        # We also trace KVbench here. No target_inserts_x needed since Figure 1 is operation breakdown.
        res = trace_generator(cmd, "/tmp/kvbench_out.txt")
        
        with open(f"{STATS_DIR}/kvbench_{w_lower}_trace.json", "w") as f:
            json.dump(res, f)

    # 2. Tectonic Run
    tectonic_specs = {
        "I": "example-specs/kvbench/i.spec.json",
        "II": "example-specs/kvbench/ii.spec.json",
        "III": "example-specs/kvbench/iii.spec.json",
        "IV": "example-specs/kvbench/iv.spec.json",
        "V": "example-specs/kvbench/v.spec.json",
    }
    for w, spec in tectonic_specs.items():
        w_lower = w.lower()
        print(f"\n--- Tectonic Workload {w} ---")
        cmd = [TECTONIC_CLI, "generate", "-w", spec, "-o", "/tmp/tectonic_out.txt"]
        res = trace_generator(cmd, "/tmp/tectonic_out.txt")
        
        with open(f"{STATS_DIR}/tectonic_{w_lower}_trace.json", "w") as f:
            json.dump(res, f)

if __name__ == "__main__":
    compile_tectonic()
    run_set2_ycsb()
    run_set1_kvbench()
    print("\n=== All generator benchmarks finished and logged! ===")
