#!/usr/bin/env python3
"""
plot_r1o7_fig12_style.py
========================
R1.O7: X-Bench vs. YCSB-IVS / YCSB-TS on RocksDB, plotted like Fig. 12 of the paper
(plot_scripts/plot_rocksdb_similarity_ycsba.py, same style and the same metrics):
  (A) bytes transferred (MB) over time: iostat of the database device, mean over the trials of each
      trace for every second (one iostat sample per second, no smoothing);
  (B) latency per operation type: p0, p25, p50, p75, p99 of the whole run (mean over the trials);
  (C) bytes RocksDB read and wrote (GB), from its statistics (mean over the trials):
      read  = rocksdb.bytes.read + rocksdb.db.iter.bytes.read + rocksdb.compact.read.bytes
      write = rocksdb.wal.bytes + rocksdb.flush.write.bytes + rocksdb.compact.write.bytes
      (Fig. 12's read has no rocksdb.db.iter.bytes.read: YCSB-A has no scans.)

Input : data/revision/R1.O7_ycsb-derivatives/results/execution.json (execute stage of
        run_scripts/run_ycsb_derivatives_fidelity.py)
Output: data/revision/R1.O7_ycsb-derivatives/plots/<workload>_{throughput,latency,data_movement}.pdf,
        plots/<workload>_throughput_log.pdf ((A) on a log y-axis starting at 10^0 MB),
        plots/ycsb-{ivs,ts}_legend.pdf (one per YCSB derivative), plots/<workload>_preview.png (the three panels side by side)
"""
import os
import sys
import json
import statistics
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.append(str(Path(__file__).resolve().parent))
import plot_style

OUT_DIR = "/home/cc/Tectonic/data/revision/R1.O7_ycsb-derivatives"
EXEC_PATH = f"{OUT_DIR}/results/execution.json"
PLOT_DIR = f"{OUT_DIR}/plots"

# the trace X-Bench is compared against; the YCSB rerun is the noise floor (in report.md)
SIDES = (("ycsb_derivative", "YCSB"), ("xbench", "Tectonic"))
OP_LABELS = {"Insert": "insert", "Point Query": "PQ", "Update": "update", "Merge": "merge",
             "Range Query": "RQ"}
PERCENTILES = (("whislo", "Minimum Latency"), ("q1", "25th Percentile Latency"),
               ("med", "50th Percentile Latency"), ("q3", "75th Percentile Latency"),
               ("whishi", "99th Percentile Latency"))
READ_TICKERS = ("rocksdb.bytes.read", "rocksdb.db.iter.bytes.read", "rocksdb.compact.read.bytes")
WRITE_TICKERS = ("rocksdb.wal.bytes", "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes")
BYTE_TO_GB = 1024 ** 3
FIGSIZE = (5, 3.6)
DATA_MOVEMENT_FIGSIZE = (2.8, 3.6)
LEFT_MARGIN_IN = 1.1  # room for the y tick labels and the y label at the style's 22 pt
WINDOW = 1  # s; no rolling average (Fig. 12 used 3 s; R1.O7 runs are as short as 2 s)
WIDTH = 0.25


def label(side, ycsb):
    return ycsb if side == "ycsb_derivative" else "X-Bench"


def ycsb_name(workload):
    """The YCSB derivative a workload comes from: ivs-* is YCSB-IVS, ts-* is YCSB-TS."""
    return {"ivs": "YCSB-IVS", "ts": "YCSB-TS"}[workload.split("-", 1)[0]]


def mean_iostat(trials):
    """Read and write MB/s of every second, mean over the trials (cut to the shortest)."""
    n = min(len(t["device_io"]["read_MBps"]) for t in trials)
    reads = np.mean([t["device_io"]["read_MBps"][:n] for t in trials], axis=0)
    writes = np.mean([t["device_io"]["write_MBps"][:n] for t in trials], axis=0)
    return reads, writes


def smooth(arr):
    if len(arr) < WINDOW:
        return arr
    return np.convolve(arr, np.ones(WINDOW) / WINDOW, mode="same")


def panel_throughput(ax, trials, ycsb, log=False):
    series = {side: mean_iostat(trials[side]) for side, _ in SIDES}
    n = min(len(r) for r, _ in series.values())
    # sample i is the mean over the second ending at i + 1; nothing has moved at x = 0
    x = np.concatenate([[0], np.arange(1, n + 1)])
    for side, style in SIDES:
        line = {**plot_style.LINE_STYLES[style], "marker": None, "linewidth": plt.rcParams["lines.linewidth"] + 1}
        r, w = series[side]
        ax.plot(x, np.concatenate([[0.0], smooth(r[:n])]), label=f"read ({label(side, ycsb)})", **{**line, "linestyle": "-"})
        ax.plot(x, np.concatenate([[0.0], smooth(w[:n])]), label=f"write ({label(side, ycsb)})", **{**line, "linestyle": "--"})
    ax.set_xlabel(plot_style.format_label("time (s)"))
    ax.set_ylabel(plot_style.format_label("bytes transferred (MB)"))
    ax.yaxis.set_label_coords(-0.16, 0.4)
    if log:  # the y-axis starts strictly at 10^0 MB; seconds that move less than 1 MB fall below it
        ax.set_yscale("log")
    plot_style.apply_plot_style(ax)
    ax.set_xlim(left=-0.03 * n)
    if log:
        ax.set_ylim(bottom=1.0)


