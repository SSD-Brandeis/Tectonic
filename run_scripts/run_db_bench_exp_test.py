#!/usr/bin/env python3
"""Compare native RocksDB db_bench with Tectonic db_bench-equivalent specs.

Native db_bench does not emit a separate workload trace. Its workload generator
is embedded in the RocksDB benchmark process, so this script compares end-to-end
RocksDB runs: db_bench internal generation + execution vs. Tectonic generation
+ RocksDB execution through `tectonic-cli benchmark`.
"""

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
OUT_DIR = ROOT_DIR / "data" / "db_bench_exp_test" / "end_to_end"
TMP_DIR = Path("/tmp/db_bench_exp_test")
PLOT_SCRIPT = PLOT_SCRIPTS_DIR / "plot_db_bench_exp_test.py"
DEFAULT_TECTONIC_CLI = ROOT_DIR / "target" / "release" / "tectonic-cli"
ROCKSDB_VENDOR_DIR = ROOT_DIR / "rocksdb-benchmark-harness" / "vendor" / "rocksdb"
DEFAULT_DB_BENCH_CANDIDATES = [
    ROCKSDB_VENDOR_DIR / "db_bench",
    ROCKSDB_VENDOR_DIR / "build" / "tools" / "db_bench",
    ROCKSDB_VENDOR_DIR / "build" / "db_bench",
]

CANONICAL_OPS = 900_000_000
DEFAULT_SCALE = 0.00001
DEFAULT_THREADS = 1
PIDSTAT_INTERVAL_S = 1
SEP = "=" * 72

WORKLOADS: dict[str, dict[str, Any]] = {
    "1": {
        "spec": ROOT_DIR / "example-specs" / "db_bench" / "1.spec.json",
        "db_benchmarks": ["fillrandom"],
        "description": "random inserts",
        "operation_mix": {"load_inserts": 1.0},
    },
    "2": {
        "spec": ROOT_DIR / "example-specs" / "db_bench" / "2.spec.json",
        "db_benchmarks": ["fillrandom", "readrandom"],
        "description": "random inserts, then random point reads",
        "operation_mix": {"load_inserts": 1.0, "point_queries": 1.0},
    },
    "3": {
        "spec": ROOT_DIR / "example-specs" / "db_bench" / "3.spec.json",
        "db_benchmarks": ["fillrandom", "readrandom"],
        "description": "random inserts, then random point reads",
        "operation_mix": {"load_inserts": 1.0, "point_queries": 1.0},
        "note": "The repo's 3.spec.json currently matches 2.spec.json.",
    },
    "4": {
        "spec": ROOT_DIR / "example-specs" / "db_bench" / "4.spec.json",
        "db_benchmarks": ["fillrandom", "seekrandom"],
        "description": "random inserts, then forward range seeks of length 11",
        "operation_mix": {"load_inserts": 1.0, "range_queries": 1.0},
        "extra_db_bench_flags": ["--seek_nexts=10"],
    },
    "4b": {
        "spec": ROOT_DIR / "example-specs" / "db_bench" / "4b.spec.json",
        "db_benchmarks": ["fillrandom", "seekrandom"],
        "description": "random inserts, then reverse range seeks of length 11",
        "operation_mix": {"load_inserts": 1.0, "reverse_range_queries": 1.0},
        "extra_db_bench_flags": ["--seek_nexts=10", "--reverse_iterator=true"],
    },
    "5": {
        "spec": ROOT_DIR / "example-specs" / "db_bench" / "5.spec.json",
        "db_benchmarks": ["fillrandom", "overwrite"],
        "description": "random inserts, then random overwrites",
        "operation_mix": {"load_inserts": 1.0, "updates": 1.0},
    },
}


