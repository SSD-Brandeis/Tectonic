#!/usr/bin/env python3
"""
analyze_wal_gap_matrix.py — iostat vs. RocksDB's counters for each config of run_wal_gap_matrix.sh
==================================================================================================
For every <device>/<config>/<workload> run (MiB):
  RocksDB read   rocksdb.non.last.level.read.bytes + rocksdb.last.level.read.bytes (panel C read)
  RocksDB write  rocksdb.wal.bytes + rocksdb.flush.write.bytes + rocksdb.compact.write.bytes (panel C write);
                 "+ hdr" adds 7 B per WAL record (7 x rocksdb.write.wal, log::kHeaderSize), which wal.bytes leaves out
  iostat         sum of the 1 s rkB/s, wkB/s samples of the device (panel A summed over time)
  diskstats      sectors read / written by the device between before and after (after sync + 2 s)
Usage: analyze_wal_gap_matrix.py <out-dir> [<out-dir> ...]
"""
import re
import sys
from pathlib import Path

MIB = 2 ** 20
CONFIG_ORDER = ["wal", "nowal", "bps", "archive", "bps-archive", "sync"]


def tickers(path):
    text = path.read_text(errors="replace")
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^(rocksdb\.[\w.\-]+) COUNT : (\d+)$", text, re.M)}


def diskstats(path):
    f = path.read_text().split()
    return int(f[5]) * 512, int(f[9]) * 512  # sectors read, sectors written


def iostat_sum(path, device):
    cols, r, w = None, 0.0, 0.0
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts and parts[0] == "Device":
            cols = parts
        elif cols and parts and parts[0] == device:
            r += float(parts[cols.index("rkB/s")])
            w += float(parts[cols.index("wkB/s")])
    return r * 1024, w * 1024


def pct(a, b):
    return f"{(a - b) / b * 100:+.2f}%" if b else "-"


def main():
    rows = []
    for out in map(Path, sys.argv[1:]):
        host = (out / "machine.txt").read_text().split()[0] if (out / "machine.txt").exists() else out.name
        for o in sorted(out.glob("*/*/*")):
            if not (o / "diskstats.after").exists():
                continue
            device_kind, config, workload = o.relative_to(out).parts
            dev = (o / "diskstats.before").read_text().split()[2]
            t = tickers(o / "cli_raw.log")
            if not t:
                continue
            b, a = diskstats(o / "diskstats.before"), diskstats(o / "diskstats.after")
            io_r, io_w = iostat_sum(o / "iostat.txt", dev)
            c_r = t["rocksdb.non.last.level.read.bytes"] + t["rocksdb.last.level.read.bytes"]
            c_w = t["rocksdb.wal.bytes"] + t["rocksdb.flush.write.bytes"] + t["rocksdb.compact.write.bytes"]
            hdr = 7 * t.get("rocksdb.write.wal", 0)
            secs = float((o / "t1").read_text()) - float((o / "t0").read_text())
            rows.append((CONFIG_ORDER.index(config) if config in CONFIG_ORDER else 99, workload, device_kind, host,
                         config, secs, c_r, io_r, a[0] - b[0], c_w, c_w + hdr, io_w, a[1] - b[1],
                         t["rocksdb.wal.bytes"], t.get("rocksdb.wal.synced", 0)))
    rows.sort(key=lambda r: (r[1], r[2], r[0]))
    print("| Workload | Device | Config | Seconds | RocksDB read | iostat read | Diff | diskstats read "
          "| RocksDB write | iostat write | Diff | + hdr | Diff (+ hdr) | diskstats write | of which WAL (RocksDB) | WAL syncs |")
    print("|" + " --- |" * 16)
    m = lambda x: f"{x / MIB:,.1f}"
    for (_, w, d, host, cfg, secs, c_r, io_r, ds_r, c_w, c_wh, io_w, ds_w, wal, synced) in rows:
        print(f"| {w} | {d} ({host}) | {cfg} | {secs:,.0f} | {m(c_r)} | {m(io_r)} | {pct(io_r, c_r)} | {m(ds_r)} "
              f"| {m(c_w)} | {m(io_w)} | {pct(io_w, c_w)} | {m(c_wh)} | {pct(io_w, c_wh)} | {m(ds_w)} | {m(wal)} | {synced:,} |")


if __name__ == "__main__":
    main()
