#!/usr/bin/env python3
"""Generate and compare small mixed db_bench-compatible and X-Bench workloads."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
import subprocess
import sys

from generate_db_bench_workload_file import DEFAULT_KEY_SIZE, DEFAULT_VALUE_SIZE, WorkloadWriter


ROOT_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT_DIR / "data" / "db_bench_exp_test" / "small_mixed"
DEFAULT_XBENCH_CLI = ROOT_DIR / "target" / "release" / "xbench-cli"
DEFAULT_LOAD_INSERTS = 100
DEFAULT_UPDATES = 50
DEFAULT_POINT_QUERIES = 50
DEFAULT_RANGE_QUERIES = 50
DEFAULT_SCAN_LENGTH = 11


def fail(message: str) -> None:
    print(f"[FATAL] {message}", file=sys.stderr)
    sys.exit(1)


def write_xbench_spec(path: Path, args: argparse.Namespace) -> None:
    spec = {
        "$schema": "../../../workload_schema.json",
        "sections": [
            {
                "groups": [
                    {
                        "name": "Load Phase",
                        "enable_granular_stats": True,
                        "inserts": {
                            "op_count": args.load_inserts,
                            "key": {"uniform": {"len": args.key_size, "character_set": "numeric"}},
                            "val": {"uniform": {"len": args.value_size}},
                        },
                    },
                    {
                        "name": "Execution Phase",
                        "enable_granular_stats": True,
                        "point_queries": {
                            "op_count": args.point_queries,
                            "selection": {"uniform": {"min": 0, "max": 1}},
                        },
                        "updates": {
                            "op_count": args.updates,
                            "selection": {"uniform": {"min": 0, "max": 1}},
                            "val": {"uniform": {"len": args.value_size}},
                        },
                        "range_queries": {
                            "op_count": args.range_queries,
                            "scan_length": args.scan_length,
                            "selection": {"uniform": {"min": 0, "max": 1}},
                        },
                    },
                ]
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(spec, f, indent=2)


def generate_db_bench_mixed(path: Path, args: argparse.Namespace) -> dict:
    writer = WorkloadWriter(
        path,
        key_size=args.key_size,
        value_size=args.value_size,
        seed=args.seed,
        include_markers=True,
    )
    phase_rng = random.Random(args.seed + 1)
    operations = (
        ["P"] * args.point_queries
        + ["U"] * args.updates
        + ["SC"] * args.range_queries
    )
    phase_rng.shuffle(operations)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb", buffering=1024 * 1024) as out:
        writer.marker_start(out, "Load Phase")
        for index in range(args.load_inserts):
            writer.write_insert(out, index, args.load_inserts)
        writer.marker_end(out, "Load Phase")

        writer.marker_start(out, "Execution Phase")
        update_index = 0
        for op in operations:
            if op == "P":
                writer.write_point_query(out, args.load_inserts)
            elif op == "U":
                writer.write_update(out, args.load_inserts, update_index)
                update_index += 1
            elif op == "SC":
                writer.write_range_query(out, args.load_inserts, args.scan_length)
        writer.marker_end(out, "Execution Phase")

    return {
        "output_path": str(path),
        "kind": "db_bench_compatible_small_mixed",
        "seed": args.seed,
        "load_inserts": args.load_inserts,
        "point_queries": args.point_queries,
        "updates": args.updates,
        "range_queries": args.range_queries,
        "scan_length": args.scan_length,
        "key_size": args.key_size,
        "value_size": args.value_size,
        "operation_counts": {key: value for key, value in writer.operation_counts.items() if value},
        "total_operations": sum(writer.operation_counts.values()),
        "note": (
            "This is a compatible text generator for the db_bench workload family. "
            "Native db_bench does not output X-Bench text workloads."
        ),
    }


def run_xbench_generate(xbench_cli: Path, spec_path: Path, output_path: Path) -> None:
    if not xbench_cli.exists():
        fail(f"xbench-cli not found: {xbench_cli}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(xbench_cli), "generate", "-w", str(spec_path), "-o", str(output_path)]
    print(f"  $ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=ROOT_DIR, check=True)


def split_line(line: bytes) -> tuple[str, list[bytes]]:
    parts = line.rstrip(b"\n").split(b" ")
    if not parts or not parts[0]:
        return "", []
    return parts[0].decode("ascii", errors="replace"), parts[1:]


def summarize_workload(path: Path) -> dict:
    counts = Counter()
    marker_counts = Counter()
    key_lengths: dict[str, Counter] = defaultdict(Counter)
    value_lengths: dict[str, Counter] = defaultdict(Counter)
    scan_lengths = Counter()
    op_sequence = []
    sample_lines = []
    line_count = 0

    with path.open("rb") as f:
        for raw in f:
            line_count += 1
            op, parts = split_line(raw)
            if not op:
                continue
            if op in {"FS", "FE"}:
                marker_counts[op] += 1
                continue
            counts[op] += 1
            op_sequence.append(op)
            if len(sample_lines) < 8:
                sample_lines.append(raw.rstrip(b"\n")[:120].decode("ascii", errors="replace"))
            if op in {"I", "U"} and len(parts) >= 2:
                key_lengths[op][len(parts[0])] += 1
                value_lengths[op][len(b" ".join(parts[1:]))] += 1
            elif op == "P" and parts:
                key_lengths[op][len(parts[0])] += 1
            elif op == "SC" and len(parts) >= 2:
                key_lengths[op][len(parts[0])] += 1
                try:
                    scan_lengths[int(parts[1])] += 1
                except ValueError:
                    scan_lengths["invalid"] += 1

    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "line_count": line_count,
        "operation_counts": dict(counts),
        "marker_counts": dict(marker_counts),
        "key_lengths": {key: dict(counter) for key, counter in key_lengths.items()},
        "value_lengths": {key: dict(counter) for key, counter in value_lengths.items()},
        "scan_lengths": dict(scan_lengths),
        "operation_sequence_prefix": op_sequence[:40],
        "sample_lines": sample_lines,
    }


def compare_summaries(db_summary: dict, xbench_summary: dict) -> dict:
    all_ops = sorted(set(db_summary["operation_counts"]) | set(xbench_summary["operation_counts"]))
    count_delta = {
        op: db_summary["operation_counts"].get(op, 0) - xbench_summary["operation_counts"].get(op, 0)
        for op in all_ops
    }
    return {
        "operation_count_delta_db_bench_minus_xbench": count_delta,
        "operation_counts_match": all(delta == 0 for delta in count_delta.values()),
        "key_lengths_match": db_summary["key_lengths"] == xbench_summary["key_lengths"],
        "value_lengths_match": db_summary["value_lengths"] == xbench_summary["value_lengths"],
        "scan_lengths_match": db_summary["scan_lengths"] == xbench_summary["scan_lengths"],
        "operation_sequence_prefix_match": (
            db_summary["operation_sequence_prefix"] == xbench_summary["operation_sequence_prefix"]
        ),
    }


def write_report(path: Path, payload: dict) -> None:
    lines = []
    lines.append("small mixed workload file comparison")
    lines.append("=" * 44)
    lines.append("")
    lines.append(f"db_bench-compatible file: {payload['db_bench']['path']}")
    lines.append(f"xbench file           : {payload['xbench']['path']}")
    lines.append("")
    lines.append("operation counts")
    for op in ["I", "P", "U", "SC"]:
        db_count = payload["db_bench"]["operation_counts"].get(op, 0)
        x_count = payload["xbench"]["operation_counts"].get(op, 0)
        lines.append(f"  {op:<2} db_bench={db_count:<6} xbench={x_count:<6}")
    lines.append("")
    lines.append(f"operation counts match : {payload['comparison']['operation_counts_match']}")
    lines.append(f"key lengths match      : {payload['comparison']['key_lengths_match']}")
    lines.append(f"value lengths match    : {payload['comparison']['value_lengths_match']}")
    lines.append(f"scan lengths match     : {payload['comparison']['scan_lengths_match']}")
    lines.append(f"prefix sequence match  : {payload['comparison']['operation_sequence_prefix_match']}")
    lines.append("")
    lines.append("db_bench-compatible sample")
    lines.extend(f"  {line}" for line in payload["db_bench"]["sample_lines"])
    lines.append("")
    lines.append("xbench sample")
    lines.extend(f"  {line}" for line in payload["xbench"]["sample_lines"])
    path.write_text("\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and compare small mixed workload files.")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--xbench-cli", default=str(DEFAULT_XBENCH_CLI))
    parser.add_argument("--load-inserts", type=int, default=DEFAULT_LOAD_INSERTS)
    parser.add_argument("--updates", type=int, default=DEFAULT_UPDATES)
    parser.add_argument("--point-queries", type=int, default=DEFAULT_POINT_QUERIES)
    parser.add_argument("--range-queries", type=int, default=DEFAULT_RANGE_QUERIES)
    parser.add_argument("--scan-length", type=int, default=DEFAULT_SCAN_LENGTH)
    parser.add_argument("--key-size", type=int, default=DEFAULT_KEY_SIZE)
    parser.add_argument("--value-size", type=int, default=DEFAULT_VALUE_SIZE)
    parser.add_argument("--seed", type=int, default=7)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for name in ("load_inserts", "updates", "point_queries", "range_queries", "scan_length", "key_size", "value_size"):
        if getattr(args, name) < 1:
            fail(f"--{name.replace('_', '-')} must be positive")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = out_dir / "xbench_small_mixed.spec.json"
    db_bench_path = out_dir / "db_bench_small_mixed.txt"
    xbench_path = out_dir / "xbench_small_mixed.txt"
    comparison_path = out_dir / "small_mixed_comparison.json"
    report_path = out_dir / "small_mixed_comparison_report.txt"

    write_xbench_spec(spec_path, args)
    db_metadata = generate_db_bench_mixed(db_bench_path, args)
    run_xbench_generate(Path(args.xbench_cli).expanduser().resolve(), spec_path, xbench_path)

    db_summary = summarize_workload(db_bench_path)
    xbench_summary = summarize_workload(xbench_path)
    payload = {
        "parameters": {
            "load_inserts": args.load_inserts,
            "updates": args.updates,
            "point_queries": args.point_queries,
            "range_queries": args.range_queries,
            "scan_length": args.scan_length,
            "key_size": args.key_size,
            "value_size": args.value_size,
            "seed": args.seed,
        },
        "xbench_spec": str(spec_path),
        "db_bench_metadata": db_metadata,
        "db_bench": db_summary,
        "xbench": xbench_summary,
        "comparison": compare_summaries(db_summary, xbench_summary),
    }
    with comparison_path.open("w") as f:
        json.dump(payload, f, indent=2)
    write_report(report_path, payload)
    print(f"db_bench-compatible workload: {db_bench_path}")
    print(f"xbench workload           : {xbench_path}")
    print(f"comparison json            : {comparison_path}")
    print(f"comparison report          : {report_path}")


if __name__ == "__main__":
    main()
