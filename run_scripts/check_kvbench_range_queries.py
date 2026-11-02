#!/usr/bin/env python3
"""
check_kvbench_range_queries.py
==============================
Checks the range queries of a KV-Bench workload file (lines "I <key> <value>", "S <start key> <end key>"), for the
range-query fix of KV-Bench `refactor` 699bc10 (2026-11-02, data/revision/fig7-corrected/kvbench-minimal-fix/):
  - operation counts;
  - every S start and end key is an inserted key, and start <= end;
  - the length of every S: the number of inserted keys in [start, end] (KV-Bench's range queries come after all
    inserts, RQ_THRESHOLD 1), as a histogram: share of length 1, min, max, mean, and counts by length bucket.
Prints one JSON object. Usage: python3 check_kvbench_range_queries.py <workload file>
"""
import sys
import json
import bisect
import collections


def main():
    keys, scans, ops = [], [], collections.Counter()
    with open(sys.argv[1]) as f:
        for line in f:
            p = line.split()
            if not p:
                continue
            ops[p[0]] += 1
            if p[0] == "I":
                keys.append(p[1])
            elif p[0] == "S":
                scans.append((p[1], p[2]))
    keys.sort()
    key_set = set(keys)
    bad_key = bad_order = 0
    lengths = []
    for s, e in scans:
        if s not in key_set or e not in key_set:
            bad_key += 1
            continue
        if s > e:
            bad_order += 1
            continue
        lengths.append(bisect.bisect_right(keys, e) - bisect.bisect_left(keys, s))
    hist = collections.Counter(lengths)
    buckets = collections.Counter(min(n // 10, 11) for n in lengths)  # 0-9, 10-19, ..., 110+
    out = {"ops": dict(ops), "inserted_keys": len(keys), "distinct_inserted_keys": len(key_set),
           "scans": len(scans), "scans_with_unknown_key": bad_key, "scans_start_after_end": bad_order,
           "length_min": min(lengths), "length_max": max(lengths), "length_mean": sum(lengths) / len(lengths),
           "share_length_1": hist[1] / len(lengths), "share_length_2": hist[2] / len(lengths),
           "count_by_length_bucket_of_10": {f"{b * 10}-{b * 10 + 9}" if b < 11 else "110+": buckets[b]
                                            for b in sorted(buckets)}}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