def fail(message: str) -> None:
    print(f"[FATAL] {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def banner(title: str) -> None:
    print(f"\n{SEP}\n  {title}\n{SEP}", flush=True)


def ensure_file(path: Path, label: str) -> None:
    if not path.exists():
        fail(f"{label} not found: {path}")


def parse_workloads(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(WORKLOADS)
    workloads = []
    for item in value.split(","):
        workload = item.strip().lower()
        if not workload:
            continue
        if workload not in WORKLOADS:
            fail(f"unknown workload {workload!r}; choose one of {', '.join(WORKLOADS)} or all")
        workloads.append(workload)
    if not workloads:
        fail("no workloads selected")
    return workloads


def scaled_ops(scale: float) -> int:
    if scale <= 0:
        fail("--scale must be positive")
    return max(1, int(round(CANONICAL_OPS * scale)))


def process_exists(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def parse_float(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_pidstat_cpu(output: str, pid: int) -> float | None:
    pid_s = str(pid)
    for line in reversed(output.splitlines()):
        parts = line.strip().split()
        if len(parts) < 8 or parts[0] == "Linux" or parts[0].startswith("#"):
            continue
        if parts[0] == "Average:" or "%CPU" in parts:
            continue
        try:
            pid_idx = parts.index(pid_s)
        except ValueError:
            continue
        cpu_idx = pid_idx + 5
        if cpu_idx < len(parts):
            return parse_float(parts[cpu_idx])
    return None


def read_proc_status_kb(pid: int) -> dict[str, int]:
    metrics = {"vmrss_kb": 0, "vmhwm_kb": 0, "vmsize_kb": 0}
    try:
        text = Path(f"/proc/{pid}/status").read_text(errors="replace")
    except (FileNotFoundError, ProcessLookupError):
        return metrics
    key_map = {"VmRSS": "vmrss_kb", "VmHWM": "vmhwm_kb", "VmSize": "vmsize_kb"}
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
    def __init__(self, pid: int, start_time: float, stop_event: threading.Event):
        self.pid = pid
        self.start_time = start_time
        self.stop_event = stop_event
        self.pidstat_samples: list[dict[str, float]] = []
        self.memory_samples: list[dict[str, float | int]] = []
        self.pidstat_available = shutil.which("pidstat") is not None
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        for target in (self._memory_loop, self._pidstat_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)

    def join(self) -> None:
        for thread in self._threads:
            thread.join(timeout=3.0)

    def add_memory_sample(self) -> None:
        metrics = read_proc_status_kb(self.pid)
        with self._lock:
            self.memory_samples.append({
                "elapsed_s": time.monotonic() - self.start_time,
                **metrics,
            })

    def add_pidstat_sample(self, cpu_percent: float | None) -> None:
        if cpu_percent is None:
            return
        with self._lock:
            self.pidstat_samples.append({
                "elapsed_s": time.monotonic() - self.start_time,
                "process_cpu_percent": max(0.0, float(cpu_percent)),
            })

    def _memory_loop(self) -> None:
        while not self.stop_event.is_set() and process_exists(self.pid):
            self.add_memory_sample()
            time.sleep(0.2)
        self.add_memory_sample()

    def _pidstat_loop(self) -> None:
        if not self.pidstat_available:
            return
        while not self.stop_event.is_set() and process_exists(self.pid):
            result = subprocess.run(
                ["pidstat", "-h", "-u", "-p", str(self.pid), str(PIDSTAT_INTERVAL_S), "1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self.add_pidstat_sample(parse_pidstat_cpu(result.stdout, self.pid))


def tail_file(path: Path, max_lines: int = 80) -> str:
    try:
        return "".join(path.read_text(errors="replace").splitlines(True)[-max_lines:])
    except FileNotFoundError:
        return ""


def peak_memory(samples: list[dict[str, float | int]], key: str) -> int:
    values = [int(sample.get(key, 0)) for sample in samples]
    return max(values) if values else 0


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def run_monitored(label: str, cmd: list[str], log_path: Path, cwd: Path | None = None) -> dict[str, Any]:
    print(f"\n  [{label}] {' '.join(cmd)}", flush=True)
    start_time = time.monotonic()
    stop_event = threading.Event()
    with log_path.open("w") as log_file:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=log_file, stderr=subprocess.STDOUT)
        collector = ResourceCollector(proc.pid, start_time, stop_event)
        collector.start()
        _, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        end_time = time.monotonic()
        stop_event.set()
        collector.join()

    duration_s = end_time - start_time
    cpu_seconds = max(0.0, usage.ru_utime + usage.ru_stime)
    process_cpu_percent = (cpu_seconds / duration_s * 100.0) if duration_s > 0 else 0.0
    pidstat_values = [float(sample["process_cpu_percent"]) for sample in collector.pidstat_samples]
    result = {
        "cmd": cmd,
        "cwd": str(cwd) if cwd else None,
        "log_path": str(log_path),
        "pid": proc.pid,
        "return_code": proc.returncode,
        "duration_s": duration_s,
        "cpu_seconds": cpu_seconds,
        "process_cpu_percent_from_wait4": process_cpu_percent,
        "ru_maxrss_kb": int(usage.ru_maxrss),
        "pidstat_available": collector.pidstat_available,
        "pidstat_avg_process_cpu_percent": average(pidstat_values),
        "pidstat_max_process_cpu_percent": max(pidstat_values) if pidstat_values else None,
        "pidstat_samples": collector.pidstat_samples,
        "memory_samples": collector.memory_samples,
        "peak_vmrss_kb": peak_memory(collector.memory_samples, "vmrss_kb"),
        "peak_vmhwm_kb": max(int(usage.ru_maxrss), peak_memory(collector.memory_samples, "vmhwm_kb")),
        "peak_vmsize_kb": peak_memory(collector.memory_samples, "vmsize_kb"),
    }
    print(
        f"  [{label}] duration={duration_s:.2f}s cpu_seconds={cpu_seconds:.2f}s "
        f"wait4_cpu={process_cpu_percent:.1f}% peak_rss={result['peak_vmrss_kb']} KiB",
        flush=True,
    )
    if proc.returncode != 0:
        print(tail_file(log_path), file=sys.stderr)
        fail(f"{label} failed with exit code {proc.returncode}; see {log_path}")
    return result


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def resolve_db_bench_bin(cli_value: str | None) -> Path | None:
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    for candidate in DEFAULT_DB_BENCH_CANDIDATES:
        if candidate.exists():
            return candidate
    found = shutil.which("db_bench")
    return Path(found).resolve() if found else None


def build_tectonic(tectonic_cli: Path, no_build: bool) -> None:
    if no_build and tectonic_cli.exists():
        return
    if no_build:
        ensure_file(tectonic_cli, "tectonic-cli")
        return
    banner("build Tectonic with RocksDB support")
    subprocess.run(
        ["cargo", "build", "--release", "--features", "db-layer/rocksdb"],
        cwd=ROOT_DIR,
        check=True,
    )
    ensure_file(tectonic_cli, "tectonic-cli")


def maybe_build_db_bench(db_bench_bin: Path | None, no_build: bool) -> Path:
    if db_bench_bin is not None and db_bench_bin.exists():
        return db_bench_bin
    if no_build:
        fail(
            "native db_bench binary was not found. Build RocksDB db_bench first "
            "or pass --db-bench-bin /path/to/db_bench."
        )
    banner("build native RocksDB db_bench")
    if not ROCKSDB_VENDOR_DIR.exists():
        fail(f"RocksDB vendor directory not found: {ROCKSDB_VENDOR_DIR}")
    subprocess.run(["make", "-j", str(os.cpu_count() or 1), "db_bench"], cwd=ROCKSDB_VENDOR_DIR, check=True)
    built = resolve_db_bench_bin(None)
    if built is None or not built.exists():
        fail("db_bench build completed but the binary was not found")
    return built


def db_bench_command(db_bench_bin: Path, workload: str, op_count: int, threads: int, db_path: Path) -> list[str]:
    cfg = WORKLOADS[workload]
    cmd = [
        str(db_bench_bin),
        f"--db={db_path}",
        f"--benchmarks={','.join(cfg['db_benchmarks'])}",
        f"--num={op_count}",
        f"--reads={op_count}",
        f"--writes={op_count}",
        "--key_size=20",
        "--value_size=400",
        f"--threads={threads}",
        "--statistics=true",
        "--histogram=false",
        "--use_existing_db=false",
    ]
    cmd.extend(cfg.get("extra_db_bench_flags", []))
    return cmd


def tectonic_command(tectonic_cli: Path, workload: str, scale: float, threads: int, db_path: Path) -> list[str]:
    spec = WORKLOADS[workload]["spec"]
    return [
        str(tectonic_cli),
        "benchmark",
        "-w",
        str(spec),
        "-d",
        "rocksdb",
        "-p",
        str(db_path),
        "-s",
        str(scale),
        "-t",
        str(threads),
    ]


def add_efficiency_metrics(result: dict[str, Any], generated_ops: int) -> dict[str, Any]:
    result["generated_operations"] = generated_ops
    result["wall_seconds_per_million_ops"] = result["duration_s"] / (generated_ops / 1_000_000.0)
    result["cpu_seconds_per_million_ops"] = result["cpu_seconds"] / (generated_ops / 1_000_000.0)
    return result


def generated_ops_for_workload(workload: str, op_count: int) -> int:
    return op_count * len(WORKLOADS[workload]["db_benchmarks"])


def run_workload(
    workload: str,
    scale: float,
    op_count: int,
    threads: int,
    tectonic_cli: Path,
    db_bench_bin: Path,
) -> dict[str, Any]:
    cfg = WORKLOADS[workload]
    banner(f"db_bench workload {workload}: {cfg['description']}")
    generated_ops = generated_ops_for_workload(workload, op_count)

    db_bench_db = TMP_DIR / f"db_bench_w{workload}"
    tectonic_db = TMP_DIR / f"tectonic_w{workload}"
    remove_tree(db_bench_db)
    remove_tree(tectonic_db)

    db_bench_result = run_monitored(
        f"native db_bench workload {workload}",
        db_bench_command(db_bench_bin, workload, op_count, threads, db_bench_db),
        OUT_DIR / f"db_bench_w{workload}.log",
        cwd=ROOT_DIR,
    )
    tectonic_result = run_monitored(
        f"Tectonic workload {workload}",
        tectonic_command(tectonic_cli, workload, scale, threads, tectonic_db),
        OUT_DIR / f"tectonic_w{workload}.log",
        cwd=ROOT_DIR,
    )

    return {
        "workload": workload,
        "description": cfg["description"],
        "note": cfg.get("note"),
        "operation_mix": cfg["operation_mix"],
        "tectonic_spec": str(cfg["spec"]),
        "scale": scale,
        "scaled_ops_per_phase": op_count,
        "generated_operations_per_tool": generated_ops,
        "threads": threads,
        "native_db_bench": add_efficiency_metrics(db_bench_result, generated_ops),
        "tectonic": add_efficiency_metrics(tectonic_result, generated_ops),
    }


def write_results(payload: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = OUT_DIR / "results.json"
    tmp_path = results_path.with_suffix(".json.tmp")
    with tmp_path.open("w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(results_path)
    return results_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run db_bench vs Tectonic RocksDB resource experiment.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--workloads", default="1,2,4,5", help="comma-separated workload ids or all")
    parser.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="scale applied to canonical 900M-op specs")
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--tectonic-cli", default=str(DEFAULT_TECTONIC_CLI))
    parser.add_argument("--db-bench-bin", help="path to native RocksDB db_bench")
    parser.add_argument("--no-build", action="store_true", help="do not build missing binaries")
    parser.add_argument("--no-plot", action="store_true", help="skip PDF plot generation")
    parser.add_argument("--keep-db", action="store_true", help="keep temporary RocksDB directories in /tmp")
    args = parser.parse_args()

    if args.threads < 1:
        fail("--threads must be positive")

    workloads = parse_workloads(args.workloads)
    op_count = scaled_ops(args.scale)
    tectonic_cli = Path(args.tectonic_cli).expanduser().resolve()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    build_tectonic(tectonic_cli, args.no_build)
    for workload in workloads:
        ensure_file(WORKLOADS[workload]["spec"], f"Tectonic db_bench workload {workload} spec")
    db_bench_bin = maybe_build_db_bench(resolve_db_bench_bin(args.db_bench_bin), args.no_build)

    print(SEP)
    print("  experiment : native RocksDB db_bench vs Tectonic on RocksDB")
    print(f"  workloads  : {workloads}")
    print(f"  scale      : {args.scale} ({op_count:,} ops per canonical phase)")
    print(f"  threads    : {args.threads}")
    print(f"  db_bench   : {db_bench_bin}")
    print(f"  tectonic   : {tectonic_cli}")
    print(f"  results    : {OUT_DIR}")
    print("  monitors   : wait4 CPU time, pidstat when installed, /proc memory samples")
    print(SEP, flush=True)

    runs = [
        run_workload(workload, args.scale, op_count, args.threads, tectonic_cli, db_bench_bin)
        for workload in workloads
    ]

    payload = {
        "experiment": "db_bench_exp_test",
        "mode": "native_db_bench_vs_tectonic_rocksdb_end_to_end",
        "root_dir": str(ROOT_DIR),
        "run_script_dir": str(RUN_SCRIPTS_DIR),
        "output_dir": str(OUT_DIR),
        "scale": args.scale,
        "canonical_ops_per_phase": CANONICAL_OPS,
        "scaled_ops_per_phase": op_count,
        "threads": args.threads,
        "workloads": workloads,
        "db_bench_bin": str(db_bench_bin),
        "tectonic_cli": str(tectonic_cli),
        "metric_definitions": {
            "duration_s": "wall-clock time around the benchmark process",
            "cpu_seconds": "wait4 user+system CPU seconds for the benchmark process",
            "process_cpu_percent_from_wait4": "cpu_seconds / duration_s * 100; values above 100 indicate multi-core use",
            "pidstat_process_cpu_percent": "pidstat process %CPU samples when pidstat is installed",
            "peak_vmrss_kb": "largest sampled resident set size from /proc/<pid>/status",
            "peak_vmhwm_kb": "max of wait4 ru_maxrss and sampled VmHWM",
            "cpu_seconds_per_million_ops": "cpu_seconds divided by generated operations in millions",
        },
        "comparison_caveat": (
            "db_bench does not emit a workload trace; this compares equivalent operation mixes "
            "and scaled operation counts on RocksDB, not byte-identical generated traces."
        ),
        "runs": runs,
    }
    results_path = write_results(payload)
    if not args.keep_db:
        shutil.rmtree(TMP_DIR, ignore_errors=True)
    print(f"\n  results saved: {results_path}", flush=True)
    if not args.no_plot:
        subprocess.run(["python3", str(PLOT_SCRIPT), "--results", str(results_path)], check=True)


if __name__ == "__main__":
    main()
