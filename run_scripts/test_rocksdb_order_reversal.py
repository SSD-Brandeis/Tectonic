#!/usr/bin/env python3
import os
import sys
import time

sys.path.insert(0, "/home/cc/Tectonic/run_scripts")
from run_table5_rerun import (
    generate_ycsb, generate_tectonic, restart_db, teardown_db,
    execute_with_dual_io, drop_caches, validate_trace, log, banner,
    OUT_DIR
)

SCALE = 2**18  # 262,144 ops
YCSB_TRACE = f"/dev/shm/test_sim_ycsb_{SCALE}.txt"
TEC_TRACE  = f"/dev/shm/test_sim_tectonic_{SCALE}.txt"

def main():
    banner("INVESTIGATION", "RocksDB Run Order Reversal & Deep-Dive Test")
    log(f"Scale: 2^18 = {SCALE:,} operations")

    # 1. Generate traces in RAM tmpfs
    if not validate_trace(YCSB_TRACE, SCALE):
        generate_ycsb(SCALE, YCSB_TRACE)
    if not validate_trace(TEC_TRACE, SCALE):
        generate_tectonic(SCALE, TEC_TRACE)

    results = {}

    def run_one(workload_name, trace_path):
        restart_db("rocksdb")
        lat, wt, tp, op_m, overall, ts, io_tot = execute_with_dual_io(trace_path, "rocksdb")
        return {
            "wall_time_s": wt,
            "throughput": tp,
            "overall_time_s": overall.get("End to End Time", wt),
            "op_metrics": op_m,
            "latencies": lat,
            "io_totals": io_tot
        }

    # =========================================================================
    # Trial 1: Standard Order (YCSB first, then X-Bench)
    # =========================================================================
    banner("TRIAL 1", "Standard Order: YCSB first -> X-Bench second")
    log("\n[Trial 1.1] Executing YCSB...")
    t1_ycsb = run_one("ycsb", YCSB_TRACE)
    log(f"  Trial 1 YCSB wall time: {t1_ycsb['wall_time_s']:.3f}s")

    log("\n[Trial 1.2] Executing X-Bench...")
    t1_tec = run_one("tectonic", TEC_TRACE)
    log(f"  Trial 1 X-Bench wall time: {t1_tec['wall_time_s']:.3f}s")

    t1_diff = abs(t1_tec['wall_time_s'] - t1_ycsb['wall_time_s']) / t1_ycsb['wall_time_s'] * 100.0
    log(f"  Trial 1 Diff: {t1_diff:.2f}%\n")

    # =========================================================================
    # Trial 2: Reversed Order (X-Bench first, then YCSB second)
    # =========================================================================
    banner("TRIAL 2", "Reversed Order: X-Bench first -> YCSB second")
    log("\n[Trial 2.1] Executing X-Bench...")
    t2_tec = run_one("tectonic", TEC_TRACE)
    log(f"  Trial 2 X-Bench wall time: {t2_tec['wall_time_s']:.3f}s")

    log("\n[Trial 2.2] Executing YCSB...")
    t2_ycsb = run_one("ycsb", YCSB_TRACE)
    log(f"  Trial 2 YCSB wall time: {t2_ycsb['wall_time_s']:.3f}s")

    t2_diff = abs(t2_tec['wall_time_s'] - t2_ycsb['wall_time_s']) / t2_ycsb['wall_time_s'] * 100.0
    log(f"  Trial 2 Diff: {t2_diff:.2f}%\n")

    # Cleanup database
    teardown_db("rocksdb")

    # Cleanup test traces
    for p in [YCSB_TRACE, TEC_TRACE]:
        if os.path.exists(p):
            os.remove(p)

    banner("SUMMARY", "Comparison of Standard vs. Reversed Order")
    print(f"{'Metric':<30} | {'Trial 1 (YCSB first)':<25} | {'Trial 2 (X-Bench first)':<25}")
    print("-" * 86)
    print(f"{'YCSB Wall Time (s)':<30} | {t1_ycsb['wall_time_s']:<25.3f} | {t2_ycsb['wall_time_s']:<25.3f}")
    print(f"{'X-Bench Wall Time (s)':<30} | {t1_tec['wall_time_s']:<25.3f} | {t2_tec['wall_time_s']:<25.3f}")
    print(f"{'Execution Time Diff (%)':<30} | {t1_diff:<25.2f}% | {t2_diff:<25.2f}%")
    print("-" * 86)

    # Per-operation breakdown for Trial 1 vs Trial 2
    for op, op_key in [("INSERT", "insert"), ("READ", "point_query"), ("UPDATE", "update")]:
        print(f"\n--- {op} (Avg Latency in us) ---")
        y1_avg = t1_ycsb["op_metrics"].get(op_key, {}).get("average", 0.0)
        t1_avg = t1_tec["op_metrics"].get(op_key, {}).get("average", 0.0)
        y2_avg = t2_ycsb["op_metrics"].get(op_key, {}).get("average", 0.0)
        t2_avg = t2_tec["op_metrics"].get(op_key, {}).get("average", 0.0)
        d1 = abs(t1_avg - y1_avg) / y1_avg * 100.0 if y1_avg > 0 else 0.0
        d2 = abs(t2_avg - y2_avg) / y2_avg * 100.0 if y2_avg > 0 else 0.0
        print(f"Trial 1 (Standard): YCSB = {y1_avg:6.2f} us, X-Bench = {t1_avg:6.2f} us  (diff = {d1:5.2f}%)")
        print(f"Trial 2 (Reversed): YCSB = {y2_avg:6.2f} us, X-Bench = {t2_avg:6.2f} us  (diff = {d2:5.2f}%)")

if __name__ == "__main__":
    main()
