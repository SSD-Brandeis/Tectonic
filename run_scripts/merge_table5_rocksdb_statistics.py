#!/usr/bin/env python3
"""Build the final Table 5 revision results: Redis, Cassandra and ScyllaDB from the
original multi-run experiment, RocksDB from the rerun with RocksDB statistics enabled
(xbench-cli -c statistics=1), which reports exact byte counters; and two cells, Redis 2^18 and
Cassandra 2^19 (both workloads, all metrics), from their rerun of 2026-11-03 on fixedkeyset-2
(run_scripts/run_table5_two_cells.py: the same harness, binary fc63df5f, trace files, images and client
configuration). Their bytes transferred (iostat) differed by 7.06% and 9.35% between YCSB and X-Bench in
the original run; the original trials stay in data/table-5-revision-multirun/.

Writes <out>/results/results_raw.json; rebuild the summary, table and documentation with
  python3 run_scripts/run_table5_revision_multirun.py --report-only \
      --out-dir <out> --rocksdb-config statistics=1
"""
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import os
import sys
import json
import argparse

ROOT_DIR = REPO_ROOT
ORIGINAL = f"{ROOT_DIR}/data/table-5-revision-multirun"
ROCKSDB_RERUN = f"{ROOT_DIR}/data/table-5-revision-multirun-rocksdb-statistics"
TWO_CELL_RERUN = f"{ROOT_DIR}/data/table-5-revision-two-cell-rerun-2026-11-03"
TWO_CELLS = [("redis", "262144"), ("cassandra", "524288")]
OUT = f"{ROOT_DIR}/data/table-5-revision-final"
TRIALS = 5


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--original", default=ORIGINAL)
    parser.add_argument("--rocksdb-rerun", default=ROCKSDB_RERUN)
    parser.add_argument("--two-cell-rerun", default=TWO_CELL_RERUN)
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()

    original = json.load(open(f"{args.original}/results/results_raw.json"))
    rerun = json.load(open(f"{args.rocksdb_rerun}/results/results_raw.json"))
    two_cell = json.load(open(f"{args.two_cell_rerun}/results/results_raw.json"))

    rocksdb = rerun["results"].get("rocksdb", {})
    problems = []
    for scale in original["results"]["rocksdb"]:
        for workload in ("ycsb", "xbench"):
            trials = rocksdb.get(scale, {}).get(workload, [])
            if len(trials) != TRIALS:
                problems.append(f"rocksdb {scale} {workload}: {len(trials)} trials")
            for t in trials:
                stats = [p for p in t["db_log_files"] if p.endswith("_rocksdb_stats.txt")]
                if len(stats) != 1 or "rocksdb.wal.bytes COUNT" not in open(stats[0]).read():
                    problems.append(f"rocksdb {scale} {workload} trial {t['trial']}: no statistics")
    if problems:
        sys.exit("RocksDB rerun incomplete:\n  " + "\n  ".join(problems))

    for key in ("client_config", "db_images", "iostat_device", "workload_dir"):
        if two_cell["config"][key] != original["config"][key]:
            problems.append(f"two-cell rerun: {key} differs from the original run")
    for db, scale in TWO_CELLS:
        for workload in ("ycsb", "xbench"):
            n = len(two_cell["results"].get(db, {}).get(scale, {}).get(workload, []))
            if n != TRIALS:
                problems.append(f"two-cell rerun {db} {scale} {workload}: {n} trials")
    if problems:
        sys.exit("two-cell rerun incomplete:\n  " + "\n  ".join(problems))

    results = {db: dict(scales) for db, scales in original["results"].items()}
    results["rocksdb"] = rocksdb
    for db, scale in TWO_CELLS:
        results[db][scale] = two_cell["results"][db][scale]
    merged = {
        "config": {
            "experiment": "table-5-revision-final",
            "trials": TRIALS,
            "sources": {
                "rocksdb": f"{args.rocksdb_rerun}/results/results_raw.json",
                "redis": f"{args.original}/results/results_raw.json",
                "cassandra": f"{args.original}/results/results_raw.json",
                "scylla": f"{args.original}/results/results_raw.json",
                **{f"{db} {scale}": f"{args.two_cell_rerun}/results/results_raw.json" for db, scale in TWO_CELLS},
            },
            "client_config": {**original["config"]["client_config"],
                              "rocksdb": rerun["config"]["client_config"]["rocksdb"]},
            "db_images": original["config"]["db_images"],
            "iostat_device": original["config"]["iostat_device"],
            "workload_dir": original["config"]["workload_dir"],
        },
        "results": results,
    }
    os.makedirs(f"{args.out}/results", exist_ok=True)
    path = f"{args.out}/results/results_raw.json"
    with open(path, "w") as f:
        json.dump(merged, f, indent=2)
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
