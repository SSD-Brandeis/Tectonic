#!/usr/bin/env python3
"""Compare db_bench-style and Tectonic YCSB-A blind workload generation."""

from __future__ import annotations

import argparse
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
RUN_SCRIPTS_DIR = ROOT_DIR / "run_scripts"
PLOT_SCRIPTS_DIR = ROOT_DIR / "plot_scripts"
OUT_DIR = ROOT_DIR / "data" / "db_bench_exp_test" / "ycsba_blind"
TMP_DIR = Path("/tmp/db_bench_ycsba_blind_generation")
GENERATOR_SCRIPT = RUN_SCRIPTS_DIR / "generate_db_bench_ycsba_blind_workload_file.py"
PLOT_SCRIPT = PLOT_SCRIPTS_DIR / "plot_ycsba_blind_generation_comparison.py"
YCSBA_BLIND_SPEC = ROOT_DIR / "example-specs" / "ycsb_blind" / "a.spec.json"
DEFAULT_TECTONIC_CLI = ROOT_DIR / "target" / "release" / "tectonic-cli"
DEFAULT_SCALES = "0.005,0.01,0.02"
DEFAULT_THREADS = 1
BASE_RECORD_COUNT = 1_000_000
BASE_OPERATION_COUNT = 1_000_000
SEP = "=" * 72


