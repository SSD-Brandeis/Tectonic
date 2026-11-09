#!/usr/bin/env python3
"""
plot_io_tool_match.py — R1.O7 Fig. 12-style plots where (A) adds up to (C)
=========================================================================
Input : <out-dir>/results/results.json and db_state/<workload>/<side>/trial<N>/bpftrace.txt
        (run_scripts/run_io_tool_match.py)
  (A) bytes RocksDB read from and wrote to its files per second, measured by bpftrace at the
      file-system layer (vfs_read/vfs_write of the executor's cgroup, above the page cache):
      read = SST files (*.sst), write = WAL files (*.log) + SST files; mean over the bpftrace trials,
      up to the moment the executor printed RocksDB's statistics;
  (C) RocksDB statistics (mean over the same trials):
      read  = rocksdb.non.last.level.read.bytes + rocksdb.last.level.read.bytes
      write = rocksdb.wal.bytes + 7 B × rocksdb.write.wal (WAL record headers, not in rocksdb.wal.bytes)
              + rocksdb.flush.write.bytes + rocksdb.compact.write.bytes
      with a black marker per bar at the sum of (A) over time (the tool's total).
Output: <out-dir>/plots/<workload>_{bytes_over_time,rocksdb_vs_tool}.pdf, <workload>_preview.png
"""
import os
import sys
import json
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "run_scripts"))
import plot_style
from run_io_tool_match import parse_bpftrace, category

SIDES = (("ycsb-derivative", "YCSB"), ("xbench", "X-Bench"))
READ_TICKERS = ("rocksdb.non.last.level.read.bytes", "rocksdb.last.level.read.bytes")
WRITE_TICKERS = ("rocksdb.wal.bytes", "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes")
WAL_HEADER = 7  # bytes per WAL record (log::kHeaderSize), not counted by rocksdb.wal.bytes
MIB, GIB = 2 ** 20, 2 ** 30
WIDTH = 0.25


def ycsb_name(workload):
    return {"ivs": "YCSB-IVS", "ts": "YCSB-TS"}.get(workload.split("-", 1)[0], "YCSB")


def label(side, ycsb):
    return ycsb if side == "ycsb-derivative" else "X-Bench"


def per_second(out_dir, t):
    """Read (SST) and write (WAL + SST) bytes of every bpftrace interval before the statistics print."""
    path = f"{out_dir}/db_state/{t['workload']}/{t['side']}/trial{t['trial']}/bpftrace.txt"
    intervals, _, _ = parse_bpftrace(path)
    reads, writes = [], []
    for iv in intervals:
        r = w = 0
        for key, n in iv["bytes"].items():
            phase, op, name = key.split("|", 2)
            if phase != "0":
                continue
            cat = category(name)
            if op == "r" and cat == "sst":
                r += n
            elif op == "w" and cat in ("sst", "wal"):
                w += n
        reads.append(r)
        writes.append(w)
    # from the first second with RocksDB file I/O to the last
    active = [i for i, (r, w) in enumerate(zip(reads, writes)) if r or w]
    lo, hi = active[0], active[-1] + 1
    return np.array(reads[lo:hi], float), np.array(writes[lo:hi], float)


def panel_over_time(ax, series, ycsb):
    n = min(len(r) for side in series for r, _ in series[side])
    x = np.concatenate([[0], np.arange(1, n + 1)])
    for side, style in SIDES:
        r = np.mean([r[:n] for r, _ in series[side]], axis=0) / MIB
        w = np.mean([w[:n] for _, w in series[side]], axis=0) / MIB
        line = {**plot_style.LINE_STYLES[style], "marker": None, "linewidth": plt.rcParams["lines.linewidth"] + 1}
        ax.plot(x, np.concatenate([[0.0], r]), label=f"read ({label(side, ycsb)})", **{**line, "linestyle": "-"})
        ax.plot(x, np.concatenate([[0.0], w]), label=f"write ({label(side, ycsb)})", **{**line, "linestyle": "--"})
    ax.set_xlabel(plot_style.format_label("time (s)"))
    ax.set_ylabel(plot_style.format_label("bytes transferred (MB)"))
    plot_style.apply_plot_style(ax)


def panel_totals(ax, trials, series, ycsb):
    x = np.arange(2) * 0.75
    for j, (side, style) in enumerate(SIDES):
        ts = trials[side]
        st = [t["rocksdb_statistics"] for t in ts]
        rocks = [np.mean([sum(s.get(k, 0) for k in READ_TICKERS) for s in st]) / GIB,
                 np.mean([sum(s.get(k, 0) for k in WRITE_TICKERS) + WAL_HEADER * s.get("rocksdb.write.wal", 0)
                          for s in st]) / GIB]
        tool = [np.mean([r.sum() for r, _ in series[side]]) / GIB, np.mean([w.sum() for _, w in series[side]]) / GIB]
        pos = x + (j - 0.5) * WIDTH
        ax.bar(pos, rocks, WIDTH, label=label(side, ycsb), **plot_style.BAR_STYLES[style])
        ax.scatter(pos, tool, marker="_", s=600, linewidths=3, color="black", zorder=3,
                   label="sum of (A)" if j == 0 else None)
    ax.set_xticks(x)
    ax.set_xticklabels(["read", "write"])
    ax.set_xlim(x[0] - WIDTH - 0.12, x[-1] + WIDTH + 0.12)
    ax.set_ylabel(plot_style.format_label("bytes (GB)"))
    plot_style.apply_plot_style(ax)


def main():
    out_dir = sys.argv[1]
    data = json.load(open(f"{out_dir}/results/results.json"))
    plot_dir = f"{out_dir}/plots"
    os.makedirs(plot_dir, exist_ok=True)
    by_wl = {}
    for t in data["trials"]:
        if t["mode"] == "bpftrace":
            by_wl.setdefault(t["workload"], {}).setdefault(t["side"], []).append(t)
    for wl, trials in by_wl.items():
        if not all(trials.get(side) for side, _ in SIDES):
            continue
        ycsb = ycsb_name(wl)
        series = {side: [per_second(out_dir, t) for t in trials[side]] for side, _ in SIDES}
        for name, draw, size in (("bytes_over_time", lambda ax: panel_over_time(ax, series, ycsb), (5, 3.6)),
                                 ("rocksdb_vs_tool", lambda ax: panel_totals(ax, trials, series, ycsb), (2.8, 3.6))):
            fig, ax = plt.subplots(figsize=size)
            draw(ax)
            fig.tight_layout()
            fig.savefig(f"{plot_dir}/{wl}_{name}.pdf")
            plt.close(fig)
        fig, axes = plt.subplots(1, 2, figsize=(9, 4.4), gridspec_kw={"width_ratios": [5, 2]})
        panel_over_time(axes[0], series, ycsb)
        panel_totals(axes[1], trials, series, ycsb)
        for ax, tag in zip(axes, "AC"):
            ax.text(-0.1, -0.36, f"({tag})", transform=ax.transAxes)
        handles, labels = [], []
        for ax in axes:
            for h, l in zip(*ax.get_legend_handles_labels()):
                if l not in labels:
                    handles.append(h)
                    labels.append(l)
        fig.legend(handles, labels, loc="upper center", ncol=4, frameon=False, fontsize=11)
        fig.tight_layout(rect=(0, 0, 1, 0.86))
        fig.savefig(f"{plot_dir}/{wl}_preview.png", dpi=120)
        plt.close(fig)
        print(f"{plot_dir}/{wl}_preview.png")


if __name__ == "__main__":
    main()
