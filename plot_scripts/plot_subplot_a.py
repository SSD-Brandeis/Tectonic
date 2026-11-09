#!/usr/bin/env python3
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])
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
DATA_DIR = Path(REPO_ROOT + "/rocksdb-benchmark-harness/experiments/workload-similarity/10x")
OUTPUT_DIR = Path(REPO_ROOT + "/experiment_plots")
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
    x_reads, x_writes = load_iostat(DATA_DIR / "iostat.xbench.1.json")
    ycsb_reads, ycsb_writes = load_iostat(DATA_DIR / "iostat.ycsb.1.json")
    
    # Downsample factor (step=10 matches paper's point density of ~40 points over 400 seconds)
    step = 10
    
    x_x = np.arange(len(x_reads))[::step]
    x_r = x_reads[::step]
    x_w = x_writes[::step]
    
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
    
    xbench_style = {
        "color": "tab:red",
        "marker": "s",
        "markersize": 8,
        "markerfacecolor": "white",
        "markeredgecolor": "tab:red",
    }
    
    # Plot lines: solid for read, dashed for write
    ax.plot(x_ycsb, ycsb_r, label="read (YCSB)", linestyle="-", **ycsb_style)
    ax.plot(x_ycsb, ycsb_w, label="write (YCSB)", linestyle="--", **ycsb_style)
    ax.plot(x_x, x_r, label="read (X-Bench)", linestyle="-", **xbench_style)
    ax.plot(x_x, x_w, label="write (X-Bench)", linestyle="--", **xbench_style)
    
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
