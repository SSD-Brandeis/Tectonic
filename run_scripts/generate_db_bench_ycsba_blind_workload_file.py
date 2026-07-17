#!/usr/bin/env python3
"""Emit a db_bench-style YCSB-A blind workload in Tectonic text format.

Native RocksDB db_bench does not materialize its internal operations as the
line-oriented workload file used by Tectonic. This compatibility generator
keeps the YCSB-A blind operation shape used by example-specs/ycsb_blind/a.spec.json:
1M scaled load inserts, then 500k scaled blind point reads and 500k scaled
updates.
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
DEFAULT_SCALE = 0.01
DEFAULT_UPDATE_VALUE_SIZE = 128
DEFAULT_INSERT_VALUE_SIZE = 1024
DEFAULT_KEY_NUMERIC_LEN = 19
KEY_PREFIX = b"usertable:user"
VALUE_CHARS = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def fail(message: str) -> None:
    print(f"[FATAL] {message}", file=sys.stderr)
    sys.exit(1)


def scaled_count(base: int, scale: float) -> int:
    if scale <= 0:
        fail("--scale must be positive")
    return max(1, int(base * scale))


def key_from_int(value: int, numeric_len: int) -> bytes:
    encoded = str(max(0, value)).encode("ascii")
    if len(encoded) >= numeric_len:
        numeric = encoded[-numeric_len:]
    else:
        numeric = b"0" * (numeric_len - len(encoded)) + encoded
    return KEY_PREFIX + numeric


def random_key(rng: random.Random, numeric_len: int) -> bytes:
    upper = 10**numeric_len
    return KEY_PREFIX + f"{rng.randrange(upper):0{numeric_len}d}".encode("ascii")


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
        self.cdf: list[float] = []
        running = 0.0
        for rank in range(1, n + 1):
            running += 1.0 / (rank**s)
            self.cdf.append(running)
        if running <= 0.0:
            fail("zipf normalizer must be positive")
        self.cdf = [value / running for value in self.cdf]

    def sample(self, rng: random.Random) -> int:
        return bisect.bisect_left(self.cdf, rng.random())


def write_workload(args: argparse.Namespace) -> dict:
    record_count = args.record_count if args.record_count is not None else scaled_count(BASE_RECORD_COUNT, args.scale)
    operation_count = (
        args.operation_count
        if args.operation_count is not None
        else scaled_count(BASE_OPERATION_COUNT, args.scale)
    )
    if record_count < 1:
        fail("--record-count must be positive")
    if operation_count < 2:
        fail("--operation-count must be at least 2 for YCSB-A")

    blind_reads = operation_count // 2
    updates = operation_count - blind_reads
    rng = random.Random(args.seed)
    op_rng = random.Random(args.seed + 1)
    zipf = ZipfSampler(record_count, args.zipf_s)
    insert_pool = make_value_pool(rng, args.insert_value_size)
    update_pool = make_value_pool(rng, args.update_value_size)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    counts = {"I": 0, "BP": 0, "U": 0}
    with output_path.open("wb", buffering=1024 * 1024) as out:
        if not args.no_markers:
            out.write(b"FS G\n")
        for index in range(record_count):
            key = random_key(rng, args.key_numeric_len)
            value = value_from_pool(insert_pool, index * args.insert_value_size, args.insert_value_size)
            out.write(b"I " + key + b" " + value + b"\n")
            counts["I"] += 1
        if not args.no_markers:
            out.write(b"FE G Load Phase\n")
            out.write(b"FS G\n")

        run_ops = ["BP"] * blind_reads + ["U"] * updates
        op_rng.shuffle(run_ops)
        for index, op in enumerate(run_ops):
            selected_key = key_from_int(zipf.sample(rng), args.key_numeric_len)
            if op == "BP":
                out.write(b"BP " + selected_key + b"\n")
                counts["BP"] += 1
            else:
                value = value_from_pool(update_pool, index * args.update_value_size, args.update_value_size)
                out.write(b"U " + selected_key + b" " + value + b"\n")
                counts["U"] += 1

        if not args.no_markers:
            out.write(b"FE G Execution Phase\n")

    metadata = {
        "output_path": str(output_path),
        "format": "Tectonic text workload: I key value, BP key, U key value",
        "workload": "YCSB-A blind",
        "scale": args.scale,
        "record_count": record_count,
        "operation_count": operation_count,
        "blind_point_queries": blind_reads,
        "updates": updates,
        "key_prefix": KEY_PREFIX.decode("ascii"),
        "key_numeric_len": args.key_numeric_len,
        "insert_value_size": args.insert_value_size,
        "update_value_size": args.update_value_size,
        "zipf_s": args.zipf_s,
        "seed": args.seed,
        "operation_counts": counts,
        "total_operations": sum(counts.values()),
    }
    if args.metadata:
        metadata_path = Path(args.metadata)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with metadata_path.open("w") as f:
            json.dump(metadata, f, indent=2)
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a db_bench-style YCSB-A blind workload text file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--scale", type=float, default=DEFAULT_SCALE)
    parser.add_argument("--record-count", type=int)
    parser.add_argument("--operation-count", type=int)
    parser.add_argument("--key-numeric-len", type=int, default=DEFAULT_KEY_NUMERIC_LEN)
    parser.add_argument("--insert-value-size", type=int, default=DEFAULT_INSERT_VALUE_SIZE)
    parser.add_argument("--update-value-size", type=int, default=DEFAULT_UPDATE_VALUE_SIZE)
    parser.add_argument("--zipf-s", type=float, default=0.99)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--metadata")
    parser.add_argument("--no-markers", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for name in ("key_numeric_len", "insert_value_size", "update_value_size"):
        if getattr(args, name) < 1:
            fail(f"--{name.replace('_', '-')} must be positive")
    metadata = write_workload(args)
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
