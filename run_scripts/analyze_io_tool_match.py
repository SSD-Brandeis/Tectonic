#!/usr/bin/env python3
"""
analyze_io_tool_match.py — compare RocksDB statistics tickers with each measurement tool
=======================================================================================
Input : <out-dir>/results/results.json from run_io_tool_match.py
Output: <out-dir>/results/comparison.md (one table per workload and side), comparison.json

"at stats" = the moment the executor printed RocksDB's counters (bpftrace phase 0, and the
/proc/<pid>/io snapshot taken when "=== RocksDB Internal Stats ===" reached stdout). Device-level
tools (cgroup io.stat, /proc/diskstats, iostat) cover the whole trial including the final sync.
"""
import sys
import json
import statistics

GIB = 2 ** 30


def tk(t, *names):
    return sum(t["rocksdb_statistics"].get(n, 0) for n in names)


def bpf(t, phase, op, cat):
    c = t.get("bpftrace_totals_by_category")
    if c is None:
        return None
    phases = ("before_stats", "after_stats") if phase == "all" else (phase,)
    return sum(c.get(f"{p}|{op}|{cat}", 0) for p in phases)


# Every WAL record carries a 7-byte header (log::kHeaderSize: checksum, length, type) that
# rocksdb.wal.bytes does not count; rocksdb.write.wal counts the WAL records written.
WAL_HEADER = 7
WAL_HDR = f"{WAL_HEADER} × rocksdb.write.wal"

ROWS = [
    # (group, RocksDB counter label, rocksdb value fn, tool label, tool value fn)
    ("write", "rocksdb.wal.bytes", lambda t: tk(t, "rocksdb.wal.bytes"),
     "bpftrace: bytes written to WAL files (*.log), at stats", lambda t: bpf(t, "before_stats", "w", "wal")),
    ("write", f"rocksdb.wal.bytes + {WAL_HDR}",
     lambda t: tk(t, "rocksdb.wal.bytes") + WAL_HEADER * tk(t, "rocksdb.write.wal"),
     "bpftrace: bytes written to WAL files (*.log), at stats", lambda t: bpf(t, "before_stats", "w", "wal")),
    ("write", "rocksdb.flush.write.bytes + rocksdb.compact.write.bytes",
     lambda t: tk(t, "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes"),
     "bpftrace: bytes written to SST files (*.sst), at stats", lambda t: bpf(t, "before_stats", "w", "sst")),
    ("write", "rocksdb.wal.bytes + rocksdb.flush.write.bytes + rocksdb.compact.write.bytes",
     lambda t: tk(t, "rocksdb.wal.bytes", "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes"),
     "/proc/<pid>/io wchar, at stats", lambda t: (t.get("at_stats") or {}).get("proc_io", {}).get("wchar")),
    ("write", f"rocksdb.wal.bytes + {WAL_HDR} + rocksdb.flush.write.bytes + rocksdb.compact.write.bytes",
     lambda t: tk(t, "rocksdb.wal.bytes", "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes")
     + WAL_HEADER * tk(t, "rocksdb.write.wal"),
     "/proc/<pid>/io wchar, at stats", lambda t: (t.get("at_stats") or {}).get("proc_io", {}).get("wchar")),
    ("write", "rocksdb.flush.write.bytes + rocksdb.compact.write.bytes",
     lambda t: tk(t, "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes"),
     "/proc/<pid>/io write_bytes - cancelled_write_bytes, at exit",
     lambda t: t["proc_io_final"]["write_bytes"] - t["proc_io_final"]["cancelled_write_bytes"]),
    ("write", "rocksdb.flush.write.bytes + rocksdb.compact.write.bytes",
     lambda t: tk(t, "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes"),
     "cgroup io.stat wbytes (block layer), whole trial", lambda t: t["cgroup_io_delta"]["wbytes"]),
    ("write", "rocksdb.flush.write.bytes + rocksdb.compact.write.bytes",
     lambda t: tk(t, "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes"),
     "/proc/diskstats sda (device), whole trial", lambda t: t["diskstats_delta"]["write_bytes"]),
    ("write", "rocksdb.flush.write.bytes + rocksdb.compact.write.bytes",
     lambda t: tk(t, "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes"),
     "iostat wMB/s summed (device, as R1.O7), whole trial", lambda t: t["iostat_sum_MB"]["write"] * 2 ** 20),
    ("read", "rocksdb.non.last.level.read.bytes + rocksdb.last.level.read.bytes",
     lambda t: tk(t, "rocksdb.non.last.level.read.bytes", "rocksdb.last.level.read.bytes"),
     "bpftrace: bytes read from SST files (*.sst), at stats", lambda t: bpf(t, "before_stats", "r", "sst")),
    ("read", "rocksdb.non.last.level.read.bytes + rocksdb.last.level.read.bytes",
     lambda t: tk(t, "rocksdb.non.last.level.read.bytes", "rocksdb.last.level.read.bytes"),
     "/proc/<pid>/io rchar - trace file size, at stats",
     lambda t: ((t.get("at_stats") or {}).get("proc_io", {}).get("rchar", 0) - t["trace_bytes"])
     if t.get("at_stats") else None),
    ("read", "rocksdb.non.last.level.read.bytes + rocksdb.last.level.read.bytes",
     lambda t: tk(t, "rocksdb.non.last.level.read.bytes", "rocksdb.last.level.read.bytes"),
     "cgroup io.stat rbytes (block layer), whole trial", lambda t: t["cgroup_io_delta"]["rbytes"]),
    ("read", "rocksdb.non.last.level.read.bytes + rocksdb.last.level.read.bytes",
     lambda t: tk(t, "rocksdb.non.last.level.read.bytes", "rocksdb.last.level.read.bytes"),
     "iostat rMB/s summed (device, as R1.O7), whole trial", lambda t: t["iostat_sum_MB"]["read"] * 2 ** 20),
]


