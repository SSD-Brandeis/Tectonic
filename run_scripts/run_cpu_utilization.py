#!/usr/bin/env python3
"""
CPU-utilization sanity experiment.

This experiment compares YCSB and Tectonic on one embedded RocksDB setup:
  1. workload generation CPU usage
  2. database execution CPU usage through the same RocksDB harness

Results are written to:
  /home/cc/Tectonic/data/CPU-utilization/results.json
"""

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
import time


EXPERIMENT_NAME = "CPU-utilization"

ROOT_DIR = "/home/cc/Tectonic"
HARNESS_DIR = f"{ROOT_DIR}/rocksdb-benchmark-harness"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"
YCSB_DIR = f"{HARNESS_DIR}/vendor/YCSB"
M2 = "/home/cc/.m2/repository"

YCSB_CP = ":".join([
    f"{YCSB_DIR}/file/conf",
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar",
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar",
])

HARNESS_BIN = f"{HARNESS_DIR}/cmake-build-release-with-stats/rocksdb-benchmark-harness"
ROCKSDB_OPTS = f"{HARNESS_DIR}/experiments/workload-similarity/rocksdb-options.ini"
OUT_DIR = f"{ROOT_DIR}/data/{EXPERIMENT_NAME}"
RESULTS_PATH = f"{OUT_DIR}/results.json"
PLOT_SCRIPT = f"{ROOT_DIR}/plot_scripts/plot_cpu_utilization.py"
DB_DIR = "/tmp/tectonic_cpu_utilization_rocksdb"
DEFAULT_SCALE = 10_000_000
DEFAULT_WORKLOAD = "a"
DEFAULT_MIN_EXECUTION_SECONDS = 60.0

CLK_TCK = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
CPU_COUNT = os.cpu_count() or 1
SEP = "=" * 72


def banner(title):
    print(f"\n{SEP}\n  {title}\n{SEP}", flush=True)


