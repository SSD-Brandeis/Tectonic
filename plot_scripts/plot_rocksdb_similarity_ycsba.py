#!/usr/bin/env python3
import os
import sys
import json
import re
import csv
from collections import defaultdict
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager
# Font setup via plot_style
import plot_style


STATS_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/cc/Tectonic/data/rocksdb_similarity_ycsba")
OUTPUT_NAME = sys.argv[2] if len(sys.argv) > 2 else "rocksdb_similarity_ycsba_accuracy"
OUTPUT_DIR = Path("/home/cc/Tectonic/experiment_plots")
OUTPUT_DIR.mkdir(exist_ok=True)

BYTE_TO_GB = 1024**3
CONVERT_TO_MILLION = 1_000_000

COUNT_LINE = re.compile(r"^(rocksdb\.[A-Za-z0-9\.\-_]+)\s+COUNT\s*:\s*([0-9]+)\s*$")
COUNT_IN_METRIC = re.compile(r"^(rocksdb\.[A-Za-z0-9\.\-_]+)\s+.*?\bCOUNT\s*:\s*([0-9]+)\b")

def select_disk(disks):
    # Loop devices (snap/squashfs mounts) are always idle; skip them so we
    # pick the real backing disk regardless of its position/name in the list.
    candidates = [d for d in disks if not d.get("disk_device", "").startswith("loop")]
    if candidates:
        return candidates[0]
    return disks[0] if disks else {}

def load_iostat(path):
    with open(path) as f:
        data = json.load(f)
    stats = data["sysstat"]["hosts"][0]["statistics"]
    # disk stats list
    # kB_read/s to MB/s: divide by 1024.0
    reads = [
        float(select_disk(s.get("disk", [])).get("kB_read/s", 0.0)) / 1024.0
        for s in stats
    ]
    writes = [
        float(select_disk(s.get("disk", [])).get("kB_wrtn/s", 0.0)) / 1024.0
        for s in stats
    ]
    return np.array(reads), np.array(writes)

def collect_iostat_runs(system):
    paths = sorted(STATS_DIR.glob(f"iostat.{system}.*.json"))
    runs = [load_iostat(p) for p in paths]
    return runs

def average_iostat_runs(runs):
    if not runs:
        return np.array([]), np.array([])
    min_len = min(len(r) for r, _ in runs)
    avg_reads = np.mean([r[:min_len] for r, _ in runs], axis=0)
    avg_writes = np.mean([w[:min_len] for _, w in runs], axis=0)
    return avg_reads, avg_writes

RAW_OP_TO_KEY = {"insert": "insert", "update": "update", "point_query": "pointquery"}

def load_raw_latencies(system):
    # op_type -> list of latency_ns, pooled across all raw CSV runs for this system
    op_latencies_ns = defaultdict(list)
    paths = sorted(STATS_DIR.glob(f"op-latency-raw.{system}.*.csv"))
    for p in paths:
        with open(p, newline='') as f:
            reader = csv.reader(f)
            next(reader, None)  # header: op_type,latency_ns
            for op_type, latency_ns in reader:
                op_latencies_ns[op_type].append(latency_ns)
    return {op: np.array(vals, dtype=np.int64) for op, vals in op_latencies_ns.items()}

def average_latency_percentiles(system):
    raw = load_raw_latencies(system)
    avg_data = {}
    for op_type, key in RAW_OP_TO_KEY.items():
        samples_ns = raw.get(op_type)
        if samples_ns is None or samples_ns.size == 0:
            continue
        samples_us = samples_ns / 1000.0  # ns -> us
        p0, p25, p50, p75, p99 = np.percentile(samples_us, [0, 25, 50, 75, 99])
        avg_data[key] = {"p0": p0, "p25": p25, "p50": p50, "p75": p75, "p99": p99}
    return avg_data

