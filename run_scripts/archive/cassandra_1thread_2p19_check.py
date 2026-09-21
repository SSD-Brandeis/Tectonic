#!/usr/bin/env python3
"""
Rerun of official YCSB's Cassandra @ 2^19 variance check with -threads
explicitly pinned to 1 (the earlier official_ycsb_variance_check.py run
relied on YCSB's own default threadcount=1 without passing -threads
explicitly). Writes to a separate results file so it doesn't collide with
or get skipped by the checkpoint already in
data/official_ycsb_variance_check/results.json.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_table5_rerun as t5
import official_ycsb_variance_check as base

TRIALS = 5
SCALE = 2 ** 19
OUT_PATH = f"{base.OUT_DIR}/results_cassandra_1thread_2p19.json"


def main():
    results = []
    if os.path.exists(OUT_PATH):
        results = json.load(open(OUT_PATH))

    for trial in range(len(results) + 1, TRIALS + 1):
        t5.banner("TRIAL", f"official YCSB cassandra (explicit -threads 1) scale=2^19 trial={trial}/{TRIALS}")
        res = base.trial_cassandra(SCALE)
        if res is None:
            t5.log("  [skip] trial failed, not recording")
            continue
        t5.log(f"  load={res['load_ms']/1000:.2f}s run={res['run_ms']/1000:.2f}s total={res['total_ms']/1000:.2f}s")
        results.append(res)
        json.dump(results, open(OUT_PATH, "w"), indent=2)

    t5.banner("SUMMARY", "cassandra @ 2^19, explicit -threads 1")
    summ = base.summarize([r["total_ms"] for r in results])
    if summ:
        t5.log(f"  mean={summ['mean_s']}s stdev={summ['stdev_s']}s min={summ['min_s']}s "
               f"max={summ['max_s']}s CV={summ['cv_pct']}% spread={summ['spread_pct']}%")
    print("COMPLETED: cassandra 1-thread 2^19 check finished")


if __name__ == "__main__":
    main()