def fail(message):
    print(f"[FATAL] {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def ensure_file(path, label):
    if not os.path.exists(path):
        fail(f"{label} not found: {path}")


def maybe_build(build):
    if not build:
        return
    banner("build required binaries")
    run_checked(["cargo", "build", "--release"], cwd=ROOT_DIR)
    run_checked(
        ["cmake", "--build", "cmake-build-release-with-stats", "--target", "rocksdb-benchmark-harness"],
        cwd=HARNESS_DIR,
    )


def run_checked(cmd, cwd=None):
    print(f"  $ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def parse_stat_cpu_seconds(stat_text):
    end_comm = stat_text.rfind(")")
    if end_comm == -1:
        return None
    fields = stat_text[end_comm + 2:].split()
    if len(fields) <= 12:
        return None
    utime = int(fields[11])
    stime = int(fields[12])
    return (utime + stime) / CLK_TCK


def read_process_cpu_seconds(pid):
    stat_path = f"/proc/{pid}/stat"
    try:
        with open(stat_path) as f:
            return parse_stat_cpu_seconds(f.read())
    except (FileNotFoundError, ProcessLookupError):
        return None


def tail_file(path, max_lines=40):
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return ""
    return "".join(lines[-max_lines:])


def trace_process(label, cmd, log_path, cwd=None, poll_interval=0.05):
    print(f"\n  [{label}] {' '.join(cmd)}", flush=True)
    start = time.monotonic()
    cpu_log = []

    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=log_file, stderr=subprocess.STDOUT)
        last_wall = time.monotonic()
        initial_cpu = read_process_cpu_seconds(proc.pid)
        last_cpu = initial_cpu

        while proc.poll() is None:
            time.sleep(poll_interval)
            now = time.monotonic()
            current_cpu = read_process_cpu_seconds(proc.pid)
            if current_cpu is not None and last_cpu is not None:
                delta_wall = now - last_wall
                delta_cpu = current_cpu - last_cpu
                if delta_wall > 0:
                    cpu_percent = max(0.0, (delta_cpu / delta_wall) * 100.0)
                    cpu_log.append({
                        "elapsed_s": now - start,
                        "cpu_percent": cpu_percent,
                        "host_cpu_percent": cpu_percent / CPU_COUNT,
                    })
                last_wall = now
                last_cpu = current_cpu

        return_code = proc.wait()

    duration_s = time.monotonic() - start
    cpu_seconds = 0.0
    if initial_cpu is not None and last_cpu is not None:
        cpu_seconds = max(0.0, last_cpu - initial_cpu)

    average_cpu_percent = (cpu_seconds / duration_s * 100.0) if duration_s > 0 else 0.0
    max_cpu_percent = max((sample["cpu_percent"] for sample in cpu_log), default=0.0)

    print(
        f"  [{label}] duration={duration_s:.2f}s "
        f"cpu_seconds={cpu_seconds:.2f}s "
        f"avg_cpu={average_cpu_percent:.1f}% "
        f"max_cpu={max_cpu_percent:.1f}%",
        flush=True,
    )

    if return_code != 0:
        print(tail_file(log_path), file=sys.stderr)
        fail(f"{label} failed with exit code {return_code}; see {log_path}")

    return {
        "cmd": cmd,
        "cwd": cwd,
        "log_path": log_path,
        "duration_s": duration_s,
        "cpu_seconds": cpu_seconds,
        "average_cpu_percent": average_cpu_percent,
        "max_cpu_percent": max_cpu_percent,
        "cpu_log": cpu_log,
    }


def remove_if_exists(path):
    if os.path.exists(path):
        os.remove(path)


def clean_metadata_lines(path):
    tmp_path = path + ".clean"
    removed = 0
    with open(path) as in_f, open(tmp_path, "w") as out_f:
        for line in in_f:
            if line.startswith(("FS ", "FE ")):
                removed += 1
                continue
            out_f.write(line)
    if removed:
        os.replace(tmp_path, path)
        print(f"  [clean] removed {removed} tectonic metadata lines", flush=True)
    else:
        remove_if_exists(tmp_path)


def count_ops(path):
    counts = collections.Counter()
    with open(path) as f:
        for line in f:
            op = line.split(" ", 1)[0]
            if op in ("I", "P", "BP", "U"):
                counts[op] += 1
    return dict(counts)


def normalize_blind_queries_for_rocksdb(input_path, output_path):
    counts = collections.Counter()
    with open(input_path) as in_f, open(output_path, "w") as out_f:
        for line in in_f:
            if line.startswith("BP "):
                out_f.write("P " + line[3:])
                counts["BP_to_P"] += 1
            else:
                out_f.write(line)
                op = line.split(" ", 1)[0]
                if op:
                    counts[f"kept_{op}"] += 1
    return dict(counts)


def offset_phase_logs(phases):
    offset = 0.0
    samples = []
    boundaries = []
    for phase in phases:
        phase_name = phase["phase"]
        result = phase["result"]
        for sample in result["cpu_log"]:
            shifted = dict(sample)
            shifted["elapsed_s"] = sample["elapsed_s"] + offset
            shifted["phase"] = phase_name
            samples.append(shifted)
        offset += result["duration_s"]
        boundaries.append(offset)
    return samples, boundaries[:-1]


def generate_ycsb_trace(workload, scale, poll_interval):
    banner(f"generate YCSB workload {workload.upper()} trace")
    ycsb_load = f"{OUT_DIR}/ycsb_workload{workload}_load.part"
    ycsb_run = f"{OUT_DIR}/ycsb_workload{workload}_run.part"
    ycsb_trace = f"{OUT_DIR}/ycsb_workload{workload}.txt"
    for path in (ycsb_load, ycsb_run, ycsb_trace):
        remove_if_exists(path)

    common = [
        "java", "-cp", YCSB_CP, "site.ycsb.Client",
        "-db", "site.ycsb.db.FileClient",
        "-P", f"workloads/workload{workload}",
        "-p", f"recordcount={scale}",
        "-p", f"operationcount={scale}",
    ]

    load_result = trace_process(
        "YCSB load generation",
        common + ["-p", f"file.output={ycsb_load}", "-load"],
        f"{OUT_DIR}/ycsb_workload{workload}_load.log",
        cwd=YCSB_DIR,
        poll_interval=poll_interval,
    )
    run_result = trace_process(
        "YCSB run generation",
        common + ["-p", f"file.output={ycsb_run}", "-t"],
        f"{OUT_DIR}/ycsb_workload{workload}_run.log",
        cwd=YCSB_DIR,
        poll_interval=poll_interval,
    )

    with open(ycsb_trace, "w") as out_f:
        for part in (ycsb_load, ycsb_run):
            with open(part) as in_f:
                shutil.copyfileobj(in_f, out_f)
    for part in (ycsb_load, ycsb_run):
        remove_if_exists(part)

    samples, boundaries = offset_phase_logs([
        {"phase": "load", "result": load_result},
        {"phase": "run", "result": run_result},
    ])

    return {
        "trace_path": ycsb_trace,
        "rocksdb_trace_path": ycsb_trace,
        "op_counts": count_ops(ycsb_trace),
        "rocksdb_op_counts": count_ops(ycsb_trace),
        "phases": {
            "load": load_result,
            "run": run_result,
        },
        "cpu_log": samples,
        "phase_boundaries_s": boundaries,
        "duration_s": load_result["duration_s"] + run_result["duration_s"],
        "cpu_seconds": load_result["cpu_seconds"] + run_result["cpu_seconds"],
    }


def generate_tectonic_trace(workload, scale, poll_interval):
    banner(f"generate Tectonic workload {workload.upper()} blind trace")
    spec_path = f"{ROOT_DIR}/example-specs/ycsb_blind/{workload}.spec.json"
    raw_trace = f"{OUT_DIR}/tectonic_workload{workload}_raw.txt"
    rocksdb_trace = f"{OUT_DIR}/tectonic_workload{workload}.txt"
    for path in (raw_trace, rocksdb_trace):
        remove_if_exists(path)
    ensure_file(spec_path, "Tectonic blind spec")

    scale_factor = scale / 1_000_000.0
    result = trace_process(
        "Tectonic generation",
        [
            TECTONIC_CLI, "generate",
            "-w", spec_path,
            "-o", raw_trace,
            "-s", str(scale_factor),
        ],
        f"{OUT_DIR}/tectonic_workload{workload}_generate.log",
        cwd=ROOT_DIR,
        poll_interval=poll_interval,
    )
    clean_metadata_lines(raw_trace)
    raw_op_counts = count_ops(raw_trace)
    normalization_counts = normalize_blind_queries_for_rocksdb(raw_trace, rocksdb_trace)
    remove_if_exists(raw_trace)

    return {
        "trace_path": rocksdb_trace,
        "rocksdb_trace_path": rocksdb_trace,
        "raw_trace_removed": True,
        "spec_path": spec_path,
        "scale_factor": scale_factor,
        "op_counts": raw_op_counts,
        "rocksdb_op_counts": count_ops(rocksdb_trace),
        "normalization": normalization_counts,
        "cpu_log": result["cpu_log"],
        "duration_s": result["duration_s"],
        "cpu_seconds": result["cpu_seconds"],
        "result": result,
    }


def reset_rocksdb_dir():
    if os.path.exists(DB_DIR):
        shutil.rmtree(DB_DIR)
    os.makedirs(DB_DIR, exist_ok=True)


def execute_trace_on_rocksdb(label, trace_path, output_prefix, poll_interval):
    banner(f"execute {label} trace on rocksdb")
    reset_rocksdb_dir()
    stats_path = f"{OUT_DIR}/{output_prefix}_stats.txt"
    latency_path = f"{OUT_DIR}/{output_prefix}_latency.csv"
    result = trace_process(
        f"{label} rocksdb execution",
        [HARNESS_BIN, ROCKSDB_OPTS, trace_path, stats_path, latency_path],
        f"{OUT_DIR}/{output_prefix}_rocksdb_execution.log",
        cwd=DB_DIR,
        poll_interval=poll_interval,
    )
    return {
        "trace_path": trace_path,
        "stats_path": stats_path,
        "latency_path": latency_path,
        "cpu_log": result["cpu_log"],
        "duration_s": result["duration_s"],
        "cpu_seconds": result["cpu_seconds"],
        "average_cpu_percent": result["average_cpu_percent"],
        "max_cpu_percent": result["max_cpu_percent"],
        "result": result,
    }


def write_results_atomic(payload):
    tmp_path = RESULTS_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, RESULTS_PATH)


