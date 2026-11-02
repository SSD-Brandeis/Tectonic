#!/usr/bin/env python3
"""
analyze_isolated_device.py
==========================
Does iostat match RocksDB's counters when the DB has a device to itself? For every run directory of
data/revision/R1.O7_ycsb-derivatives/direct-io-enabled/diagnostics-2026-11-01/isolated-device/run<N>/<workload>/
(written by run.sh there: DB on an ext4 file system on loop0, direct_io=1, disable_wal=1, statistics=1):

  iostat          sum over the 1 s samples of rkB/s and wkB/s of the DB's loop device (iostat.txt; x 1024)
  diskstats       sectors read / written x 512 of the loop device over the run (+ sync + 2 s)
  RocksDB read    rocksdb.non.last.level.read.bytes + rocksdb.last.level.read.bytes (cli_raw.log)
  RocksDB write   rocksdb.flush.write.bytes + rocksdb.compact.write.bytes (+ rocksdb.wal.bytes, 0 with the WAL off)
  bpftrace        loop device block requests by type (rwbs); the executor's pread64 / pwrite64 / write bytes

Usage: python3 run_scripts/analyze_isolated_device.py [-v] [isolated-device dir]
"""
import re
import sys
import glob
import statistics
import collections
from pathlib import Path

ARGS = [a for a in sys.argv[1:] if a != "-v"]
VERBOSE = "-v" in sys.argv[1:]
D = Path(ARGS[0] if ARGS else
         "data/revision/R1.O7_ycsb-derivatives/direct-io-enabled/diagnostics-2026-11-01/isolated-device")
MIB = 1 << 20
WORKLOADS = ["ivs-uniform", "ivs-uniform-x10", "ts-energy"]


def iostat_bytes(path, dev):
    """Bytes read and written by dev: the sum of its rkB/s and wkB/s over the 1 s samples, x 1024."""
    r = w = 0.0
    cols = None
    for line in path.read_text().splitlines():
        f = line.split()
        if f and f[0] == "Device":
            cols = f
        elif cols and f and f[0] == dev:
            r += float(f[cols.index("rkB/s")]) * 1024
            w += float(f[cols.index("wkB/s")]) * 1024
    return r, w


def diskstats(p, dev):
    b = next(l.split() for l in (p / "diskstats.before").read_text().splitlines() if l.split()[2] == dev)
    a = next(l.split() for l in (p / "diskstats.after").read_text().splitlines() if l.split()[2] == dev)
    return (int(a[5]) - int(b[5])) * 512, (int(a[9]) - int(b[9])) * 512


def bt_map(text, name):
    """@name[key]: value lines -> {key: value}."""
    return {k: int(v) for k, v in re.findall(rf"^@{name}\[(.+?)\]: (\d+)", text, re.M)}


def run(p, loop):
    st = dict(re.findall(r"^(rocksdb\.\S+) COUNT : (\d+)", (p / "cli_raw.log").read_text(), re.M))
    c = lambda k: int(st[f"rocksdb.{k}"])
    bt = (p / "bpftrace.txt").read_text()
    lb = bt_map(bt, "loop_bytes")
    # Loop device writes by request type: WS = sync data (RocksDB's O_DIRECT SST writes), WSM / WFSM = ext4
    # journal, W = page-cache writeback (the LOG, MANIFEST, OPTIONS files), WM = metadata writeback.
    kind = collections.Counter()
    for rwbs, v in lb.items():
        if "W" in rwbs:
            kind[{"WS": "direct", "WSM": "journal", "WFSM": "journal", "W": "writeback", "WM": "meta"}.get(rwbs, "other")] += v
        elif rwbs.startswith("R"):
            kind["read " + rwbs] += v
    io_r, io_w = iostat_bytes(p / "iostat.txt", loop)
    ds_r, ds_w = diskstats(p, loop)
    return {
        "secs": float((p / "t1").read_text()) - float((p / "t0").read_text()),
        "rocks_r": c("non.last.level.read.bytes") + c("last.level.read.bytes"),
        "rocks_w": c("flush.write.bytes") + c("compact.write.bytes") + c("wal.bytes"),
        "reads": c("non.last.level.read.count") + c("last.level.read.count"),
        "io_r": io_r, "io_w": io_w, "ds_r": ds_r, "ds_w": ds_w, "kind": kind,
        "pread_req": sum(bt_map(bt, "pread_req_bytes").values()), "pread_ret": sum(bt_map(bt, "pread_ret_bytes").values()),
        "pread_calls": sum(bt_map(bt, "pread_calls").values()),
        "pread_aligned": sum(bt_map(bt, "pread_aligned_calls").values()),
        "pwrite": sum(bt_map(bt, "pwrite_req_bytes").values()), "write": sum(bt_map(bt, "write_req_bytes").values()),
        "ops_s": float(re.findall(r"\[Overall\] Throughput.*?: ([\d.]+)ops/sec", (p / "cli_raw.log").read_text())[-1]),
    }


