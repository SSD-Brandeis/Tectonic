#!/usr/bin/env python3
"""Repeat the concurrent blind generation experiment with resource monitoring."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
EXPERIMENT_NAME = "concurrent_blind_generation_resource_monitoring"
DATA_DIR = ROOT_DIR / "data" / EXPERIMENT_NAME
LOG_DIR = DATA_DIR / "logs"
PLOT_DIR = DATA_DIR / "plots"
RESULTS_PATH = DATA_DIR / f"{EXPERIMENT_NAME}_results.json"
RUNNER_SCRIPT = ROOT_DIR / "run_scripts" / f"run_{EXPERIMENT_NAME}.py"
PLOT_SCRIPT = ROOT_DIR / "plot_scripts" / f"plot_{EXPERIMENT_NAME}.py"

SOURCE_RUNNER = ROOT_DIR / "run_scripts" / "run_concurrent_blind_generation_experiment.py"
SOURCE_PLOT = ROOT_DIR / "plot_scripts" / "plot_concurrent_blind_generation_experiment.py"
SOURCE_RESULTS = ROOT_DIR / "data" / "concurrent_blind_experiment" / "results.json"
SOURCE_PDF = ROOT_DIR / "blind_unique_compare" / "generation_speedup_comparison.pdf"

TECTONIC_CLI = ROOT_DIR / "target" / "release" / "tectonic-cli"
YCSB_DIR = ROOT_DIR / "rocksdb-benchmark-harness" / "vendor" / "YCSB"
M2 = Path("/home/cc/.m2/repository")

YCSB_CP = (
    f"{YCSB_DIR}/file/conf:"
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar:"
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar:"
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar:"
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar:"
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar:"
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar"
)

THREADS_LIST = [1] + list(range(2, 62, 2))
ITERATIONS = 1
POLL_INTERVAL_S = 0.25
RECORD_COUNT = 10_000_000
OPERATION_COUNT = 10_000_000
SCALE = 10
TMP_OUTPUTS = {
    "ycsb": Path("/tmp/ycsb_out.txt"),
    "tectonic": Path("/tmp/tectonic_out.txt"),
    "tectonic_unique": Path("/tmp/tectonic_unique_out.txt"),
}
TOOLS = ("ycsb", "tectonic", "tectonic_unique")
CLK_TCK = os.sysconf(os.sysconf_names["SC_CLK_TCK"])


def cpu_count() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def parse_threads(value: str | None) -> list[int]:
    if value is None:
        return THREADS_LIST
    threads: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_s, end_s = item.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            threads.extend(range(start, end + 1))
        else:
            threads.append(int(item))
    if not threads:
        raise ValueError("no thread counts provided")
    return sorted(dict.fromkeys(threads))


def ensure_setup() -> None:
    if not TECTONIC_CLI.exists():
        raise FileNotFoundError(f"tectonic cli not found: {TECTONIC_CLI}")
    if not YCSB_DIR.exists():
        raise FileNotFoundError(f"YCSB directory not found: {YCSB_DIR}")
    for path in (DATA_DIR, LOG_DIR, PLOT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def check_tmp_space(min_free_gib: float) -> None:
    usage = shutil.disk_usage("/tmp")
    free_gib = usage.free / (1024**3)
    print(f"tmp free space: {free_gib:.1f} GiB")
    if free_gib < min_free_gib:
        raise RuntimeError(
            f"not enough /tmp space for exact-scale run: free={free_gib:.1f} GiB, "
            f"required={min_free_gib:.1f} GiB"
        )


def cleanup_files() -> None:
    for path in TMP_OUTPUTS.values():
        path.unlink(missing_ok=True)
    for t in range(128):
        for prefix in (TMP_OUTPUTS["tectonic"], TMP_OUTPUTS["tectonic_unique"]):
            Path(f"{prefix}.{t}").unlink(missing_ok=True)


def process_exists(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def read_proc_status(pid: int) -> dict[str, int]:
    metrics = {"vmrss_kb": 0, "vmhwm_kb": 0, "vmsize_kb": 0, "threads": 0}
    try:
        text = Path(f"/proc/{pid}/status").read_text(errors="replace")
    except (FileNotFoundError, ProcessLookupError):
        return metrics
    key_map = {
        "VmRSS": "vmrss_kb",
        "VmHWM": "vmhwm_kb",
        "VmSize": "vmsize_kb",
        "Threads": "threads",
    }
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        out_key = key_map.get(name)
        if out_key is None:
            continue
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            metrics[out_key] = int(parts[0])
        except ValueError:
            pass
    return metrics


def read_proc_cpu_seconds(pid: int) -> float:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(errors="replace")
    except (FileNotFoundError, ProcessLookupError):
        return 0.0
    close = text.rfind(")")
    if close < 0:
        return 0.0
    fields = text[close + 2 :].split()
    if len(fields) <= 12:
        return 0.0
    try:
        utime = int(fields[11])
        stime = int(fields[12])
    except ValueError:
        return 0.0
    return (utime + stime) / float(CLK_TCK)


class ResourceCollector:
    def __init__(self, pid: int, start_time: float, poll_interval: float, stop_event: threading.Event):
        self.pid = pid
        self.start_time = start_time
        self.poll_interval = poll_interval
        self.stop_event = stop_event
        self.samples: list[dict[str, float | int]] = []
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def join(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def add_sample(self) -> None:
        status = read_proc_status(self.pid)
        sample = {
            "elapsed_s": time.monotonic() - self.start_time,
            "proc_cpu_seconds": read_proc_cpu_seconds(self.pid),
            **status,
        }
        with self._lock:
            self.samples.append(sample)

    def _loop(self) -> None:
        while not self.stop_event.is_set() and process_exists(self.pid):
            self.add_sample()
            time.sleep(self.poll_interval)
        self.add_sample()


def tail_file(path: Path, max_lines: int = 80) -> str:
    try:
        return "".join(path.read_text(errors="replace").splitlines(True)[-max_lines:])
    except FileNotFoundError:
        return ""


def peak(samples: list[dict[str, float | int]], key: str) -> int:
    return max((int(sample.get(key, 0)) for sample in samples), default=0)


def sample_cpu_stats(samples: list[dict[str, float | int]], cpu_total: int) -> dict[str, float | None]:
    if len(samples) < 2:
        return {
            "sample_avg_process_cpu_percent": None,
            "sample_peak_process_cpu_percent": None,
            "sample_avg_all_cores_cpu_percent": None,
            "sample_peak_all_cores_cpu_percent": None,
        }
    ordered = sorted(samples, key=lambda sample: float(sample["elapsed_s"]))
    first = ordered[0]
    last = ordered[-1]
    elapsed = float(last["elapsed_s"]) - float(first["elapsed_s"])
    cpu_delta = float(last["proc_cpu_seconds"]) - float(first["proc_cpu_seconds"])
    avg_process = (cpu_delta / elapsed * 100.0) if elapsed > 0 else None
    peaks: list[float] = []
    for left, right in zip(ordered, ordered[1:]):
        dt_s = float(right["elapsed_s"]) - float(left["elapsed_s"])
        dcpu_s = float(right["proc_cpu_seconds"]) - float(left["proc_cpu_seconds"])
        if dt_s > 0 and dcpu_s >= 0:
            peaks.append(dcpu_s / dt_s * 100.0)
    peak_process = max(peaks) if peaks else None
    return {
        "sample_avg_process_cpu_percent": avg_process,
        "sample_peak_process_cpu_percent": peak_process,
        "sample_avg_all_cores_cpu_percent": (avg_process / cpu_total) if avg_process is not None else None,
        "sample_peak_all_cores_cpu_percent": (peak_process / cpu_total) if peak_process is not None else None,
    }


def run_monitored(
    label: str,
    cmd: list[str],
    log_path: Path,
    env: dict[str, str] | None,
    cwd: Path | None,
    poll_interval: float,
    cpu_total: int,
) -> dict[str, Any]:
    print(f"  [{label}] {' '.join(cmd)}", flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start_time = time.monotonic()
    stop_event = threading.Event()
    with log_path.open("w") as log_file:
        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=str(cwd) if cwd is not None else None,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        collector = ResourceCollector(proc.pid, start_time, poll_interval, stop_event)
        collector.start()
        _, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        end_time = time.monotonic()
        stop_event.set()
        collector.join()

    duration_s = end_time - start_time
    cpu_seconds_value = max(0.0, usage.ru_utime + usage.ru_stime)
    wait4_process_cpu = (cpu_seconds_value / duration_s * 100.0) if duration_s > 0 else 0.0
    result = {
        "label": label,
        "cmd": cmd,
        "cwd": str(cwd) if cwd is not None else None,
        "log_path": str(log_path),
        "pid": proc.pid,
        "return_code": proc.returncode,
        "duration_s": duration_s,
        "cpu_seconds": cpu_seconds_value,
        "process_cpu_percent_from_wait4": wait4_process_cpu,
        "all_cores_cpu_percent_from_wait4": wait4_process_cpu / cpu_total,
        "ru_maxrss_kb": int(usage.ru_maxrss),
        "resource_samples": collector.samples,
        "peak_vmrss_kb": peak(collector.samples, "vmrss_kb"),
        "peak_vmhwm_kb": max(int(usage.ru_maxrss), peak(collector.samples, "vmhwm_kb")),
        "peak_vmsize_kb": peak(collector.samples, "vmsize_kb"),
        "peak_threads": peak(collector.samples, "threads"),
    }
    result.update(sample_cpu_stats(collector.samples, cpu_total))
    print(
        f"  [{label}] duration={duration_s:.3f}s "
        f"cpu={wait4_process_cpu:.1f}% peak_hwm={result['peak_vmhwm_kb']} KiB "
        f"log={log_path}",
        flush=True,
    )
    return result


def output_file_metrics(output_path: Path) -> dict[str, Any]:
    paths = []
    if output_path.exists():
        paths.append(output_path)
    paths.extend(sorted(output_path.parent.glob(f"{output_path.name}.*")))
    files = [{"path": str(path), "bytes": path.stat().st_size} for path in paths if path.exists()]
    return {
        "output_path": str(output_path),
        "output_file_count": len(files),
        "output_bytes": sum(item["bytes"] for item in files),
        "output_files": files,
    }


def combine_phase_results(
    tool: str,
    threads: int,
    iteration: int,
    phases: list[dict[str, Any]],
    output_path: Path,
    cpu_total: int,
) -> dict[str, Any]:
    duration_s = sum(float(phase["duration_s"]) for phase in phases)
    cpu_seconds_value = sum(float(phase["cpu_seconds"]) for phase in phases)
    process_cpu = (cpu_seconds_value / duration_s * 100.0) if duration_s > 0 else 0.0
    successful = all(int(phase["return_code"]) == 0 for phase in phases)
    peak_sample_cpu = [
        phase.get("sample_peak_process_cpu_percent")
        for phase in phases
        if phase.get("sample_peak_process_cpu_percent") is not None
    ]
    result = {
        "tool": tool,
        "threads": threads,
        "iteration": iteration,
        "return_code": 0 if successful else 1,
        "duration_s": duration_s,
        "cpu_seconds": cpu_seconds_value,
        "process_cpu_percent_from_wait4": process_cpu,
        "all_cores_cpu_percent_from_wait4": process_cpu / cpu_total,
        "peak_vmrss_kb": max((int(phase["peak_vmrss_kb"]) for phase in phases), default=0),
        "peak_vmhwm_kb": max((int(phase["peak_vmhwm_kb"]) for phase in phases), default=0),
        "peak_vmsize_kb": max((int(phase["peak_vmsize_kb"]) for phase in phases), default=0),
        "peak_threads": max((int(phase["peak_threads"]) for phase in phases), default=0),
        "sample_peak_process_cpu_percent": max(peak_sample_cpu) if peak_sample_cpu else None,
        "sample_peak_all_cores_cpu_percent": (max(peak_sample_cpu) / cpu_total) if peak_sample_cpu else None,
        "phases": phases,
    }
    result.update(output_file_metrics(output_path))
    return result


def ycsb_load_cmd(threads: int) -> list[str]:
    return [
        "java",
        "-cp",
        YCSB_CP,
        "site.ycsb.Client",
        "-db",
        "site.ycsb.db.FileClient",
        "-P",
        "workloads/workloadc",
        "-p",
        f"file.output={TMP_OUTPUTS['ycsb']}",
        "-p",
        f"recordcount={RECORD_COUNT}",
        "-p",
        f"operationcount={OPERATION_COUNT}",
        "-threads",
        str(threads),
        "-load",
    ]


def ycsb_run_cmd(threads: int) -> list[str]:
    return [
        "java",
        "-cp",
        YCSB_CP,
        "site.ycsb.Client",
        "-db",
        "site.ycsb.db.FileClient",
        "-P",
        "workloads/workloadc",
        "-p",
        f"file.output={TMP_OUTPUTS['ycsb']}",
        "-p",
        f"recordcount={RECORD_COUNT}",
        "-p",
        f"operationcount={OPERATION_COUNT}",
        "-threads",
        str(threads),
        "-t",
    ]


def tectonic_cmd(threads: int, unique: bool) -> list[str]:
    spec = ROOT_DIR / "example-specs" / ("ycsb-unique" if unique else "ycsb_blind") / "c.spec.json"
    output = TMP_OUTPUTS["tectonic_unique" if unique else "tectonic"]
    return [
        str(TECTONIC_CLI),
        "generate",
        "-w",
        str(spec),
        "-o",
        str(output),
        "-s",
        str(SCALE),
        "-t",
        str(threads),
    ]


def run_ycsb(threads: int, iteration: int, poll_interval: float, cpu_total: int) -> dict[str, Any] | None:
    cleanup_files()
    phases = []
    load = run_monitored(
        "ycsb_load",
        ycsb_load_cmd(threads),
        LOG_DIR / f"{EXPERIMENT_NAME}_ycsb_threads_{threads:02d}_iter_{iteration}_load.log",
        None,
        YCSB_DIR,
        poll_interval,
        cpu_total,
    )
    phases.append(load)
    if load["return_code"] != 0:
        print(tail_file(Path(load["log_path"])), file=sys.stderr)
        return None
    run = run_monitored(
        "ycsb_run",
        ycsb_run_cmd(threads),
        LOG_DIR / f"{EXPERIMENT_NAME}_ycsb_threads_{threads:02d}_iter_{iteration}_run.log",
        None,
        YCSB_DIR,
        poll_interval,
        cpu_total,
    )
    phases.append(run)
    if run["return_code"] != 0:
        print(tail_file(Path(run["log_path"])), file=sys.stderr)
        return None
    return combine_phase_results("ycsb", threads, iteration, phases, TMP_OUTPUTS["ycsb"], cpu_total)


def run_tectonic(
    tool: str,
    threads: int,
    iteration: int,
    poll_interval: float,
    cpu_total: int,
) -> dict[str, Any] | None:
    cleanup_files()
    unique = tool == "tectonic_unique"
    env = os.environ.copy()
    env["TECTONIC_PARALLEL_GEN"] = "1"
    result = run_monitored(
        tool,
        tectonic_cmd(threads, unique),
        LOG_DIR / f"{EXPERIMENT_NAME}_{tool}_threads_{threads:02d}_iter_{iteration}.log",
        env,
        None,
        poll_interval,
        cpu_total,
    )
    if result["return_code"] != 0:
        print(tail_file(Path(result["log_path"])), file=sys.stderr)
        return None
    combined = combine_phase_results(tool, threads, iteration, [result], TMP_OUTPUTS[tool], cpu_total)
    return combined


def best_run(runs: list[dict[str, Any]], tool: str, threads: int) -> dict[str, Any] | None:
    candidates = [
        run
        for run in runs
        if run.get("tool") == tool and int(run.get("threads", -1)) == threads and int(run.get("return_code", 1)) == 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda run: float(run["duration_s"]))


def summary_from_runs(runs: list[dict[str, Any]], threads_list: list[int]) -> dict[str, Any]:
    summary: dict[str, Any] = {"threads": threads_list}
    metric_map = {
        "latency_s": "duration_s",
        "cpu_seconds": "cpu_seconds",
        "avg_process_cpu_percent": "process_cpu_percent_from_wait4",
        "avg_all_cores_cpu_percent": "all_cores_cpu_percent_from_wait4",
        "peak_process_cpu_percent": "sample_peak_process_cpu_percent",
        "peak_all_cores_cpu_percent": "sample_peak_all_cores_cpu_percent",
        "peak_vmrss_mib": "peak_vmrss_kb",
        "peak_vmhwm_mib": "peak_vmhwm_kb",
        "peak_vmsize_mib": "peak_vmsize_kb",
        "peak_threads": "peak_threads",
        "output_bytes": "output_bytes",
    }
    for out_key, source_key in metric_map.items():
        summary[out_key] = {}
        for tool in TOOLS:
            values = []
            for threads in threads_list:
                run = best_run(runs, tool, threads)
                if run is None:
                    values.append(None)
                    continue
                value = run.get(source_key)
                if value is not None and source_key.endswith("_kb"):
                    value = float(value) / 1024.0
                values.append(value)
            summary[out_key][tool] = values
    return summary


def base_results(threads_list: list[int], iterations: int, poll_interval: float, cpu_total: int) -> dict[str, Any]:
    return {
        "experiment": EXPERIMENT_NAME,
        "complete": False,
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "provenance": {
            "runner_script": str(RUNNER_SCRIPT),
            "plot_script": str(PLOT_SCRIPT),
            "data_dir": str(DATA_DIR),
            "artifact_dir": str(DATA_DIR),
            "log_dir": str(LOG_DIR),
            "plot_dir": str(PLOT_DIR),
            "result_json": str(RESULTS_PATH),
            "source_runner_repeated": str(SOURCE_RUNNER),
            "source_plot_repeated": str(SOURCE_PLOT),
            "source_results_repeated": str(SOURCE_RESULTS),
            "source_pdf_repeated": str(SOURCE_PDF),
        },
        "setup": {
            "workload": "YCSB workload c",
            "blind_operations": "10 M",
            "record_count": RECORD_COUNT,
            "operation_count": OPERATION_COUNT,
            "scale": SCALE,
            "threads": threads_list,
            "iterations": iterations,
            "poll_interval_s": poll_interval,
            "cpu_count_available": cpu_total,
            "tectonic_parallel_gen": "1",
            "temporary_outputs": {tool: str(path) for tool, path in TMP_OUTPUTS.items()},
            "ycsb_dir": str(YCSB_DIR),
            "tectonic_cli": str(TECTONIC_CLI),
        },
        "metric_definitions": {
            "duration_s": "wall-clock latency measured around each generator command; YCSB is load plus run phase",
            "cpu_seconds": "wait4 user plus system CPU seconds for the generator process",
            "process_cpu_percent_from_wait4": "cpu_seconds divided by duration_s, so multithreaded runs may exceed 100",
            "all_cores_cpu_percent_from_wait4": "process CPU percent divided by available logical CPUs",
            "resource_samples": "periodic /proc/<pid>/status and /proc/<pid>/stat samples",
            "peak_vmrss_kb": "largest sampled resident set size from /proc/<pid>/status VmRSS",
            "peak_vmhwm_kb": "max of wait4 ru_maxrss and sampled /proc/<pid>/status VmHWM",
            "peak_vmsize_kb": "largest sampled virtual memory size from /proc/<pid>/status VmSize",
            "peak_threads": "largest sampled /proc/<pid>/status Threads value",
            "output_bytes": "sum of generated workload output files for the run",
        },
        "runs": [],
        "summary": {},
    }


def write_results(data: dict[str, Any], threads_list: list[int]) -> None:
    data["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    data["summary"] = summary_from_runs(data["runs"], threads_list)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("w") as f:
        json.dump(data, f, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run concurrent blind workload generation with latency, memory, CPU, and output-size metrics."
    )
    parser.add_argument("--threads", help="comma-separated thread counts or ranges; default repeats the original list")
    parser.add_argument("--iterations", type=int, default=ITERATIONS)
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL_S)
    parser.add_argument("--min-free-gib", type=float, default=25.0)
    parser.add_argument("--skip-plot", action="store_true")
    args = parser.parse_args()

    threads_list = parse_threads(args.threads)
    cpu_total = cpu_count()
    ensure_setup()
    check_tmp_space(args.min_free_gib)

    print("=== concurrent blind generation resource monitoring ===")
    print(f"threads: {threads_list}")
    print(f"iterations: {args.iterations}")
    print(f"results: {RESULTS_PATH}")
    print(f"logs: {LOG_DIR}")

    data = base_results(threads_list, args.iterations, args.poll_interval, cpu_total)
    write_results(data, threads_list)

    try:
        for threads in threads_list:
            print(f"\n--- thread count: {threads} ---", flush=True)
            for iteration in range(1, args.iterations + 1):
                print(f"iteration {iteration}/{args.iterations}", flush=True)
                ycsb = run_ycsb(threads, iteration, args.poll_interval, cpu_total)
                if ycsb is not None:
                    data["runs"].append(ycsb)
                    write_results(data, threads_list)
                for tool in ("tectonic", "tectonic_unique"):
                    result = run_tectonic(tool, threads, iteration, args.poll_interval, cpu_total)
                    if result is not None:
                        data["runs"].append(result)
                        write_results(data, threads_list)
    finally:
        write_results(data, threads_list)

    data["complete"] = True
    write_results(data, threads_list)

    if not args.skip_plot:
        subprocess.run([sys.executable, str(PLOT_SCRIPT), "--results", str(RESULTS_PATH)], check=True)
    print(f"results saved to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
