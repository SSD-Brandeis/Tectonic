#!/usr/bin/env python3
import os
import re
import json

ROOT_DIR = "/home/cc/Tectonic"
OUT_DIR = f"{ROOT_DIR}/data/table5_rerun"
RESULTS_PATH = f"{OUT_DIR}/results_io_bytes.json"
CLI_TABLE_OUT = f"{OUT_DIR}/table_5_cli_execution_time.md"
SERVER_TABLE_OUT = f"{OUT_DIR}/table_5_server_execution_time.md"

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

def get_server_stats(db, scale, workload, results_data):
    wl_folder = "X-Bench" if workload in ["tectonic", "X-Bench"] else "YCSB"
    wl_code = "tectonic" if workload in ["tectonic", "X-Bench"] else "ycsb"
    prefix = f"{OUT_DIR}/{db}/{wl_folder}/{db}_scale{scale}_{wl_code}"
    
    server_time = 0.0
    time_desc = ""
    bytes_mb = 0.0
    bytes_desc = ""

    if db == "rocksdb":
        time_desc = "Engine Process Uptime"
        bytes_desc = "Compaction Writes + Flush + WAL"
        sp = f"{prefix}_rocksdb_stats.txt"
        if os.path.exists(sp):
            txt = open(sp).read()
            m = re.search(r'Uptime\(secs\):\s*([\d\.]+)', txt)
            if m:
                server_time = float(m.group(1))
        sdata = results_data.get(db, {}).get(str(scale), {}).get(wl_code, {}).get("db_internal_metrics", {})
        bytes_mb = sdata.get("compaction_write_mb", 0.0) + sdata.get("wal_written_mb", 0.0) + sdata.get("flush_cumulative_mb", 0.0)

    elif db == "redis":
        time_desc = "Engine Command Processing Time"
        bytes_desc = "Network Ingress (Command Stream Payload)"
        ip = f"{prefix}_redis_info.txt"
        if os.path.exists(ip):
            txt = open(ip).read()
            gm = re.search(r'cmdstat_get:calls=\d+,usec=(\d+)', txt)
            sm = re.search(r'cmdstat_set:calls=\d+,usec=(\d+)', txt)
            gus = int(gm.group(1)) if gm else 0
            sus = int(sm.group(1)) if sm else 0
            server_time = (gus + sus) / 1e6
        sdata = results_data.get(db, {}).get(str(scale), {}).get(wl_code, {}).get("db_internal_metrics", {})
        bytes_mb = sdata.get("total_net_input_bytes_mb", 0.0)

    elif db == "cassandra":
        time_desc = "Cumulative Query Execution Latency"
        bytes_desc = "Memtable Live Data Size in RAM"
        tp = f"{prefix}_cassandra_tablestats.txt"
        if os.path.exists(tp):
            txt = open(tp).read()
            rc = float(re.search(r'Read Count:\s*(\d+)', txt).group(1))
            rl = float(re.search(r'Read Latency:\s*([\d\.\w\-]+)', txt).group(1))
            wc = float(re.search(r'Write Count:\s*(\d+)', txt).group(1))
            wl = float(re.search(r'Write Latency:\s*([\d\.\w\-]+)', txt).group(1))
            server_time = (rc * rl + wc * wl) / 1000.0
            msize = float(re.search(r'Memtable data size:\s*(\d+)', txt).group(1))
            bytes_mb = round(msize / (1024 * 1024), 2)

    elif db == "scylla":
        time_desc = "Reactor Core Query Latency Sum"
        bytes_desc = "CommitLog Bytes Written (Across All Shards)"
        pp = f"{prefix}_scylla_metrics.prom"
        if os.path.exists(pp):
            txt = open(pp).read()
            rm = re.search(r'scylla_column_family_read_latency_sum\{cf=\"data\",ks=\"tectonic\"\}\s*([\d\.\w\+\-]+)', txt)
            wm = re.search(r'scylla_column_family_write_latency_sum\{cf=\"data\",ks=\"tectonic\"\}\s*([\d\.\w\+\-]+)', txt)
            if rm and wm:
                server_time = (float(rm.group(1)) + float(wm.group(1))) / 1e6
            bytes_mb = round(sum(float(l.split()[-1]) for l in txt.splitlines() if l.startswith('scylla_commitlog_bytes_written{')) / (1024 * 1024), 2)

    return server_time, time_desc, bytes_mb, bytes_desc

