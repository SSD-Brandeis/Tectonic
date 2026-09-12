#!/usr/bin/env python3
import os
import json

ROOT_DIR = "/home/cc/Tectonic"
OUT_DIR = f"{ROOT_DIR}/data/table5_rerun"
RESULTS_PATH = f"{OUT_DIR}/results_io_bytes.json"
TABLE_OUT = f"{OUT_DIR}/table_5_db_internal_unambiguous.md"

def format_aligned_markdown_table(headers, alignments, rows):
    all_rows = [headers] + rows
    col_widths = [max(len(str(r[i])) for r in all_rows) for i in range(len(headers))]
    col_widths = [max(w, len(alignments[i])) for i, w in enumerate(col_widths)]

    lines = []
    h_cells = [headers[i].ljust(col_widths[i]) for i in range(len(headers))]
    lines.append("| " + " | ".join(h_cells) + " |")

    sep_cells = []
    for i, a in enumerate(alignments):
        w = col_widths[i]
        if a == ":---:":
            sep_cells.append(":" + "-" * (w - 2) + ":")
        elif a == "---:":
            sep_cells.append("-" * (w - 1) + ":")
        else:
            sep_cells.append(":" + "-" * (w - 1))
    lines.append("| " + " | ".join(sep_cells) + " |")

    for r in rows:
        r_cells = []
        for i, val in enumerate(r):
            w = col_widths[i]
            a = alignments[i]
            sval = str(val)
            if a == ":---:":
                r_cells.append(sval.center(w))
            elif a == "---:":
                r_cells.append(sval.rjust(w))
            else:
                r_cells.append(sval.ljust(w))
        lines.append("| " + " | ".join(r_cells) + " |")

    return "\n".join(lines) + "\n"

