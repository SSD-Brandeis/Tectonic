#!/usr/bin/env python3
"""
plot_mixgraph_fig12_style.py
============================
R1.O9 / R2.O1 / R2.Q1: X-Bench vs. the db_bench mixgraph (Prefix_dist) trace on RocksDB, plotted like
Fig. 12 of the paper and like the R1.O7 plots (plot_r1o7_fig12_style.py: same panels, style and metrics):
  (A) bytes transferred (MB) over time: iostat of the database device, mean over the trials of each
      trace for every second (one iostat sample per second, no smoothing);
  (B) latency per operation type: p0, p25, p50, p75, p99 of the whole run (mean over the trials);
  (C) bytes RocksDB read and wrote (GB), from its statistics (mean over the trials):
      read  = rocksdb.bytes.read + rocksdb.db.iter.bytes.read + rocksdb.compact.read.bytes
      write = rocksdb.wal.bytes + rocksdb.flush.write.bytes + rocksdb.compact.write.bytes
Two extra panels per operation type (get, put, scan) and for all operations together:
  operation count, as the executor counted it (the same in every trial), and
  throughput (kOPS) = operation count / end-to-end time of the trial (mean over the trials)
Every log y-axis starts at 10^0.

Input : data/revision/R1.O9-R2.O1_mixgraph/results/execution.json (execute stage of
        run_scripts/run_mixgraph_fidelity.py)
Output: data/revision/R1.O9-R2.O1_mixgraph/plots/mixgraph_{throughput,latency,data_movement}.pdf,
        plots/mixgraph_throughput_log.pdf ((A) on a log y-axis starting at 10^0 MB),
        plots/mixgraph_op_count.pdf, plots/mixgraph_op_throughput.pdf (the two extra panels),
        plots/mixgraph_legend.pdf, plots/mixgraph_preview.png (the three panels side by side)
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

OUT_DIR = "/home/cc/Tectonic/data/revision/R1.O9-R2.O1_mixgraph"
EXEC_PATH = f"{OUT_DIR}/results/execution.json"
PLOT_DIR = f"{OUT_DIR}/plots"

# the db_bench trace is the reference (grey, as the derivative in the R1.O7 plots), X-Bench in blue
SIDES = (("dbbench", "YCSB", "db_bench"), ("xbench", "Tectonic", "X-Bench"))
# the executor's operation names, labelled with db_bench's (a Seek and the Next calls after it: a scan)
OP_LABELS = {"Point Query": "get", "Insert": "put", "Range Query": "scan"}
PERCENTILES = (("whislo", "Minimum Latency"), ("q1", "25th Percentile Latency"),
               ("med", "50th Percentile Latency"), ("q3", "75th Percentile Latency"),
               ("whishi", "99th Percentile Latency"))
READ_TICKERS = ("rocksdb.bytes.read", "rocksdb.db.iter.bytes.read", "rocksdb.compact.read.bytes")
WRITE_TICKERS = ("rocksdb.wal.bytes", "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes")
BYTE_TO_GB = 1024 ** 3
FIGSIZE = (5, 3.6)
DATA_MOVEMENT_FIGSIZE = (2.8, 3.6)
OP_FIGSIZE = (4.2, 3.6)
LEFT_MARGIN_IN = 1.1  # room for the y tick labels and the y label at the style's 22 pt
WIDTH = 0.25


def mean_iostat(trials):
    """Read and write MB/s of every second, mean over the trials (cut to the shortest)."""
    n = min(len(t["device_io"]["read_MBps"]) for t in trials)
    reads = np.mean([t["device_io"]["read_MBps"][:n] for t in trials], axis=0)
    writes = np.mean([t["device_io"]["write_MBps"][:n] for t in trials], axis=0)
    return reads, writes


def panel_throughput(ax, trials, log=False):
    series = {side: mean_iostat(trials[side]) for side, _, _ in SIDES}
    n = min(len(r) for r, _ in series.values())
    # sample i is the mean over the second ending at i + 1; nothing has moved at x = 0
    x = np.concatenate([[0], np.arange(1, n + 1)])
    for side, style, name in SIDES:
        line = {**plot_style.LINE_STYLES[style], "marker": None, "linewidth": plt.rcParams["lines.linewidth"] + 1}
        r, w = series[side]
        ax.plot(x, np.concatenate([[0.0], r[:n]]), label=f"read ({name})", **{**line, "linestyle": "-"})
        ax.plot(x, np.concatenate([[0.0], w[:n]]), label=f"write ({name})", **{**line, "linestyle": "--"})
    ax.set_xlabel(plot_style.format_label("time (s)"))
    ax.set_ylabel(plot_style.format_label("bytes transferred (MB)"))
    ax.yaxis.set_label_coords(-0.16, 0.4)
    if log:  # the y-axis starts strictly at 10^0 MB; seconds that move less than 1 MB fall below it
        ax.set_yscale("log")
    plot_style.apply_plot_style(ax)
    ax.set_xlim(left=-0.03 * n)
    if log:
        ax.set_ylim(bottom=1.0)


def panel_throughput_log(ax, trials):
    panel_throughput(ax, trials, log=True)


def panel_latency(ax, trials):
    ops = [op for op in OP_LABELS if op in trials["xbench"][0]["operations"]]
    positions = np.arange(len(ops))
    for j, (side, style, _) in enumerate(SIDES):
        stats = [{key: statistics.mean(t["operations"][op][metric] for t in trials[side])
                  for key, metric in PERCENTILES} for op in ops]
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
    # scans take milliseconds and gets microseconds: a linear axis would hide the gets
    ax.set_yscale("log")
    ax.set_ylabel(plot_style.format_label(r"latency ($\mu$s)"))
    plot_style.apply_plot_style(ax)
    ax.set_ylim(bottom=1.0)


def panel_data_movement(ax, trials):
    categories = ["read", "write"]
    x = np.arange(len(categories)) * 0.75
    all_vals = []
    for j, (side, style, name) in enumerate(SIDES):
        vals = [statistics.mean(sum(t["rocksdb_statistics"][k] for k in tickers) for t in trials[side]) / BYTE_TO_GB
                for tickers in (READ_TICKERS, WRITE_TICKERS)]
        all_vals += vals
        ax.bar(x + (j - 0.5) * WIDTH, vals, WIDTH, label=name, **plot_style.BAR_STYLES[style])
    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_xlim(x[0] - WIDTH - 0.12, x[-1] + WIDTH + 0.12)
    ax.set_ylabel(plot_style.format_label("bytes (GB)"))
    # the seeks read ~30x what the workload writes: a linear axis would hide the writes
    log = max(all_vals) / min(all_vals) > 20
    if log:
        ax.set_yscale("log")
    plot_style.apply_plot_style(ax)
    if log:
        ax.set_ylim(bottom=1.0)


def panel_per_operation(ax, trials, value, ylabel):
    ops = [op for op in OP_LABELS if op in trials["xbench"][0]["operations"]] + ["all"]
    x = np.arange(len(ops))
    highest = 0
    for j, (side, style, name) in enumerate(SIDES):
        vals = [statistics.mean(value(t, op) for t in trials[side]) for op in ops]
        highest = max(highest, *vals)
        ax.bar(x + (j - 0.5) * WIDTH, vals, WIDTH, label=name, **plot_style.BAR_STYLES[style])
    ax.set_xticks(x)
    ax.set_xticklabels([OP_LABELS.get(op, op) for op in ops])
    ax.set_ylabel(plot_style.format_label(ylabel))
    # gets outnumber scans ~28x: a linear axis would hide the scans
    ax.set_yscale("log")
    plot_style.apply_plot_style(ax)
    # room above the tallest bar ("all"): it reaches 80% of the axis height
    ax.set_ylim(bottom=1.0, top=highest ** (1 / 0.8))


def op_count(trial, op):
    return trial["overall"]["Total Operations"] if op == "all" else trial["operations"][op]["Count"]


def panel_op_count(ax, trials):
    panel_per_operation(ax, trials, op_count, "operation count")


def panel_op_throughput(ax, trials):
    panel_per_operation(ax, trials, lambda t, op: op_count(t, op) / t["overall"]["End to End Time"] / 1000,
                        "throughput (kOPS)")


PANELS = (("throughput", panel_throughput, FIGSIZE), ("latency", panel_latency, FIGSIZE),
          ("data_movement", panel_data_movement, DATA_MOVEMENT_FIGSIZE))
# saved as separate PDFs but left out of the preview
EXTRA_PANELS = (("throughput_log", panel_throughput_log, FIGSIZE), ("op_count", panel_op_count, OP_FIGSIZE),
                ("op_throughput", panel_op_throughput, OP_FIGSIZE))


def main():
    with open(EXEC_PATH) as f:
        trials = json.load(f)
    if not all(trials.get(side) for side, _, _ in SIDES):
        sys.exit("execution.json has no trials for every trace yet")
    os.makedirs(PLOT_DIR, exist_ok=True)
    handles = {}
    for panel, draw, size in PANELS + EXTRA_PANELS:
        fig, ax = plt.subplots(figsize=size)
        draw(ax, trials)
        for h, l in zip(*ax.get_legend_handles_labels()):
            handles.setdefault(l, h)
        fig.subplots_adjust(left=LEFT_MARGIN_IN / size[0], right=0.97, bottom=0.24, top=0.97)
        fig.savefig(f"{PLOT_DIR}/mixgraph_{panel}.pdf")
        plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4), gridspec_kw={"width_ratios": [5, 5, 2]})
    for ax, (_, draw, _) in zip(axes, PANELS):
        draw(ax, trials)
    for ax, tag in zip(axes, "ABC"):
        ax.text(-0.1, -0.36, f"({tag})", transform=ax.transAxes)
    fig.tight_layout()
    fig.savefig(f"{PLOT_DIR}/mixgraph_preview.png", dpi=120)
    plt.close(fig)
    legend_fig, legend_ax = plt.subplots()
    legend_ax.axis("off")
    legend_ax.legend(list(handles.values()), list(handles.keys()), loc="center", ncol=len(handles), frameon=False)
    plot_style.save_legend(legend_ax, f"{PLOT_DIR}/mixgraph")
    plt.close(legend_fig)
    print(f"{PLOT_DIR}/mixgraph_{{throughput,latency,data_movement}}.pdf, mixgraph_legend.pdf, mixgraph_preview.png "
          f"({', '.join(f'{name}: {len(trials[side])} trials' for side, _, name in SIDES)})")


if __name__ == "__main__":
    main()
