#!/usr/bin/env python3
import os
import json
import re
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager

# Font setup via plot_style
import plot_style


STATS_DIR = Path("/home/cc/Tectonic/data/rocksdb_similarity_ycsba")
OUTPUT_DIR = Path("/home/cc/Tectonic/experiment_plots")
OUTPUT_DIR.mkdir(exist_ok=True)

BYTE_TO_GB = 1024**3
CONVERT_TO_MILLION = 1_000_000

COUNT_LINE = re.compile(r"^(rocksdb\.[A-Za-z0-9\.\-_]+)\s+COUNT\s*:\s*([0-9]+)\s*$")
COUNT_IN_METRIC = re.compile(r"^(rocksdb\.[A-Za-z0-9\.\-_]+)\s+.*?\bCOUNT\s*:\s*([0-9]+)\b")

def load_iostat(path):
    with open(path) as f:
        data = json.load(f)
    stats = data["sysstat"]["hosts"][0]["statistics"]
    # disk stats list
    # kB_read/s to MB/s: divide by 1024.0
    reads = [
        float(s.get("disk", [{}])[0].get("kB_read/s", 0.0)) / 1024.0
        for s in stats
    ]
    writes = [
        float(s.get("disk", [{}])[0].get("kB_wrtn/s", 0.0)) / 1024.0
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

def parse_latency_file(filepath):
    data = {}
    current_op = None
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if ',' not in line:
                op_name = line.split(' ')[0].lower() # e.g. "insert", "point" -> "pointquery", "update"
                if op_name == "point":
                    op_name = "pointquery"
                current_op = op_name
                data[current_op] = {}
            else:
                parts = line.split(',')
                if len(parts) == 2 and current_op is not None:
                    pct = parts[0]
                    # val is in ns, convert to us (microseconds)
                    val = float(parts[1]) / 1000.0
                    data[current_op][pct] = val
    return data

def average_latency_percentiles(system):
    op_stats = {"insert": [], "pointquery": [], "update": []}
    paths = sorted(STATS_DIR.glob(f"op-latency.{system}.*.json"))
    for p in paths:
        run_data = parse_latency_file(p)
        for op in op_stats:
            if op in run_data:
                op_stats[op].append(run_data[op])
    
    avg_data = {}
    for op in op_stats:
        if not op_stats[op]:
            continue
        avg_data[op] = {}
        for pct in ["p0", "p25", "p50", "p75", "p99"]:
            avg_data[op][pct] = np.mean([run[pct] for run in op_stats[op]])
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
    fig, axs = plt.subplots(1, 4, figsize=(18, 4.5))
    
    # ------------------ (A) THROUGHPUT OVER TIME ------------------
    ax = axs[0]
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
    
    # Decimate marker frequency so they don't overlap, using markevery
    x = np.arange(n)
    
    # Apply standard LINE_STYLES
    ax.plot(x, sr_ycsb, label="read (YCSB)", markevery=max(1, n//10), **{**plot_style.LINE_STYLES['YCSB'], "linestyle": "-"})
    ax.plot(x, sw_ycsb, label="write (YCSB)", markevery=max(1, n//10), **{**plot_style.LINE_STYLES['YCSB'], "linestyle": "--"})
    ax.plot(x, sr_tec, label="read (Tectonic)", markevery=max(1, n//10), **{**plot_style.LINE_STYLES['Tectonic'], "linestyle": "-"})
    ax.plot(x, sw_tec, label="write (Tectonic)", markevery=max(1, n//10), **{**plot_style.LINE_STYLES['Tectonic'], "linestyle": "--"})
    
    ax.set_xlabel(plot_style.format_label("time (s)"))
    ax.set_ylabel(plot_style.format_label("MB/s"))
    ax.set_title(plot_style.format_label("(a) throughput over time"), pad=10)
    ax.text(-0.15, 1.05, plot_style.format_label("(a)"), transform=ax.transAxes, fontsize=16, fontweight='bold', va='top', ha='right')
    plot_style.apply_plot_style(ax)
    
    # ------------------ (B) OP LATENCY BOXPLOTS ------------------
    ax = axs[1]
    operations = ["insert", "pointquery", "update"]
    positions = np.arange(len(operations))
    width = 0.35
    
    ycsb_box_stats = []
    tec_box_stats = []
    for op in operations:
        ycsb_box_stats.append({
            "med": ycsb_latency[op]["p50"],
            "q1": ycsb_latency[op]["p25"],
            "q3": ycsb_latency[op]["p75"],
            "whislo": ycsb_latency[op]["p0"],
            "whishi": ycsb_latency[op]["p99"],
            "label": "insert" if op == "insert" else ("point\nquery" if op == "pointquery" else "update")
        })
        tec_box_stats.append({
            "med": tec_latency[op]["p50"],
            "q1": tec_latency[op]["p25"],
            "q3": tec_latency[op]["p75"],
            "whislo": tec_latency[op]["p0"],
            "whishi": tec_latency[op]["p99"],
            "label": "insert" if op == "insert" else ("point\nquery" if op == "pointquery" else "update")
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
        patch.set_facecolor("tab:red")
        patch.set_edgecolor("tab:red")
    for element in ["whiskers", "caps", "medians"]:
        for item in bp_tec[element]:
            item.set_color("black")
            
    ax.set_xticks(positions)
    ax.set_xticklabels(["insert", "point\nquery", "update"])
    ax.set_ylabel(plot_style.format_label("latency (us)"))
    ax.set_title(plot_style.format_label("(b) latency boxplots"), pad=10)
    ax.text(-0.15, 1.05, plot_style.format_label("(b)"), transform=ax.transAxes, fontsize=16, fontweight='bold', va='top', ha='right')
    plot_style.apply_plot_style(ax)
 
    # ------------------ (C) DATA MOVEMENT ------------------
    ax = axs[2]
    categories = ["read", "write"]
    x = np.arange(len(categories))
    
    ax.bar(x - width/2, ycsb_bytes_vals, width, label="YCSB", **plot_style.BAR_STYLES['YCSB'])
    ax.bar(x + width/2, tec_bytes_vals, width, label="Tectonic", **plot_style.BAR_STYLES['Tectonic'])
    
    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_ylabel(plot_style.format_label("bytes (GB)"))
    ax.set_title(plot_style.format_label("(c) data movement"), pad=10)
    ax.text(-0.15, 1.05, plot_style.format_label("(c)"), transform=ax.transAxes, fontsize=16, fontweight='bold', va='top', ha='right')
    plot_style.apply_plot_style(ax)
    
    # ------------------ (D) CACHE BEHAVIOR ------------------
    ax = axs[3]
    categories = ["cache hit", "cache miss"]
    x = np.arange(len(categories))
    
    ax.bar(x - width/2, ycsb_cache_vals, width, **plot_style.BAR_STYLES['YCSB'])
    ax.bar(x + width/2, tec_cache_vals, width, **plot_style.BAR_STYLES['Tectonic'])
    
    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_ylabel(plot_style.format_label("count (millions)"))
    ax.set_title(plot_style.format_label("(d) cache behavior"), pad=10)
    ax.text(-0.15, 1.05, plot_style.format_label("(d)"), transform=ax.transAxes, fontsize=16, fontweight='bold', va='top', ha='right')
    plot_style.apply_plot_style(ax)
    
    # ------------------ LEGEND ------------------
    # Generate unified legend
    # We collect legend handles from ax0 (Throughput) and ax2 (Data Movement)
    handles0, labels0 = axs[0].get_legend_handles_labels()
    # Add dummy proxy artists for YCSB / Tectonic bar chart legend
    import matplotlib.patches as mpatches
    ycsb_patch = mpatches.Patch(facecolor="white", edgecolor="grey", hatch="///", label="YCSB")
    tec_patch = mpatches.Patch(facecolor="tab:red", edgecolor="tab:red", label="Tectonic")
    
    all_handles = handles0 + [ycsb_patch, tec_patch]
    all_labels = labels0 + ["YCSB", "Tectonic"]
    
    fig.legend(all_handles, all_labels, loc="upper center", ncol=6, bbox_to_anchor=(0.5, 1.02), frameon=False)
    
    pdf_path = OUTPUT_DIR / "rocksdb_similarity_ycsba_accuracy.pdf"
    png_path = OUTPUT_DIR / "rocksdb_similarity_ycsba_accuracy.png"
    
    # Save the legend separately
    plot_style.save_legend(fig, str(OUTPUT_DIR / "rocksdb_similarity_ycsba_accuracy"))
    
    plt.tight_layout()
    # Pull layout down slightly to make room for unified legend if it is present (it is removed now, so we keep layout tight)
    plt.savefig(pdf_path, bbox_inches="tight")
    plt.savefig(png_path, bbox_inches="tight")
    plt.close()
    
    print(f"Plot saved to: {pdf_path}")
    print(f"Plot saved to: {png_path}")

if __name__ == "__main__":
    main()
