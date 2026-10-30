#!/usr/bin/env python3
"""Consistency checks for a run of run_table5_rerun_db_counters.py.

For every trial, compares the database-attributed byte counts against independent
measurements: device-level iostat, the workload trace size, and (where the database
reports them) the database's own counters.

Usage: check_table5_rerun_db_counters.py <out-dir>
"""
import os
import re
import sys
import json

MB = 2 ** 20


def rocksdb_tickers(trial):
    stats = [p for p in trial["db_log_files"] if p.endswith("_rocksdb_stats.txt")]
    text = open(stats[0]).read() if stats else ""
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"^(rocksdb\.[a-z.]+) COUNT : (\d+)$", text, re.M)}


def main():
    out_dir = sys.argv[1]
    data = json.load(open(f"{out_dir}/results/results_raw.json"))
    workload_dir = data["config"]["workload_dir"]
    problems = []
    for db, scales in data["results"].items():
        for scale, workloads in sorted(scales.items(), key=lambda kv: int(kv[0])):
            for workload, trials in workloads.items():
                name = "xbench" if workload == "tectonic" else "ycsb"
                trace_mb = os.path.getsize(f"{workload_dir}/{name}_workload_a_scale_{scale}.txt") / MB
                for t in trials:
                    tag = f"{db} 2^{int(scale).bit_length() - 1} {workload} trial {t['trial']}"
                    io = t["io_bytes_total"]
                    cg = t["cgroup_io"]
                    client, cont = cg["client"], cg["container"]
                    d = t["db_counters"]["delta"]
                    iostat_w, iostat_r = io.get("total_write_mb", 0), io.get("total_read_mb", 0)
                    disk_w, disk_r = io.get("hardware_write_mb", 0), io.get("hardware_read_mb", 0)
                    db_w = (cont["wbytes"] if cont else client["wbytes"]) / MB
                    print(f"\n{tag}: wall {t['wall_time_s']:.2f}s")
                    print(f"  iostat samples (device)      read {iostat_r:10.1f} MB  write {iostat_w:10.1f} MB")
                    print(f"  /proc/diskstats (device)     read {disk_r:10.1f} MB  write {disk_w:10.1f} MB")
                    print(f"  client cgroup                read {client['rbytes'] / MB:10.1f} MB  write {client['wbytes'] / MB:10.1f} MB"
                          f"   (trace file {trace_mb:.1f} MB)")
                    if cont:
                        print(f"  container cgroup             read {cont['rbytes'] / MB:10.1f} MB  write {cont['wbytes'] / MB:10.1f} MB")
                    cg_w = (client["wbytes"] + (cont["wbytes"] if cont else 0)) / MB
                    if cg_w > disk_w * 1.02 + 2:
                        problems.append(f"{tag}: cgroup writes {cg_w:.1f} MB exceed device writes {disk_w:.1f} MB")
                    if abs(client["rbytes"] / MB - trace_mb) > 0.1 * trace_mb + 5:
                        problems.append(f"{tag}: client reads {client['rbytes'] / MB:.1f} MB vs trace {trace_mb:.1f} MB")

                    if db == "rocksdb":
                        tk = rocksdb_tickers(t)
                        wal = tk.get("rocksdb.wal.bytes", 0) / MB
                        sst = (tk.get("rocksdb.flush.write.bytes", 0) + tk.get("rocksdb.compact.write.bytes", 0)) / MB
                        cw = client["wbytes"] / MB
                        print(f"  RocksDB flush+compaction write bytes      {sst:10.1f} MB  WAL bytes {wal:.1f} MB"
                              f"  (client cgroup write {cw:.1f} MB; WAL reaching disk {cw - sst:.1f} MB)")
                        if not (sst * 0.98 - 2 <= cw <= sst + wal + 2):
                            problems.append(f"{tag}: client cgroup {cw:.1f} MB outside [flush+compaction "
                                            f"{sst:.1f}, + WAL {sst + wal:.1f}] MB")
                    elif db == "scylla":
                        queue_w = sum(v for k, v in d.items() if k.startswith("scylla_io_queue_total_write_bytes")) / MB
                        per_class = {k.split("class=")[1].rstrip("]"): round(v / MB, 1)
                                     for k, v in d.items()
                                     if k.startswith("scylla_io_queue_total_write_bytes") and v}
                        print(f"  Scylla io_queue write bytes (delta)       {queue_w:10.1f} MB  {per_class}")
                        print(f"  Scylla commitlog_bytes_written (delta)    {d.get('scylla_commitlog_bytes_written', 0) / MB:10.1f} MB")
                        if abs(queue_w - cont["wbytes"] / MB) > 0.1 * queue_w + 5:
                            problems.append(f"{tag}: Scylla io_queue {queue_w:.1f} MB vs container cgroup "
                                            f"{cont['wbytes'] / MB:.1f} MB")
                    elif db == "cassandra":
                        flushed = d.get("type=Table,keyspace=tectonic,scope=data,name=BytesFlushed", 0) / MB
                        compacted = d.get("type=Table,keyspace=tectonic,scope=data,name=CompactionBytesWritten", 0) / MB
                        print(f"  Cassandra BytesFlushed {flushed:.1f} MB + CompactionBytesWritten {compacted:.1f} MB"
                              f" = {flushed + compacted:.1f} MB; rest of container write (commit log etc.)"
                              f" {cont['wbytes'] / MB - flushed - compacted:.1f} MB")
                        if flushed + compacted > cont["wbytes"] / MB * 1.05 + 5:
                            problems.append(f"{tag}: Cassandra flush+compaction exceed container writes")
                    elif db == "redis":
                        print(f"  Redis total_net_input_bytes (delta)       {d.get('total_net_input_bytes', 0) / MB:10.1f} MB"
                              f"  commands {d.get('total_commands_processed', 0):,}")
                        if cont["wbytes"] / MB > 5:
                            problems.append(f"{tag}: Redis (persistence off) wrote {cont['wbytes'] / MB:.1f} MB")
                    print(f"  database-attributed write {db_w:.1f} MB = {db_w / disk_w * 100 if disk_w else 0:.1f}% of device write")
    print("\nPROBLEMS:" if problems else "\nNo problems found.")
    for p in problems:
        print("  - " + p)


if __name__ == "__main__":
    main()
