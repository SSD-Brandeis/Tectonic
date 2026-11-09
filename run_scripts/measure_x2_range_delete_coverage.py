#!/usr/bin/env python3
"""
measure_tec2_range_delete_coverage.py
=====================================
R1.O8 / R2.O2 (ii), audit item B3: how much of the live key set each range delete of Tec-2 removes,
measured on the final spec (x-bench-specs/table4/Tec-2_interleaved.spec.json) with the KeySet Algorithm 1
selects (--keyset adaptive), as in the experiment.

One `tectonic-cli generate` run (single-threaded, page cache dropped), then a replay of the trace:
the live keys are the inserted keys minus point deletes and range deletes. A range delete in StartCount
format ("<op> <start key> <count>") removes the first <count> live keys >= <start key>.

Output: data/revision/R1.O8-R2.O2_keyset-selection/analysis/tec2_range_delete_coverage.{json,md}
"""
import os
import json
import time
import bisect
import statistics
import subprocess

ROOT_DIR = "/home/cc/Tectonic"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"
SPEC = f"{ROOT_DIR}/x-bench-specs/table4/Tec-2_interleaved.spec.json"
OUT_DIR = f"{ROOT_DIR}/data/revision/R1.O8-R2.O2_keyset-selection/analysis"
TRACE = f"{OUT_DIR}/tmp_tec2_trace.txt"


def generate():
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)
    env = {k: v for k, v in os.environ.items() if k != "TECTONIC_PARALLEL_GEN"}  # single-threaded
    p = subprocess.run([TECTONIC_CLI, "generate", "-w", SPEC, "-o", TRACE, "--keyset", "adaptive"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, check=True)
    return [line for line in p.stderr.splitlines() if "[Tectonic KeySet]" in line]


def replay():
    live = []  # sorted live keys
    ops, deletes = {}, []
    with open(TRACE) as f:
        for line in f:
            parts = line.split()
            op = parts[0]
            ops[op] = ops.get(op, 0) + 1
            if op == "I":
                bisect.insort(live, parts[1])
            elif op == "D":
                i = bisect.bisect_left(live, parts[1])
                if i < len(live) and live[i] == parts[1]:
                    live.pop(i)
            elif op not in ("P", "U", "M", "S"):  # a range delete: start key and count
                start, count = parts[1], int(parts[2])
                i = bisect.bisect_left(live, start)
                removed = min(count, len(live) - i)
                deletes.append({"live_before": len(live), "removed": removed, "count": count,
                                "start_rank": i})
                del live[i:i + removed]
    return ops, deletes


def main():
    t0 = time.time()
    keysets = generate()
    ops, deletes = replay()
    os.remove(TRACE)
    live = [d["live_before"] for d in deletes]
    full = sum(d["removed"] == d["live_before"] for d in deletes)
    res = {"spec": SPEC, "keyset": keysets, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
           "op_counts": ops, "range_deletes": len(deletes),
           "range_deletes_removing_every_live_key": full,
           "live_keys_before_a_range_delete": {"mean": statistics.mean(live), "min": min(live), "max": max(live)},
           "range_delete_count_field": {"min": min(d["count"] for d in deletes),
                                        "max": max(d["count"] for d in deletes)},
           "elapsed_s": time.time() - t0}
    with open(f"{OUT_DIR}/tec2_range_delete_coverage.json", "w") as f:
        json.dump(res, f, indent=2)
    with open(f"{OUT_DIR}/tec2_range_delete_coverage.md", "w") as f:
        f.write(f"""# Tec-2 range-delete coverage, final spec (audit item B3)

Measured {res['timestamp']} by `run_scripts/measure_tec2_range_delete_coverage.py`: one generation of
`x-bench-specs/table4/Tec-2_interleaved.spec.json` with `--keyset adaptive` ({'; '.join(keysets)}), then a
replay of the trace that tracks the live keys.

| | |
| --- | --- |
| Range deletes in the trace | {len(deletes):,} |
| Range deletes that removed every live key | {full:,} |
| Live keys before a range delete: mean / min / max | {res['live_keys_before_a_range_delete']['mean']:,.0f} / {min(live):,} / {max(live):,} |
| Operations in the trace | {', '.join(f'{k}={v:,}' for k, v in sorted(ops.items()))} |
""")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
