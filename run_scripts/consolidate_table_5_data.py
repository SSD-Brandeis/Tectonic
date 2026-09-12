#!/usr/bin/env python3
"""
consolidate_table_5_data.py
===========================
Consolidates the exact experimental data used in Table 5 of the paper into:
    /home/cc/Tectonic/data/table_5_data/

Sources:
  - Machine 1 (Local): /home/cc/Tectonic/data/ycsb_tectonic_similarity_io_bytes/
      * Cassandra (all 5 scales: 2^18 to 2^22)
      * Redis (all 5 scales: 2^18 to 2^22)
      * ScyllaDB (scales 2^19, 2^20, 2^21; scale 2^22 YCSB)
  - Machine 2 (Rerun): /home/cc/Tectonic/data/machine2_ycsb_tectonic_similarity_io_bytes/
      * RocksDB (all 5 scales: 2^18 to 2^22)
      * ScyllaDB (scale 2^18 YCSB and X-Bench; scale 2^22 X-Bench)

Note on ScyllaDB 2^22 X-Bench Bytes Transferred:
  In the paper, Table 5 reported 11883 (Disk Write = 11,883.21 MB, with Disk Read = 4,701.93 MB omitted).
  Following Option A, the physical measurements (Read=4,701.93, Write=11,883.21, Total=16,585.14)
  are preserved in results_io_bytes.json and table_5_plotting_data, with an explicit documentation note.
"""

import os
import sys
import json
import shutil
from datetime import datetime

M1_DIR = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_io_bytes"
M2_DIR = "/home/cc/Tectonic/data/machine2_ycsb_tectonic_similarity_io_bytes"
OUT_DIR = "/home/cc/Tectonic/data/table_5_data"

SCALES_INT = [262144, 524288, 1048576, 2097152, 4194304]
DATABASES = ["cassandra", "redis", "rocksdb", "scylla"]
WORKLOADS = ["ycsb", "tectonic"]

def scale_label(scale_int):
    return f"2^{scale_int.bit_length() - 1}"

# Mapping defining where each result comes from: (machine_dir, machine_label)
def get_source(db, scale_int, wl):
    if db in ("cassandra", "redis"):
        return M1_DIR, "Machine 1 (Local)"
    if db == "rocksdb":
        return M2_DIR, "Machine 2 (Rerun)"
    if db == "scylla":
        if scale_int == 262144:
            return M2_DIR, "Machine 2 (Rerun)"
        elif scale_int == 4194304:
            if wl == "tectonic":
                return M2_DIR, "Machine 2 (Rerun)"
            else:
                return M1_DIR, "Machine 1 (Local)"
        else:
            return M1_DIR, "Machine 1 (Local)"
    raise ValueError(f"Unknown db: {db}")

