#!/usr/bin/env python3
"""Pidstat-only CPU utilization for scaled YCSB-E workload generation."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time


ROOT_DIR = Path(__file__).resolve().parents[1]
HARNESS_DIR = ROOT_DIR / "rocksdb-benchmark-harness"
TECTONIC_CLI = Path(os.environ.get("TECTONIC_CLI", str(ROOT_DIR / "target/release/tectonic-cli")))
YCSB_SPEC = ROOT_DIR / "example-specs/ycsb/e.spec.json"
KV_BENCH = Path(os.environ.get("KV_BENCH_BIN", "/home/cc/KV-WorkloadGenerator/bin/load_gen"))
YCSB_DIR = Path(os.environ.get("YCSB_DIR", str(HARNESS_DIR / "vendor/YCSB")))
M2 = Path(os.environ.get("M2_REPO", "/home/cc/.m2/repository"))
OUT_DIR = ROOT_DIR / "data/CPU-utilization"
TMP_DIR = Path("/tmp/tectonic_cpu_pidstat_multithread")
RESULTS_PATH = OUT_DIR / "pidstat_multithread_generation_results.json"
PLOT_SCRIPT = ROOT_DIR / "plot_scripts/plot_cpu_pidstat_multithread_generation.py"
PIDSTAT_INTERVAL_S = 1
SEP = "=" * 72
GIB = 1024 ** 3

WORKLOAD_NAME = "YCSB-E"
YCSB_WORKLOAD_FILE = "workloade"
SCALE_MULTIPLIER = 10
BASE_LOAD_COUNT = 1_000_000
BASE_OPERATION_COUNT = 1_000_000
LOAD_COUNT = BASE_LOAD_COUNT * SCALE_MULTIPLIER
OPERATION_COUNT = BASE_OPERATION_COUNT * SCALE_MULTIPLIER
RUN_RANGE_QUERY_COUNT = int(OPERATION_COUNT * 0.95)
RUN_INSERT_COUNT = OPERATION_COUNT - RUN_RANGE_QUERY_COUNT
TOTAL_GENERATED_OPS = LOAD_COUNT + OPERATION_COUNT
SCAN_LENGTH = 100
REPRODUCTION_COMMAND = (
    "python3 run_scripts/run_cpu_pidstat_multithread_generation.py "
    "--threads 2,4,8,16,32,48 --time-series-threads 48"
)

YCSB_CP = ":".join([
    str(YCSB_DIR / "file/conf"),
    str(YCSB_DIR / "file/target/file-binding-0.18.0-SNAPSHOT.jar"),
    str(M2 / "org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar"),
    str(M2 / "org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar"),
    str(M2 / "org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar"),
    str(M2 / "org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar"),
    str(YCSB_DIR / "core/target/core-0.18.0-SNAPSHOT.jar"),
])

MULTI_TOOL_KEYS = ("ycsb", "tectonic")
SINGLE_TOOL_KEYS = ("ycsb", "tectonic", "kvbench")


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
    values = [2, 4, 8, 16, 32, cpu_count]
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


def fmt_gib(num_bytes):
    return f"{num_bytes / GIB:.2f} GiB"


def estimate_peak_temp_bytes():
    ycsb_load = LOAD_COUNT * 1_500
    ycsb_run = RUN_RANGE_QUERY_COUNT * 96 + RUN_INSERT_COUNT * 1_500
    tectonic = (LOAD_COUNT + RUN_INSERT_COUNT) * 1_200 + RUN_RANGE_QUERY_COUNT * 96
    kvbench = (LOAD_COUNT + RUN_INSERT_COUNT) * 1_200 + RUN_RANGE_QUERY_COUNT * 128
    return int(max(ycsb_load, ycsb_run, tectonic, kvbench) * 1.5)


def check_temp_space(min_free_gib):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(TMP_DIR)
    estimated_peak = estimate_peak_temp_bytes()
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


def run_monitored(label, cmd, log_path, cpu_count, cwd=None, env=None):
    print(f"\n  [{label}] {' '.join(cmd)}", flush=True)
    start_time = time.monotonic()
    stop_event = threading.Event()
    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log_file, stderr=subprocess.STDOUT)
        collector = PidstatCollector([proc.pid], start_time, cpu_count, stop_event)
        collector.start()
        _, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        end_time = time.monotonic()
        stop_event.set()
        collector.join()

    duration_s = end_time - start_time
    cpu_seconds = max(0.0, usage.ru_utime + usage.ru_stime)
    cpu_time_avg = min(100.0, (cpu_seconds / duration_s / cpu_count * 100.0) if duration_s > 0 else 0.0)
    pidstat_values = [sample["avg_cpu_percent"] for sample in collector.samples]
    pidstat_avg = sum(pidstat_values) / len(pidstat_values) if pidstat_values else None
    pidstat_max = max(pidstat_values) if pidstat_values else None
    print(
        f"  [{label}] duration={duration_s:.2f}s cpu_seconds={cpu_seconds:.2f}s "
        f"cpu_time_avg_all_cores={cpu_time_avg:.2f}% pidstat_samples={len(pidstat_values)}",
        flush=True,
    )
    if proc.returncode != 0:
        print(tail_file(log_path), file=sys.stderr)
        fail(f"{label} failed with exit code {proc.returncode}; see {log_path}")
    return {
        "cmds": [cmd],
        "cwd": str(cwd) if cwd else None,
        "log_paths": [str(log_path)],
        "worker_pids": [proc.pid],
        "worker_count": 1,
        "duration_s": duration_s,
        "cpu_seconds": cpu_seconds,
        "cpu_time_avg_cpu_percent": cpu_time_avg,
        "pidstat_avg_cpu_percent": pidstat_avg,
        "pidstat_max_cpu_percent": pidstat_max,
        "pidstat_samples": collector.samples,
    }


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
    return all(run[key].get("pidstat_samples") for key in MULTI_TOOL_KEYS)


def choose_plotted_time_series_threads(runs, requested_threads):
    for run in runs:
        if int(run["threads"]) == int(requested_threads) and has_time_series_samples(run):
            return requested_threads
    sampled = [int(run["threads"]) for run in runs if has_time_series_samples(run)]
    if sampled:
        return max(sampled)
    return requested_threads


def ycsb_command(output_path, threads, transaction_phase):
    cmd = [
        "java", "-cp", YCSB_CP, "site.ycsb.Client",
        "-db", "site.ycsb.db.FileClient",
        "-P", f"workloads/{YCSB_WORKLOAD_FILE}",
        "-p", f"recordcount={LOAD_COUNT}",
        "-p", f"operationcount={OPERATION_COUNT}",
        "-p", "fieldcount=10",
        "-p", "fieldlength=100",
        "-p", "readallfields=true",
        "-p", f"minscanlength={SCAN_LENGTH}",
        "-p", f"maxscanlength={SCAN_LENGTH}",
        "-p", "scanlengthdistribution=uniform",
        "-p", f"file.output={output_path}",
        "-threads", str(threads),
    ]
    cmd.append("-t" if transaction_phase else "-load")
    return cmd


def generate_ycsb(threads, cpu_count):
    tag = f"ycsbe10x_threads{threads}"
    load_path = TMP_DIR / f"{tag}_load.txt"
    run_path = TMP_DIR / f"{tag}_run.txt"
    for path in (load_path, run_path):
        remove_output_prefix(path)
    load_result = run_monitored(
        f"YCSB load generation threads={threads}",
        ycsb_command(load_path, threads, transaction_phase=False),
        TMP_DIR / f"{tag}_load.log",
        cpu_count,
        cwd=YCSB_DIR,
    )
    remove_output_prefix(load_path)
    run_result = run_monitored(
        f"YCSB run generation threads={threads}",
        ycsb_command(run_path, threads, transaction_phase=True),
        TMP_DIR / f"{tag}_run.log",
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


def generate_tectonic(threads, cpu_count):
    tag = f"tectonic_ycsbe10x_threads{threads}"
    output_path = TMP_DIR / f"{tag}.txt"
    remove_output_prefix(output_path)
    env = os.environ.copy()
    env["TECTONIC_PARALLEL_GEN"] = "1"
    result = run_monitored(
        f"Tectonic generation threads={threads}",
        [str(TECTONIC_CLI), "generate", "-w", str(YCSB_SPEC), "-o", str(output_path), "-t", str(threads), "-s", str(SCALE_MULTIPLIER)],
        TMP_DIR / f"{tag}.log",
        cpu_count,
        cwd=ROOT_DIR,
        env=env,
    )
    remove_output_prefix(output_path)
    result.update({
        "threads": threads,
        "spec_path": str(YCSB_SPEC),
        "scale_multiplier": SCALE_MULTIPLIER,
        "load_count": LOAD_COUNT,
        "operation_count": OPERATION_COUNT,
    })
    return result


def kvbench_command(output_path):
    effective_insert_count = LOAD_COUNT + RUN_INSERT_COUNT
    range_selectivity = min(1.0, SCAN_LENGTH / max(1, effective_insert_count))
    return [
        str(KV_BENCH),
        f"--insert={effective_insert_count}",
        f"--range_query={RUN_RANGE_QUERY_COUNT}",
        f"--range_query_selectivity={range_selectivity}",
        "--entry_size=1024",
        "--lambda=0.02",
        f"--output-path={output_path}",
    ]


def generate_kvbench_single(cpu_count):
    tag = "kvbench_ycsbe10x_threads1"
    output_path = TMP_DIR / f"{tag}.txt"
    remove_output_prefix(output_path)
    result = run_monitored(
        "KVBench generation threads=1",
        kvbench_command(output_path),
        TMP_DIR / f"{tag}.log",
        cpu_count,
        cwd=ROOT_DIR,
    )
    remove_output_prefix(output_path)
    result.update({
        "threads": 1,
        "insert_count": LOAD_COUNT + RUN_INSERT_COUNT,
        "range_query_count": RUN_RANGE_QUERY_COUNT,
        "range_query_selectivity": min(1.0, SCAN_LENGTH / max(1, LOAD_COUNT + RUN_INSERT_COUNT)),
        "scan_length": SCAN_LENGTH,
    })
    return result


def add_cpu_work_metric(result):
    result["cpu_seconds_per_million_ops"] = result["cpu_seconds"] / (TOTAL_GENERATED_OPS / 1_000_000.0)
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


def run_for_threads(threads, cpu_count):
    banner(f"{WORKLOAD_NAME} 10x workload generation with {threads} threads")
    results = {
        "ycsb": add_cpu_work_metric(generate_ycsb(threads, cpu_count)),
        "tectonic": add_cpu_work_metric(generate_tectonic(threads, cpu_count)),
    }
    add_common_window_metric(results, cpu_count)
    return {"threads": threads, **results}


def run_single_thread(cpu_count):
    banner(f"{WORKLOAD_NAME} 10x single-thread generation with KVBench")
    results = {
        "ycsb": add_cpu_work_metric(generate_ycsb(1, cpu_count)),
        "tectonic": add_cpu_work_metric(generate_tectonic(1, cpu_count)),
        "kvbench": add_cpu_work_metric(generate_kvbench_single(cpu_count)),
    }
    add_common_window_metric(results, cpu_count)
    return {"threads": 1, **results}


def write_results(payload):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RESULTS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, RESULTS_PATH)


def main():
    parser = argparse.ArgumentParser(
        description="Run pidstat-only CPU utilization for 10x YCSB-E workload generation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "reproduce the CPU-utilization figures on this machine:\n"
            f"  {REPRODUCTION_COMMAND}\n\n"
            "override external tool locations with TECTONIC_CLI, YCSB_DIR, KV_BENCH_BIN, and M2_REPO."
        ),
    )
    parser.add_argument("--threads", help="comma-separated thread counts for the multithread sweep")
    parser.add_argument("--time-series-threads", type=int, help="thread count to use for the time-series plot")
    parser.add_argument("--min-free-gib", type=float, default=20.0, help="minimum free GiB to keep after estimated peak tmp usage")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    ensure_file(TECTONIC_CLI, "tectonic-cli")
    ensure_file(YCSB_SPEC, "Tectonic YCSB-E spec")
    ensure_file(KV_BENCH, "KVBench load_gen")
    ensure_file(YCSB_DIR / "workloads" / YCSB_WORKLOAD_FILE, "YCSB-E workload")
    cpu_count = available_cpu_count()
    thread_counts = parse_threads(args.threads, cpu_count) if args.threads else default_threads(cpu_count)
    time_series_threads = args.time_series_threads or max(thread_counts)
    if time_series_threads not in thread_counts:
        fail("--time-series-threads must be included in --threads")
    check_temp_space(args.min_free_gib)

    print(SEP)
    print("  experiment : CPU-utilization pidstat, workload generation only")
    print("  workload   : 10x YCSB-E short ranges")
    print(f"  spec       : {YCSB_SPEC}")
    print(f"  load       : {LOAD_COUNT:,} inserts")
    print(f"  run        : {RUN_RANGE_QUERY_COUNT:,} scans and {RUN_INSERT_COUNT:,} inserts")
    print(f"  scan len   : {SCAN_LENGTH}")
    print(f"  cpu count  : {cpu_count} available logical cpus")
    print(f"  threads    : {thread_counts}")
    print(f"  time series: {time_series_threads} threads")
    print("  multithread: YCSB and Tectonic")
    print("  single     : YCSB, Tectonic, and KVBench")
    print("  metric     : summed pidstat process %CPU divided by available logical cpus")
    print(SEP, flush=True)

    runs = []
    for threads in thread_counts:
        runs.append(run_for_threads(threads, cpu_count))
    single_thread = run_single_thread(cpu_count)
    plotted_time_series_threads = choose_plotted_time_series_threads(runs, time_series_threads)
    if plotted_time_series_threads != time_series_threads:
        print(
            f"  time series fallback: requested {time_series_threads} threads, "
            f"plotting {plotted_time_series_threads} threads because pidstat samples were missing",
            flush=True,
        )

    payload = {
        "experiment": "CPU-utilization",
        "mode": "pidstat_ycsbe_10x_workload_generation",
        "workload": WORKLOAD_NAME,
        "workload_file": YCSB_WORKLOAD_FILE,
        "scale_multiplier": SCALE_MULTIPLIER,
        "tectonic_spec_path": str(YCSB_SPEC),
        "multithread_tools": ["YCSB", "Tectonic"],
        "single_thread_tools": ["YCSB", "Tectonic", "KVBench"],
        "load_count": LOAD_COUNT,
        "operation_count": OPERATION_COUNT,
        "run_range_query_count": RUN_RANGE_QUERY_COUNT,
        "run_insert_count": RUN_INSERT_COUNT,
        "scan_length": SCAN_LENGTH,
        "cpu_count": cpu_count,
        "thread_counts": thread_counts,
        "requested_time_series_threads": time_series_threads,
        "time_series_threads": plotted_time_series_threads,
        "time_series_thread_note": "falls back to the highest thread count with pidstat samples for both multithreaded tools",
        "composition": {
            "standard_workload": "YCSB workload E short ranges, scaled by 10x",
            "load_inserts": LOAD_COUNT,
            "execution_operations": OPERATION_COUNT,
            "execution_range_queries": RUN_RANGE_QUERY_COUNT,
            "execution_inserts": RUN_INSERT_COUNT,
            "range_scan_length": SCAN_LENGTH,
            "selection_distribution": "zipfian",
            "value_size_bytes": "approximately 1000 bytes for inserted records",
            "rationale": "YCSB-E is the standard scan-heavy YCSB workload; scaling by 10x gives pidstat enough time-series samples while preserving the same workload across all plots.",
        },
        "cpu_metric": "average cpu utilization across all available logical cpus; summed pidstat process %CPU divided by cpu_count",
        "cpu_time_metric": "wait4 CPU seconds divided by generated operations",
        "common_window_cpu_metric": "wait4 CPU seconds divided by the slowest generator wall time for the same thread count and cpu_count",
        "total_generated_operations_per_tool": TOTAL_GENERATED_OPS,
        "monitor": "pidstat",
        "reproduction_command": REPRODUCTION_COMMAND,
        "figure_intent": {
            "cpu_utilization_pidstat_multithread_timeseries.pdf": "shows instantaneous average CPU utilization across all available logical CPUs at the largest thread count; a generator drops to 0 after it finishes, so shorter occupancy means less CPU capacity held over time",
            "cpu_utilization_pidstat_multithread_by_threads.pdf": "shows shared-window average CPU utilization across all available logical CPUs for each thread count; the denominator is the slower generator's wall time at that thread count",
            "cpu_utilization_cpu_time_by_threads.pdf": "primary resource-efficiency figure; shows total CPU seconds per million generated operations for the same YCSB-E workload as threads scale",
            "cpu_utilization_single_thread_cpu_time.pdf": "single-thread CPU seconds per million generated operations, including KVBench as a sequential baseline",
            "cpu_utilization_single_thread_cpu_utilization.pdf": "single-thread active CPU utilization normalized to one core; this checks saturation rather than resource efficiency",
        },
        "metric_definitions": {
            "pidstat_sample_avg_cpu_percent": "at each sample, sum pidstat %CPU for the generator process and divide by available logical CPUs; 100 means all available logical CPUs are busy",
            "shared_window_avg_cpu_percent": "CPU seconds divided by the slower generator wall time for the same thread count, then divided by available logical CPUs; 100 means all CPUs were occupied for the entire shared window",
            "cpu_seconds_per_million_ops": "wait4 user+system CPU seconds divided by generated operations in millions; lower means fewer CPU cycles consumed per generated operation",
            "single_thread_one_core_utilization_percent": "wait4 user+system CPU seconds divided by generator wall time; 100 means one fully occupied core while the generator is active",
        },
        "estimated_peak_temp_bytes": estimate_peak_temp_bytes(),
        "runs": runs,
        "single_thread": single_thread,
    }
    write_results(payload)
    print(f"\n  results saved: {RESULTS_PATH}", flush=True)
    if not args.no_plot:
        subprocess.run(["python3", str(PLOT_SCRIPT)], check=True)


if __name__ == "__main__":
    main()