def main():
    if not os.path.exists(RESULTS_PATH):
        print(f"Results file not found: {RESULTS_PATH}")
        return

    data = json.load(open(RESULTS_PATH))
    results = data.get("results", {})

    output_lines = []
    output_lines.append("# Updated Table 5: Direct Database Log Empirical Metrics\n")
    output_lines.append("This table presents the empirical comparison between **YCSB** and **X-Bench** using metrics measured **directly from the native database engine logs and management telemetry**, completely bypassing operating system page-cache and filesystem buffering artifacts.\n")

    headers_t5 = [
        "Database", "Scale", "Workload",
        "Execution Time (s)",
        "Database Log Metric Description", "Database Log Measured (MB)",
        "Execution Time Diff (%)", "Log Bytes Diff (%)"
    ]
    align_t5 = [
        ":---", ":---:", ":---:",
        "---:",
        ":---", "---:",
        ":---:", ":---:"
    ]
    rows_t5 = []

    dbs = sorted(list(results.keys()))

    for db in dbs:
        scales = sorted([int(s) for s in results[db].keys()])
        for scale in scales:
            lbl = f"2^{scale.bit_length() - 1}"
            sdata = results[db][str(scale)]
            if "ycsb" not in sdata or "tectonic" not in sdata:
                continue

            y = sdata["ycsb"]
            t = sdata["tectonic"]

            # 1. Execution time
            y_et = y.get("overall_metrics", {}).get("end_to_end_time_s", y.get("wall_time_s", 0.0))
            t_et = t.get("overall_metrics", {}).get("end_to_end_time_s", t.get("wall_time_s", 0.0))
            et_diff = abs(t_et - y_et) / y_et * 100.0 if y_et > 0 else 0.0

            # 2. Operations & metrics
            y_im = y.get("db_internal_metrics", {})
            t_im = t.get("db_internal_metrics", {})

            metric_desc = ""
            y_bytes = 0.0
            t_bytes = 0.0

            if db == "rocksdb":
                metric_desc = "Compaction Writes + Flush + WAL"
                y_bytes = y_im.get("compaction_write_mb", 0.0) + y_im.get("wal_written_mb", 0.0) + y_im.get("flush_cumulative_mb", 0.0)
                t_bytes = t_im.get("compaction_write_mb", 0.0) + t_im.get("wal_written_mb", 0.0) + t_im.get("flush_cumulative_mb", 0.0)

            elif db == "redis":
                metric_desc = "Network Ingress (Command Stream Payload)"
                y_bytes = y_im.get("total_net_input_bytes_mb", 0.0)
                t_bytes = t_im.get("total_net_input_bytes_mb", 0.0)

            elif db == "cassandra":
                metric_desc = "Memtable Live Data Size in RAM"
                y_bytes = y_im.get("memtable_data_size_mb", 0.0)
                t_bytes = t_im.get("memtable_data_size_mb", 0.0)

            elif db == "scylla":
                metric_desc = "CommitLog Bytes Written (Across All Shards)"
                y_bytes = y_im.get("scylla_commitlog_bytes_mb", 0.0)
                t_bytes = t_im.get("scylla_commitlog_bytes_mb", 0.0)
                if y_bytes == 0.0 or t_bytes == 0.0:
                    for lf in y.get("db_log_files", []):
                        sub = os.path.join(OUT_DIR, "scylla", "YCSB", os.path.basename(lf))
                        p = sub if os.path.exists(sub) else lf
                        if p.endswith(".prom") and os.path.exists(p):
                            y_bytes = round(sum(float(l.split()[-1]) for l in open(p) if l.startswith("scylla_commitlog_bytes_written{")) / (1024*1024), 2)
                    for lf in t.get("db_log_files", []):
                        sub = os.path.join(OUT_DIR, "scylla", "X-Bench", os.path.basename(lf))
                        p = sub if os.path.exists(sub) else lf
                        if p.endswith(".prom") and os.path.exists(p):
                            t_bytes = round(sum(float(l.split()[-1]) for l in open(p) if l.startswith("scylla_commitlog_bytes_written{")) / (1024*1024), 2)

            bytes_diff = abs(t_bytes - y_bytes) / y_bytes * 100.0 if y_bytes > 0 else 0.0

            rows_t5.append([
                f"**{db}**", lbl, "YCSB",
                f"{y_et:.2f}",
                metric_desc, f"{y_bytes:.2f} MB",
                "—", "—"
            ])
            rows_t5.append([
                f"**{db}**", lbl, "X-Bench",
                f"{t_et:.2f}",
                metric_desc, f"{t_bytes:.2f} MB",
                f"**{et_diff:.2f}%**", f"**{bytes_diff:.2f}%**"
            ])

    output_lines.append(format_aligned_markdown_table(headers_t5, align_t5, rows_t5))

    output_lines.append("\n## Database Log Metric Specification & Calculation Methodology\n")
    output_lines.append("""For every database engine, the metrics above are extracted **directly from native database logs and administrative telemetry APIs**:

### 1. RocksDB (`rocksdb`)
- **Primary Source**: Physical RocksDB engine log (`LOG`) and `rocksdb.stats` generated directly by the RocksDB C++ engine.
- **Measured Metric**: **Compaction Writes + Flush + WAL**.
  - `compaction_write_mb`: Cumulative data written during background SSTable compactions (`Cumulative compaction: ... write GB`).
  - `flush_cumulative_mb`: Cumulative data flushed from in-memory MemTables to L0 SSTables (`Cumulative flush: ... write GB`).
  - `wal_written_mb`: Cumulative data written to the physical Write-Ahead Log (`Cumulative WAL: ... write GB`).
- **Formula**: `compaction_write_mb + flush_cumulative_mb + wal_written_mb`.

### 2. Redis (`redis`)
- **Primary Source**: Redis official management telemetry via `redis-cli info all`.
- **Measured Metric**: **Network Ingress Payload (`total_net_input_bytes`)**.
  - Redis runs as an in-memory database with persistence disabled (`--appendonly no --save ""`). Therefore, disk writes are 0 MB by definition.
  - The true empirical workload byte transfer is the exact byte stream of `SET` and `GET` requests transferred over the network socket from the benchmark driver into the Redis engine (`total_net_input_bytes` in `INFO stats`).
- **Formula**: `total_net_input_bytes / (1024 * 1024)`.

### 3. Cassandra (`cassandra`)
- **Primary Source**: Cassandra JMX management MBeans via `nodetool tablestats tectonic.data`.
- **Measured Metric**: **In-Memory Memtable Live Data Size (`Memtable data size`)**.
  - In Cassandra, newly inserted and updated records accumulate in an in-memory **Memtable** while mutations are appended to the commit log on disk.
  - Cassandra only creates and flushes on-disk SSTables when the Memtable exceeds its flush threshold (typically ~1–2 GB).
  - At scales $2^{18}$ and $2^{19}$ (~232 MB and ~462 MB), 100% of data resides in the Memtable. When point queries (`get`) are executed, Cassandra serves them directly from RAM (`Local read count: 131,072` at $2^{18}$).
  - `Memtable data size` reports the exact physical byte footprint of mutations accumulated in active memory before SSTable flush.
- **Formula**: `Memtable data size / (1024 * 1024)`.

### 4. ScyllaDB (`scylla`)
- **Primary Source**: ScyllaDB native Prometheus metrics endpoint (`http://127.0.0.1:9180/metrics`).
- **Measured Metric**: **CommitLog Bytes Written (`scylla_commitlog_bytes_written`)**.
  - ScyllaDB operates a thread-per-core Seastar architecture with 48 reactor shards. Every write and update operation is immediately appended to the on-disk commit log across all shards.
  - ScyllaDB reports the exact count of bytes written to the commit log per shard: `scylla_commitlog_bytes_written{shard="0..47"}`.
- **Formula**: `sum(scylla_commitlog_bytes_written for all shards) / (1024 * 1024)`.
""")

    content = "\n".join(output_lines)
    with open(TABLE_OUT, "w") as f:
        f.write(content)

    print(f"Successfully generated updated Table 5: {TABLE_OUT}")

if __name__ == "__main__":
    main()