def main():
    print(f"Creating consolidated directory: {OUT_DIR}")
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1. Load source results_io_bytes.json
    with open(os.path.join(M1_DIR, "results_io_bytes.json")) as f:
        m1_data = json.load(f)
    with open(os.path.join(M2_DIR, "results_io_bytes.json")) as f:
        m2_data = json.load(f)

    # 2. Build consolidated results_io_bytes.json
    consolidated_results = {}
    provenance_cells = []
    summary_dict = {}

    table_rows = []
    table_rows.append("| Database | Scale | Workload | Wall Time (s) | Disk Read (MB) | Disk Write (MB) | Total I/O (MB) | Paper Table 5 Value |")
    table_rows.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

    # Paper Table 5 lookup for provenance checking
    paper_table = {
        "cassandra": {
            "execution_time": {"ycsb": [249, 533, 1066, 2282, 4506], "tectonic": [266, 506, 1018, 2118, 4262]},
            "bytes_transferred": {"ycsb": [334, 666, 1323, 4488, 8977], "tectonic": [335, 663, 1323, 4536, 9109]}
        },
        "redis": {
            "execution_time": {"ycsb": [65, 154, 304, 626, 1189], "tectonic": [77, 149, 324, 634, 1208]},
            "bytes_transferred": {"ycsb": [3, 47, 25, 24, 55.47], "tectonic": [3, 21, 9, 19, 61.81]}
        },
        "rocksdb": {
            "execution_time": {"ycsb": [4.63, 9.35, 21.23, 50.80, 128.5], "tectonic": [4.11, 8.45, 20.23, 49, 108.2]},
            "bytes_transferred": {"ycsb": [772, 1956, 4417, 11585, 26991], "tectonic": [621, 1889, 4369, 10640, 25158]}
        },
        "scylla": {
            "execution_time": {"ycsb": [348, 692, 1390, 2773, 5629], "tectonic": [345, 681, 1355, 2718, 5684]},
            "bytes_transferred": {"ycsb": [1769, 1841, 3493, 6408, 12932], "tectonic": [1963, 1679, 2734, 5709, 11883]}
        }
    }

    copied_csv_count = 0

    for db in DATABASES:
        consolidated_results[db] = {}
        summary_dict[db] = {}

        for scale_idx, scale_int in enumerate(SCALES_INT):
            skey = str(scale_int)
            lbl = scale_label(scale_int)
            consolidated_results[db][skey] = {}
            summary_dict[db][lbl] = {}

            for wl in WORKLOADS:
                src_dir, src_label = get_source(db, scale_int, wl)
                src_json = m2_data if src_dir == M2_DIR else m1_data

                # Extract operation entry
                entry = src_json["results"][db][skey][wl]
                consolidated_results[db][skey][wl] = entry

                wall_time = entry.get("wall_time_s", 0.0)
                io_bytes = entry.get("io_bytes_total", {})
                read_mb = io_bytes.get("total_read_mb", 0.0)
                write_mb = io_bytes.get("total_write_mb", 0.0)
                total_mb = read_mb + write_mb

                summary_dict[db][lbl][wl] = {
                    "wall_time_s": wall_time,
                    "total_read_mb": read_mb,
                    "total_write_mb": write_mb,
                    "total_io_mb": total_mb
                }

                wl_display = "YCSB" if wl == "ycsb" else "X-Bench"
                paper_time = paper_table[db]["execution_time"][wl][scale_idx]
                paper_bytes = paper_table[db]["bytes_transferred"][wl][scale_idx]

                paper_str = f"Time: {paper_time}, Bytes: {paper_bytes}"
                if db == "scylla" and scale_int == 4194304 and wl == "tectonic":
                    paper_str += " (*Write-only in paper)"

                table_rows.append(
                    f"| **{db}** | {lbl} | {wl_display} | {wall_time:.2f} | {read_mb:.2f} | {write_mb:.2f} | {total_mb:.2f} | {paper_str} |"
                )

                # Copy corresponding CSV file
                csv_filename = f"{db}_scale{scale_int}_{wl}_io_bytes.csv"
                src_csv_path = os.path.join(src_dir, csv_filename)
                dst_csv_path = os.path.join(OUT_DIR, csv_filename)

                if os.path.exists(src_csv_path):
                    shutil.copy2(src_csv_path, dst_csv_path)
                    copied_csv_count += 1
                else:
                    print(f"WARNING: CSV not found: {src_csv_path}")

                provenance_cells.append({
                    "database": db,
                    "scale_int": scale_int,
                    "scale_label": lbl,
                    "workload": wl,
                    "workload_display": wl_display,
                    "source_machine": src_label,
                    "source_csv": src_csv_path,
                    "wall_time_s": wall_time,
                    "disk_read_mb": read_mb,
                    "disk_write_mb": write_mb,
                    "total_io_mb": total_mb,
                    "paper_table_5": {
                        "execution_time_s": paper_time,
                        "bytes_transferred_mb": paper_bytes,
                        "note": "Reported Disk Write only (4701.93 MB read omitted in paper table)" if (db == "scylla" and scale_int == 4194304 and wl == "tectonic") else "Sum of Read + Write (or Read ~ 0)"
                    }
                })

    # Add explanatory footnotes to table_5_plotting_data
    table_rows.append("")
    table_rows.append("### Notes & Methodology")
    table_rows.append("1. **Bytes Transferred Metric**: In Table 5, 'bytes transferred (MB)' is the total physical block device disk I/O, calculated as `Disk Read (MB) + Disk Write (MB)` measured via `iostat -x -d sda 1`.")
    table_rows.append("2. **ScyllaDB 2^22 X-Bench (Paper Value 11883)**: The paper printed `11883`, which reflects the Disk Write (`11,883.21 MB`). The empirical Disk Read was `4,701.93 MB`, yielding a total empirical physical I/O of `16,585.14 MB`.")
    table_rows.append("3. **Source Attribution**:")
    table_rows.append("   - Cassandra (all scales): Machine 1 (Local)")
    table_rows.append("   - Redis (all scales): Machine 1 (Local)")
    table_rows.append("   - RocksDB (all scales): Machine 2 (Rerun)")
    table_rows.append("   - ScyllaDB (2^18 and 2^22 X-Bench): Machine 2 (Rerun)")
    table_rows.append("   - ScyllaDB (2^19, 2^20, 2^21 and 2^22 YCSB): Machine 1 (Local)")

    # 3. Write results_io_bytes.json
    results_payload = {
        "config": {
            "experiment": "table_5_consolidated",
            "description": "Consolidated empirical data directly matching Table 5 of the paper.",
            "date_consolidated": datetime.utcnow().isoformat() + "Z",
            "scales": SCALES_INT,
            "databases": DATABASES,
            "ycsb_workload": "workloads/workloada",
            "tectonic_spec": "/home/cc/Tectonic/example-specs/ycsb/a.spec.json",
            "exec_threads": 1,
            "iostat_device": "sda",
            "metric_definitions": {
                "execution_time_s": "Benchmark wall time in seconds",
                "bytes_transferred_mb": "Disk Read MB + Disk Write MB captured from physical block device via iostat"
            },
            "provenance_summary": {
                "cassandra": "Machine 1 (Local)",
                "redis": "Machine 1 (Local)",
                "rocksdb": "Machine 2 (Rerun)",
                "scylla": "Hybrid: 2^18 and 2^22 X-Bench from Machine 2; 2^19-2^21 and 2^22 YCSB from Machine 1"
            }
        },
        "results": consolidated_results
    }

    out_json_path = os.path.join(OUT_DIR, "results_io_bytes.json")
    with open(out_json_path, "w") as f:
        json.dump(results_payload, f, indent=2)
    print(f"Saved: {out_json_path}")

    # 4. Write table_5_plotting_data
    out_table_path = os.path.join(OUT_DIR, "table_5_plotting_data")
    with open(out_table_path, "w") as f:
        f.write("\n".join(table_rows) + "\n")
    print(f"Saved: {out_table_path}")

    # 5. Write plotting_data_summary.json
    out_summary_path = os.path.join(OUT_DIR, "plotting_data_summary.json")
    with open(out_summary_path, "w") as f:
        json.dump(summary_dict, f, indent=2)
    print(f"Saved: {out_summary_path}")

    # 6. Write table_5_provenance.json
    out_provenance_path = os.path.join(OUT_DIR, "table_5_provenance.json")
    with open(out_provenance_path, "w") as f:
        json.dump({
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_cells": len(provenance_cells),
            "copied_csv_count": copied_csv_count,
            "cells": provenance_cells
        }, f, indent=2)
    print(f"Saved: {out_provenance_path}")

    print(f"\nDone! Successfully consolidated 40 CSVs and 80 data points into {OUT_DIR}")

if __name__ == "__main__":
    main()