def main():
    if not os.path.exists(RESULTS_PATH):
        print(f"Results file not found: {RESULTS_PATH}")
        return

    data = json.load(open(RESULTS_PATH))
    results = data.get("results", {})

    dbs = sorted(list(results.keys()))

    # =========================================================================
    # TABLE 1: CLI Execution Time
    # =========================================================================
    cli_lines = []
    cli_lines.append("# Table 5: Client Driver (CLI) End-to-End Execution Time\n")
    cli_lines.append("This table presents the comparison between **YCSB** and **X-Bench** using **client-perceived end-to-end wall-clock execution time** measured by `tectonic-cli execute`, alongside direct database log data transfer metrics.\n")

    cli_headers = [
        "Database", "Scale", "Workload",
        "CLI Execution Time (s)",
        "Database Log Metric Description", "Database Log Measured (MB)",
        "CLI Time Diff (%)", "Log Bytes Diff (%)"
    ]
    cli_align = [
        ":---", ":---:", ":---:",
        "---:",
        ":---", "---:",
        ":---:", ":---:"
    ]
    cli_rows = []

    # =========================================================================
    # TABLE 2: Database Server Execution Time
    # =========================================================================
    srv_lines = []
    srv_lines.append("# Table 5: Database Server Internal Execution Time\n")
    srv_lines.append("This table presents the comparison between **YCSB** and **X-Bench** using **database server execution time recorded directly in the database engine logs and management telemetry**, isolating database compute from client network roundtrips.\n")

    srv_headers = [
        "Database", "Scale", "Workload",
        "Server Execution Time (s)", "Server Time Metric Description",
        "Database Log Metric Description", "Database Log Measured (MB)",
        "Server Time Diff (%)", "Log Bytes Diff (%)"
    ]
    srv_align = [
        ":---", ":---:", ":---:",
        "---:", ":---",
        ":---", "---:",
        ":---:", ":---:"
    ]
    srv_rows = []

    for db in dbs:
        scales = sorted([int(s) for s in results[db].keys()])
        for scale in scales:
            lbl = f"2^{scale.bit_length() - 1}"
            sdata = results[db][str(scale)]
            if "ycsb" not in sdata or "tectonic" not in sdata:
                continue

            y = sdata["ycsb"]
            t = sdata["tectonic"]

            # 1. CLI Time
            y_cli = y.get("overall_metrics", {}).get("end_to_end_time_s", y.get("wall_time_s", 0.0))
            t_cli = t.get("overall_metrics", {}).get("end_to_end_time_s", t.get("wall_time_s", 0.0))
            cli_diff = abs(t_cli - y_cli) / y_cli * 100.0 if y_cli > 0 else 0.0

            # 2. Server Time & Byte Metrics
            y_srv_t, time_desc, y_bytes, bytes_desc = get_server_stats(db, scale, "ycsb", results)
            t_srv_t, _, t_bytes, _ = get_server_stats(db, scale, "tectonic", results)

            # Fallbacks if server stats 0 in files yet
            if y_bytes == 0.0:
                y_im = y.get("db_internal_metrics", {})
                if db == "rocksdb":
                    y_bytes = y_im.get("compaction_write_mb", 0.0) + y_im.get("wal_written_mb", 0.0) + y_im.get("flush_cumulative_mb", 0.0)
                elif db == "redis":
                    y_bytes = y_im.get("total_net_input_bytes_mb", 0.0)
                elif db == "cassandra":
                    y_bytes = y_im.get("memtable_data_size_mb", 0.0)
                elif db == "scylla":
                    y_bytes = y_im.get("scylla_commitlog_bytes_mb", 0.0)

            if t_bytes == 0.0:
                t_im = t.get("db_internal_metrics", {})
                if db == "rocksdb":
                    t_bytes = t_im.get("compaction_write_mb", 0.0) + t_im.get("wal_written_mb", 0.0) + t_im.get("flush_cumulative_mb", 0.0)
                elif db == "redis":
                    t_bytes = t_im.get("total_net_input_bytes_mb", 0.0)
                elif db == "cassandra":
                    t_bytes = t_im.get("memtable_data_size_mb", 0.0)
                elif db == "scylla":
                    t_bytes = t_im.get("scylla_commitlog_bytes_mb", 0.0)

            srv_diff = abs(t_srv_t - y_srv_t) / y_srv_t * 100.0 if y_srv_t > 0 else 0.0
            bytes_diff = abs(t_bytes - y_bytes) / y_bytes * 100.0 if y_bytes > 0 else 0.0

            # Table 1: CLI
            cli_rows.append([
                f"**{db}**", lbl, "YCSB",
                f"{y_cli:.2f}",
                bytes_desc, f"{y_bytes:.2f} MB",
                "—", "—"
            ])
            cli_rows.append([
                f"**{db}**", lbl, "X-Bench",
                f"{t_cli:.2f}",
                bytes_desc, f"{t_bytes:.2f} MB",
                f"**{cli_diff:.2f}%**", f"**{bytes_diff:.2f}%**"
            ])

            # Table 2: Server
            if y_srv_t > 0 and t_srv_t > 0:
                srv_rows.append([
                    f"**{db}**", lbl, "YCSB",
                    f"{y_srv_t:.2f}", time_desc,
                    bytes_desc, f"{y_bytes:.2f} MB",
                    "—", "—"
                ])
                srv_rows.append([
                    f"**{db}**", lbl, "X-Bench",
                    f"{t_srv_t:.2f}", time_desc,
                    bytes_desc, f"{t_bytes:.2f} MB",
                    f"**{srv_diff:.2f}%**", f"**{bytes_diff:.2f}%**"
                ])

    cli_lines.append(format_aligned_markdown_table(cli_headers, cli_align, cli_rows))
    cli_lines.append("\n## Notes on CLI Execution Time\n")
    cli_lines.append("- **CLI Execution Time** measures the total duration from the client issuing the first query until all queries complete (`End to End Time` measured by the Rust driver `tectonic-cli`).\n")
    cli_lines.append("- This metric captures full turnaround time experienced by applications, including client serialization, network transmission, and server execution.\n")

    srv_lines.append(format_aligned_markdown_table(srv_headers, srv_align, srv_rows))
    srv_lines.append("\n## Database Server Execution Time Methodology\n")
    srv_lines.append("1. **RocksDB (`rocksdb`)**: Extracted from RocksDB engine `rocksdb.stats` (`Uptime(secs)`). Because RocksDB is embedded into the client process, engine uptime closely tracks client time.\n")
    srv_lines.append("2. **Redis (`redis`)**: Extracted from `redis-cli info commandstats` (`cmdstat_get` and `cmdstat_set`). It captures pure CPU execution time inside Redis's single-threaded event loop, eliminating localhost TCP network socket latency.\n")
    srv_lines.append("3. **Cassandra (`cassandra`)**: Extracted from `nodetool tablestats` (`Read Latency * Read Count + Write Latency * Write Count`). It measures cumulative server-side query processing duration inside Cassandra's JVM.\n")
    srv_lines.append("4. **ScyllaDB (`scylla`)**: Extracted from Prometheus metrics (`scylla_column_family_read_latency_sum` + `scylla_column_family_write_latency_sum`). It measures total server-side query execution time across Seastar reactor cores.\n")

    with open(CLI_TABLE_OUT, "w") as f:
        f.writelines(cli_lines)
    print(f"Generated CLI Table: {CLI_TABLE_OUT}")

    with open(SERVER_TABLE_OUT, "w") as f:
        f.writelines(srv_lines)
    print(f"Generated Server Table: {SERVER_TABLE_OUT}")

if __name__ == "__main__":
    main()
