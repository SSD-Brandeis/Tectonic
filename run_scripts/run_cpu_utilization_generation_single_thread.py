#!/usr/bin/env python3
"""
Single-core workload-generation CPU-utilization experiment.

This pins each generator process to one logical CPU and measures only workload
materialization. Generated traces are deleted immediately after each phase to
avoid retaining multi-GB temporary files.
"""

import argparse
import json
import os
import subprocess
import sys
import time


EXPERIMENT_NAME = "CPU-utilization"
ROOT_DIR = "/home/cc/Tectonic"
HARNESS_DIR = f"{ROOT_DIR}/rocksdb-benchmark-harness"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"
YCSB_DIR = f"{HARNESS_DIR}/vendor/YCSB"
M2 = "/home/cc/.m2/repository"
OUT_DIR = f"{ROOT_DIR}/data/{EXPERIMENT_NAME}"
RESULTS_PATH = f"{OUT_DIR}/generation_single_thread_results.json"
PLOT_SCRIPT = f"{ROOT_DIR}/plot_scripts/plot_cpu_utilization_generation_single_thread.py"
DEFAULT_SCALE = 10_000_000
DEFAULT_WORKLOAD = "a"

YCSB_CP = ":".join([
    f"{YCSB_DIR}/file/conf",
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar",
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar",
])

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


def remove_if_exists(path):
    if os.path.exists(path):
        os.remove(path)


def parse_stat_cpu_seconds(stat_text):
    end_comm = stat_text.rfind(")")
    if end_comm == -1:
        return None
    fields = stat_text[end_comm + 2:].split()
    if len(fields) <= 12:
        return None
    return (int(fields[11]) + int(fields[12])) / CLK_TCK


def read_process_cpu_seconds(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
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


def available_cpu():
    affinity = sorted(os.sched_getaffinity(0))
    if not affinity:
        fail("no CPUs available in current affinity mask")
    return affinity[0]


def trace_process(label, cmd, log_path, cpu_id, poll_interval=0.05, cwd=None):
    print(f"\n  [{label}] cpu={cpu_id} {' '.join(cmd)}", flush=True)
    start = time.monotonic()
    cpu_log = []

    def pin_child():
        os.sched_setaffinity(0, {cpu_id})

    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            preexec_fn=pin_child,
        )
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
        "cpu_id": cpu_id,
        "log_path": log_path,
        "duration_s": duration_s,
        "cpu_seconds": cpu_seconds,
        "average_cpu_percent": average_cpu_percent,
        "max_cpu_percent": max_cpu_percent,
        "cpu_log": cpu_log,
    }


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


def generate_ycsb(workload, scale, poll_interval, cpu_id):
    banner(f"generate YCSB workload {workload.upper()} trace on one logical cpu")
    ycsb_load = f"{OUT_DIR}/single_thread_ycsb_workload{workload}_load.part"
    ycsb_run = f"{OUT_DIR}/single_thread_ycsb_workload{workload}_run.part"
    for path in (ycsb_load, ycsb_run):
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
        f"{OUT_DIR}/single_thread_ycsb_workload{workload}_load.log",
        cpu_id,
        poll_interval=poll_interval,
        cwd=YCSB_DIR,
    )
    remove_if_exists(ycsb_load)

    run_result = trace_process(
        "YCSB run generation",
        common + ["-p", f"file.output={ycsb_run}", "-t"],
        f"{OUT_DIR}/single_thread_ycsb_workload{workload}_run.log",
        cpu_id,
        poll_interval=poll_interval,
        cwd=YCSB_DIR,
    )
    remove_if_exists(ycsb_run)

    samples, boundaries = offset_phase_logs([
        {"phase": "load", "result": load_result},
        {"phase": "run", "result": run_result},
    ])
    return {
        "phases": {"load": load_result, "run": run_result},
        "cpu_log": samples,
        "phase_boundaries_s": boundaries,
        "duration_s": load_result["duration_s"] + run_result["duration_s"],
        "cpu_seconds": load_result["cpu_seconds"] + run_result["cpu_seconds"],
    }


def generate_tectonic(workload, scale, poll_interval, cpu_id):
    banner(f"generate Tectonic workload {workload.upper()} blind trace on one logical cpu")
    spec_path = f"{ROOT_DIR}/example-specs/ycsb_blind/{workload}.spec.json"
    output_path = f"{OUT_DIR}/single_thread_tectonic_workload{workload}.txt"
    remove_if_exists(output_path)
    ensure_file(spec_path, "Tectonic blind spec")

    scale_factor = scale / 1_000_000.0
    result = trace_process(
        "Tectonic generation",
        [TECTONIC_CLI, "generate", "-w", spec_path, "-o", output_path, "-s", str(scale_factor)],
        f"{OUT_DIR}/single_thread_tectonic_workload{workload}_generate.log",
        cpu_id,
        poll_interval=poll_interval,
        cwd=ROOT_DIR,
    )
    remove_if_exists(output_path)

    return {
        "spec_path": spec_path,
        "scale_factor": scale_factor,
        "cpu_log": result["cpu_log"],
        "duration_s": result["duration_s"],
        "cpu_seconds": result["cpu_seconds"],
        "result": result,
    }


def write_results(payload):
    tmp_path = RESULTS_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, RESULTS_PATH)


def main():
    parser = argparse.ArgumentParser(description="Run single-core workload-generation CPU-utilization experiment.")
    parser.add_argument("--workload", choices=["a", "b", "c", "d"], default=DEFAULT_WORKLOAD)
    parser.add_argument("--scale", type=int, default=DEFAULT_SCALE)
    parser.add_argument("--poll-interval", type=float, default=0.05)
    parser.add_argument("--cpu", type=int, default=None, help="Logical CPU id to pin to. Defaults to the first allowed CPU.")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    ensure_file(TECTONIC_CLI, "tectonic-cli")
    cpu_id = args.cpu if args.cpu is not None else available_cpu()
    if cpu_id not in os.sched_getaffinity(0):
        fail(f"requested CPU {cpu_id} is not in current affinity mask: {sorted(os.sched_getaffinity(0))}")

    print(SEP)
    print("  experiment : CPU-utilization workload generation, single logical cpu")
    print(f"  workload   : YCSB {args.workload.upper()}")
    print(f"  scale      : {args.scale:,} records + {args.scale:,} operations")
    print(f"  cpu pin    : {cpu_id}")
    print(f"  output dir : {OUT_DIR}")
    print(SEP, flush=True)

    ycsb_generation = generate_ycsb(args.workload, args.scale, args.poll_interval, cpu_id)
    tectonic_generation = generate_tectonic(args.workload, args.scale, args.poll_interval, cpu_id)

    payload = {
        "experiment": "CPU-utilization",
        "mode": "workload_generation_single_logical_cpu",
        "workload": args.workload,
        "scale": args.scale,
        "poll_interval_s": args.poll_interval,
        "cpu_count": CPU_COUNT,
        "cpu_affinity": [cpu_id],
        "cpu_metric": "process cpu percent; 100% equals one fully used logical cpu",
        "generation": {
            "ycsb": ycsb_generation,
            "tectonic": tectonic_generation,
        },
    }
    write_results(payload)
    print(f"\n  results saved: {RESULTS_PATH}", flush=True)

    if not args.no_plot:
        subprocess.run(["python3", PLOT_SCRIPT], check=True)


if __name__ == "__main__":
    main()
