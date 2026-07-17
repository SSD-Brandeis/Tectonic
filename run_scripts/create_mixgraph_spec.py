#!/usr/bin/env python3
"""Programmatically create a Tectonic .spec.json file for the mixgraph benchmark."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Tectonic mixgraph spec file.")
    parser.add_argument("--num", type=int, default=10000, help="Number of entries to load")
    parser.add_argument("--reads", type=int, default=4200, help="Number of execution queries")
    parser.add_argument("--key-size", type=int, default=48, help="Key size in bytes")
    parser.add_argument("--output", required=True, help="Output spec path")

    # Ratios
    parser.add_argument("--mix-get-ratio", type=float, default=0.85)
    parser.add_argument("--mix-put-ratio", type=float, default=0.14)
    parser.add_argument("--mix-seek-ratio", type=float, default=0.01)

    # Prefix modeling parameters
    parser.add_argument("--keyrange-dist-a", type=float, default=14.18)
    parser.add_argument("--keyrange-dist-b", type=float, default=-2.917)
    parser.add_argument("--keyrange-dist-c", type=float, default=0.0164)
    parser.add_argument("--keyrange-dist-d", type=float, default=-0.08082)
    parser.add_argument("--keyrange-num", type=int, default=30)

    # Pareto modeling parameters
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

    # 1. Calculate the prefix weights using the two-term exponential distribution
    weights = []
    # In C++, the loop goes from keyrange_num down to 1
    for pfx in range(args.keyrange_num, 0, -1):
        p = args.keyrange_dist_a * math.exp(args.keyrange_dist_b * pfx) + \
            args.keyrange_dist_c * math.exp(args.keyrange_dist_d * pfx)
        if p < 1e-16:
            p = 0.0
        weights.append(p)

    total_w = sum(weights)
    if total_w > 0:
        normalized_weights = [w / total_w for w in weights]
    else:
        normalized_weights = [1.0 / args.keyrange_num] * args.keyrange_num

    # 2. Build the weighted prefix choices
    # Format prefix as "kXX:" (4 bytes)
    prefix_choices = []
    for i, w in enumerate(normalized_weights):
        prefix_choices.append({
            "weight": w,
            "value": f"k{i:02d}:"
        })

    # Suffix length to make the key exactly key_size bytes
    suffix_len = args.key_size - 4
    if suffix_len < 1:
        suffix_len = 8

    key_expr = {
        "segmented": {
            "separator": "",
            "segments": [
                {
                    "weighted": prefix_choices
                },
                {
                    "uniform": {
                        "len": suffix_len,
                        "character_set": "numeric"
                    }
                }
            ]
        }
    }

    # 3. Calculate operation counts based on ratios
    total_mix_ops = args.reads
    sum_ratios = args.mix_get_ratio + args.mix_put_ratio + args.mix_seek_ratio
    
    gets = int(round(total_mix_ops * (args.mix_get_ratio / sum_ratios)))
    puts = int(round(total_mix_ops * (args.mix_put_ratio / sum_ratios)))
    seeks = total_mix_ops - gets - puts

    spec = {
        "$schema": "./workload_schema.json",
        "sections": [
            {
                "groups": [
                    {
                        "name": "Load Phase",
                        "enable_granular_stats": True,
                        "inserts": {
                            "op_count": args.num,
                            "key": key_expr,
                            "val": {
                                "uniform": {
                                    "len": 100
                                }
                            }
                        }
                    },
                    {
                        "name": "Execution Phase",
                        "enable_granular_stats": True,
                        "inserts": {
                            "op_count": puts,
                            "key": key_expr,
                            "val": {
                                "uniform": {
                                    "len": {
                                        "pareto": {
                                            "scale": args.value_sigma,
                                            "shape": 1.0 / args.value_k if args.value_k > 0 else 3.824
                                        }
                                    }
                                }
                            }
                        },
                        "point_queries": {
                            "op_count": gets,
                            "selection": {
                                "uniform": {
                                    "min": 0.0,
                                    "max": 1.0
                                }
                            }
                        },
                        "range_queries": {
                            "op_count": seeks,
                            "selection": {
                                "uniform": {
                                    "min": 0.0,
                                    "max": 1.0
                                }
                            },
                            "scan_length": {
                                "pareto": {
                                    "scale": args.iter_sigma,
                                    "shape": args.iter_k
                                }
                            }
                        }
                    }
                ]
            }
        ]
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(spec, f, indent=2)
    
    print(f"Created Tectonic spec file: {out_path}")
    print(f"Load Phase: {args.num} inserts")
    print(f"Execution Phase: {gets} Gets, {puts} Puts, {seeks} Seeks")

if __name__ == "__main__":
    main()
