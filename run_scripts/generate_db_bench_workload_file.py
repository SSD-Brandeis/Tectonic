#!/usr/bin/env python3
"""Emit db_bench-style workloads in Tectonic's text operation format.

This is a compatibility generator, not a wrapper around native RocksDB
db_bench. Native db_bench does not expose its generated operations as the
line-oriented text file that Tectonic can execute. This script recreates the
db_bench workload families used by example-specs/db_bench/*.spec.json and
writes them as executable Tectonic workload text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys


CANONICAL_OPS = 900_000_000
DEFAULT_SCALE = 0.00001
DEFAULT_KEY_SIZE = 20
DEFAULT_VALUE_SIZE = 400
DEFAULT_SCAN_LENGTH = 11
VALUE_CHARS = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

WORKLOADS = {
    "1": {
        "description": "fillrandom",
        "phases": [("load", "insert")],
    },
    "2": {
        "description": "fillrandom,readrandom",
        "phases": [("Load Phase", "insert"), ("Execution Phase", "point_query")],
    },
    "3": {
        "description": "fillrandom,readrandom",
        "phases": [("Load Phase", "insert"), ("Execution Phase", "point_query")],
        "note": "The repository's db_bench/3.spec.json currently matches 2.spec.json.",
    },
    "4": {
        "description": "fillrandom,seekrandom",
        "phases": [("Load Phase", "insert"), ("Execution Phase", "range_query")],
    },
    "4b": {
        "description": "fillrandom,seekrandom reverse",
        "phases": [("Load Phase", "insert"), ("Execution Phase", "range_query")],
        "note": (
            "Tectonic's text format has SC start count but no reverse-scan opcode; "
            "this file preserves the range-query count, not reverse iterator direction."
        ),
    },
    "5": {
        "description": "fillrandom,overwrite",
        "phases": [("Load Phase", "insert"), ("Execution Phase", "update")],
    },
}


def fail(message: str) -> None:
    print(f"[FATAL] {message}", file=sys.stderr)
    sys.exit(1)


def scaled_ops(scale: float) -> int:
    if scale <= 0:
        fail("--scale must be positive")
    return max(1, int(round(CANONICAL_OPS * scale)))


def key_from_int(value: int, key_size: int) -> bytes:
    encoded = str(max(0, value)).encode("ascii")
    if len(encoded) >= key_size:
        return encoded[-key_size:]
    return b"0" * (key_size - len(encoded)) + encoded


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


class WorkloadWriter:
    def __init__(self, path: Path, key_size: int, value_size: int, seed: int, include_markers: bool):
        self.path = path
        self.key_size = key_size
        self.value_size = value_size
        self.include_markers = include_markers
        self.rng = random.Random(seed)
        self.value_pool = make_value_pool(self.rng, value_size)
        self.operation_counts = {"I": 0, "P": 0, "SC": 0, "U": 0}

    def random_key(self, loaded_keys: int) -> bytes:
        upper = max(1, loaded_keys)
        return key_from_int(self.rng.randrange(upper), self.key_size)

    def random_value(self, index: int) -> bytes:
        jitter = self.rng.randrange(len(self.value_pool))
        return value_from_pool(self.value_pool, index * self.value_size + jitter, self.value_size)

    def marker_start(self, out, name: str) -> None:
        if self.include_markers and name != "load":
            out.write(b"FS G\n")

    def marker_end(self, out, name: str) -> None:
        if self.include_markers and name != "load":
            out.write(b"FE G " + name.encode("ascii") + b"\n")

    def write_insert(self, out, index: int, key_space: int) -> None:
        key = key_from_int(self.rng.randrange(max(1, key_space)), self.key_size)
        out.write(b"I " + key + b" " + self.random_value(index) + b"\n")
        self.operation_counts["I"] += 1

    def write_point_query(self, out, loaded_keys: int) -> None:
        out.write(b"P " + self.random_key(loaded_keys) + b"\n")
        self.operation_counts["P"] += 1

    def write_range_query(self, out, loaded_keys: int, scan_length: int) -> None:
        max_start = max(1, loaded_keys - max(1, scan_length) + 1)
        key = key_from_int(self.rng.randrange(max_start), self.key_size)
        out.write(b"SC " + key + b" " + str(scan_length).encode("ascii") + b"\n")
        self.operation_counts["SC"] += 1

    def write_update(self, out, loaded_keys: int, index: int) -> None:
        out.write(b"U " + self.random_key(loaded_keys) + b" " + self.random_value(index) + b"\n")
        self.operation_counts["U"] += 1

    def write(self, workload: str, op_count: int, scan_length: int) -> dict:
        loaded_keys = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wb", buffering=1024 * 1024) as out:
            for phase_name, phase_kind in WORKLOADS[workload]["phases"]:
                self.marker_start(out, phase_name)
                if phase_kind == "insert":
                    for index in range(op_count):
                        self.write_insert(out, index, op_count)
                    loaded_keys += op_count
                elif phase_kind == "point_query":
                    for _ in range(op_count):
                        self.write_point_query(out, loaded_keys)
                elif phase_kind == "range_query":
                    for _ in range(op_count):
                        self.write_range_query(out, loaded_keys, scan_length)
                elif phase_kind == "update":
                    for index in range(op_count):
                        self.write_update(out, loaded_keys, index)
                else:
                    fail(f"unknown phase kind: {phase_kind}")
                self.marker_end(out, phase_name)

        return {
            "output_path": str(self.path),
            "workload": workload,
            "description": WORKLOADS[workload]["description"],
            "note": WORKLOADS[workload].get("note"),
            "scaled_ops_per_phase": op_count,
            "scan_length": scan_length,
            "key_size": self.key_size,
            "value_size": self.value_size,
            "format": "Tectonic text workload: I key value, P key, SC key count, U key value",
            "operation_counts": {key: value for key, value in self.operation_counts.items() if value},
            "total_operations": sum(self.operation_counts.values()),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a db_bench-style Tectonic workload text file.")
    parser.add_argument("--workload", required=True, choices=sorted(WORKLOADS))
    parser.add_argument("--output", required=True)
    parser.add_argument("--scale", type=float, default=DEFAULT_SCALE)
    parser.add_argument("--op-count", type=int, help="override scaled operations per phase")
    parser.add_argument("--key-size", type=int, default=DEFAULT_KEY_SIZE)
    parser.add_argument("--value-size", type=int, default=DEFAULT_VALUE_SIZE)
    parser.add_argument("--scan-length", type=int, default=DEFAULT_SCAN_LENGTH)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--metadata", help="optional JSON metadata output path")
    parser.add_argument("--no-markers", action="store_true", help="do not emit FS/FE phase markers")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.key_size < 1:
        fail("--key-size must be positive")
    if args.value_size < 1:
        fail("--value-size must be positive")
    if args.scan_length < 1:
        fail("--scan-length must be positive")
    op_count = args.op_count if args.op_count is not None else scaled_ops(args.scale)
    if op_count < 1:
        fail("--op-count must be positive")

    output_path = Path(args.output)
    writer = WorkloadWriter(
        output_path,
        key_size=args.key_size,
        value_size=args.value_size,
        seed=args.seed,
        include_markers=not args.no_markers,
    )
    metadata = writer.write(args.workload, op_count, args.scan_length)
    metadata["scale"] = args.scale
    metadata["seed"] = args.seed

    if args.metadata:
        metadata_path = Path(args.metadata)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with metadata_path.open("w") as f:
            json.dump(metadata, f, indent=2)

    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
