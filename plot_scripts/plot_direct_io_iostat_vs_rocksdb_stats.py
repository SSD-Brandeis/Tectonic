#!/usr/bin/env python3
"""
plot_direct_io_iostat_vs_rocksdb_stats.py
==========================================
Fig. 12-style plots of the WAL-off runs of the WAL gap matrix (run_scripts/run_wal_gap_matrix.sh, 2026-11-02/03),
drawn like the R1.O7 / mixgraph plots (plot_r1o7_fig12_style.py) and, in (C), with the "sum of (A)" marker of
plot_io_tool_match.py. With direct I/O on, WAL off (disable_wal=1) is the setting where iostat adds up to
RocksDB's counters best (2026-11-03: only these plots are kept; the other settings are in README.md / analysis.md).
One X-Bench run (one trial) per workload and device:
  (A) bytes transferred (MB) per second: iostat of the DB's device (one sample per second, no smoothing);
  (B) latency per operation type: p0, p25, p50, p75, p99 of the whole run (the executor's overall stats);
  (C) bytes RocksDB read and wrote (GB), from its statistics (the colleague's formula, as in the reruns):
      read  = rocksdb.non.last.level.read.bytes + rocksdb.last.level.read.bytes
      write = rocksdb.wal.bytes + rocksdb.flush.write.bytes + rocksdb.compact.write.bytes
      every bar labelled with its value, and a black marker at the sum of (A) (iostat's total).
Devices: loop = the DB on a device of its own ("own-device"); sda = the shared root disk, as in the experiments
("shared-sda").

Input : data/revision/R1.O7-R1.O9_direct-io_iostat-vs-rocksdb-stats/<machine>/<device>/nowal/<workload>/
Output: data/revision/R1.O7-R1.O9_direct-io_iostat-vs-rocksdb-stats/plots/
          <workload>_<own-device|shared-sda>_{preview.png,throughput.pdf,latency.pdf,data_movement.pdf},
          x-bench_legend.pdf
"""
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import re
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.append(str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "run_scripts"))
import plot_style
from run_ycsb_derivatives_fidelity import parse_execute_output

DATA = Path(REPO_ROOT + "/data/revision/R1.O7-R1.O9_direct-io_iostat-vs-rocksdb-stats")
PLOT_DIR = DATA / "plots"
CONFIG = "nowal"  # WAL off
DEVICE_LABELS = {"loop": "own device", "sda": "shared sda"}
DEVICE_NAMES = {"loop": "own-device", "sda": "shared-sda"}
STYLE = "X-Bench"  # X-Bench's colours in the R1.O7 plots
OP_LABELS = {"Insert": "insert", "Point Query": "PQ", "Update": "update", "Merge": "merge",
             "Range Query": "RQ", "Point Delete": "PD", "Range Delete": "RD"}
# mixgraph: db_bench's names and order, as plot_mixgraph_fig12_style.py
MIXGRAPH_OP_LABELS = {"Point Query": "get", "Insert": "put", "Range Query": "scan"}
PERCENTILES = (("whislo", "Minimum Latency"), ("q1", "25th Percentile Latency"),
               ("med", "50th Percentile Latency"), ("q3", "75th Percentile Latency"),
               ("whishi", "99th Percentile Latency"))
READ_TICKERS = ("rocksdb.non.last.level.read.bytes", "rocksdb.last.level.read.bytes")
WRITE_TICKERS = ("rocksdb.wal.bytes", "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes")
MB, GB = 2 ** 20, 2 ** 30  # iostat's MB is 2^20 bytes
FIGSIZE = (5, 3.6)
DATA_MOVEMENT_FIGSIZE = (2.8, 3.6)
LEFT_MARGIN_IN = 1.1
WIDTH = 0.25


def tickers(text):
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^(rocksdb\.[\w.\-]+) COUNT : (\d+)$", text, re.M)}


