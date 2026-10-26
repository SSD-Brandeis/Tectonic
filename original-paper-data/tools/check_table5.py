#!/usr/bin/env python3
"""
Compares Table 5 of the paper (read from the PDF's text) with the consolidated data
(data/table_5_data/plotting_data_summary.json, written by run_scripts/consolidate_table_5_data.py).
Execution time = wall_time_s; bytes transferred = total_read_mb + total_write_mb.
A cell matches if the paper's number equals the data rounded to the paper's number of decimals.
Writes original-paper-data/reproduced/table5/table5_check.md.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path("/home/cc/Tectonic")
PDF = ROOT / "original-paper-data" / "X_Bench (5).pdf"
SUMMARY = ROOT / "data" / "table_5_data" / "plotting_data_summary.json"
OUT = ROOT / "original-paper-data" / "reproduced" / "table5" / "table5_check.md"
DBS = ["cassandra", "redis", "rocksdb", "scylla"]
NAMES = {"cassandra": "Cassandra", "redis": "Redis", "rocksdb": "RocksDB", "scylla": "ScyllaDB"}
SCALES = ["2^18", "2^19", "2^20", "2^21", "2^22"]
TOOLS = {"YCSB": "ycsb", "X-Bench": "tectonic"}


def paper_rows():
    text = subprocess.run(["pdftotext", "-f", "16", "-l", "16", "-raw", str(PDF), "-"],
                          capture_output=True, text=True, check=True).stdout
    rows = re.findall(r"^(YCSB|X-Bench) ([0-9.,]+(?: [0-9.,]+){4})$", text, re.M)
    assert len(rows) == 16, rows
    out, i = [], 0
    for db in DBS:
        for metric in ["time", "bytes"]:
            for _ in range(2):
                tool, nums = rows[i]
                out.append((db, metric, tool, nums.replace(",", "").split()))
                i += 1
    return out


def main():
    data = json.load(open(SUMMARY))
    lines = ["# Table 5: paper vs. data", "",
             "Paper: `X_Bench (5).pdf`, page 16. Data: `data/table_5_data/plotting_data_summary.json` "
             "(`run_scripts/consolidate_table_5_data.py`). Time = wall_time_s; bytes = read + write (MB).", "",
             "| DB | metric | tool | " + " | ".join(SCALES) + " |", "| --- | --- | --- |" + " --- |" * 5]
    mismatches = []
    for db, metric, tool, nums in paper_rows():
        cells = []
        for scale, p in zip(SCALES, nums):
            r = data[db][scale][TOOLS[tool]]
            v = r["wall_time_s"] if metric == "time" else r["total_read_mb"] + r["total_write_mb"]
            decimals = len(p.split(".")[1]) if "." in p else 0
            ok = f"{v:.{decimals}f}" == p
            cells.append(p if ok else f"**{p}** (data {v:.2f})")
            if not ok:
                mismatches.append((NAMES[db], metric, tool, scale, p, v, r))
        lines.append(f"| {NAMES[db]} | {metric} | {tool} | " + " | ".join(cells) + " |")
    lines += ["", f"{80 - len(mismatches)} of 80 cells match; mismatches in bold.", ""]
    for name, metric, tool, scale, p, v, r in mismatches:
        lines.append(f"- {name} {tool} {scale} {metric}: paper {p}, data {v:.4f} "
                     f"(read {r['total_read_mb']}, write {r['total_write_mb']}, time {r['wall_time_s']:.4f})")
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
