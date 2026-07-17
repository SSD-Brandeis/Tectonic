#!/usr/bin/env python3
"""Emit db_bench-style YCSB A-F workloads in Tectonic text format.

Native RocksDB db_bench does not materialize generated operations as the
line-oriented workload file used in the YCSB/Tectonic generator comparison.
This compatibility generator keeps the same full-scale YCSB operation counts
and value sizes as example-specs/ycsb/*.spec.json, so it can be timed as an
additional workload-file generation baseline.
"""

from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path
import random
import sys


BASE_RECORD_COUNT = 1_000_000
BASE_OPERATION_COUNT = 1_000_000
DEFAULT_SCALE = 1.0
KEY_PREFIX = b"usertable:user"
KEY_NUMERIC_LEN = 19
INSERT_VALUE_SIZE = 1024
UPDATE_VALUE_SIZE = 128
SCAN_LENGTH = 100
VALUE_CHARS = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

WORKLOAD_MIX = {
    "A": {"point_queries": 500_000, "updates": 500_000},
    "B": {"point_queries": 950_000, "updates": 50_000},
    "C": {"point_queries": 1_000_000},
    "D": {"latest_point_queries": 950_000, "inserts": 50_000},
    "E": {"range_queries": 950_000, "inserts": 50_000},
    "F": {"point_queries": 500_000, "merges": 500_000},
}


def fail(message: str) -> None:
    print(f"[FATAL] {message}", file=sys.stderr)
    sys.exit(1)


def scaled(value: int, scale: float) -> int:
    if scale <= 0:
        fail("--scale must be positive")
    return max(1, int(round(value * scale)))


def key_from_int(value: int) -> bytes:
    encoded = str(max(0, value)).encode("ascii")
    if len(encoded) >= KEY_NUMERIC_LEN:
        numeric = encoded[-KEY_NUMERIC_LEN:]
    else:
        numeric = b"0" * (KEY_NUMERIC_LEN - len(encoded)) + encoded
    return KEY_PREFIX + numeric


def make_value_pool(rng: random.Random, value_size: int) -> bytes:
    pool_size = max(1024 * 1024, value_size * 4096)
    return bytes(VALUE_CHARS[rng.randrange(len(VALUE_CHARS))] for _ in range(pool_size))


def value_from_pool(pool: bytes, offset: int, value_size: int) -> bytes:
    offset %= len(pool)
    end = offset + value_size
    if end <= len(pool):
        return pool[offset:end]
    split = len(pool) - offset
    return pool[offset:] + pool[: value_size - split]


class ZipfSampler:
    def __init__(self, n: int, s: float):
        if n < 1:
            fail("zipf n must be positive")
        running = 0.0
        self.cdf: list[float] = []
        for rank in range(1, n + 1):
            running += 1.0 / (rank**s)
            self.cdf.append(running)
        self.cdf = [value / running for value in self.cdf]

    def sample(self, rng: random.Random) -> int:
        return bisect.bisect_left(self.cdf, rng.random())


def write_value_op(out, op: bytes, key: bytes, pool: bytes, index: int, value_size: int) -> None:
    out.write(op + b" " + key + b" " + value_from_pool(pool, index * value_size, value_size) + b"\n")


def write_workload(args: argparse.Namespace) -> dict:
    workload = args.workload.upper()
    if workload not in WORKLOAD_MIX:
        fail(f"unknown workload {workload!r}")
    record_count = scaled(BASE_RECORD_COUNT, args.scale)
    mix = {name: scaled(count, args.scale) for name, count in WORKLOAD_MIX[workload].items()}
    rng = random.Random(args.seed)
    op_rng = random.Random(args.seed + ord(workload))
    insert_pool = make_value_pool(rng, INSERT_VALUE_SIZE)
    update_pool = make_value_pool(rng, UPDATE_VALUE_SIZE)
    zipf = ZipfSampler(max(record_count, scaled(1_050_000, args.scale)), 0.99)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    counts = {"I": 0, "P": 0, "U": 0, "SC": 0, "M": 0}
    loaded_keys = record_count
    with output_path.open("wb", buffering=1024 * 1024) as out:
        if not args.no_markers:
            out.write(b"FS G\n")
        for index in range(record_count):
            write_value_op(out, b"I", key_from_int(index), insert_pool, index, INSERT_VALUE_SIZE)
            counts["I"] += 1
        if not args.no_markers:
            out.write(b"FE G Load Phase\n")
            out.write(b"FS G\n")

        run_ops: list[str] = []
        for name, count in mix.items():
            run_ops.extend([name] * count)
        op_rng.shuffle(run_ops)

        inserted_in_run = 0
        for index, op in enumerate(run_ops):
            key_space = loaded_keys + inserted_in_run
            if op == "point_queries":
                out.write(b"P " + key_from_int(zipf.sample(rng) % max(1, key_space)) + b"\n")
                counts["P"] += 1
            elif op == "latest_point_queries":
                hot_window = max(1, min(key_space, 10_000))
                out.write(b"P " + key_from_int(key_space - 1 - rng.randrange(hot_window)) + b"\n")
                counts["P"] += 1
            elif op == "updates":
                key = key_from_int(zipf.sample(rng) % max(1, key_space))
                write_value_op(out, b"U", key, update_pool, index, UPDATE_VALUE_SIZE)
                counts["U"] += 1
            elif op == "merges":
                key = key_from_int(zipf.sample(rng) % max(1, key_space))
                write_value_op(out, b"M", key, update_pool, index, UPDATE_VALUE_SIZE)
                counts["M"] += 1
            elif op == "range_queries":
                max_start = max(1, key_space - SCAN_LENGTH + 1)
                out.write(b"SC " + key_from_int(zipf.sample(rng) % max_start) + b" " + str(SCAN_LENGTH).encode("ascii") + b"\n")
                counts["SC"] += 1
            elif op == "inserts":
                key = key_from_int(loaded_keys + inserted_in_run)
                write_value_op(out, b"I", key, insert_pool, record_count + inserted_in_run, INSERT_VALUE_SIZE)
                counts["I"] += 1
                inserted_in_run += 1
            else:
                fail(f"unsupported operation {op!r}")

        if not args.no_markers:
            out.write(b"FE G Execution Phase\n")

    metadata = {
        "workload": workload,
        "scale": args.scale,
        "record_count": record_count,
        "run_mix": mix,
        "operation_counts": {key: value for key, value in counts.items() if value},
        "total_operations": sum(counts.values()),
        "format": "Tectonic text workload: I, P, U, SC, M",
        "comparison_note": (
            "db_bench-compatible workload-file generator; native RocksDB db_bench "
            "does not emit comparable text workload files."
        ),
    }
    if args.metadata:
        metadata_path = Path(args.metadata)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with metadata_path.open("w") as f:
            json.dump(metadata, f, indent=2)
    print(json.dumps(metadata, indent=2))
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a db_bench-compatible YCSB A-F workload file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--workload", required=True, choices=sorted(WORKLOAD_MIX))
    parser.add_argument("--output", required=True)
    parser.add_argument("--scale", type=float, default=DEFAULT_SCALE)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--metadata")
    parser.add_argument("--no-markers", action="store_true")
    return parser.parse_args()


def main() -> None:
    write_workload(parse_args())


if __name__ == "__main__":
    main()