def parse_stats_file(path: Path) -> dict:
    metrics = {}
    with open(path, "r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            m = COUNT_LINE.match(line)
            if m:
                metrics[m.group(1)] = int(m.group(2))
                continue
            m2 = COUNT_IN_METRIC.match(line)
            if m2:
                metrics[m2.group(1)] = int(m2.group(2))
    return metrics

def avg_metrics(system: str) -> dict:
    files = sorted(STATS_DIR.glob(f"stats.{system}.*.json"))
    if not files:
        raise RuntimeError(f"No stats files for system '{system}'")
    acc = {}
    count = 0
    for f in files:
        metrics = parse_stats_file(f)
        if not metrics:
            continue
        count += 1
        for k, v in metrics.items():
            acc[k] = acc.get(k, 0) + v
    if count == 0:
        raise RuntimeError(f"No parsable stats for system '{system}'")
    return {k: v / count for k, v in acc.items()}

FIGSIZE = (5, 3.6)

def save_fixed_size(fig, path):
    # Lock every subplot PDF to exactly FIGSIZE inches, regardless of dataset
    # content: bbox_inches="tight" crops to the rendered content extent, which
    # drifts slightly between datasets (different tick-label digit counts,
    # etc.), so page sizes end up inconsistent across runs. Fixed margins
    # keep the page size identical while still leaving room for labels.
    fig.subplots_adjust(left=0.28, right=0.97, bottom=0.24, top=0.97)
    fig.savefig(path)
    plt.close(fig)

def nudge_zero_xtick(ax):
    # Separate the x-axis "0" label from the y-axis "0" label at the origin
    # corner by left-aligning it (text starts at the tick, extends right)
    # instead of the default center alignment.
    for tick_val, label in zip(ax.get_xticks(), ax.get_xticklabels()):
        if abs(tick_val) < 1e-9:
            label.set_ha('left')

def main():
    print("Parsing experiment data...")

    # 1. IOSTAT throughput over time (Subplot A)
    tec_iostat_runs = collect_iostat_runs("tectonic")
    ycsb_iostat_runs = collect_iostat_runs("ycsb")
    r_tec, w_tec = average_iostat_runs(tec_iostat_runs)
    r_ycsb, w_ycsb = average_iostat_runs(ycsb_iostat_runs)

    # 2. Latency boxplots (Subplot B)
    tec_latency = average_latency_percentiles("tectonic")
    ycsb_latency = average_latency_percentiles("ycsb")

    # 3. Read/Write Data Movement (Subplot C)
    m_tec = avg_metrics("tectonic")
    m_ycsb = avg_metrics("ycsb")

    # Calculate bytes
    bytes_read_y = (m_ycsb.get("rocksdb.bytes.read", 0) + m_ycsb.get("rocksdb.compact.read.bytes", 0)) / BYTE_TO_GB
    bytes_written_y = (
        m_ycsb.get("rocksdb.wal.bytes", 0)
        + m_ycsb.get("rocksdb.flush.write.bytes", 0)
        + m_ycsb.get("rocksdb.compact.write.bytes", 0)
    ) / BYTE_TO_GB
    ycsb_bytes_vals = np.array([bytes_read_y, bytes_written_y])

    bytes_read_t = (m_tec.get("rocksdb.bytes.read", 0) + m_tec.get("rocksdb.compact.read.bytes", 0)) / BYTE_TO_GB
    bytes_written_t = (
        m_tec.get("rocksdb.wal.bytes", 0)
        + m_tec.get("rocksdb.flush.write.bytes", 0)
        + m_tec.get("rocksdb.compact.write.bytes", 0)
    ) / BYTE_TO_GB
    tec_bytes_vals = np.array([bytes_read_t, bytes_written_t])

    # 4. Cache hit/miss counts (Subplot D)
    ycsb_cache_vals = np.array([
        m_ycsb.get("rocksdb.block.cache.hit", 0),
        m_ycsb.get("rocksdb.block.cache.miss", 0)
    ]) / CONVERT_TO_MILLION

    tec_cache_vals = np.array([
        m_tec.get("rocksdb.block.cache.hit", 0),
        m_tec.get("rocksdb.block.cache.miss", 0)
    ]) / CONVERT_TO_MILLION

    print("Data parsing complete. Plotting...")
    saved_paths = []

    # ------------------ (A) THROUGHPUT OVER TIME ------------------
    fig, ax = plt.subplots(figsize=FIGSIZE)
    n_tec = len(r_tec)
    n_ycsb = len(r_ycsb)
    n = min(n_tec, n_ycsb)

    # Apply rolling average smoothing to reduce compaction noise
    WINDOW = 3  # 3-second rolling average
    def smooth(arr, window=WINDOW):
        if len(arr) < window:
            return arr
        kernel = np.ones(window) / window
        # 'same' mode keeps the output length equal to input length
        return np.convolve(arr, kernel, mode='same')

    sr_ycsb = smooth(r_ycsb[:n])
    sw_ycsb = smooth(w_ycsb[:n])
    sr_tec  = smooth(r_tec[:n])
    sw_tec  = smooth(w_tec[:n])

    # Each iostat sample is the average throughput over the 1-second interval
    # ending at that second, so sample i truthfully belongs at x=i+1. Prepend
    # a real (0, 0) point at x=0: zero bytes have moved before the run starts.
    x = np.concatenate([[0], np.arange(1, n + 1)])
    sr_ycsb = np.concatenate([[0.0], sr_ycsb])
    sw_ycsb = np.concatenate([[0.0], sw_ycsb])
    sr_tec  = np.concatenate([[0.0], sr_tec])
    sw_tec  = np.concatenate([[0.0], sw_tec])

    # Apply standard LINE_STYLES, but without markers and with a thicker line
    # for this plot specifically (too many samples for markers to read well).
    no_marker_style = lambda key: {**plot_style.LINE_STYLES[key], "marker": None,
                                    "linewidth": plt.rcParams["lines.linewidth"] + 1}
    ax.plot(x, sr_ycsb, label="read (YCSB)", **{**no_marker_style('YCSB'), "linestyle": "-"})
    ax.plot(x, sw_ycsb, label="write (YCSB)", **{**no_marker_style('YCSB'), "linestyle": "--"})
    ax.plot(x, sr_tec, label="read (X-Bench)", **{**no_marker_style('Tectonic'), "linestyle": "-"})
    ax.plot(x, sw_tec, label="write (X-Bench)", **{**no_marker_style('Tectonic'), "linestyle": "--"})

    ax.set_xlabel(plot_style.format_label("time (s)"))
    ax.set_ylabel(plot_style.format_label("bytes transferred (MB/s)"))
    # This label is long enough that, centered by default, its top overflows
    # the fixed page's slim top margin and gets clipped. Shift the label's
    # vertical anchor down so it fits within the page.
    ax.yaxis.set_label_coords(-0.16, 0.4)
    plot_style.apply_plot_style(ax)
    # Give the left edge a little breathing room so the line/markers at x=0
    # aren't drawn flush against the spine (which visually "cuts off" them).
    ax.set_xlim(left=-0.03 * n)
    nudge_zero_xtick(ax)
    throughput_handles, throughput_labels = ax.get_legend_handles_labels()
    path = OUTPUT_DIR / f"{OUTPUT_NAME}_throughput.pdf"
    save_fixed_size(fig, path)
    saved_paths.append(path)

    # ------------------ (B) OP LATENCY BOXPLOTS ------------------
    fig, ax = plt.subplots(figsize=FIGSIZE)
    operations = ["insert", "pointquery", "update"]
    positions = np.arange(len(operations))
    width = 0.25

    ycsb_box_stats = []
    tec_box_stats = []
    for op in operations:
        ycsb_box_stats.append({
            "med": ycsb_latency[op]["p50"],
            "q1": ycsb_latency[op]["p25"],
            "q3": ycsb_latency[op]["p75"],
            "whislo": ycsb_latency[op]["p0"],
            "whishi": ycsb_latency[op]["p99"],
            "label": "insert" if op == "insert" else ("PQ" if op == "pointquery" else "update")
        })
        tec_box_stats.append({
            "med": tec_latency[op]["p50"],
            "q1": tec_latency[op]["p25"],
            "q3": tec_latency[op]["p75"],
            "whislo": tec_latency[op]["p0"],
            "whishi": tec_latency[op]["p99"],
            "label": "insert" if op == "insert" else ("PQ" if op == "pointquery" else "update")
        })

    bp_ycsb = ax.bxp(ycsb_box_stats, positions=positions - width/2, widths=0.25, patch_artist=True, showfliers=False)
    for patch in bp_ycsb["boxes"]:
        patch.set_facecolor("white")
        patch.set_edgecolor("grey")
        patch.set_hatch("///")
    for element in ["whiskers", "caps", "medians"]:
        for item in bp_ycsb[element]:
            item.set_color("black")

    bp_tec = ax.bxp(tec_box_stats, positions=positions + width/2, widths=0.25, patch_artist=True, showfliers=False)
    for patch in bp_tec["boxes"]:
        patch.set_facecolor(plot_style.BAR_STYLES['Tectonic']["facecolor"])
        patch.set_edgecolor(plot_style.BAR_STYLES['Tectonic']["edgecolor"])
    for element in ["whiskers", "caps", "medians"]:
        for item in bp_tec[element]:
            item.set_color("black")

    ax.set_xticks(positions)
    ax.set_xticklabels(["insert", "PQ", "update"])
    ax.set_ylabel(plot_style.format_label("latency (us)"))
    plot_style.apply_plot_style(ax)
    path = OUTPUT_DIR / f"{OUTPUT_NAME}_latency.pdf"
    save_fixed_size(fig, path)
    saved_paths.append(path)

    # ------------------ (C) DATA MOVEMENT ------------------
    DATA_MOVEMENT_FIGSIZE = (2, 3.6)  # narrower than the shared FIGSIZE
    fig, ax = plt.subplots(figsize=DATA_MOVEMENT_FIGSIZE)
    categories = ["read", "write"]
    category_spacing = 0.55  # tighter gap between the two bar groups
    x = np.arange(len(categories)) * category_spacing

    ax.bar(x - width/2, ycsb_bytes_vals, width, label="YCSB", **plot_style.BAR_STYLES['YCSB'])
    ax.bar(x + width/2, tec_bytes_vals, width, label="X-Bench", **plot_style.BAR_STYLES['Tectonic'])

    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    edge_pad = 0.12  # snug against the bars to minimize edge whitespace
    ax.set_xlim(x[0] - width - edge_pad, x[-1] + width + edge_pad)
    ax.set_ylabel(plot_style.format_label("bytes (GB)"))
    plot_style.apply_plot_style(ax)
    bar_handles, bar_labels = ax.get_legend_handles_labels()
    path = OUTPUT_DIR / f"{OUTPUT_NAME}_data_movement.pdf"
    save_fixed_size(fig, path)
    saved_paths.append(path)

    # ------------------ (D) CACHE BEHAVIOR ------------------
    fig, ax = plt.subplots(figsize=FIGSIZE)
    categories = ["cache hit", "cache miss"]
    x = np.arange(len(categories))

    ax.bar(x - width/2, ycsb_cache_vals, width, label="YCSB", **plot_style.BAR_STYLES['YCSB'])
    ax.bar(x + width/2, tec_cache_vals, width, label="X-Bench", **plot_style.BAR_STYLES['Tectonic'])

    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_ylabel(plot_style.format_label("count (millions)"))
    plot_style.apply_plot_style(ax)
    path = OUTPUT_DIR / f"{OUTPUT_NAME}_cache.pdf"
    save_fixed_size(fig, path)
    saved_paths.append(path)

    # ------------------ LEGEND (separate PDF, shared across subplots) ------------------
    all_handles = list(throughput_handles) + list(bar_handles)
    all_labels = list(throughput_labels) + list(bar_labels)
    legend_fig, legend_ax = plt.subplots()
    legend_ax.axis("off")
    legend_ax.legend(all_handles, all_labels, loc="center", ncol=len(all_labels), frameon=False)
    plot_style.save_legend(legend_ax, str(OUTPUT_DIR / OUTPUT_NAME))
    plt.close(legend_fig)
    saved_paths.append(OUTPUT_DIR / f"{OUTPUT_NAME}_legend.pdf")

    for path in saved_paths:
        print(f"Plot saved to: {path}")

if __name__ == "__main__":
    main()
