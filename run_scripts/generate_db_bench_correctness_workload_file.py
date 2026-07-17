#!/usr/bin/env python3
"""Emit a db_bench-style correctness workload in Tectonic text format.

This adapter models the native db_bench thread-count behavior relevant to the
correctness experiment: every worker receives the full configured phase count.
For a requested N-record Workload B style workload, threads=2 therefore emits
2N inserts, 2*0.95N point queries, and 2*0.05N updates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys


KEY_PREFIX = b"usertable:user"
KEY_NUMERIC_LEN = 19
VALUE_CHARS = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
POINT_QUERY_RATIO = 0.95
UPDATE_RATIO = 0.05


def fail(message: str) -> None:
    print(f"[FATAL] {message}", file=sys.stderr)
    sys.exit(1)


def key_from_int(value: int) -> bytes:
    encoded = str(max(0, value)).encode("ascii")
    if len(encoded) >= KEY_NUMERIC_LEN:
        numeric = encoded[-KEY_NUMERIC_LEN:]
    else:
        numeric = b"0" * (KEY_NUMERIC_LEN - len(encoded)) + encoded
    return KEY_PREFIX + numeric


def make_value_pool(rng: random.Random, value_size: int) -> bytes:
    pool_size = max(4096, value_size * 256)
    return bytes(VALUE_CHARS[rng.randrange(len(VALUE_CHARS))] for _ in range(pool_size))


def value_from_pool(pool: bytes, offset: int, value_size: int) -> bytes:
    offset %= len(pool)
    end = offset + value_size
    if end <= len(pool):
        return pool[offset:end]
    split = len(pool) - offset
    return pool[offset:] + pool[: value_size - split]


def write_value_op(out, op: bytes, key: bytes, pool: bytes, index: int, value_size: int) -> None:
    out.write(op + b" " + key + b" " + value_from_pool(pool, index * value_size, value_size) + b"\n")


def expected_counts(record_count: int) -> dict[str, int]:
    point_queries = int(round(record_count * POINT_QUERY_RATIO))
    updates = int(round(record_count * UPDATE_RATIO))
    return {"I": record_count, "P": point_queries, "U": updates}


def write_workload(args: argparse.Namespace) -> dict:
    if args.record_count < 1:
        fail("--record-count must be positive")
    if args.record_count % 20 != 0:
        fail("--record-count must be a multiple of 20 so the 95/5 split is integral")
    if args.threads < 1:
        fail("--threads must be positive")
    if args.insert_value_size < 1 or args.update_value_size < 1:
        fail("value sizes must be positive")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    op_rng = random.Random(args.seed + 17)
    insert_pool = make_value_pool(rng, args.insert_value_size)
    update_pool = make_value_pool(rng, args.update_value_size)
    per_thread = expected_counts(args.record_count)
    counts = {"I": 0, "P": 0, "U": 0}

    with output_path.open("wb", buffering=1024 * 1024) as out:
        for thread_id in range(args.threads):
            if not args.no_markers:
                out.write(f"FS G db_bench_thread_{thread_id}_load\n".encode("ascii"))
            for index in range(args.record_count):
                write_value_op(out, b"I", key_from_int(index), insert_pool, index, args.insert_value_size)
                counts["I"] += 1
            if not args.no_markers:
                out.write(f"FE G db_bench_thread_{thread_id}_load\n".encode("ascii"))
                out.write(f"FS G db_bench_thread_{thread_id}_run\n".encode("ascii"))

            run_ops = ["P"] * per_thread["P"] + ["U"] * per_thread["U"]
            op_rng.shuffle(run_ops)
            for index, op in enumerate(run_ops):
                key = key_from_int(rng.randrange(args.record_count))
                if op == "P":
                    out.write(b"P " + key + b"\n")
                    counts["P"] += 1
                else:
                    write_value_op(out, b"U", key, update_pool, index, args.update_value_size)
                    counts["U"] += 1
            if not args.no_markers:
                out.write(f"FE G db_bench_thread_{thread_id}_run\n".encode("ascii"))

    metadata = {
        "adapter": "db_bench_correctness_workload_file",
        "record_count": args.record_count,
        "threads": args.threads,
        "thread_generation_model": "db_bench-compatible per-worker phase counts",
        "requested_single_thread_operation_counts": per_thread,
        "operation_counts": counts,
        "total_operations": sum(counts.values()),
        "insert_value_size": args.insert_value_size,
        "update_value_size": args.update_value_size,
        "output_path": str(output_path),
        "format": "Tectonic text workload: I key value, P key, U key value",
    }
    if args.metadata:
        metadata_path = Path(args.metadata)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with metadata_path.open("w") as f:
            json.dump(metadata, f, indent=2)
            f.write("\n")
    print(json.dumps(metadata, indent=2))
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a db_bench-style correctness workload trace.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--record-count", type=int, required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--output", required=True)
    parser.add_argument("--metadata")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--insert-value-size", type=int, default=16)
    parser.add_argument("--update-value-size", type=int, default=16)
    parser.add_argument("--no-markers", action="store_true")
    return parser.parse_args()


def main() -> None:
    write_workload(parse_args())


if __name__ == "__main__":
    main()