def fail(message: str) -> None:
    print(f"[FATAL] {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def banner(title: str) -> None:
    print(f"\n{SEP}\n  {title}\n{SEP}", flush=True)


def ensure_file(path: Path, label: str) -> None:
    if not path.exists():
        fail(f"{label} not found: {path}")


def parse_scales(value: str) -> list[float]:
    scales = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            scale = float(item)
        except ValueError:
            fail(f"invalid scale: {item!r}")
        if scale <= 0:
            fail("scales must be positive")
        scales.append(scale)
    if not scales:
        fail("no scales provided")
    return scales


def scaled_count(base: int, scale: float) -> int:
    return max(1, int(base * scale))


def available_cpu() -> int:
    affinity = sorted(os.sched_getaffinity(0))
    if not affinity:
        fail("no CPUs available in current affinity mask")
    return affinity[0]


def process_exists(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def read_proc_status(pid: int) -> dict[str, int]:
    metrics = {
        "vmrss_kb": 0,
        "vmhwm_kb": 0,
        "vmsize_kb": 0,
        "threads": 0,
    }
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
        if parts:
            try:
                metrics[out_key] = int(parts[0])
            except ValueError:
                pass
    return metrics


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
        metrics = read_proc_status(self.pid)
        with self._lock:
            self.samples.append({"elapsed_s": time.monotonic() - self.start_time, **metrics})

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


def run_monitored(
    label: str,
    cmd: list[str],
    log_path: Path,
    cpu_id: int | None,
    poll_interval: float,
    cwd: Path,
) -> dict[str, Any]:
    print(f"\n  [{label}] {' '.join(cmd)}", flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start_time = time.monotonic()
    stop_event = threading.Event()

    def pin_child() -> None:
        if cpu_id is not None:
            os.sched_setaffinity(0, {cpu_id})

    with log_path.open("w") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            preexec_fn=pin_child if cpu_id is not None else None,
        )
        collector = ResourceCollector(proc.pid, start_time, poll_interval, stop_event)
        collector.start()
        _, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        end_time = time.monotonic()
        stop_event.set()
        collector.join()

    duration_s = end_time - start_time
    cpu_seconds = max(0.0, usage.ru_utime + usage.ru_stime)
    result = {
        "cmd": cmd,
        "cwd": str(cwd),
        "log_path": str(log_path),
        "pid": proc.pid,
        "return_code": proc.returncode,
        "cpu_id": cpu_id,
        "duration_s": duration_s,
        "cpu_seconds": cpu_seconds,
        "process_cpu_percent_from_wait4": (cpu_seconds / duration_s * 100.0) if duration_s > 0 else 0.0,
        "ru_maxrss_kb": int(usage.ru_maxrss),
        "memory_samples": collector.samples,
        "peak_vmrss_kb": peak(collector.samples, "vmrss_kb"),
        "peak_vmhwm_kb": max(int(usage.ru_maxrss), peak(collector.samples, "vmhwm_kb")),
        "peak_vmsize_kb": peak(collector.samples, "vmsize_kb"),
        "peak_threads": peak(collector.samples, "threads"),
    }
    print(
        f"  [{label}] duration={duration_s:.3f}s cpu_seconds={cpu_seconds:.3f}s "
        f"peak_hwm={result['peak_vmhwm_kb']} KiB peak_threads={result['peak_threads']}",
        flush=True,
    )
    if proc.returncode != 0:
        print(tail_file(log_path), file=sys.stderr)
        fail(f"{label} failed with exit code {proc.returncode}; see {log_path}")
    return result


def file_operation_counts(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with path.open("rb") as f:
        for line in f:
            if not line.strip():
                continue
            op = line.split(maxsplit=1)[0].decode("ascii", errors="replace")
            if op in {"FS", "FE"}:
                continue
            counts[op] = counts.get(op, 0) + 1
    return counts


def line_count(path: Path) -> int:
    count = 0
    with path.open("rb") as f:
        for _ in f:
            count += 1
    return count


def add_file_metrics(result: dict[str, Any], output_path: Path, total_ops: int) -> dict[str, Any]:
    result["output_path"] = str(output_path)
    result["output_bytes"] = output_path.stat().st_size if output_path.exists() else 0
    result["output_lines"] = line_count(output_path) if output_path.exists() else 0
    result["operation_counts"] = file_operation_counts(output_path) if output_path.exists() else {}
    result["generated_operations"] = total_ops
    result["wall_seconds_per_million_ops"] = result["duration_s"] / (total_ops / 1_000_000.0)
    result["cpu_seconds_per_million_ops"] = result["cpu_seconds"] / (total_ops / 1_000_000.0)
    return result


def run_scale(
    scale: float,
    threads: int,
    cpu_id: int | None,
    poll_interval: float,
    tectonic_cli: Path,
) -> dict[str, Any]:
    record_count = scaled_count(BASE_RECORD_COUNT, scale)
    operation_count = scaled_count(BASE_OPERATION_COUNT, scale)
    blind_point_queries = operation_count // 2
    updates = operation_count - blind_point_queries
    total_ops = record_count + operation_count
    tag = f"scale_{scale:g}".replace(".", "p")
    db_output = TMP_DIR / f"db_bench_ycsba_blind_{tag}.txt"
    tectonic_output = TMP_DIR / f"tectonic_ycsba_blind_{tag}.txt"
    metadata_output = OUT_DIR / f"db_bench_ycsba_blind_{tag}.metadata.json"

    for path in (db_output, tectonic_output):
        if path.exists():
            path.unlink()

    banner(f"YCSB-A blind workload generation scale={scale:g}")
    db_result = run_monitored(
        f"db_bench-style YCSB-A blind generation scale={scale:g}",
        [
            "python3",
            str(GENERATOR_SCRIPT),
            "--output",
            str(db_output),
            "--scale",
            str(scale),
            "--metadata",
            str(metadata_output),
        ],
        OUT_DIR / f"db_bench_ycsba_blind_generate_{tag}.log",
        cpu_id,
        poll_interval,
        ROOT_DIR,
    )
    tectonic_result = run_monitored(
        f"Tectonic YCSB-A blind generation scale={scale:g}",
        [
            str(tectonic_cli),
            "generate",
            "-w",
            str(YCSBA_BLIND_SPEC),
            "-o",
            str(tectonic_output),
            "-s",
            str(scale),
            "-t",
            str(threads),
        ],
        OUT_DIR / f"tectonic_ycsba_blind_generate_{tag}.log",
        cpu_id,
        poll_interval,
        ROOT_DIR,
    )

    return {
        "workload": "YCSB-A blind",
        "scale": scale,
        "record_count": record_count,
        "operation_count": operation_count,
        "blind_point_queries": blind_point_queries,
        "updates": updates,
        "generated_operations_per_tool": total_ops,
        "threads": threads,
        "cpu_affinity": [cpu_id] if cpu_id is not None else None,
        "db_bench_style": add_file_metrics(db_result, db_output, total_ops),
        "tectonic": add_file_metrics(tectonic_result, tectonic_output, total_ops),
    }


def write_results(payload: dict[str, Any], results_path: Path) -> Path:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = results_path.with_suffix(".json.tmp")
    with tmp_path.open("w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(results_path)
    return results_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run db_bench-style vs Tectonic YCSB-A blind generation comparison.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--scales", default=DEFAULT_SCALES, help="comma-separated scale factors")
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS, help="Tectonic generate -t value")
    parser.add_argument("--tectonic-cli", default=str(DEFAULT_TECTONIC_CLI))
    parser.add_argument("--cpu", type=int, help="logical CPU id for single-core pinning")
    parser.add_argument("--no-affinity", action="store_true", help="do not pin generator processes to one CPU")
    parser.add_argument("--poll-interval", type=float, default=0.05)
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--keep-files", action="store_true", help="keep generated workload files in /tmp")
    args = parser.parse_args()

    if args.threads < 1:
        fail("--threads must be positive")
    if args.poll_interval <= 0:
        fail("--poll-interval must be positive")

    scales = parse_scales(args.scales)
    tectonic_cli = Path(args.tectonic_cli).expanduser().resolve()
    cpu_id = None if args.no_affinity else (args.cpu if args.cpu is not None else available_cpu())
    if cpu_id is not None and cpu_id not in os.sched_getaffinity(0):
        fail(f"requested CPU {cpu_id} is not in current affinity mask: {sorted(os.sched_getaffinity(0))}")

    ensure_file(GENERATOR_SCRIPT, "db_bench-style YCSB-A blind generator")
    ensure_file(YCSBA_BLIND_SPEC, "Tectonic YCSB-A blind spec")
    ensure_file(tectonic_cli, "tectonic-cli")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    print(SEP)
    print("  experiment : db_bench-style vs Tectonic YCSB-A blind workload generation")
    print(f"  spec       : {YCSBA_BLIND_SPEC}")
    print(f"  scales     : {scales}")
    print(f"  threads    : {args.threads}")
    print(f"  cpu pin    : {cpu_id if cpu_id is not None else 'none'}")
    print(f"  output dir : {OUT_DIR}")
    print(SEP, flush=True)

    runs = [run_scale(scale, args.threads, cpu_id, args.poll_interval, tectonic_cli) for scale in scales]
    payload = {
        "experiment": "db_bench_exp_test",
        "mode": "db_bench_style_vs_tectonic_ycsba_blind_generation",
        "root_dir": str(ROOT_DIR),
        "output_dir": str(OUT_DIR),
        "tmp_dir": str(TMP_DIR),
        "workload": "YCSB-A blind",
        "tectonic_spec": str(YCSBA_BLIND_SPEC),
        "generator_script": str(GENERATOR_SCRIPT),
        "tectonic_cli": str(tectonic_cli),
        "scales": scales,
        "threads": args.threads,
        "cpu_affinity": [cpu_id] if cpu_id is not None else None,
        "single_thread_note": (
            "The runner uses Tectonic -t 1 by default and pins both processes to one logical CPU. "
            "Tectonic blind point-key pregeneration can still create worker OS threads internally; "
            "peak_threads records that behavior."
        ),
        "comparison_caveat": (
            "Native RocksDB db_bench does not output a Tectonic/YCSB text workload file. "
            "The db_bench_style side is a compatibility generator that emits the same YCSB-A blind "
            "operation shape for file-level generation comparison."
        ),
        "metric_definitions": {
            "duration_s": "wall-clock time around workload file generation",
            "cpu_seconds": "wait4 user+system CPU seconds for the generator process",
            "peak_vmhwm_kb": "max of wait4 ru_maxrss and sampled VmHWM",
            "peak_threads": "largest sampled /proc/<pid>/status Threads value",
            "wall_seconds_per_million_ops": "duration_s divided by generated operations in millions",
        },
        "runs": runs,
    }
    results_path = write_results(payload, OUT_DIR / "ycsba_blind_generation_results.json")
    if not args.keep_files:
        shutil.rmtree(TMP_DIR, ignore_errors=True)
    print(f"\n  results saved: {results_path}", flush=True)
    if not args.no_plot:
        subprocess.run(["python3", str(PLOT_SCRIPT), "--results", str(results_path)], check=True)


if __name__ == "__main__":
    main()
