#!/usr/bin/env python3
"""Synthesize a db_bench-equivalent mixgraph workload in Tectonic's text operation format."""

from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

# Characters for generating values
VALUE_CHARS = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

def key_from_int(value: int, key_size: int) -> bytes:
    """Formats a key exactly like RocksDB's GenerateKeyFromInt does for ASCII keys."""
    encoded = str(max(0, value)).encode("ascii")
    if len(encoded) >= key_size:
        return encoded[-key_size:]
    return b"0" * (key_size - len(encoded)) + encoded

def pareto_cdf_inversion(u: float, theta: float, k: float, sigma: float) -> int:
    if k == 0.0:
        ret = theta - sigma * math.log(u)
    else:
        ret = theta + sigma * (math.pow(u, -1.0 * k) - 1.0) / k
    return int(math.ceil(ret))

class QueryDecider:
    def __init__(self, ratios: list[float]):
        range_max = 1000
        sum_ratios = sum(ratios)
        self.type_boundaries = []
        current_range = 0
        for ratio in ratios:
            current_range += int(math.ceil(range_max * (ratio / sum_ratios)))
            self.type_boundaries.append(current_range)
        self.range = current_range

    def get_type(self, rand_num: int) -> int:
        pos = rand_num % self.range
        for i, boundary in enumerate(self.type_boundaries):
            if pos < boundary:
                return i
        return 0