def panel_throughput_log(ax, trials, ycsb):
    panel_throughput(ax, trials, ycsb, log=True)


def panel_latency(ax, trials, ycsb):
    ops = [op for op in OP_LABELS if op in trials["xbench"][0]["operations"]]
    positions = np.arange(len(ops))
    lowest = []
    for j, (side, style) in enumerate(SIDES):
        stats = [{key: statistics.mean(t["operations"][op][metric] for t in trials[side])
                  for key, metric in PERCENTILES} for op in ops]
        lowest += [s["whislo"] for s in stats]
        bp = ax.bxp(stats, positions=positions + (j - 0.5) * WIDTH, widths=WIDTH, patch_artist=True,
                    showfliers=False)
        for patch in bp["boxes"]:
            patch.set_facecolor(plot_style.BAR_STYLES[style]["facecolor"])
            patch.set_edgecolor(plot_style.BAR_STYLES[style]["edgecolor"])
            patch.set_hatch(plot_style.BAR_STYLES[style]["hatch"])
        for element in ("whiskers", "caps", "medians"):
            for item in bp[element]:
                item.set_color("black")
    ax.set_xticks(positions)
    ax.set_xticklabels([OP_LABELS[op] for op in ops])
    # range queries take milliseconds and inserts microseconds: a linear axis would hide the inserts
    ax.set_yscale("log")
    ax.set_ylabel(plot_style.format_label(r"latency ($\mu$s)"))
    plot_style.apply_plot_style(ax)
    ax.set_ylim(bottom=min(lowest) / 2)


def panel_data_movement(ax, trials, ycsb):
    categories = ["read", "write"]
    x = np.arange(len(categories)) * 0.75
    all_vals = []
    for j, (side, style) in enumerate(SIDES):
        vals = [statistics.mean(sum(t["rocksdb_statistics"][k] for k in tickers) for t in trials[side]) / BYTE_TO_GB
                for tickers in (READ_TICKERS, WRITE_TICKERS)]
        all_vals += vals
        ax.bar(x + (j - 0.5) * WIDTH, vals, WIDTH, label=label(side, ycsb), **plot_style.BAR_STYLES[style])
    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_xlim(x[0] - WIDTH - 0.12, x[-1] + WIDTH + 0.12)
    ax.set_ylabel(plot_style.format_label("bytes (GB)"))
    # YCSB-TS range queries read ~100x what the workload writes: a linear axis would hide the writes
    log = max(all_vals) / min(all_vals) > 20
    if log:
        ax.set_yscale("log")
    plot_style.apply_plot_style(ax)
    if log:
        ax.set_ylim(bottom=min(all_vals) / 3)


PANELS = (("throughput", panel_throughput, FIGSIZE), ("latency", panel_latency, FIGSIZE),
          ("data_movement", panel_data_movement, DATA_MOVEMENT_FIGSIZE))
# saved as separate PDFs but left out of the preview
EXTRA_PANELS = (("throughput_log", panel_throughput_log, FIGSIZE),)


def main():
    with open(EXEC_PATH) as f:
        results = json.load(f)
    os.makedirs(PLOT_DIR, exist_ok=True)
    handles = {}  # per YCSB derivative: one legend for YCSB-IVS, one for YCSB-TS
    for name, trials in results.items():
        if not all(trials.get(side) and "rocksdb_statistics" in trials[side][0] for side, _ in SIDES):
            print(f"skip {name}: no RocksDB statistics")
            continue
        ycsb = ycsb_name(name)
        for panel, draw, size in PANELS + EXTRA_PANELS:
            fig, ax = plt.subplots(figsize=size)
            draw(ax, trials, ycsb)
            for h, l in zip(*ax.get_legend_handles_labels()):
                handles.setdefault(ycsb, {}).setdefault(l, h)
            fig.subplots_adjust(left=LEFT_MARGIN_IN / size[0], right=0.97, bottom=0.24, top=0.97)
            fig.savefig(f"{PLOT_DIR}/{name}_{panel}.pdf")
            plt.close(fig)
        fig, axes = plt.subplots(1, 3, figsize=(13, 4.4), gridspec_kw={"width_ratios": [5, 5, 2]})
        for ax, (_, draw, _) in zip(axes, PANELS):
            draw(ax, trials, ycsb)
        for ax, tag in zip(axes, "ABC"):
            ax.text(-0.1, -0.36, f"({tag})", transform=ax.transAxes)
        fig.tight_layout()
        fig.savefig(f"{PLOT_DIR}/{name}_preview.png", dpi=120)
        plt.close(fig)
        print(f"{PLOT_DIR}/{name}_{{throughput,latency,data_movement}}.pdf, {name}_preview.png")
    for ycsb, entries in handles.items():
        legend_fig, legend_ax = plt.subplots()
        legend_ax.axis("off")
        legend_ax.legend(list(entries.values()), list(entries.keys()), loc="center", ncol=len(entries), frameon=False)
        plot_style.save_legend(legend_ax, f"{PLOT_DIR}/{ycsb.lower()}")
        plt.close(legend_fig)


if __name__ == "__main__":
    main()
