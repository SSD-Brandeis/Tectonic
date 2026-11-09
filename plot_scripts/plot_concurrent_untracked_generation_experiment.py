#!/usr/bin/env python3
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import os
import sys
import json
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.lines as mlines

# Add parent directory to path to import plot_style
sys.path.append(REPO_ROOT)
from plot_style import apply_plot_style, save_fig, format_label, get_seq_par_line_style, save_legend

STATS_DIR = REPO_ROOT + "/data/concurrent_untracked_experiment"
PLOTS_DIR = REPO_ROOT + "/concurrent_untracked_experiment"
os.makedirs(PLOTS_DIR, exist_ok=True)
RESULTS_FILE = f"{STATS_DIR}/results.json"

def main():
    if not os.path.exists(RESULTS_FILE):
        print(f"Error: Results file not found at {RESULTS_FILE}")
        print("Please run the experiment script first to generate metrics.")
        sys.exit(1)

    with open(RESULTS_FILE, "r") as f:
        data = json.load(f)

    threads_list = data["threads"]
    ycsb_data = data["ycsb"]
    xbench_data = data["xbench"]
    xbench_unique_data = data.get("xbench_unique", [0.0] * len(threads_list))

    print("generating performance comparison plot...")
    fig, ax = plt.subplots(figsize=(8, 6))

    # We extract styles for sequential and parallel configurations
    ycsb_seq = get_seq_par_line_style("YCSB", "sequential")
    ycsb_par = get_seq_par_line_style("YCSB", "parallel")

    x_seq = get_seq_par_line_style("X-Bench", "sequential")
    x_par = get_seq_par_line_style("X-Bench", "parallel")

    xu_seq = get_seq_par_line_style("X-Bench Unique", "sequential")
    xu_par = get_seq_par_line_style("X-Bench Unique", "parallel")

    # 1. YCSB Plot
    # Draw line first connecting all points
    ax.plot(threads_list, ycsb_data, color=ycsb_par['color'], linestyle=ycsb_par['linestyle'], linewidth=2)
    # Draw point markers
    ax.plot(threads_list, ycsb_data, marker=ycsb_par['marker'], 
            markersize=ycsb_par['markersize'], markerfacecolor=ycsb_par['markerfacecolor'], 
            markeredgecolor=ycsb_par['color'], linestyle='None')

    # 2. X-Bench Plot
    # Draw line first
    ax.plot(threads_list, xbench_data, color=x_par['color'], linestyle=x_par['linestyle'], linewidth=2)
    # Draw point markers
    ax.plot(threads_list, xbench_data, marker=x_par['marker'], 
            markersize=x_par['markersize'], markerfacecolor=x_par['markerfacecolor'], 
            markeredgecolor=x_par['color'], linestyle='None')

    # 3. X-Bench Unique Plot
    # Draw line first
    ax.plot(threads_list, xbench_unique_data, color=xu_par['color'], linestyle=xu_par['linestyle'], linewidth=2)
    # Draw point markers
    ax.plot(threads_list, xbench_unique_data, marker=xu_par['marker'], 
            markersize=xu_par['markersize'], markerfacecolor=xu_par['markerfacecolor'], 
            markeredgecolor=xu_par['color'], linestyle='None')

    # X-axis limits and equal ticks of 10 from 0 to 60
    ax.set_xlim(0, 62)
    ax.set_xticks([0, 10, 20, 30, 40, 50, 60])
    ax.set_xticklabels(["0", "10", "20", "30", "40", "50", "60"])

    # Y-axis limits starting at 0
    max_val = max(max(ycsb_data), max(xbench_data), max(xbench_unique_data))
    ax.set_ylim(bottom=0, top=max_val * 1.15)

    apply_plot_style(ax, 
                     title="YCSB workload c (10 M untracked operations)", 
                     xlabel="number of threads", 
                     ylabel="end-to-end latency (s)")

    # Legend elements: show only one entry per tool, using the parallel configuration style as the base
    legend_elements = [
        mlines.Line2D([], [], color=ycsb_par['color'], linestyle=ycsb_par['linestyle'],
                      marker=ycsb_par['marker'], markersize=ycsb_par['markersize'],
                      markerfacecolor=ycsb_par['markerfacecolor'], markeredgecolor=ycsb_par['color'],
                      label=format_label('YCSB')),
                      
        mlines.Line2D([], [], color=x_par['color'], linestyle=x_par['linestyle'],
                      marker=x_par['marker'], markersize=x_par['markersize'],
                      markerfacecolor=x_par['markerfacecolor'], markeredgecolor=x_par['color'],
                      label=format_label('X-Bench')),
                      
        mlines.Line2D([], [], color=xu_par['color'], linestyle=xu_par['linestyle'],
                      marker=xu_par['marker'], markersize=xu_par['markersize'],
                      markerfacecolor=xu_par['markerfacecolor'], markeredgecolor=xu_par['color'],
                      label=format_label('X-Bench Unique'))
    ]
    ax.legend(handles=legend_elements, frameon=False)

    # Save legend separately and then save figure
    save_legend(fig, f"{PLOTS_DIR}/generation_speedup_comparison")
    save_fig(fig, f"{PLOTS_DIR}/generation_speedup_comparison")
    print(f"Plot saved to {PLOTS_DIR}/generation_speedup_comparison.png and .pdf")

    # Copy files to the untracked_unique_compare directory
    COMP_DIR = REPO_ROOT + "/untracked_unique_compare"
    os.makedirs(COMP_DIR, exist_ok=True)
    import shutil
    shutil.copy2(f"{PLOTS_DIR}/generation_speedup_comparison.pdf", f"{COMP_DIR}/generation_speedup_comparison.pdf")
    shutil.copy2(f"{PLOTS_DIR}/generation_speedup_comparison.png", f"{COMP_DIR}/generation_speedup_comparison.png")
    shutil.copy2(f"{PLOTS_DIR}/generation_speedup_comparison_legend.pdf", f"{COMP_DIR}/generation_speedup_comparison_legend.pdf")
    shutil.copy2(f"{PLOTS_DIR}/generation_speedup_comparison_legend.png", f"{COMP_DIR}/generation_speedup_comparison_legend.png")
    shutil.copy2(__file__, f"{COMP_DIR}/plot_untracked_unique_compare.py")
    print(f"Standalone copies saved to {COMP_DIR}")

if __name__ == "__main__":
    main()