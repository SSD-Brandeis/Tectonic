#!/usr/bin/env python3
"""
analyze_direct_io_device_match.py
=================================
Do the bytes iostat saw on the device (sum of `iostat -m` rMB/s and wMB/s of every second, sda; MiB) match
RocksDB's own counters (`-c statistics=1`)? Per trial of an R1.O7 or mixgraph `results/execution.json`:

  read:  device read  vs. RocksDB file reads   rocksdb.non.last.level.read.bytes + rocksdb.last.level.read.bytes
                      and Fig. 12 (C) read     rocksdb.bytes.read + rocksdb.db.iter.bytes.read + rocksdb.compact.read.bytes
  write: device write vs. RocksDB file writes  rocksdb.wal.bytes + 7 x rocksdb.write.wal (WAL record header)
                                               + rocksdb.flush.write.bytes + rocksdb.compact.write.bytes
                      and Fig. 12 (C) write    the same without the 7 B header term
                      and SST writes only      rocksdb.flush.write.bytes + rocksdb.compact.write.bytes

Usage: python3 run_scripts/analyze_direct_io_device_match.py <execution.json> [-v]   (-v: every trial)
"""
import sys
import json
import statistics as st

MIB = 1 << 20


def ratios(t):
    s, d = t["rocksdb_statistics"], t["device_io"]
    dev_r, dev_w = sum(d["read_MBps"]) * MIB, sum(d["write_MBps"]) * MIB
    file_r = s["rocksdb.non.last.level.read.bytes"] + s["rocksdb.last.level.read.bytes"]
    fig12_r = s["rocksdb.bytes.read"] + s["rocksdb.db.iter.bytes.read"] + s["rocksdb.compact.read.bytes"]
    sst_w = s["rocksdb.flush.write.bytes"] + s["rocksdb.compact.write.bytes"]
    fig12_w = s["rocksdb.wal.bytes"] + sst_w
    file_w = fig12_w + 7 * s["rocksdb.write.wal"]
    return {"device read": dev_r / MIB, "file read": file_r / MIB, "read vs file": dev_r / file_r - 1,
            "read vs Fig. 12": dev_r / fig12_r - 1, "device write": dev_w / MIB, "file write": file_w / MIB,
            "write vs file": dev_w / file_w - 1, "write vs Fig. 12": dev_w / fig12_w - 1,
            "write vs SST only": dev_w / sst_w - 1}


def main():
    E = json.load(open(sys.argv[1]))
    print("| Workload | Trials | Device read (MiB) | RocksDB file read (MiB) | Diff | vs Fig. 12 read | Device write (MiB) "
          "| RocksDB file write (MiB) | Diff | vs Fig. 12 write | vs SST writes only |")
    print("| --- " * 11 + "|")
    for w, sides in E.items():
        if isinstance(sides, list):  # mixgraph's execution.json: {trace: [trials]}
            sides = {w: sides}
        rs = [ratios(t) for trials in sides.values() for t in trials]
        if "-v" in sys.argv:
            for side, trials in sides.items():
                for t in trials:
                    r = ratios(t)
                    print(f"  {w} {side} {t['trial']}: " + ", ".join(
                        f"{k} {v:+.2%}" if "vs" in k else f"{k} {v:,.1f}" for k, v in r.items()))
        mib = lambda k: f"{st.mean(r[k] for r in rs):,.1f}"
        pct = lambda k: (f"{st.mean(r[k] for r in rs):+.2%} ({min(r[k] for r in rs):+.2%} to "
                         f"{max(r[k] for r in rs):+.2%})")
        print(f"| {w} | {len(rs)} | {mib('device read')} | {mib('file read')} | {pct('read vs file')} | "
              f"{pct('read vs Fig. 12')} | {mib('device write')} | {mib('file write')} | {pct('write vs file')} | "
              f"{pct('write vs Fig. 12')} | {pct('write vs SST only')} |")


if __name__ == "__main__":
    main()
