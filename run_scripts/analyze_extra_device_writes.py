#!/usr/bin/env python3
"""
analyze_extra_device_writes.py
==============================
Where do device writes beyond RocksDB's flush + compaction come from? For every phase directory of
data/revision/R1.O7_ycsb-derivatives/direct-io-enabled/diagnostics-2026-11-01/trace-extra-writes/run<N>/<phase>/
(written by run.sh / run_repeats.sh there: bpftrace trace.bt + /proc/diskstats of sda, WAL off, direct I/O):

  device write    /proc/diskstats sectors written x 512 over the phase
  RocksDB         rocksdb.flush.write.bytes + rocksdb.compact.write.bytes (cli_raw.log)
  block writes    bytes of the block write requests issued to sda, by request type (rwbs)
  dirtied pages   page-cache pages first dirtied in files on sda, by task (systemd-journal, rsyslog "rs:main Q:Reg", ...)

Usage: python3 run_scripts/analyze_extra_device_writes.py [trace-extra-writes dir]
"""
import re
import sys
import glob
import collections
from pathlib import Path

D = Path(sys.argv[1] if len(sys.argv) > 1 else
         "data/revision/R1.O7_ycsb-derivatives/direct-io-enabled/diagnostics-2026-11-01/trace-extra-writes")
MIB = 1 << 20
LOGGERS = {"systemd-journal": "journald", "journal-offline": "journald", "rs:main Q:Reg": "rsyslog",
           "jbd2/sda3-8": "ext4 journal (jbd2)"}


def phase(p):
    b, a = (p / "diskstats.before").read_text().split(), (p / "diskstats.after").read_text().split()
    secs = float((p / "t1").read_text()) - float((p / "t0").read_text())
    dev_w = (int(a[9]) - int(b[9])) * 512
    bt = (p / "bpftrace.txt").read_text()
    # Block writes by request type (rwbs): WS = sync data writes (RocksDB's O_DIRECT SST writes; the issuing task
    # is not reliable, a request can be dispatched from another task's context), WSM / WFSM = ext4 journal
    # (jbd2) blocks and commit records, W = page-cache writeback, WM = metadata writeback.
    kind = collections.Counter()
    for comm, rwbs, v in re.findall(r"^@blk_bytes\[(.+?), (\w+)\]: (\d+)", bt, re.M):
        if "W" in rwbs:
            kind[{"WS": "direct", "WSM": "journal", "WFSM": "journal", "W": "writeback", "WM": "meta"}.get(rwbs, "other")] += int(v)
    dirt = collections.Counter()
    for comm, _, v in re.findall(r"^@dirtied_pages\[(.+?), (\d+)\]: (\d+)", bt, re.M):
        dirt[LOGGERS.get(comm, "tectonic-cli" if comm == "tectonic-cli" else "other")] += int(v) * 4096
    commits = int((re.findall(r"@jbd2_commits: (\d+)", bt) or [0])[0])
    entries = len((p / "journal.txt").read_text().splitlines()) if (p / "journal.txt").exists() else None
    rocks = None
    if (p / "cli_raw.log").exists():
        st = dict(re.findall(r"^(rocksdb\.\S+) COUNT : (\d+)", (p / "cli_raw.log").read_text(), re.M))
        rocks = int(st["rocksdb.flush.write.bytes"]) + int(st["rocksdb.compact.write.bytes"])
    return secs, dev_w, rocks, kind, dirt, commits, entries


def main():
    m = lambda v: f"{v / MIB:,.2f}"
    print("| Run | Phase | Seconds | Device write | RocksDB flush + compaction | Device - RocksDB | Direct (WS) "
          "| WS - RocksDB | ext4 journal (WSM, WFSM) | Writeback (W) | Metadata (WM) | Unclassified "
          "| Dirtied: journald / rsyslog / jbd2 / tectonic-cli / other | jbd2 commits | Journal entries |")
    print("| --- " * 15 + "|")
    for p in sorted(Path(x) for x in glob.glob(str(D / "run*" / "*")) if (Path(x) / "bpftrace.txt").exists()):
        secs, dev_w, rocks, k, dirt, commits, entries = phase(p)
        classified = k["direct"] + k["journal"] + k["writeback"] + k["meta"]
        r = lambda f: f(rocks) if rocks is not None else "-"
        d = " / ".join(m(dirt[x]) for x in ("journald", "rsyslog", "ext4 journal (jbd2)", "tectonic-cli", "other"))
        print(f"| {p.parent.name} | {p.name} | {secs:.0f} | {m(dev_w)} | {r(m)} | {r(lambda v: m(dev_w - v))} | "
              f"{m(k['direct'])} | {r(lambda v: m(k['direct'] - v))} | {m(k['journal'])} | {m(k['writeback'])} | "
              f"{m(k['meta'])} | {m(dev_w - classified)} | {d} | {commits} | {entries} |")
    print("\nAll sizes MiB. Device = /proc/diskstats of sda; block writes by type from bpftrace (tracepoint "
          "block:block_rq_issue); dirtied = page-cache pages first dirtied in files on sda, by task "
          "(tracepoint writeback:writeback_dirty_folio); journal entries = journalctl lines in the phase.")


if __name__ == "__main__":
    main()