def main():
    out_dir = sys.argv[1]
    data = json.load(open(f"{out_dir}/results/results.json"))
    cells = {}
    for t in data["trials"]:
        cells.setdefault((t["workload"], t["side"]), []).append(t)
    md = ["# Which tool reports the bytes RocksDB's statistics report?", "",
          f"Host `{data['config']['host']}`, executor sha256 `{data['config']['cli_sha256'][:16]}…`, "
          f"{data['config']['bpftrace']}. Values in GiB; diff = (tool − RocksDB) / RocksDB. "
          "Trial modes: plain = no bpftrace, bpftrace = all tools.", ""]
    out = {}
    for (wl, side), trials in cells.items():
        trials.sort(key=lambda t: t["trial"])
        md += [f"## {wl} — {side}", "",
               "| RocksDB counter(s) | Tool | " + " | ".join(
                   f"trial {t['trial']} ({t['mode']}) RocksDB / tool / diff" for t in trials) + " |",
               "|---|---|" + "---|" * len(trials)]
        for group, rlabel, rfn, tlabel, tfn in ROWS:
            cols = []
            for t in trials:
                r, v = rfn(t), tfn(t)
                if v is None:
                    cols.append("—")
                    continue
                diff = (v - r) / r * 100 if r else float("nan")
                cols.append(f"{r / GIB:.4f} / {v / GIB:.4f} / {diff:+.2f}%")
                out.setdefault(f"{wl}|{side}", []).append(
                    {"trial": t["trial"], "mode": t["mode"], "group": group, "rocksdb": rlabel, "tool": tlabel,
                     "rocksdb_bytes": r, "tool_bytes": v, "diff_pct": diff})
            md.append(f"| `{rlabel}` | {tlabel} | " + " | ".join(cols) + " |")
        md += ["", "Wall time (s): " + ", ".join(f"trial {t['trial']} ({t['mode']}) {t['wall_time_s']:.2f}"
                                                 for t in trials), ""]
    with open(f"{out_dir}/results/comparison.md", "w") as f:
        f.write("\n".join(md) + "\n")
    with open(f"{out_dir}/results/comparison.json", "w") as f:
        json.dump(out, f, indent=1)
    print("\n".join(md))


if __name__ == "__main__":
    main()
