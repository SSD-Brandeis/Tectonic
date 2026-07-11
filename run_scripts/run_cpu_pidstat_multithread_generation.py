#!/usr/bin/env python3
"""Pidstat-only CPU utilization for multithreaded workload generation."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time


ROOT_DIR = Path("/home/cc/Tectonic")
HARNESS_DIR = ROOT_DIR / "rocksdb-benchmark-harness"
TECTONIC_CLI = ROOT_DIR / "target/release/tectonic-cli"
COMMON_SPEC = ROOT_DIR / "example-specs/cpu_utilization/ycsb_kvbench_common.spec.json"
KV_BENCH = Path("/home/cc/KV-WorkloadGenerator/bin/load_gen")
YCSB_DIR = HARNESS_DIR / "vendor/YCSB"
M2 = Path("/home/cc/.m2/repository")
OUT_DIR = ROOT_DIR / "data/CPU-utilization"
TMP_DIR = Path("/tmp/tectonic_cpu_pidstat_multithread")
RESULTS_PATH = OUT_DIR / "pidstat_multithread_generation_results.json"
PLOT_SCRIPT = ROOT_DIR / "plot_scripts/plot_cpu_pidstat_multithread_generation.py"
DEFAULT_SCALE = 1_000_000
DEFAULT_WORKLOAD = "e"
PIDSTAT_INTERVAL_S = 1
SEP = "=" * 72
GIB = 1024 ** 3

YCSB_CP = ":".join([
    str(YCSB_DIR / "file/conf"),
    str(YCSB_DIR / "file/target/file-binding-0.18.0-SNAPSHOT.jar"),
    str(M2 / "org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar"),
    str(M2 / "org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar"),
    str(M2 / "org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar"),
    str(M2 / "org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar"),
    str(YCSB_DIR / "core/target/core-0.18.0-SNAPSHOT.jar"),
])


def fail(message):
    print(f"[FATAL] {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def banner(title):
    print(f"\n{SEP}\n  {title}\n{SEP}", flush=True)


def ensure_file(path, label):
    if not Path(path).exists():
        fail(f"{label} not found: {path}")


def available_cpu_count():
    return max(1, len(os.sched_getaffinity(0)))


def default_threads(cpu_count):
    values = [1, 2, 4, 8, 16, 32, cpu_count]
    return sorted({value for value in values if 1 <= value <= cpu_count})


def parse_threads(value, cpu_count):
    threads = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        thread_count = int(item)
        if thread_count < 1:
            fail("thread counts must be positive")
        if thread_count > cpu_count:
            fail(f"thread count {thread_count} exceeds available cpu count {cpu_count}")
        threads.append(thread_count)
    if not threads:
        fail("no thread counts were provided")
    return sorted(set(threads))


def process_exists(pid):
    return Path(f"/proc/{pid}").exists()


def any_process_exists(pids):
    return any(process_exists(pid) for pid in pids)


def parse_float(text):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def parse_pidstat_cpu_sum(output, pids):
    pid_set = {str(pid) for pid in pids}
    seen = set()
    total = 0.0
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) < 8 or parts[0] == "Linux" or parts[0].startswith("#"):
            continue
        if parts[0] == "Average:" or "%CPU" in parts:
            continue
        for pid_s in pid_set:
            try:
                pid_idx = parts.index(pid_s)
            except ValueError:
                continue
            if pid_s in seen:
                break
            cpu_idx = pid_idx + 5
            if cpu_idx < len(parts):
                value = parse_float(parts[cpu_idx])
                if value is not None:
                    total += value
                    seen.add(pid_s)
            break
    return total if seen else None


def remove_output_prefix(path):
    path = Path(path)
    if path.exists():
        path.unlink()
    for candidate in path.parent.glob(path.name + ".*"):
        if candidate.is_file():
            candidate.unlink()


def tail_file(path, max_lines=50):
    try:
        return "".join(Path(path).read_text(errors="replace").splitlines(True)[-max_lines:])
    except FileNotFoundError:
        return ""


def split_count(total, parts):
    base = total // parts
    remainder = total % parts
    return [base + (1 if idx < remainder else 0) for idx in range(parts)]


def fmt_gib(num_bytes):
    return f"{num_bytes / GIB:.2f} GiB"


def estimate_peak_temp_bytes(load_count, range_query_count):
    """Conservative peak temporary trace estimate for the largest single tool run."""
    ycsb_load = load_count * 1_500
    ycsb_run = range_query_count * 96
    tectonic = load_count * 1_200 + range_query_count * 96
    kvbench = load_count * 1_200 + range_query_count * 128
    return int(max(ycsb_load, ycsb_run, tectonic, kvbench) * 1.5)


def check_temp_space(load_count, range_query_count, min_free_gib):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(TMP_DIR)
    estimated_peak = estimate_peak_temp_bytes(load_count, range_query_count)
    min_free = int(min_free_gib * GIB)
    free_after_peak = usage.free - estimated_peak
    print(f"  tmp free   : {fmt_gib(usage.free)} before run")
    print(f"  tmp peak   : {fmt_gib(estimated_peak)} estimated conservative max")
    print(f"  tmp margin : require {fmt_gib(min_free)} after estimated peak")
    if free_after_peak < min_free:
        fail(
            "not enough free temporary disk space: "
            f"free={fmt_gib(usage.free)}, estimated_peak={fmt_gib(estimated_peak)}, "
            f"required_remaining={fmt_gib(min_free)}"
        )


def write_common_spec(load_count, range_query_count):
    spec_path = TMP_DIR / f"tectonic_range_scan_load{load_count}_range{range_query_count}.spec.json"
    spec = {
        "$schema": "../../workload_schema.json",
        "sections": [{
            "groups": [
                {
                    "name": "load phase",
                    "enable_granular_stats": True,
                    "inserts": {
                        "op_count": load_count,
                        "key": {
                            "segmented": {
                                "segments": [
                                    "usertable:user",
                                    {"uniform": {"len": 19, "character_set": "numeric"}},
                                ],
                                "separator": "",
                            }
                        },
                        "val": {"uniform": {"len": 1024}},
                    },
                },
                {
                    "name": "execution phase",
                    "enable_granular_stats": True,
                    "range_queries": {
                        "op_count": range_query_count,
                        "scan_length": 100,
                        "selection": {"zipf": {"s": 0.99, "n": load_count}},
                    },
                },
            ]
        }],
    }
    tmp = spec_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(spec, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, spec_path)
    return spec_path


class PidstatCollector:
    def __init__(self, pids, start_time, cpu_count, stop_event):
        self.pids = list(pids)
        self.start_time = start_time
        self.cpu_count = cpu_count
        self.stop_event = stop_event
        self.samples = []
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def join(self):
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def add_sample(self, raw_cpu_percent):
        if raw_cpu_percent is None:
            return
        normalized = raw_cpu_percent / self.cpu_count
        clipped = min(100.0, max(0.0, normalized))
        with self._lock:
            self.samples.append({
                "elapsed_s": time.monotonic() - self.start_time,
                "process_cpu_percent": raw_cpu_percent,
                "avg_cpu_percent": clipped,
                "unclipped_avg_cpu_percent": normalized,
            })

    def _loop(self):
        pid_arg = ",".join(str(pid) for pid in self.pids)
        while not self.stop_event.is_set() and any_process_exists(self.pids):
            result = subprocess.run(
                ["pidstat", "-h", "-u", "-p", pid_arg, str(PIDSTAT_INTERVAL_S), "1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self.add_sample(parse_pidstat_cpu_sum(result.stdout, self.pids))


def run_monitored_group(label, command_specs, cpu_count):
    first_cmd = command_specs[0]["cmd"] if command_specs else []
    if len(command_specs) == 1:
        print(f"\n  [{label}] {' '.join(first_cmd)}", flush=True)
    else:
        print(f"\n  [{label}] {len(command_specs)} worker processes; first: {' '.join(first_cmd)}", flush=True)

    start_time = time.monotonic()
    stop_event = threading.Event()
    procs = []
    log_files = []
    collector = None
    try:
        for spec in command_specs:
            log_file = open(spec["log_path"], "w")
            log_files.append(log_file)
            proc = subprocess.Popen(
                spec["cmd"],
                cwd=spec.get("cwd"),
                env=spec.get("env"),
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            procs.append((proc, spec))

        collector = PidstatCollector([proc.pid for proc, _ in procs], start_time, cpu_count, stop_event)
        collector.start()
        active = {proc.pid: proc for proc, _ in procs}
        usage_by_pid = {}
        while active:
            for pid in list(active):
                waited_pid, status, usage = os.wait4(pid, os.WNOHANG)
                if waited_pid == pid:
                    proc = active.pop(pid)
                    proc.returncode = os.waitstatus_to_exitcode(status)
                    usage_by_pid[pid] = usage.ru_utime + usage.ru_stime
            if active:
                time.sleep(0.05)
        end_time = time.monotonic()
        stop_event.set()
        collector.join()
    finally:
        stop_event.set()
        if collector is not None:
            collector.join()
        for log_file in log_files:
            log_file.close()

    duration_s = end_time - start_time
    cpu_seconds = sum(max(0.0, value) for value in usage_by_pid.values())
    cpu_time_avg = min(100.0, (cpu_seconds / duration_s / cpu_count * 100.0) if duration_s > 0 else 0.0)
    pidstat_values = [sample["avg_cpu_percent"] for sample in collector.samples]
    pidstat_avg = sum(pidstat_values) / len(pidstat_values) if pidstat_values else None
    pidstat_max = max(pidstat_values) if pidstat_values else None
    print(
        f"  [{label}] duration={duration_s:.2f}s cpu_seconds={cpu_seconds:.2f}s "
        f"cpu_time_avg_all_cores={cpu_time_avg:.2f}% pidstat_samples={len(pidstat_values)}",
        flush=True,
    )

    failed = [(proc, spec) for proc, spec in procs if proc.returncode != 0]
    if failed:
        proc, spec = failed[0]
        print(tail_file(spec["log_path"]), file=sys.stderr)
        fail(f"{label} failed with exit code {proc.returncode}; see {spec['log_path']}")

    return {
        "cmds": [spec["cmd"] for _, spec in procs],
        "cwd": str(command_specs[0].get("cwd")) if command_specs and command_specs[0].get("cwd") else None,
        "log_paths": [str(spec["log_path"]) for _, spec in procs],
        "worker_pids": [proc.pid for proc, _ in procs],
        "worker_count": len(procs),
        "duration_s": duration_s,
        "cpu_seconds": cpu_seconds,
        "cpu_time_avg_cpu_percent": cpu_time_avg,
        "pidstat_avg_cpu_percent": pidstat_avg,
        "pidstat_max_cpu_percent": pidstat_max,
        "pidstat_samples": collector.samples,
    }


def run_monitored(label, cmd, log_path, cpu_count, cwd=None, env=None):
    return run_monitored_group(label, [{
        "cmd": cmd,
        "log_path": log_path,
        "cwd": cwd,
        "env": env,
    }], cpu_count)


def offset_phase_samples(phases):
    offset = 0.0
    samples = []
    boundaries = []
    for phase in phases:
        phase_name = phase["phase"]
        result = phase["result"]
        for sample in result["pidstat_samples"]:
            shifted = dict(sample)
            shifted["elapsed_s"] = sample["elapsed_s"] + offset
            shifted["phase"] = phase_name
            samples.append(shifted)
        offset += result["duration_s"]
        boundaries.append(offset)
    return samples, boundaries[:-1]


def pidstat_average(samples):
    values = [sample["avg_cpu_percent"] for sample in samples]
    return sum(values) / len(values) if values else None


def pidstat_max(samples):
    values = [sample["avg_cpu_percent"] for sample in samples]
    return max(values) if values else None


def has_time_series_samples(run):
    return all(run[key].get("pidstat_samples") for key in ("ycsb", "tectonic", "kvbench"))


def choose_plotted_time_series_threads(runs, requested_threads):
    for run in runs:
        if int(run["threads"]) == int(requested_threads) and has_time_series_samples(run):
            return requested_threads
    sampled = [int(run["threads"]) for run in runs if has_time_series_samples(run)]
    if sampled:
        return max(sampled)
    return requested_threads


def ycsb_command(workload, load_count, range_query_count, output_path, threads, transaction_phase):
    cmd = [
        "java", "-cp", YCSB_CP, "site.ycsb.Client",
        "-db", "site.ycsb.db.FileClient",
        "-P", f"workloads/workload{workload}",
        "-p", f"recordcount={load_count}",
        "-p", f"operationcount={range_query_count}",
        "-p", "readproportion=0",
        "-p", "updateproportion=0",
        "-p", "scanproportion=1",
        "-p", "insertproportion=0",
        "-p", "requestdistribution=zipfian",
        "-p", "minscanlength=100",
        "-p", "maxscanlength=100",
        "-p", "scanlengthdistribution=uniform",
        "-p", "fieldcount=10",
        "-p", "fieldlength=100",
        "-p", f"file.output={output_path}",
        "-threads", str(threads),
    ]
    cmd.append("-t" if transaction_phase else "-load")
    return cmd


def generate_ycsb(workload, load_count, range_query_count, threads, cpu_count):
    tag = f"load{load_count}_range{range_query_count}_threads{threads}"
    load_path = TMP_DIR / f"ycsb_range_scan_{tag}_load.txt"
    run_path = TMP_DIR / f"ycsb_range_scan_{tag}_run.txt"
    for path in (load_path, run_path):
        remove_output_prefix(path)
    load_result = run_monitored(
        f"YCSB load generation threads={threads}",
        ycsb_command(workload, load_count, range_query_count, load_path, threads, transaction_phase=False),
        TMP_DIR / f"ycsb_range_scan_{tag}_load.log",
        cpu_count,
        cwd=YCSB_DIR,
    )
    remove_output_prefix(load_path)
    run_result = run_monitored(
        f"YCSB run generation threads={threads}",
        ycsb_command(workload, load_count, range_query_count, run_path, threads, transaction_phase=True),
        TMP_DIR / f"ycsb_range_scan_{tag}_run.log",
        cpu_count,
        cwd=YCSB_DIR,
    )
    remove_output_prefix(run_path)
    samples, boundaries = offset_phase_samples([
        {"phase": "load", "result": load_result},
        {"phase": "run", "result": run_result},
    ])
    duration_s = load_result["duration_s"] + run_result["duration_s"]
    cpu_seconds = load_result["cpu_seconds"] + run_result["cpu_seconds"]
    return {
        "threads": threads,
        "phases": {"load": load_result, "run": run_result},
        "phase_boundaries_s": boundaries,
        "pidstat_samples": samples,
        "duration_s": duration_s,
        "cpu_seconds": cpu_seconds,
        "cpu_time_avg_cpu_percent": min(100.0, (cpu_seconds / duration_s / cpu_count * 100.0) if duration_s > 0 else 0.0),
        "pidstat_avg_cpu_percent": pidstat_average(samples),
        "pidstat_max_cpu_percent": pidstat_max(samples),
    }

def generate_tectonic(load_count, range_query_count, threads, cpu_count):
    spec_path = write_common_spec(load_count, range_query_count)
    tag = f"load{load_count}_range{range_query_count}_threads{threads}"
    output_path = TMP_DIR / f"tectonic_range_scan_{tag}.txt"
    remove_output_prefix(output_path)
    env = os.environ.copy()
    env["TECTONIC_PARALLEL_GEN"] = "1"
    result = run_monitored(
        f"Tectonic generation threads={threads}",
        [str(TECTONIC_CLI), "generate", "-w", str(spec_path), "-o", str(output_path), "-t", str(threads)],
        TMP_DIR / f"tectonic_range_scan_{tag}.log",
        cpu_count,
        cwd=ROOT_DIR,
        env=env,
    )
    remove_output_prefix(output_path)
    result.update({
        "threads": threads,
        "spec_path": str(spec_path),
        "load_count": load_count,
        "range_query_count": range_query_count,
    })
    return result

def kvbench_command(insert_count, range_query_count, output_path):
    range_selectivity = min(1.0, 100.0 / max(1, insert_count))
    return [
        str(KV_BENCH),
        f"--insert={insert_count}",
        f"--range_query={range_query_count}",
        f"--range_query_selectivity={range_selectivity}",
        "--entry_size=1024",
        "--lambda=0.02",
        f"--output-path={output_path}",
    ]


def generate_kvbench(load_count, range_query_count, threads, cpu_count):
    insert_counts = split_count(load_count, threads)
    range_query_counts = split_count(range_query_count, threads)
    command_specs = []
    output_paths = []
    tag = f"load{load_count}_range{range_query_count}_threads{threads}"
    for idx in range(threads):
        output_path = TMP_DIR / f"kvbench_range_scan_{tag}_shard{idx}.txt"
        remove_output_prefix(output_path)
        output_paths.append(output_path)
        command_specs.append({
            "cmd": kvbench_command(insert_counts[idx], range_query_counts[idx], output_path),
            "log_path": TMP_DIR / f"kvbench_range_scan_{tag}_shard{idx}.log",
            "cwd": ROOT_DIR,
            "env": None,
        })
    result = run_monitored_group(f"KVBench generation threads={threads}", command_specs, cpu_count)
    for output_path in output_paths:
        remove_output_prefix(output_path)
    result.update({
        "threads": threads,
        "parallelization": "sharded independent KVBench processes because KVBench has no native thread-count option",
        "insert_count": load_count,
        "range_query_count": range_query_count,
        "scan_length": 100,
    })
    return result

def add_cpu_work_metric(result, total_ops):
    result["cpu_seconds_per_million_ops"] = (
        result["cpu_seconds"] / (total_ops / 1_000_000.0) if total_ops > 0 else None
    )
    return result


def add_common_window_metric(results, cpu_count):
    common_window_s = max(result["duration_s"] for result in results.values())
    for result in results.values():
        result["common_window_duration_s"] = common_window_s
        result["common_window_avg_cpu_percent"] = (
            min(100.0, result["cpu_seconds"] / common_window_s / cpu_count * 100.0)
            if common_window_s > 0 else 0.0
        )
    return results


def run_for_threads(workload, load_count, range_query_count, threads, cpu_count):
    banner(f"workload generation with {threads} threads")
    total_ops = load_count + range_query_count
    results = {
        "ycsb": add_cpu_work_metric(generate_ycsb(workload, load_count, range_query_count, threads, cpu_count), total_ops),
        "tectonic": add_cpu_work_metric(generate_tectonic(load_count, range_query_count, threads, cpu_count), total_ops),
        "kvbench": add_cpu_work_metric(generate_kvbench(load_count, range_query_count, threads, cpu_count), total_ops),
    }
    add_common_window_metric(results, cpu_count)
    return {"threads": threads, **results}

def write_results(payload):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RESULTS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, RESULTS_PATH)


def main():
    parser = argparse.ArgumentParser(description="Run pidstat-only CPU utilization for multithreaded workload generation.")
    parser.add_argument("--workload", choices=["e"], default=DEFAULT_WORKLOAD)
    parser.add_argument("--scale", type=int, default=DEFAULT_SCALE)
    parser.add_argument("--load-count", type=int, help="number of load-phase inserts; defaults to --scale")
    parser.add_argument("--range-query-count", type=int, help="number of run-phase range scans; defaults to --scale")
    parser.add_argument("--threads", help="comma-separated thread counts for the sweep")
    parser.add_argument("--time-series-threads", type=int, help="thread count to use for the time-series plot")
    parser.add_argument("--min-free-gib", type=float, default=20.0, help="minimum free GiB to keep after estimated peak tmp usage")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    ensure_file(TECTONIC_CLI, "tectonic-cli")
    ensure_file(KV_BENCH, "KVBench load_gen")
    ensure_file(YCSB_DIR / "workloads" / f"workload{args.workload}", "YCSB workload")
    load_count = args.load_count if args.load_count is not None else args.scale
    range_query_count = args.range_query_count if args.range_query_count is not None else args.scale
    if load_count < 1 or range_query_count < 1:
        fail("--load-count and --range-query-count must be positive")

    cpu_count = available_cpu_count()
    thread_counts = parse_threads(args.threads, cpu_count) if args.threads else default_threads(cpu_count)
    time_series_threads = args.time_series_threads or max(thread_counts)
    if time_series_threads not in thread_counts:
        fail("--time-series-threads must be included in --threads")
    check_temp_space(load_count, range_query_count, args.min_free_gib)

    print(SEP)
    print("  experiment : CPU-utilization pidstat, workload generation only")
    print("  composition: range-heavy fixed-length range-scan generation")
    print(f"  YCSB file  : workload{args.workload}, overridden to zipfian fixed-length scans")
    print(f"  load       : {load_count:,} inserts")
    print(f"  run        : {range_query_count:,} fixed-length range scans")
    print(f"  cpu count  : {cpu_count} available logical cpus")
    print(f"  threads    : {thread_counts}")
    print(f"  time series: {time_series_threads} threads")
    print("  metric     : summed pidstat process %CPU divided by available logical cpus")
    print("  KVBench    : N threads are represented by N sharded generator processes")
    print(SEP, flush=True)

    runs = []
    for threads in thread_counts:
        runs.append(run_for_threads(args.workload, load_count, range_query_count, threads, cpu_count))
    plotted_time_series_threads = choose_plotted_time_series_threads(runs, time_series_threads)
    if plotted_time_series_threads != time_series_threads:
        print(
            f"  time series fallback: requested {time_series_threads} threads, "
            f"plotting {plotted_time_series_threads} threads because pidstat samples were missing",
            flush=True,
        )

    payload = {
        "experiment": "CPU-utilization",
        "mode": "pidstat_multithread_workload_generation",
        "workload": "range_heavy_common_zipf_range_scan",
        "scale": args.scale,
        "load_count": load_count,
        "range_query_count": range_query_count,
        "cpu_count": cpu_count,
        "thread_counts": thread_counts,
        "requested_time_series_threads": time_series_threads,
        "time_series_threads": plotted_time_series_threads,
        "time_series_thread_note": "falls back to the highest thread count with pidstat samples for all tools",
        "composition": {
            "load_inserts": load_count,
            "execution_range_queries": range_query_count,
            "range_scan_length": 100,
            "selection_distribution": "zipfian",
            "value_size_bytes": "approximately 1000 bytes for generated values",
            "rationale": "range-heavy run phase emphasizes compact start-count range-query generation without scaling the value-heavy load phase",
        },
        "cpu_metric": "average cpu utilization across all available logical cpus; summed pidstat process %CPU divided by cpu_count",
        "cpu_time_metric": "wait4 CPU seconds divided by each generator wall time and cpu_count",
        "common_window_cpu_metric": "wait4 CPU seconds divided by the slowest generator wall time for the same thread count and cpu_count",
        "total_generated_operations_per_tool": load_count + range_query_count,
        "monitor": "pidstat",
        "kvbench_parallelization": "sharded independent KVBench processes because KVBench has no native thread-count option",
        "estimated_peak_temp_bytes": estimate_peak_temp_bytes(load_count, range_query_count),
        "runs": runs,
    }
    write_results(payload)
    print(f"\n  results saved: {RESULTS_PATH}", flush=True)
    if not args.no_plot:
        subprocess.run(["python3", str(PLOT_SCRIPT)], check=True)


if __name__ == "__main__":
    main()
