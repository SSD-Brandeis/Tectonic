#!/usr/bin/env python3
"""
Exports complete raw per-trial CSV files from the raw JSON records and log references.
"""

import csv
import json
from pathlib import Path

ROOT = Path("/home/cc/Tectonic")
RESULTS_DIR = ROOT / "data" / "revision" / "R2.O2_ycsb-bounded-memory" / "results"

# 1. YCSB raw trials (480 rows)
with open(RESULTS_DIR / "ycsb_bounded_memory.json") as f:
    ycsb_data = json.load(f)

ycsb_rows = []
for w, w_data in ycsb_data.items():
    for heap, h_data in w_data.items():
        for tr in h_data.get("load_trials", []):
            t_num = tr["trial"]
            log_name = f"logs/ycsb/{w}_{heap}_trial{t_num}_load.log"
            tp = tr.get("throughput_ops_sec")
            ycsb_rows.append({
                "Workload": w.upper(),
                "Phase": "Load",
                "HeapBound": heap,
                "Trial": t_num,
                "Duration_s": round(tr["duration_s"], 4),
                "PeakRSS_MB": round(tr["peak_rss_mb"], 2),
                "Throughput_ops_sec": round(tp, 2) if tp is not None else "",
                "GCCount": tr.get("gc_count", 0),
                "GCTime_ms": round(tr.get("gc_time_ms", 0.0), 2),
                "GCTime_pct": round(tr.get("gc_time_pct", 0.0), 2),
                "LogFile": log_name,
            })
        for tr in h_data.get("run_trials", []):
            t_num = tr["trial"]
            log_name = f"logs/ycsb/{w}_{heap}_trial{t_num}_run.log"
            tp = tr.get("throughput_ops_sec")
            ycsb_rows.append({
                "Workload": w.upper(),
                "Phase": "Run",
                "HeapBound": heap,
                "Trial": t_num,
                "Duration_s": round(tr["duration_s"], 4),
                "PeakRSS_MB": round(tr["peak_rss_mb"], 2),
                "Throughput_ops_sec": round(tp, 2) if tp is not None else "",
                "GCCount": tr.get("gc_count", 0),
                "GCTime_ms": round(tr.get("gc_time_ms", 0.0), 2),
                "GCTime_pct": round(tr.get("gc_time_pct", 0.0), 2),
                "LogFile": log_name,
            })

with open(RESULTS_DIR / "ycsb_raw_trials.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(ycsb_rows[0].keys()))
    writer.writeheader()
    writer.writerows(ycsb_rows)
print(f"Exported {len(ycsb_rows)} raw YCSB trial rows to ycsb_raw_trials.csv")

# 2. X-Bench raw trials (30 rows)
with open(RESULTS_DIR / "xbench_baseline.json") as f:
    xb_data = json.load(f)

xb_rows = []
for w, w_data in xb_data.items():
    for tr in w_data.get("trials", []):
        t_num = tr["trial"]
        log_name = f"logs/xbench/{w}_trial{t_num}.log"
        xb_rows.append({
            "Workload": w.upper(),
            "Trial": t_num,
            "Duration_s": round(tr["duration_s"], 4),
            "PeakRSS_MB": round(tr["peak_rss_mb"], 2),
            "OutputSize_MB": round(tr.get("output_size_mb", 0.0), 2),
            "ExitCode": tr.get("exit_code", 0),
            "LogFile": log_name,
        })

with open(RESULTS_DIR / "xbench_raw_trials.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(xb_rows[0].keys()))
    writer.writeheader()
    writer.writerows(xb_rows)
print(f"Exported {len(xb_rows)} raw X-Bench trial rows to xbench_raw_trials.csv")

# 3. Base JVM raw trials (15 rows)
with open(RESULTS_DIR / "jvm_base_footprint.json") as f:
    base_data = json.load(f)

base_rows = []
for name, b_data in base_data.items():
    for tr in b_data.get("trials", []):
        t_num = tr["trial"]
        log_name = f"logs/base_jvm/{name}_trial{t_num}.log"
        base_rows.append({
            "Benchmark": name,
            "Trial": t_num,
            "Duration_s": round(tr["duration_s"], 4),
            "PeakRSS_MB": round(tr["peak_rss_mb"], 2),
            "ExitCode": tr.get("exit_code", 0),
            "LogFile": log_name,
        })

with open(RESULTS_DIR / "jvm_base_raw_trials.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(base_rows[0].keys()))
    writer.writeheader()
    writer.writerows(base_rows)
print(f"Exported {len(base_rows)} raw Base JVM trial rows to jvm_base_raw_trials.csv")
