#!/usr/bin/env python3
"""Build the final Table 5 revision results: Redis, Cassandra and ScyllaDB from the
original multi-run experiment, RocksDB from the rerun with RocksDB statistics enabled
(tectonic-cli -c statistics=1), which reports exact byte counters.

Writes <out>/results/results_raw.json; rebuild the summary, table and documentation with
  python3 run_scripts/run_table5_revision_multirun.py --report-only \
      --out-dir <out> --rocksdb-config statistics=1
"""
import os
import sys
import json
import argparse

ROOT_DIR = "/home/cc/Tectonic"
ORIGINAL = f"{ROOT_DIR}/data/table-5-revision-multirun"
ROCKSDB_RERUN = f"{ROOT_DIR}/data/table-5-revision-multirun-rocksdb-statistics"
OUT = f"{ROOT_DIR}/data/table-5-revision-final"
TRIALS = 5


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--original", default=ORIGINAL)
    parser.add_argument("--rocksdb-rerun", default=ROCKSDB_RERUN)
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()

    original = json.load(open(f"{args.original}/results/results_raw.json"))
    rerun = json.load(open(f"{args.rocksdb_rerun}/results/results_raw.json"))

    rocksdb = rerun["results"].get("rocksdb", {})
    problems = []
    for scale in original["results"]["rocksdb"]:
        for workload in ("ycsb", "tectonic"):
            trials = rocksdb.get(scale, {}).get(workload, [])
            if len(trials) != TRIALS:
                problems.append(f"rocksdb {scale} {workload}: {len(trials)} trials")
            for t in trials:
                stats = [p for p in t["db_log_files"] if p.endswith("_rocksdb_stats.txt")]
                if len(stats) != 1 or "rocksdb.wal.bytes COUNT" not in open(stats[0]).read():
                    problems.append(f"rocksdb {scale} {workload} trial {t['trial']}: no statistics")
    if problems:
        sys.exit("RocksDB rerun incomplete:\n  " + "\n  ".join(problems))

    results = dict(original["results"])
    results["rocksdb"] = rocksdb
    merged = {
        "config": {
            "experiment": "table-5-revision-final",
            "trials": TRIALS,
            "sources": {
                "rocksdb": f"{args.rocksdb_rerun}/results/results_raw.json",
                "redis": f"{args.original}/results/results_raw.json",
                "cassandra": f"{args.original}/results/results_raw.json",
                "scylla": f"{args.original}/results/results_raw.json",
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