class GenerateTwoTermExpKeys:
    def __init__(self, total_keys: int, keyrange_num: int, prefix_a: float, prefix_b: float, prefix_c: float, prefix_d: float):
        self.keyrange_num = keyrange_num
        self.keyrange_size = total_keys // keyrange_num
        self.keyrange_set = []

        amplify = 0
        keyrange_start = 0

        # Calculate prefix probabilities in reverse order as in C++
        for pfx in range(keyrange_num, 0, -1):
            keyrange_p = prefix_a * math.exp(prefix_b * pfx) + prefix_c * math.exp(prefix_d * pfx)
            if keyrange_p < 1e-16:
                keyrange_p = 0.0

            if amplify == 0 and keyrange_p > 0:
                amplify = int(math.floor(1.0 / keyrange_p)) + 1

            p_unit = {
                'keyrange_start': keyrange_start,
                'keyrange_access': 0 if keyrange_p <= 0 else int(math.floor(amplify * keyrange_p)),
                'keyrange_keys': self.keyrange_size
            }
            self.keyrange_set.append(p_unit)
            keyrange_start += p_unit['keyrange_access']

        self.keyrange_rand_max = keyrange_start

        # Shuffle using keyrange_rand_max as seed
        rng = random.Random(self.keyrange_rand_max)
        for i in range(keyrange_num):
            pos = rng.randint(0, keyrange_num - 1)
            self.keyrange_set[i], self.keyrange_set[pos] = self.keyrange_set[pos], self.keyrange_set[i]

        # Recalculate start offsets
        offset = 0
        for p_unit in self.keyrange_set:
            p_unit['keyrange_start'] = offset
            offset += p_unit['keyrange_access']

    def get_key_id(self, ini_rand: int, key_dist_a: float, key_dist_b: float) -> int:
        keyrange_rand = ini_rand % self.keyrange_rand_max

        # Binary search keyrange
        start, end = 0, len(self.keyrange_set)
        while start + 1 < end:
            mid = start + (end - start) // 2
            if keyrange_rand < self.keyrange_set[mid]['keyrange_start']:
                end = mid
            else:
                start = mid
        keyrange_id = start

        if key_dist_a == 0.0 or key_dist_b == 0.0:
            key_offset = ini_rand % self.keyrange_size
        else:
            u = float(ini_rand % self.keyrange_size) / self.keyrange_size
            if u <= 0:
                u = 1e-9
            key_seed = int(math.ceil(math.pow(u / key_dist_a, 1.0 / key_dist_b)))
            rng = random.Random(key_seed)
            key_offset = rng.randint(0, self.keyrange_size - 1)

        return self.keyrange_size * keyrange_id + key_offset

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a mixgraph trace in Tectonic text format.")
    parser.add_argument("--num", type=int, default=10000, help="Total number of entries (FLAGS_num)")
    parser.add_argument("--reads", type=int, default=4200, help="Total operations to execute")
    parser.add_argument("--key-size", type=int, default=48, help="Key size in bytes")
    parser.add_argument("--output", required=True, help="Output trace file path")
    parser.add_argument("--seed", type=int, default=42, help="Seed for tracing generator")
    
    # Mixgraph ratios
    parser.add_argument("--mix-get-ratio", type=float, default=0.85)
    parser.add_argument("--mix-put-ratio", type=float, default=0.14)
    parser.add_argument("--mix-seek-ratio", type=float, default=0.01)

    # Prefix Modeling
    parser.add_argument("--keyrange-dist-a", type=float, default=14.18)
    parser.add_argument("--keyrange-dist-b", type=float, default=-2.917)
    parser.add_argument("--keyrange-dist-c", type=float, default=0.0164)
    parser.add_argument("--keyrange-dist-d", type=float, default=-0.08082)
    parser.add_argument("--keyrange-num", type=int, default=30)

    # Pareto modeling
    parser.add_argument("--value-theta", type=float, default=0.0)
    parser.add_argument("--value-k", type=float, default=0.2615)
    parser.add_argument("--value-sigma", type=float, default=25.45)
    parser.add_argument("--mix-max-value-size", type=int, default=1024)

    parser.add_argument("--iter-theta", type=float, default=0.0)
    parser.add_argument("--iter-k", type=float, default=2.517)
    parser.add_argument("--iter-sigma", type=float, default=14.236)
    parser.add_argument("--mix-max-scan-len", type=int, default=10000)

    # Key distribution within keyrange (defaults to uniform if 0)
    parser.add_argument("--key-dist-a", type=float, default=0.0)
    parser.add_argument("--key-dist-b", type=float, default=0.0)

    args = parser.parse_args()

    # Ratios decider
    decider = QueryDecider([args.mix_get_ratio, args.mix_put_ratio, args.mix_seek_ratio])

    # Keyrange generator
    gen_exp = GenerateTwoTermExpKeys(
        total_keys=args.num,
        keyrange_num=args.keyrange_num,
        prefix_a=args.keyrange_dist_a,
        prefix_b=args.keyrange_dist_b,
        prefix_c=args.keyrange_dist_c,
        prefix_d=args.keyrange_dist_d
    )

    rng = random.Random(args.seed)

    # Pre-build a value pool to draw from quickly
    value_pool_size = max(1024 * 1024, args.mix_max_value_size * 2)
    value_pool = bytes(VALUE_CHARS[rng.randint(0, len(VALUE_CHARS) - 1)] for _ in range(value_pool_size))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    op_counts = {"Gets": 0, "Puts": 0, "Seeks": 0}

    print(f"Generating {args.reads} mixgraph ops (num={args.num}) to {args.output}...")

    with open(out_path, "wb") as f:
        for _ in range(args.reads):
            ini_rand = rng.getrandbits(63) # 63-bit positive random number
            rand_v = ini_rand % args.num
            
            # Select query type
            q_type = decider.get_type(rand_v)

            # Select key
            key_id = gen_exp.get_key_id(ini_rand, args.key_dist_a, args.key_dist_b)
            key_bytes = key_from_int(key_id, args.key_size)

            if q_type == 0:
                # Get
                f.write(b"P " + key_bytes + b"\n")
                op_counts["Gets"] += 1
            elif q_type == 1:
                # Put
                u = float(ini_rand % gen_exp.keyrange_size) / gen_exp.keyrange_size
                if u <= 0:
                    u = 1e-9
                val_size = pareto_cdf_inversion(u, args.value_theta, args.value_k, args.value_sigma)
                if val_size < 10:
                    val_size = 10
                elif val_size > args.mix_max_value_size:
                    val_size = val_size % args.mix_max_value_size
                
                # Slice from value pool
                offset = rng.randint(0, len(value_pool) - val_size - 1)
                value_bytes = value_pool[offset : offset + val_size]
                f.write(b"U " + key_bytes + b" " + value_bytes + b"\n")
                op_counts["Puts"] += 1
            else:
                # Seek
                u = float(ini_rand % gen_exp.keyrange_size) / gen_exp.keyrange_size
                if u <= 0:
                    u = 1e-9
                scan_len = pareto_cdf_inversion(u, args.iter_theta, args.iter_k, args.iter_sigma)
                scan_len = scan_len % args.mix_max_scan_len
                if scan_len <= 0:
                    scan_len = 1
                f.write(b"SC " + key_bytes + b" " + str(scan_len).encode("ascii") + b"\n")
                op_counts["Seeks"] += 1

    print(f"Generated operations: {op_counts}")

if __name__ == "__main__":
    main()
