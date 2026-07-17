#!/usr/bin/env python3
"""Record modified RocksDB db_bench YCSB A-F workload-generation logs.

This runner mirrors the scale of data/overall_benchmarks/logs_fig3:
1M load operations plus 1M run-phase operations per YCSB workload. The workload
file is emitted by the locally modified RocksDB db_bench binary itself via
--benchmarks=ycsbtrace and --tectonic_trace_file. Python only orchestrates the
runs and records metadata; it does not generate workload operations.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT_DIR / "data" / "overall_benchmarks" / "db_bench_ycsb_workloads"
TMP_DIR = Path("/tmp/db_bench_ycsb_workloads")
DEFAULT_DB_BENCH = (
    ROOT_DIR
    / "rocksdb-benchmark-harness"
    / "cmake-build-release"
    / "vendor"
    / "rocksdb"
    / "db_bench"
)
WORKLOADS = ["A", "B", "C", "D", "E", "F"]
BASE_RECORD_COUNT = 1_000_000
BASE_OPERATION_COUNT = 1_000_000
INSERT_VALUE_SIZE = 1024
UPDATE_VALUE_SIZE = 128
SCAN_LENGTH = 100
SEP = "=" * 72


def fail(message: str) -> None:
    print(f"[FATAL] {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def scaled(value: int, scale: float) -> int:
    if scale <= 0:
        fail("--scale must be positive")
    return max(1, int(round(value * scale)))


def parse_workloads(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return WORKLOADS
    selected = []
    for item in value.split(","):
        workload = item.strip().upper()
        if not workload:
            continue
        if workload not in WORKLOADS:
            fail(f"unknown workload {workload!r}; choose A-F or all")
        selected.append(workload)
    if not selected:
        fail("no workloads selected")
    return selected


def workload_run_mix(workload: str, run_count: int) -> dict[str, int]:
    if workload == "A":
        point_queries = run_count // 2
        return {"point_queries": point_queries, "updates": run_count - point_queries}
    if workload == "B":
        point_queries = (run_count * 95) // 100
        return {"point_queries": point_queries, "updates": run_count - point_queries}
    if workload == "C":
        return {"point_queries": run_count}
    if workload == "D":
        latest_point_queries = (run_count * 95) // 100
        return {"latest_point_queries": latest_point_queries, "inserts": run_count - latest_point_queries}
    if workload == "E":
        range_queries = (run_count * 95) // 100
        return {"range_queries": range_queries, "inserts": run_count - range_queries}
    if workload == "F":
        point_queries = run_count // 2
        return {"point_queries": point_queries, "merges": run_count - point_queries}
    fail(f"unknown workload {workload!r}")


def operation_counts(record_count: int, run_mix: dict[str, int]) -> dict[str, int]:
    counts: dict[str, int] = {"I": record_count}
    if "inserts" in run_mix:
        counts["I"] += run_mix["inserts"]
    if "point_queries" in run_mix:
        counts["P"] = run_mix["point_queries"]
    if "latest_point_queries" in run_mix:
        counts["P"] = counts.get("P", 0) + run_mix["latest_point_queries"]
    if "updates" in run_mix:
        counts["U"] = run_mix["updates"]
    if "range_queries" in run_mix:
        counts["SC"] = run_mix["range_queries"]
    if "merges" in run_mix:
        counts["M"] = run_mix["merges"]
    return counts


def write_metadata(
    metadata_path: Path,
    workload: str,
    scale: float,
    record_count: int,
    run_count: int,
    run_mix: dict[str, int],
    counts: dict[str, int],
    db_bench_bin: Path,
    output_path: Path,
    cmd: list[str],
) -> None:
    metadata = {
        "workload": workload,
        "scale": scale,
        "record_count": record_count,
        "run_count": run_count,
        "run_mix": run_mix,
        "operation_counts": counts,
        "total_operations": record_count + run_count,
        "format": "Tectonic text workload: FS/FE markers plus I, P, U, SC, M records",
        "generator": "modified RocksDB db_bench",
        "mode": "native_db_bench_ycsbtrace",
        "db_bench_bin": str(db_bench_bin),
        "trace_file": str(output_path),
        "trace_file_generated_by": "db_bench --benchmarks=ycsbtrace --tectonic_trace_file=<path>",
        "threads": 1,
        "seed": 1,
        "writer_buffer_bytes": 1_048_576,
        "insert_value_size": INSERT_VALUE_SIZE,
        "update_value_size": UPDATE_VALUE_SIZE,
        "scan_length": SCAN_LENGTH,
        "command": cmd,
        "comparison_note": (
            "db_bench was modified in vendor/rocksdb/tools/db_bench_tool.cc to "
            "materialize generated YCSB A-F operations as a Tectonic text workload file. "
            "No standalone Python workload generator is used for these records."
        ),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")


def run_one(workload: str, scale: float, db_bench_bin: Path, keep_output: bool) -> None:
    record_count = scaled(BASE_RECORD_COUNT, scale)
    run_count = scaled(BASE_OPERATION_COUNT, scale)
    run_mix = workload_run_mix(workload, run_count)
    counts = operation_counts(record_count, run_mix)
    output_path = TMP_DIR / f"db_bench_ycsb_{workload}.txt"
    metadata_path = OUT_DIR / f"DBBench_{workload}.metadata.json"
    log_path = OUT_DIR / f"DBBench_{workload}.log"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    cmd = [
        "/usr/bin/time",
        "-v",
        str(db_bench_bin),
        "--benchmarks=ycsbtrace",
        f"--tectonic_trace_file={output_path}",
        f"--tectonic_trace_ycsb_workload={workload}",
        f"--tectonic_trace_load_count={record_count}",
        f"--tectonic_trace_run_count={run_count}",
        f"--tectonic_trace_insert_value_size={INSERT_VALUE_SIZE}",
        f"--tectonic_trace_update_value_size={UPDATE_VALUE_SIZE}",
        f"--tectonic_trace_scan_length={SCAN_LENGTH}",
        f"--num={record_count}",
        f"--reads={run_count}",
        "--threads=1",
        "--seed=1",
    ]
    print(f"\n{SEP}\n  modified db_bench YCSB workload {workload}\n{SEP}", flush=True)
    print("  " + " ".join(cmd), flush=True)
    with log_path.open("w") as log_file:
        result = subprocess.run(cmd, cwd=ROOT_DIR, stdout=log_file, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        fail(f"workload {workload} failed with exit code {result.returncode}; see {log_path}")

    write_metadata(
        metadata_path=metadata_path,
        workload=workload,
        scale=scale,
        record_count=record_count,
        run_count=run_count,
        run_mix=run_mix,
        counts=counts,
        db_bench_bin=db_bench_bin,
        output_path=output_path,
        cmd=cmd,
    )
    if output_path.exists() and not keep_output:
        output_path.unlink()
    print(f"  log saved     : {log_path}", flush=True)
    print(f"  metadata saved: {metadata_path}", flush=True)
    if keep_output:
        print(f"  trace saved   : {output_path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate modified db_bench logs for the derived YCSB workloads comparison.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--workloads", default="all", help="comma-separated workload letters or all")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--db-bench-bin", type=Path, default=DEFAULT_DB_BENCH)
    parser.add_argument("--keep-output", action="store_true", help="keep generated workload files under /tmp")
    args = parser.parse_args()
    if args.scale <= 0:
        fail("--scale must be positive")

    db_bench_bin = args.db_bench_bin.resolve()
    if not db_bench_bin.exists():
        fail(f"db_bench binary not found: {db_bench_bin}")
    if not db_bench_bin.is_file():
        fail(f"db_bench path is not a file: {db_bench_bin}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    workloads = parse_workloads(args.workloads)
    print(SEP)
    print("  experiment : modified db_bench YCSB A-F workload-generation baseline")
    print(f"  workloads  : {workloads}")
    print(f"  scale      : {args.scale}")
    print("  threads    : 1")
    print(f"  db_bench   : {db_bench_bin}")
    print(f"  output dir : {OUT_DIR}")
    print(SEP, flush=True)
    for workload in workloads:
        run_one(workload, args.scale, db_bench_bin, args.keep_output)


if __name__ == "__main__":
    main()
