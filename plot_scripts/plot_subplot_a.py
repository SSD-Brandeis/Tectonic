#!/usr/bin/env python3
import os
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager
from pathlib import Path

# Add plot_scripts directory to path to import plot_style
import sys
sys.path.append(str(Path(__file__).resolve().parent))
import plot_style

# Data and output directories
DATA_DIR = Path("/home/cc/Tectonic/rocksdb-benchmark-harness/experiments/workload-similarity/10x")
OUTPUT_DIR = Path("/home/cc/Tectonic/experiment_plots")
OUTPUT_DIR.mkdir(exist_ok=True)

def load_iostat(path):
    with open(path) as f:
        data = json.load(f)
    stats = data["sysstat"]["hosts"][0]["statistics"]
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

def main():
    print("Loading iostat data...")
    tec_reads, tec_writes = load_iostat(DATA_DIR / "iostat.tectonic.1.json")
    ycsb_reads, ycsb_writes = load_iostat(DATA_DIR / "iostat.ycsb.1.json")
    
    # Downsample factor (step=10 matches paper's point density of ~40 points over 400 seconds)
    step = 10
    
    x_tec = np.arange(len(tec_reads))[::step]
    tec_r = tec_reads[::step]
    tec_w = tec_writes[::step]
    
    x_ycsb = np.arange(len(ycsb_reads))[::step]
    ycsb_r = ycsb_reads[::step]
    ycsb_w = ycsb_writes[::step]
    
    # Create the figure
    fig, ax = plt.subplots(figsize=(6, 4.5))
    
    # Standard styles from plot_style
    # Since this is sequential, use hollow markers (markerfacecolor='white')
    ycsb_style = {
        "color": "grey",
        "marker": "^",
        "markersize": 8,
        "markerfacecolor": "white",
        "markeredgecolor": "grey",
    }
    
    tectonic_style = {
        "color": "tab:red",
        "marker": "s",
        "markersize": 8,
        "markerfacecolor": "white",
        "markeredgecolor": "tab:red",
    }
    
    # Plot lines: solid for read, dashed for write
    ax.plot(x_ycsb, ycsb_r, label="read (YCSB)", linestyle="-", **ycsb_style)
    ax.plot(x_ycsb, ycsb_w, label="write (YCSB)", linestyle="--", **ycsb_style)
    ax.plot(x_tec, tec_r, label="read (Tectonic)", linestyle="-", **tectonic_style)
    ax.plot(x_tec, tec_w, label="write (Tectonic)", linestyle="--", **tectonic_style)
    
    # Enforce axis labels
    # Use "time (s)" and "Mb/s" strictly as per lowercase rules with exceptions
    ax.set_xlabel("time (s)", labelpad=8)
    ax.set_ylabel("Mb/s", labelpad=8)
    
    # Set tick range explicitly up to 400 to match old plot
    ax.set_xlim(left=0.0, right=410.0)
    ax.set_ylim(bottom=0.0, top=300.0)
    
    # Apply standard borders, tick sizes, zero values etc.
    plot_style.apply_plot_style(ax)
    
    # Save the figure and separate legend
    base_output_path = str(OUTPUT_DIR / "subplot_a")
    
    # Extract and save legend separately
    plot_style.save_legend(ax, base_output_path)
    
    # Save main plot figure
    plot_style.save_fig(fig, base_output_path)
    
    print(f"Plot saved to: {base_output_path}.pdf and .png")
    print(f"Legend saved to: {base_output_path}_legend.pdf and _legend.png")

if __name__ == "__main__":
    main()
