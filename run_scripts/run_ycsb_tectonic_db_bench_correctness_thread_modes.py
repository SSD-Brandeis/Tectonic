#!/usr/bin/env python3
"""Run workload-count correctness checks for Tectonic, YCSB, and db_bench.

The experiment uses YCSB Workload B composition with compact values:
  load: 100 percent inserts
  run:  95 percent point queries and 5 percent updates

Tectonic has exact per-operation counts in its spec. YCSB uses probabilistic
operation selection for the 95/5 run phase. The db_bench-compatible adapter
models native db_bench's per-worker phase counts, where every worker receives
the full configured --num/--reads/--writes count.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data" / "ycsb_tectonic_correctness_thread_modes"
SPEC_DIR = DATA_DIR / "specs"
TRACE_DIR = DATA_DIR / "workloads"
LOG_DIR = DATA_DIR / "logs"
RESULTS_PATH = DATA_DIR / "results.json"
PLOT_SCRIPT = ROOT_DIR / "plot_scripts" / "plot_ycsb_tectonic_db_bench_correctness_thread_modes.py"
TECTONIC_CLI = ROOT_DIR / "target" / "release" / "tectonic-cli"
DB_BENCH_ADAPTER = ROOT_DIR / "run_scripts" / "generate_db_bench_correctness_workload_file.py"
HARNESS_DIR = ROOT_DIR / "rocksdb-benchmark-harness"
YCSB_DIR = HARNESS_DIR / "vendor" / "YCSB"
M2 = Path("/home/cc/.m2/repository")

BASE_RECORD_COUNT = 1_000_000
POINT_QUERY_RATIO = 0.95
UPDATE_RATIO = 0.05
INSERT_VALUE_SIZE = 1024
UPDATE_VALUE_SIZE = 128
DEFAULT_COUNTS = [200, 400, 1_000, 2_000, 5_000, 10_000, 20_000]
DEFAULT_THREADS = [1, 2]
DEFAULT_REPETITIONS = 3

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


def fail(message: str) -> None:
    print(f"[FATAL] {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def ensure_file(path: Path, label: str) -> None:
    if not path.exists():
        fail(f"{label} not found: {path}")


def parse_int_list(value: str, label: str) -> list[int]:
    parsed = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError:
            fail(f"{label} contains a non-integer value: {item!r}")
        if number < 1:
            fail(f"{label} values must be positive")
        parsed.append(number)
    if not parsed:
        fail(f"{label} cannot be empty")
    return parsed


def write_custom_spec() -> Path:
    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    spec_path = SPEC_DIR / "workload_b_correctness_compact.spec.json"
    spec = {
        "$schema": "../../../workload_schema.json",
        "sections": [
            {
                "groups": [
                    {
                        "name": "Load Phase",
                        "enable_granular_stats": True,
                        "inserts": {
                            "op_count": BASE_RECORD_COUNT,
                            "key": {
                                "segmented": {
                                    "segments": [
                                        "usertable:user",
                                        {"uniform": {"len": 19, "character_set": "numeric"}},
                                    ],
                                    "separator": "",
                                }
                            },
                            "val": {"uniform": {"len": INSERT_VALUE_SIZE}},
                        },
                    },
                    {
                        "name": "Execution Phase",
                        "enable_granular_stats": True,
                        "point_queries": {
                            "op_count": int(BASE_RECORD_COUNT * POINT_QUERY_RATIO),
                            "selection": {"zipf": {"s": 0.99, "n": BASE_RECORD_COUNT}},
                        },
                        "updates": {
                            "op_count": int(BASE_RECORD_COUNT * UPDATE_RATIO),
                            "val": {"uniform": {"len": UPDATE_VALUE_SIZE}},
                            "selection": {"zipf": {"s": 0.99, "n": BASE_RECORD_COUNT}},
                        },
                    },
                ]
            }
        ],
    }
    with spec_path.open("w") as f:
        json.dump(spec, f, indent=2)
        f.write("\n")
    return spec_path


def expected_counts(record_count: int) -> dict[str, int]:
    point_queries = int(round(record_count * POINT_QUERY_RATIO))
    updates = int(round(record_count * UPDATE_RATIO))
    return {"I": record_count, "P": point_queries, "U": updates}


def scale_for_count(record_count: int) -> float:
    return record_count / BASE_RECORD_COUNT


def output_for_thread(base: Path, thread_id: int) -> Path:
    return base.with_suffix(base.suffix + f".{thread_id}")


def repetition_tag(repetition: int) -> str:
    return f"r{repetition}"


def stats_log_path(repetitions: int) -> Path:
    return DATA_DIR / f"stats_log_{repetitions}runs.jsonl"


def count_trace_ops(paths: list[Path]) -> dict[str, int]:
    counts = {"I": 0, "P": 0, "U": 0, "SC": 0, "M": 0, "D": 0, "malformed": 0}
    for path in paths:
        with path.open("rb") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                op = line.split(maxsplit=1)[0].decode("ascii", errors="replace")
                if op == "S":
                    op = "SC"
                if op in counts:
                    counts[op] += 1
                elif op not in {"FS", "FE"}:
                    counts["malformed"] += 1
    return {key: value for key, value in counts.items() if value}


def accuracy(actual: int, expected: int) -> float:
    if expected == 0:
        return 100.0 if actual == 0 else 0.0
    return max(0.0, 100.0 * (1.0 - abs(actual - expected) / expected))


def add_accuracy(actual_counts: dict[str, int], expected: dict[str, int]) -> dict[str, Any]:
    per_op = {op: accuracy(actual_counts.get(op, 0), expected_count) for op, expected_count in expected.items()}
    total_actual = sum(actual_counts.get(op, 0) for op in expected)
    total_expected = sum(expected.values())
    per_op_abs_error = {op: abs(actual_counts.get(op, 0) - expected_count) for op, expected_count in expected.items()}
    return {
        "actual": {op: actual_counts.get(op, 0) for op in expected},
        "unexpected": {op: count for op, count in actual_counts.items() if op not in expected and count},
        "accuracy_by_operation": per_op,
        "minimum_operation_accuracy_percent": min(per_op.values()),
        "total_operation_accuracy_percent": accuracy(total_actual, total_expected),
        "absolute_error_by_operation": per_op_abs_error,
        "sum_absolute_error_percent": 100.0 * sum(per_op_abs_error.values()) / total_expected,
    }


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def sample_stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def run_cmd(label: str, cmd: list[str], log_path: Path, cwd: Path | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    print(f"  [{label}] {' '.join(cmd)}", flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    with log_path.open("w") as log:
        result = subprocess.run(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    elapsed = time.monotonic() - start
    print(f"  [{label}] exit={result.returncode} elapsed={elapsed:.2f}s log={log_path}", flush=True)
    if result.returncode != 0:
        try:
            tail = "".join(log_path.read_text(errors="replace").splitlines(True)[-80:])
        except FileNotFoundError:
            tail = ""
        if tail:
            print(tail, file=sys.stderr)
        fail(f"{label} failed with exit code {result.returncode}; see {log_path}")
    return {
        "cmd": cmd,
        "cwd": str(cwd) if cwd else None,
        "log_path": str(log_path),
        "duration_s": elapsed,
        "return_code": result.returncode,
    }


def remove_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def generate_tectonic(spec_path: Path, record_count: int, threads: int, repetition: int, keep_workloads: bool) -> dict[str, Any]:
    rep = repetition_tag(repetition)
    base = TRACE_DIR / f"tectonic_b_count{record_count}_t{threads}_{rep}.txt"
    remove_paths([base] + [output_for_thread(base, t) for t in range(threads)])
    cmd = [
        str(TECTONIC_CLI),
        "generate",
        "-w",
        str(spec_path),
        "-o",
        str(base),
        "-s",
        f"{scale_for_count(record_count):.12g}",
        "-t",
        str(threads),
    ]
    env = os.environ.copy()
    if threads > 1:
        env["TECTONIC_PARALLEL_GEN"] = "1"
    command = run_cmd(
        "Tectonic",
        cmd,
        LOG_DIR / f"tectonic_count{record_count}_t{threads}_{rep}.log",
        cwd=ROOT_DIR,
        env=env,
    )
    outputs = [base] if threads == 1 else [output_for_thread(base, t) for t in range(threads)]
    counts = count_trace_ops(outputs)
    if not keep_workloads:
        remove_paths(outputs)
    return {"output_paths": [str(path) for path in outputs], "counts": counts, "commands": [command]}


def generate_ycsb(record_count: int, threads: int, repetition: int, keep_workloads: bool) -> dict[str, Any]:
    rep = repetition_tag(repetition)
    load_part = TRACE_DIR / f"ycsb_b_count{record_count}_t{threads}_{rep}.load.part"
    run_part = TRACE_DIR / f"ycsb_b_count{record_count}_t{threads}_{rep}.run.part"
    combined = TRACE_DIR / f"ycsb_b_count{record_count}_t{threads}_{rep}.txt"
    remove_paths([load_part, run_part, combined])
    common = [
        "java",
        "-cp",
        YCSB_CP,
        "site.ycsb.Client",
        "-db",
        "site.ycsb.db.FileClient",
        "-P",
        "workloads/workloadb",
        "-threads",
        str(threads),
        "-p",
        f"recordcount={record_count}",
        "-p",
        f"operationcount={record_count}",
        "-p",
        "fieldcount=1",
        "-p",
        f"fieldlength={INSERT_VALUE_SIZE}",
    ]
    load_command = run_cmd(
        "YCSB load",
        common + ["-p", f"file.output={load_part}", "-load"],
        LOG_DIR / f"ycsb_load_count{record_count}_t{threads}_{rep}.log",
        cwd=YCSB_DIR,
    )
    run_command = run_cmd(
        "YCSB run",
        common + ["-p", f"file.output={run_part}", "-t"],
        LOG_DIR / f"ycsb_run_count{record_count}_t{threads}_{rep}.log",
        cwd=YCSB_DIR,
    )
    with combined.open("wb") as out:
        for part in (load_part, run_part):
            with part.open("rb") as inp:
                shutil.copyfileobj(inp, out)
    counts = count_trace_ops([combined])
    if not keep_workloads:
        remove_paths([load_part, run_part, combined])
    return {
        "output_paths": [str(combined)],
        "phase_part_paths": [str(load_part), str(run_part)],
        "counts": counts,
        "commands": [load_command, run_command],
    }


def generate_db_bench(record_count: int, threads: int, repetition: int, keep_workloads: bool) -> dict[str, Any]:
    rep = repetition_tag(repetition)
    output = TRACE_DIR / f"db_bench_b_count{record_count}_t{threads}_{rep}.txt"
    metadata = DATA_DIR / "metadata" / f"db_bench_b_count{record_count}_t{threads}_{rep}.metadata.json"
    remove_paths([output, metadata])
    cmd = [
        "python3",
        str(DB_BENCH_ADAPTER),
        "--record-count",
        str(record_count),
        "--threads",
        str(threads),
        "--output",
        str(output),
        "--insert-value-size",
        str(INSERT_VALUE_SIZE),
        "--update-value-size",
        str(UPDATE_VALUE_SIZE),
        "--metadata",
        str(metadata),
    ]
    command = run_cmd(
        "db_bench-compatible",
        cmd,
        LOG_DIR / f"db_bench_count{record_count}_t{threads}_{rep}.log",
        cwd=ROOT_DIR,
    )
    counts = count_trace_ops([output])
    if not keep_workloads:
        remove_paths([output])
    return {"output_paths": [str(output)], "metadata_path": str(metadata), "counts": counts, "commands": [command]}


def append_stats_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        json.dump(record, f, sort_keys=True)
        f.write("\n")


def run_repetition(
    spec_path: Path,
    record_count: int,
    threads: int,
    repetition: int,
    keep_workloads: bool,
    stats_path: Path,
) -> dict[str, Any]:
    expected = expected_counts(record_count)
    print(f"\n=== count={record_count:,} threads={threads} repetition={repetition} ===", flush=True)
    tool_runs = {
        "tectonic": generate_tectonic(spec_path, record_count, threads, repetition, keep_workloads),
        "ycsb": generate_ycsb(record_count, threads, repetition, keep_workloads),
        "db_bench": generate_db_bench(record_count, threads, repetition, keep_workloads),
    }
    tools = {}
    for tool, result in tool_runs.items():
        metrics = add_accuracy(result["counts"], expected)
        tools[tool] = {**result, **metrics}
        stats_record = {
            "record_type": "tool_repetition_stats",
            "record_count": record_count,
            "threads": threads,
            "setting": "sequential" if threads == 1 else "parallel",
            "repetition": repetition,
            "tool": tool,
            "expected": expected,
            **tools[tool],
        }
        append_stats_record(stats_path, stats_record)
        print(
            f"  {tool:9s} actual={metrics['actual']} "
            f"min_acc={metrics['minimum_operation_accuracy_percent']:.2f}% "
            f"sum_abs_err={metrics['sum_absolute_error_percent']:.2f}%",
            flush=True,
        )
    return {"repetition": repetition, "tools": tools}


def aggregate_tool(tool: str, raw_runs: list[dict[str, Any]], expected: dict[str, int]) -> dict[str, Any]:
    tool_runs = [run["tools"][tool] for run in raw_runs]
    min_values = [float(run["minimum_operation_accuracy_percent"]) for run in tool_runs]
    total_values = [float(run["total_operation_accuracy_percent"]) for run in tool_runs]
    sum_error_values = [float(run["sum_absolute_error_percent"]) for run in tool_runs]
    accuracy_by_operation = {
        op: mean([float(run["accuracy_by_operation"][op]) for run in tool_runs])
        for op in expected
    }
    actual_mean = {
        op: mean([float(run["actual"].get(op, 0)) for run in tool_runs])
        for op in expected
    }
    absolute_error_mean = {
        op: mean([float(run["absolute_error_by_operation"].get(op, 0)) for run in tool_runs])
        for op in expected
    }
    return {
        "actual": actual_mean,
        "accuracy_by_operation": accuracy_by_operation,
        "minimum_operation_accuracy_percent": mean(min_values),
        "minimum_operation_accuracy_percent_stddev": sample_stddev(min_values),
        "total_operation_accuracy_percent": mean(total_values),
        "total_operation_accuracy_percent_stddev": sample_stddev(total_values),
        "sum_absolute_error_percent": mean(sum_error_values),
        "sum_absolute_error_percent_stddev": sample_stddev(sum_error_values),
        "absolute_error_by_operation": absolute_error_mean,
        "repetition_values": {
            "minimum_operation_accuracy_percent": min_values,
            "total_operation_accuracy_percent": total_values,
            "sum_absolute_error_percent": sum_error_values,
        },
    }


def run_config(
    spec_path: Path,
    record_count: int,
    threads: int,
    repetitions: int,
    keep_workloads: bool,
    stats_path: Path,
) -> dict[str, Any]:
    expected = expected_counts(record_count)
    raw_runs = [
        run_repetition(spec_path, record_count, threads, repetition, keep_workloads, stats_path)
        for repetition in range(1, repetitions + 1)
    ]
    tools = {tool: aggregate_tool(tool, raw_runs, expected) for tool in ("tectonic", "ycsb", "db_bench")}
    summary = {
        "record_type": "config_average_stats",
        "record_count": record_count,
        "threads": threads,
        "setting": "sequential" if threads == 1 else "parallel",
        "repetitions": repetitions,
        "expected": expected,
        "tools": tools,
    }
    append_stats_record(stats_path, summary)
    print(
        f"  averaged over {repetitions} runs: "
        f"Tectonic+={tools['tectonic']['minimum_operation_accuracy_percent']:.2f}% "
        f"YCSB={tools['ycsb']['minimum_operation_accuracy_percent']:.2f}% "
        f"db_bench={tools['db_bench']['minimum_operation_accuracy_percent']:.2f}%",
        flush=True,
    )
    return {
        "record_count": record_count,
        "threads": threads,
        "setting": "sequential" if threads == 1 else "parallel",
        "repetitions": repetitions,
        "expected": expected,
        "tools": tools,
        "raw_runs": raw_runs,
    }


def write_results(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RESULTS_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(RESULTS_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Tectonic/YCSB/db_bench workload-count correctness experiment.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--counts", default=",".join(str(value) for value in DEFAULT_COUNTS))
    parser.add_argument("--threads", default=",".join(str(value) for value in DEFAULT_THREADS))
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--keep-workloads", action="store_true")
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--plot-only", action="store_true")
    args = parser.parse_args()

    if args.repetitions < 1:
        fail("--repetitions must be positive")

    counts = parse_int_list(args.counts, "--counts")
    thread_settings = parse_int_list(args.threads, "--threads")
    for count in counts:
        if count % 20 != 0:
            fail("--counts values must be multiples of 20 so the 95/5 expected split is integral")
        expected = expected_counts(count)
        for threads in thread_settings:
            indivisible = [op for op, expected_count in expected.items() if expected_count % threads != 0]
            if indivisible:
                fail(
                    f"count {count} is not exactly divisible across {threads} threads "
                    f"for operations {indivisible}; choose counts that divide I/P/U per thread"
                )

    for path, label in (
        (TECTONIC_CLI, "tectonic-cli"),
        (DB_BENCH_ADAPTER, "db_bench-compatible adapter"),
        (YCSB_DIR / "file" / "target" / "file-binding-0.18.0-SNAPSHOT.jar", "YCSB file binding jar"),
        (YCSB_DIR / "core" / "target" / "core-0.18.0-SNAPSHOT.jar", "YCSB core jar"),
    ):
        ensure_file(path, label)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    spec_path = write_custom_spec()
    current_stats_path = stats_log_path(args.repetitions)

    if not args.plot_only:
        if current_stats_path.exists():
            archive_path = current_stats_path.with_suffix(f".prev_{int(time.time())}.jsonl")
            current_stats_path.replace(archive_path)
            print(f"archived previous stats log: {archive_path}", flush=True)
        runs = []
        for threads in thread_settings:
            for record_count in counts:
                runs.append(run_config(spec_path, record_count, threads, args.repetitions, args.keep_workloads, current_stats_path))
        payload = {
            "experiment": "ycsb_tectonic_correctness_thread_modes",
            "mode": "workload_b_operation_count_accuracy_averaged",
            "root_dir": str(ROOT_DIR),
            "data_dir": str(DATA_DIR),
            "stats_log_path": str(current_stats_path),
            "plot_dir": str(ROOT_DIR / "ycsb_tectonic_correctness_plots" / "thread_modes"),
            "custom_tectonic_spec": str(spec_path),
            "workload": {
                "name": "YCSB workload B compact value count accuracy",
                "base_record_count": BASE_RECORD_COUNT,
                "load": {"inserts": 1.0},
                "run": {"point_queries": POINT_QUERY_RATIO, "updates": UPDATE_RATIO},
                "insert_value_size": INSERT_VALUE_SIZE,
                "update_value_size": UPDATE_VALUE_SIZE,
            },
            "counts": counts,
            "thread_settings": thread_settings,
            "repetitions": args.repetitions,
            "tool_notes": {
                "tectonic": "generate --threads divides the requested scale across worker output files.",
                "YCSB": "Client.java divides operationcount across worker threads, but CoreWorkload selects the 95/5 mix probabilistically.",
                "db_bench": "The adapter models native db_bench phase counters as per-worker counts, so threads multiply generated operations.",
            },
            "metric_definitions": {
                "minimum_operation_accuracy_percent": "average across repetitions of the minimum over I/P/U of max(0, 100 * (1 - abs(actual - expected) / expected))",
                "minimum_operation_accuracy_percent_stddev": "sample standard deviation across repetitions",
                "sum_absolute_error_percent": "average across repetitions of sum over I/P/U abs(actual - expected) divided by expected I+P+U, as a percentage",
            },
            "runs": runs,
        }
        write_results(payload)
        print(f"\nresults saved: {RESULTS_PATH}", flush=True)
        print(f"stats log saved: {current_stats_path}", flush=True)

    if not args.no_plot:
        run_cmd(
            "plot",
            ["python3", str(PLOT_SCRIPT), "--results", str(RESULTS_PATH)],
            LOG_DIR / f"plot_{args.repetitions}runs.log",
            cwd=ROOT_DIR,
        )


if __name__ == "__main__":
    main()