def iostat_series(path, device):
    """Read and write bytes of every 1 s sample of the device."""
    cols, reads, writes = None, [], []
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts and parts[0] == "Device":
            cols = parts
        elif cols and parts and parts[0] == device:
            reads.append(float(parts[cols.index("rkB/s")]) * 1024)
            writes.append(float(parts[cols.index("wkB/s")]) * 1024)
    return np.array(reads), np.array(writes)


def load():
    runs = {}  # (workload, device) -> run
    for machine in sorted(p for p in DATA.iterdir() if (p / "DONE").exists()):
        for o in sorted(machine.glob("*/*/*")):
            if not (o / "diskstats.after").exists():
                continue
            device, config, workload = o.relative_to(machine).parts
            if config != CONFIG:
                continue
            text = (o / "cli_raw.log").read_text(errors="replace")
            t = tickers(text)
            r, w = iostat_series(o / "iostat.txt", (o / "diskstats.before").read_text().split()[2])
            runs[(workload, device)] = {
                "host": machine.name, "workload": workload, "read_s": r, "write_s": w, "operations": parse_execute_output(text)[0],
                "c": (sum(t[k] for k in READ_TICKERS), sum(t[k] for k in WRITE_TICKERS))}
    return runs


def pct(a, c):
    return (a - c) / c * 100


def panel_throughput(ax, run):
    n = len(run["read_s"])
    x = np.arange(n + 1)  # sample i is the second ending at i + 1; nothing has moved at x = 0
    line = {**plot_style.LINE_STYLES[STYLE], "marker": None, "linewidth": plt.rcParams["lines.linewidth"] + 1}
    ax.plot(x, np.concatenate([[0.0], run["read_s"] / MB]), label="read (X-Bench)", **{**line, "linestyle": "-"})
    ax.plot(x, np.concatenate([[0.0], run["write_s"] / MB]), label="write (X-Bench)", **{**line, "linestyle": "--"})
    ax.set_xlabel(plot_style.format_label("time (s)"))
    ax.set_ylabel(plot_style.format_label("bytes transferred (MB)"))
    ax.yaxis.set_label_coords(-0.16, 0.4)
    plot_style.apply_plot_style(ax)
    plot_style.set_even_xticks(ax, n)


def panel_latency(ax, run):
    labels = MIXGRAPH_OP_LABELS if run["workload"] == "mixgraph" else OP_LABELS
    ops = [op for op in labels if op in run["operations"]]
    positions = np.arange(len(ops))
    stats = [{key: run["operations"][op][metric] for key, metric in PERCENTILES} for op in ops]
    bp = ax.bxp(stats, positions=positions, widths=WIDTH, patch_artist=True, showfliers=False)
    for patch in bp["boxes"]:
        patch.set_facecolor(plot_style.BAR_STYLES[STYLE]["facecolor"])
        patch.set_edgecolor(plot_style.BAR_STYLES[STYLE]["edgecolor"])
    for element in ("whiskers", "caps", "medians"):
        for item in bp[element]:
            item.set_color("black")
    ax.set_xticks(positions)
    ax.set_xticklabels([labels[op] for op in ops])
    ax.set_xlim(-0.6, len(ops) - 0.4)
    ax.set_yscale("log")
    ax.set_ylabel(plot_style.format_label(r"latency ($\mu$s)"))
    plot_style.apply_plot_style(ax)
    ax.set_ylim(bottom=min(s["whislo"] for s in stats) / 2)