def trigger_plot():
    if os.path.exists(PLOT_SCRIPT):
        print("\n  [plot] generating CPU time-series plots", flush=True)
        subprocess.run(["python3", PLOT_SCRIPT], check=True)


def main():
    parser = argparse.ArgumentParser(description="Run the CPU-utilization sanity experiment.")
    parser.add_argument("--workload", choices=["a", "b", "c", "d"], default=DEFAULT_WORKLOAD)
    parser.add_argument("--scale", type=int, default=DEFAULT_SCALE)
    parser.add_argument("--poll-interval", type=float, default=0.05)
    parser.add_argument(
        "--min-execution-seconds",
        type=float,
        default=DEFAULT_MIN_EXECUTION_SECONDS,
        help="Minimum required RocksDB execution duration for each trace. Use 0 to disable.",
    )
    parser.add_argument("--build", action="store_true", help="Build tectonic-cli and the RocksDB harness first.")
    parser.add_argument("--no-plot", action="store_true", help="Skip plot generation after writing results.json.")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    maybe_build(args.build)
    ensure_file(TECTONIC_CLI, "tectonic-cli")
    ensure_file(HARNESS_BIN, "RocksDB harness")
    ensure_file(ROCKSDB_OPTS, "RocksDB options")

    print(SEP)
    print(f"  experiment : {EXPERIMENT_NAME}")
    print(f"  workload   : YCSB {args.workload.upper()}")
    print(f"  database   : rocksdb")
    print(f"  scale      : {args.scale:,} records + {args.scale:,} operations")
    print(f"  min rocksdb execution : {args.min_execution_seconds:.1f}s per trace")
    print(f"  output dir : {OUT_DIR}")
    print(f"  cpu metric : process cpu percent, where 100% equals one fully used logical cpu")
    print(SEP, flush=True)

    ycsb_generation = generate_ycsb_trace(args.workload, args.scale, args.poll_interval)
    tectonic_generation = generate_tectonic_trace(args.workload, args.scale, args.poll_interval)

    banner("generation complete; start rocksdb cpu tracking")
    print("  both workload traces are fully materialized before any rocksdb execution sampling starts", flush=True)

    ycsb_execution = execute_trace_on_rocksdb(
        "YCSB",
        ycsb_generation["rocksdb_trace_path"],
        f"ycsb_workload{args.workload}",
        args.poll_interval,
    )
    tectonic_execution = execute_trace_on_rocksdb(
        "Tectonic",
        tectonic_generation["rocksdb_trace_path"],
        f"tectonic_workload{args.workload}",
        args.poll_interval,
    )

    execution_window_s = max(ycsb_execution["duration_s"], tectonic_execution["duration_s"])
    if args.min_execution_seconds > 0.0:
        short = {
            name: result["duration_s"]
            for name, result in (("YCSB", ycsb_execution), ("Tectonic", tectonic_execution))
            if result["duration_s"] < args.min_execution_seconds
        }
        if short:
            details = ", ".join(f"{name}={duration:.2f}s" for name, duration in short.items())
            fail(
                f"rocksdb execution shorter than requested {args.min_execution_seconds:.1f}s: {details}. "
                "rerun with a larger --scale or set --min-execution-seconds 0 for a quick smoke test."
            )

    payload = {
        "experiment": EXPERIMENT_NAME,
        "workload": args.workload,
        "database": "rocksdb",
        "scale": args.scale,
        "poll_interval_s": args.poll_interval,
        "min_execution_seconds": args.min_execution_seconds,
        "execution_window_s": execution_window_s,
        "generation_completed_before_execution": True,
        "cpu_metric": "process cpu percent; 100% equals one fully used logical cpu",
        "cpu_count": CPU_COUNT,
        "generation": {
            "ycsb": ycsb_generation,
            "tectonic": tectonic_generation,
        },
        "execution": {
            "ycsb": ycsb_execution,
            "tectonic": tectonic_execution,
        },
    }
    write_results_atomic(payload)
    print(f"\n  results saved: {RESULTS_PATH}", flush=True)

    if not args.no_plot:
        trigger_plot()


if __name__ == "__main__":
    main()
