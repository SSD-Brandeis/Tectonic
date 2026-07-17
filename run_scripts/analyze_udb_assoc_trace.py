#!/usr/bin/env python3
import sys
import os
import math
from collections import Counter

def main():
    trace_path = "/tmp/udb_assoc_test.txt"
    if not os.path.exists(trace_path):
        print(f"Error: trace file {trace_path} does not exist. Run tectonic-cli first.")
        sys.exit(1)

    print("Analyzing trace file:", trace_path)
    
    # Store stats per group
    # A group starts with "FS G" and ends with "FE G <name>"
    current_group = "Preload"
    group_stats = {}
    
    # We will also collect overall characteristics for non-preload operations
    all_keys = []
    all_values_len = []
    all_scan_lens = []
    
    op_counts = Counter()
    group_op_counts = {}
    
    with open(trace_path, "r") as f:
        for line in f:
            parts = line.strip().split()
            if not parts:
                continue
            
            op = parts[0]
            if op == "FS":
                # Start of group, but name is not here
                continue
            elif op == "FE":
                # End of group, parts[2:] is group name
                group_name = " ".join(parts[2:])
                # Save counts
                group_op_counts[group_name] = dict(op_counts)
                op_counts.clear()
                continue
                
            op_counts[op] += 1
            
            if op == "I":
                # I <key> <val>
                key = parts[1]
                val = parts[2]
                all_keys.append(key)
                all_values_len.append(len(val))
            elif op == "P":
                # P <key>
                key = parts[1]
                all_keys.append(key)
            elif op == "SC":
                # SC <key> <len>
                key = parts[1]
                scan_len = int(parts[2])
                all_keys.append(key)
                all_scan_lens.append(scan_len)

    print("\n=== Group Operations Summary ===")
    for gname, counts in group_op_counts.items():
        total = sum(counts.values())
        print(f"\nGroup: {gname} (Total Ops: {total})")
        for op, count in counts.items():
            pct = (count / total) * 100 if total > 0 else 0
            print(f"  {op}: {count} ({pct:.2f}%)")
            
    # Key length analysis
    key_lens = [len(k) for k in all_keys]
    key_len_counts = Counter(key_lens)
    total_keys = len(all_keys)
    print("\n=== Key Length Distribution ===")
    for length in sorted(key_len_counts.keys()):
        count = key_len_counts[length]
        pct = (count / total_keys) * 100
        print(f"  {length} bytes: {count} ({pct:.2f}%)")
        
    # Prefix distribution analysis (Zipfian check)
    prefixes = [k[:3] for k in all_keys if len(k) >= 3]
    prefix_counts = Counter(prefixes)
    total_prefixes = len(prefixes)
    print("\n=== Prefix Distribution (Top 10) ===")
    for prefix, count in prefix_counts.most_common(10):
        pct = (count / total_prefixes) * 100
        print(f"  {prefix}: {count} ({pct:.2f}%)")
        
    print("\n=== Prefix Distribution (Bottom 5) ===")
    for prefix, count in prefix_counts.most_common()[-5:]:
        pct = (count / total_prefixes) * 100
        print(f"  {prefix}: {count} ({pct:.2f}%)")

    # Value length analysis (Pareto check)
    if all_values_len:
        min_val = min(all_values_len)
        max_val = max(all_values_len)
        avg_val = sum(all_values_len) / len(all_values_len)
        print("\n=== Value Size Statistics ===")
        print(f"  Min size: {min_val} bytes")
        print(f"  Max size: {max_val} bytes")
        print(f"  Avg size: {avg_val:.2f} bytes (Target: ~50 bytes)")
        
        # Percentiles
        sorted_val = sorted(all_values_len)
        p50 = sorted_val[int(len(sorted_val) * 0.5)]
        p90 = sorted_val[int(len(sorted_val) * 0.9)]
        p99 = sorted_val[int(len(sorted_val) * 0.99)]
        print(f"  50th percentile: {p50} bytes")
        print(f"  90th percentile: {p90} bytes")
        print(f"  99th percentile: {p99} bytes")

    # Scan length analysis
    if all_scan_lens:
        min_scan = min(all_scan_lens)
        max_scan = max(all_scan_lens)
        avg_scan = sum(all_scan_lens) / len(all_scan_lens)
        print("\n=== Range Query Scan Length Statistics ===")
        print(f"  Min scan length: {min_scan}")
        print(f"  Max scan length: {max_scan}")
        print(f"  Avg scan length: {avg_scan:.2f} (Target: rapid decay)")
        
        sorted_scan = sorted(all_scan_lens)
        p50 = sorted_scan[int(len(sorted_scan) * 0.5)]
        p90 = sorted_scan[int(len(sorted_scan) * 0.9)]
        p99 = sorted_scan[int(len(sorted_scan) * 0.99)]
        print(f"  50th percentile: {p50}")
        print(f"  90th percentile: {p90}")
        print(f"  99th percentile: {p99}")

if __name__ == "__main__":
    main()