def panel_data_movement(ax, run):
    x = np.arange(2) * 0.75
    vals = [v / GB for v in run["c"]]
    tool = [run["read_s"].sum() / GB, run["write_s"].sum() / GB]
    bars = ax.bar(x, vals, WIDTH * 1.6, label="X-Bench (RocksDB stats)", **plot_style.BAR_STYLES[STYLE])
    ax.scatter(x, tool, marker="_", s=900, linewidths=3, color="black", zorder=3, label="sum of (A)")
    ax.set_xticks(x)
    ax.set_xticklabels(["read", "write"])
    ax.set_xlim(x[0] - WIDTH - 0.12, x[-1] + WIDTH + 0.12)
    ax.set_ylabel(plot_style.format_label("bytes (GB)"))
    every = vals + tool
    log = max(every) / min(every) > 20  # as plot_r1o7_fig12_style.py: reads can be ~100x the writes
    if log:
        ax.set_yscale("log")
    plot_style.apply_plot_style(ax)
    if log:
        ax.set_ylim(bottom=min(every) / 3)
    for b, v, a in zip(bars, vals, tool):  # the bar's value above the bar or the marker, whichever is higher
        ax.annotate(f"{v:#.3g}".rstrip("."), (b.get_x() + b.get_width() / 2, max(v, a)), xytext=(0, 6),
                    textcoords="offset points", ha="center", va="bottom", rotation=90, fontsize=14)
    lo, hi = ax.get_ylim()[0], max(every)
    if log:
        ax.set_ylim(lo, 10 ** (np.log10(lo) + (np.log10(hi) - np.log10(lo)) / 0.62))
    else:
        ax.set_ylim(lo, hi / 0.62)


PANELS = (("throughput", panel_throughput, FIGSIZE), ("latency", panel_latency, FIGSIZE),
          ("data_movement", panel_data_movement, DATA_MOVEMENT_FIGSIZE))


def run_figures(runs):
    handles = {}
    for (workload, device), run in sorted(runs.items()):
        name = f"{workload}_{DEVICE_NAMES[device]}"
        for panel, draw, size in PANELS:
            fig, ax = plt.subplots(figsize=size)
            draw(ax, run)
            for h, l in zip(*ax.get_legend_handles_labels()):
                handles.setdefault(l, h)
            fig.subplots_adjust(left=LEFT_MARGIN_IN / size[0], right=0.97, bottom=0.24, top=0.97)
            fig.savefig(PLOT_DIR / f"{name}_{panel}.pdf")
            plt.close(fig)
        fig, axes = plt.subplots(1, 3, figsize=(13, 5.6), gridspec_kw={"width_ratios": [5, 5, 2]})
        for ax, (_, draw, _) in zip(axes, PANELS):
            draw(ax, run)
        for ax, tag in zip(axes, "ABC"):
            ax.text(-0.1, -0.36, f"({tag})", transform=ax.transAxes)
        a = [v / GB for v in (run["read_s"].sum(), run["write_s"].sum())]
        c = [v / GB for v in run["c"]]
        fig.suptitle(f"{workload}, WAL off, DB on {DEVICE_LABELS[device]} ({run['host']}), direct I/O\n"
                     f"sum of (A): read {a[0]:,.2f} GB ({pct(a[0], c[0]):+.1f}\\% vs (C)), "
                     f"write {a[1]:,.2f} GB ({pct(a[1], c[1]):+.1f}\\% vs (C))", fontsize=15, y=0.99)
        fig.legend(*zip(*[(h, l) for l, h in handles.items()]), loc="upper center", ncol=4, frameon=False,
                   fontsize=12, bbox_to_anchor=(0.5, 0.87))
        fig.subplots_adjust(left=0.08, right=0.98, bottom=0.25, top=0.76, wspace=0.42)
        fig.savefig(PLOT_DIR / f"{name}_preview.png", dpi=120)
        plt.close(fig)
        print(PLOT_DIR / f"{name}_{{preview.png,throughput,latency,data_movement}}.pdf")
    legend_fig, legend_ax = plt.subplots()
    legend_ax.axis("off")
    legend_ax.legend(list(handles.values()), list(handles.keys()), loc="center", ncol=len(handles), frameon=False)
    plot_style.save_legend(legend_ax, str(PLOT_DIR / "x-bench"))  # -> x-bench_legend.pdf
    plt.close(legend_fig)


def main():
    PLOT_DIR.mkdir(exist_ok=True)
    runs = load()
    run_figures(runs)


if __name__ == "__main__":
    main()
