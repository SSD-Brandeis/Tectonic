#!/usr/bin/env python3
"""Parse large mixgraph traces for db_bench and X-Bench, and extract key-space access heatmap data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

def parse_trace(trace_path: Path) -> tuple[list[str], list[str]]:
    """Parses a X-Bench flat trace file.
    
    Returns:
      - all_keys: List of all keys encountered in the trace (for building sorted key sequence)
      - exec_gets: List of keys accessed by Gets (P or BP operations) in the Execution Phase (Group 1)
    """
    all_keys = []
    exec_gets = []
    
    # Check if there are group markers in the file
    has_markers = False
    with trace_path.open("r", errors="replace") as f:
        for line in f:
            if "FS G" in line:
                has_markers = True
                break
                
    group_idx = 1 if not has_markers else -1
    
    with trace_path.open("r", errors="replace") as f:
        for line in f:
            line = line.strip()
            # Skip empty lines, status messages or comments
            if not line or line.startswith("[") or line.startswith("#"):
                continue
                
            parts = line.split()
            if not parts:
                continue
                
            op = parts[0]
            
            if op == "FS" and len(parts) > 1 and parts[1] == "G":
                group_idx += 1
                continue
            elif op == "FE" and len(parts) > 1 and parts[1] == "G":
                continue
                
            # Parse operations
            if op in ("I", "U", "P", "BP", "SC", "BR"):
                if len(parts) < 2:
                    continue
                key = parts[1]
                all_keys.append(key)
                
                # If we are in the Execution Phase, record Point Queries (Gets / BP / P)
                if op in ("P", "BP") and group_idx == 1:
                    exec_gets.append(key)
                    
    return all_keys, exec_gets

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract heatmap data from mixgraph traces.")
    parser.add_argument("--db-bench-trace", required=True, help="Path to db_bench flat trace")
    parser.add_argument("--xbench-trace", required=True, help="Path to X-Bench flat trace")
    parser.add_argument("--output", required=True, help="Path to save output JSON data")
    args = parser.parse_args()

    db_bench_trace = Path(args.db_bench_trace)
    xbench_trace = Path(args.xbench_trace)
    output_path = Path(args.output)

    print(f"Parsing db_bench trace: {db_bench_trace}...")
    db_all_keys, db_gets = parse_trace(db_bench_trace)
    
    print(f"Parsing X-Bench trace: {xbench_trace}...")
    x_all_keys, x_gets = parse_trace(x_trace_path := xbench_trace)

    # Process db_bench heatmap data
    print("Processing db_bench key sequences...")
    db_unique_keys = sorted(list(set(db_all_keys)))
    db_key_to_id = {key: idx for idx, key in enumerate(db_unique_keys)}
    
    db_counts = [0] * len(db_unique_keys)
    for key in db_gets:
        if key in db_key_to_id:
            db_counts[db_key_to_id[key]] += 1
            
    # Process X-Bench heatmap data
    print("Processing X-Bench key sequences...")
    x_unique_keys = sorted(list(set(x_all_keys)))
    x_key_to_id = {key: idx for idx, key in enumerate(x_unique_keys)}
    
    x_counts = [0] * len(x_unique_keys)
    for key in x_gets:
        if key in x_key_to_id:
            x_counts[x_key_to_id[key]] += 1

    payload = {
        "db_bench": {
            "key_sequence": list(range(len(db_unique_keys))),
            "access_counts": db_counts
        },
        "xbench": {
            "key_sequence": list(range(len(x_unique_keys))),
            "access_counts": x_counts
        }
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(payload, f)

    print(f"Saved parsed heatmap data to {output_path}")
    print(f"db_bench keys count: {len(db_unique_keys)}, total execution gets: {len(db_gets)}")
    print(f"X-Bench keys count: {len(x_unique_keys)}, total execution gets: {len(x_gets)}")

if __name__ == "__main__":
    main()