def main():
    loop = re.search(r"/dev/(loop\d+)", (D / "device.txt").read_text()).group(1)
    runs = collections.defaultdict(list)
    for p in sorted(Path(x) for x in glob.glob(str(D / "run*" / "*")) if (Path(x) / "bpftrace.txt").exists()):
        runs[p.name].append((p.parent.name, run(p, loop)))
    m = lambda v: f"{v / MIB:,.2f}"
    pct = lambda a, b: f"{(a - b) / b * 100:+.2f}%"
    mean = lambda rs, k: statistics.mean(r[k] for _, r in rs)
    print(f"Mean of {max(len(v) for v in runs.values())} runs (MiB); iostat of the DB's device ({loop}) alone.\n")
    print("| Workload | Runs | RocksDB read | iostat read | Difference | RocksDB write | iostat write | Difference |")
    print("| --- " * 8 + "|")
    for w in WORKLOADS:
        rs = runs.get(w, [])
        if not rs:
            continue
        print(f"| {w} | {len(rs)} | {m(mean(rs, 'rocks_r'))} | {m(mean(rs, 'io_r'))} | "
              f"{pct(mean(rs, 'io_r'), mean(rs, 'rocks_r'))} | {m(mean(rs, 'rocks_w'))} | {m(mean(rs, 'io_w'))} | "
              f"{pct(mean(rs, 'io_w'), mean(rs, 'rocks_w'))} |")
    print("\nPer run (MiB): reads from RocksDB's counter down to the device; writes on the device by request type.\n")
    print("| Workload | Run | ops/s | Reads (RocksDB) | RocksDB read | pread requested | pread returned | preads 512-aligned "
          "| Device read (diskstats) | iostat read | RocksDB write | pwrite | write() | Direct (WS) | ext4 journal "
          "| Writeback (W) | Metadata (WM) | Other | Device write (diskstats) | iostat write |")
    print("| --- " * 20 + "|")
    for w in WORKLOADS:
        for name, r in runs.get(w, []):
            k = r["kind"]
            print(f"| {w} | {name} | {r['ops_s']:,.0f} | {r['reads']:,} | {m(r['rocks_r'])} | {m(r['pread_req'])} | "
                  f"{m(r['pread_ret'])} | {r['pread_aligned']:,} of {r['pread_calls']:,} | {m(r['ds_r'])} | {m(r['io_r'])} | "
                  f"{m(r['rocks_w'])} | {m(r['pwrite'])} | {m(r['write'])} | {m(k['direct'])} | {m(k['journal'])} | "
                  f"{m(k['writeback'])} | {m(k['meta'])} | {m(k['other'])} | {m(r['ds_w'])} | {m(r['io_w'])} |")
            if VERBOSE:
                print("  ", {kk: m(v) for kk, v in k.items()})


if __name__ == "__main__":
    main()
