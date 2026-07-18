#!/usr/bin/env python3
"""
Sanity runner for figure_6_validate.

This script runs one YCSB workload and one KVBench workload, captures human-readable
logs, and stores parsed empirical timing data under data/figure_6_validate.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/home/cc/Tectonic")
OUT_DIR = ROOT / "data" / "figure_6_validate"
SANITY_DIR = OUT_DIR / "sanity"
LOG_PATH = OUT_DIR / "figure_6_validate_sanity.log"

KVBENCH_CLI = Path("/home/cc/KV-WorkloadGenerator/bin/load_gen")
YCSB_DIR = ROOT / "rocksdb-benchmark-harness" / "vendor" / "YCSB"
M2 = Path("/home/cc/.m2/repository")

YCSB_CP = ":".join(
    [
        str(YCSB_DIR / "file" / "conf"),
        str(YCSB_DIR / "file" / "target" / "file-binding-0.18.0-SNAPSHOT.jar"),
        str(M2 / "org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar"),
        str(M2 / "org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar"),
        str(M2 / "org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar"),
        str(M2 / "org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar"),
        str(YCSB_DIR / "core" / "target" / "core-0.18.0-SNAPSHOT.jar"),
    ]
)

ALL_OPS = ["Insert", "Update", "Point Query", "Point Delete", "Range Query", "Range Delete"]
YCSB_OP_MAP = {
    "INSERT": "Insert",
    "UPDATE": "Update",
    "READ": "Point Query",
    "DELETE": "Point Delete",
    "SCAN": "Range Query",
}

KVBENCH_YCSB_ARGS = {
    "A": "-I 1000000 -Q 500000 -U 500000 --UD 3 --ED 3 --entry_size 1050 -L 0.025",
    "B": "-I 1000000 -Q 950000 -U 50000 --UD 3 --ED 3 --entry_size 1050 -L 0.025",
    "C": "-I 1000000 -Q 1000000 --UD 3 --ED 3 --entry_size 1050 -L 0.025",
    "D": "-I 1050000 -Q 950000 --UD 3 --ED 3 --entry_size 1050 -L 0.025",
    "E": "-I 1050000 -S 950000 -Y 0.0001 --YCSB=1 --ED 3 --entry_size 1050 -L 0.025",
}


class HumanLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("w", encoding="utf-8")

    def write(self, line: str = "") -> None:
        print(line)
        self.handle.write(line + "\n")
        self.handle.flush()

    def close(self) -> None:
        self.handle.close()


def get_process_rss_mb(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (FileNotFoundError, ProcessLookupError):
        return 0.0
    return 0.0


def run_command(cmd, cwd: Path | None, output_file: Path, log: HumanLog, poll_interval: float = 0.01):
    if output_file.exists():
        output_file.unlink()

    log.write("")
    log.write("command:")
    log.write("  " + " ".join(str(part) for part in cmd))
    log.write(f"working directory: {cwd if cwd else ROOT}")
    log.write(f"temporary workload output: {output_file}")

    start = time.time()
    proc = subprocess.Popen(
        [str(part) for part in cmd],
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    stdout_lines: list[str] = []

    def reader():
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_lines.append(line)
            log.write("  stdout | " + line.rstrip())

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    mem_log = []
    last_poll = 0.0
    while proc.poll() is None:
        elapsed = time.time() - start
        if elapsed - last_poll >= poll_interval:
            rss = get_process_rss_mb(proc.pid)
            if rss > 0.0:
                mem_log.append([elapsed, rss])
            last_poll = elapsed
        time.sleep(0.005)

    thread.join(timeout=2.0)
    duration = time.time() - start
    if proc.returncode != 0:
        raise RuntimeError(f"command failed with exit code {proc.returncode}: {' '.join(str(part) for part in cmd)}")

    log.write(f"finished: duration_s={duration:.6f}, peak_rss_mb={max((m[1] for m in mem_log), default=0.0):.3f}")

    if output_file.exists():
        output_file.unlink()

    return {
        "command": [str(part) for part in cmd],
        "cwd": str(cwd) if cwd else None,
        "stdout": stdout_lines,
        "total_duration": duration,
        "mem_log": mem_log,
    }


def parse_ycsb_stdout(stdout_lines: list[str]) -> dict:
    by_raw_op: dict[str, dict[str, float]] = {}
    pattern = re.compile(r"^\[(?P<op>[A-Z]+)\],\s*(?P<metric>[^,]+),\s*(?P<value>[-0-9.]+)")
    for line in stdout_lines:
        match = pattern.match(line.strip())
        if not match:
            continue
        raw_op = match.group("op")
        metric = match.group("metric")
        value = float(match.group("value"))
        by_raw_op.setdefault(raw_op, {})[metric] = value

    op_durations = {op: 0.0 for op in ALL_OPS}
    op_counts = {op: 0 for op in ALL_OPS}
    for raw_op, metrics in by_raw_op.items():
        mapped = YCSB_OP_MAP.get(raw_op)
        if mapped is None:
            continue
        operations = int(metrics.get("Operations", 0))
        avg_us = metrics.get("AverageLatency(us)", 0.0)
        op_counts[mapped] += operations
        op_durations[mapped] += operations * avg_us / 1_000_000.0

    return {"op_counts": op_counts, "op_durations": op_durations, "raw_metrics": by_raw_op}


def parse_kvbench_stdout(stdout_lines: list[str]) -> dict:
    op_durations = {op: 0.0 for op in ALL_OPS}
    load_phase_end = None
    for line in stdout_lines:
        stripped = line.strip()
        if stripped.startswith("KVbench_Op_Timings:"):
            payload = stripped.split("KVbench_Op_Timings:", 1)[1].strip()
            parsed = json.loads(payload)
            op_durations.update({op: float(parsed.get(op, 0.0)) for op in ALL_OPS})
        elif stripped.startswith("KVbench_LoadPhase_End:"):
            load_phase_end = float(stripped.split(":", 1)[1].strip())
    return {"op_durations": op_durations, "loading_phase_end_time": load_phase_end}


def run_ycsb(workload: str, log: HumanLog) -> dict:
    workload_lower = workload.lower()
    common = [
        "java",
        "-cp",
        YCSB_CP,
        "site.ycsb.Client",
        "-db",
        "site.ycsb.db.FileClient",
        "-P",
        f"workloads/workload{workload_lower}",
        "-p",
        "file.output=/tmp/figure_6_validate_ycsb_out.txt",
        "-p",
        "recordcount=1000000",
        "-p",
        "operationcount=1000000",
    ]

    log.write("")
    log.write(f"=== YCSB workload {workload}: load phase ===")
    load = run_command(common + ["-load"], YCSB_DIR, Path("/tmp/figure_6_validate_ycsb_out.txt"), log)
    load_parsed = parse_ycsb_stdout(load["stdout"])

    log.write("")
    log.write(f"=== YCSB workload {workload}: execution phase ===")
    run = run_command(common + ["-t"], YCSB_DIR, Path("/tmp/figure_6_validate_ycsb_out.txt"), log)
    run_parsed = parse_ycsb_stdout(run["stdout"])

    result = {
        "tool": "YCSB",
        "workload": workload,
        "load": {
            "total_duration": load["total_duration"],
            "op_counts": load_parsed["op_counts"],
            "op_durations": load_parsed["op_durations"],
            "raw_metrics": load_parsed["raw_metrics"],
            "mem_log": load["mem_log"],
            "command": load["command"],
        },
        "run": {
            "total_duration": run["total_duration"],
            "op_counts": run_parsed["op_counts"],
            "op_durations": run_parsed["op_durations"],
            "raw_metrics": run_parsed["raw_metrics"],
            "mem_log": run["mem_log"],
            "command": run["command"],
        },
    }
    return result


def run_kvbench_ycsb_shape(workload: str, log: HumanLog) -> dict:
    args = KVBENCH_YCSB_ARGS[workload].split()
    cmd = [KVBENCH_CLI] + args + ["--OP", "/tmp/figure_6_validate_kvbench_out.txt"]

    log.write("")
    log.write(f"=== KVBench workload {workload} shaped like YCSB {workload} ===")
    raw = run_command(cmd, ROOT, Path("/tmp/figure_6_validate_kvbench_out.txt"), log)
    parsed = parse_kvbench_stdout(raw["stdout"])
    load_end = parsed["loading_phase_end_time"]
    exec_phase = raw["total_duration"] - load_end if load_end is not None else None

    result = {
        "tool": "KVBench",
        "workload": workload,
        "total_duration": raw["total_duration"],
        "op_durations": parsed["op_durations"],
        "empirical_op_durations": parsed["op_durations"],
        "loading_phase_end_time": load_end,
        "execution_phase_duration": exec_phase,
        "mem_log": raw["mem_log"],
        "command": raw["command"],
    }
    return result


def summarize_ycsb(result: dict, log: HumanLog) -> None:
    log.write("")
    log.write("parsed YCSB empirical timings:")
    for phase in ["load", "run"]:
        phase_result = result[phase]
        log.write(f"  {phase} wall_time_s: {phase_result['total_duration']:.6f}")
        for op in ALL_OPS:
            count = phase_result["op_counts"].get(op, 0)
            duration = phase_result["op_durations"].get(op, 0.0)
            if count or duration:
                log.write(f"  {phase} {op}: operations={count}, total_latency_s={duration:.6f}")


def summarize_kvbench(result: dict, log: HumanLog) -> None:
    log.write("")
    log.write("parsed KVBench empirical timings:")
    log.write(f"  wall_time_s: {result['total_duration']:.6f}")
    log.write(f"  load_phase_end_s: {result['loading_phase_end_time']:.6f}")
    log.write(f"  execution_phase_s: {result['execution_phase_duration']:.6f}")
    log.write("  load-phase rule: first pure initial insert segment ends when KVBench emits KVbench_LoadPhase_End")
    for op in ALL_OPS:
        duration = result["op_durations"].get(op, 0.0)
        if duration:
            log.write(f"  {op}: total_latency_s={duration:.6f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run figure_6_validate sanity workloads.")
    parser.add_argument("--ycsb-workload", default="A", choices=list("ABCDEF"))
    parser.add_argument("--kvbench-workload", default="A", choices=list(KVBENCH_YCSB_ARGS.keys()))
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SANITY_DIR.mkdir(parents=True, exist_ok=True)

    log = HumanLog(LOG_PATH)
    try:
        log.write("figure_6_validate sanity run")
        log.write(f"started_utc: {datetime.now(timezone.utc).isoformat()}")
        log.write(f"output_dir: {OUT_DIR}")
        log.write(f"sanity_dir: {SANITY_DIR}")

        ycsb_result = run_ycsb(args.ycsb_workload, log)
        summarize_ycsb(ycsb_result, log)
        ycsb_path = SANITY_DIR / f"ycsb_{args.ycsb_workload.lower()}_sanity_trace.json"
        ycsb_path.write_text(json.dumps(ycsb_result, indent=2), encoding="utf-8")
        log.write(f"saved: {ycsb_path}")

        kvbench_result = run_kvbench_ycsb_shape(args.kvbench_workload, log)
        summarize_kvbench(kvbench_result, log)
        kvbench_path = SANITY_DIR / f"kvbench_{args.kvbench_workload.lower()}_sanity_trace.json"
        kvbench_path.write_text(json.dumps(kvbench_result, indent=2), encoding="utf-8")
        log.write(f"saved: {kvbench_path}")

        log.write("")
        log.write(f"finished_utc: {datetime.now(timezone.utc).isoformat()}")
        return 0
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
