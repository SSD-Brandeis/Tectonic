#!/usr/bin/env python3
import os
import sys
import json
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.lines as mlines

# Add parent directory to path to import plot_style
sys.path.append("/home/cc/Tectonic")
from plot_style import apply_plot_style, save_fig, format_label, get_seq_par_line_style, save_legend

STATS_DIR = "/home/cc/Tectonic/data/concurrent_blind_experiment"
PLOTS_DIR = "/home/cc/Tectonic/concurrent_blind_experiment"
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
    tectonic_data = data["tectonic"]
    tectonic_unique_data = data.get("tectonic_unique", [0.0] * len(threads_list))

    print("generating performance comparison plot...")
    fig, ax = plt.subplots(figsize=(8, 6))

    # We extract styles for sequential and parallel configurations
    ycsb_seq = get_seq_par_line_style("YCSB", "sequential")
    ycsb_par = get_seq_par_line_style("YCSB", "parallel")

    tec_seq = get_seq_par_line_style("Tectonic", "sequential")
    tec_par = get_seq_par_line_style("Tectonic", "parallel")

    tecu_seq = get_seq_par_line_style("Tectonic Unique", "sequential")
    tecu_par = get_seq_par_line_style("Tectonic Unique", "parallel")

    # 1. YCSB Plot
    # Draw line first connecting all points
    ax.plot(threads_list, ycsb_data, color=ycsb_par['color'], linestyle=ycsb_par['linestyle'], linewidth=2)
    # Draw point markers
    ax.plot(threads_list, ycsb_data, marker=ycsb_par['marker'], 
            markersize=ycsb_par['markersize'], markerfacecolor=ycsb_par['markerfacecolor'], 
            markeredgecolor=ycsb_par['color'], linestyle='None')

    # 2. Tectonic Plot
    # Draw line first
    ax.plot(threads_list, tectonic_data, color=tec_par['color'], linestyle=tec_par['linestyle'], linewidth=2)
    # Draw point markers
    ax.plot(threads_list, tectonic_data, marker=tec_par['marker'], 
            markersize=tec_par['markersize'], markerfacecolor=tec_par['markerfacecolor'], 
            markeredgecolor=tec_par['color'], linestyle='None')

    # 3. Tectonic Unique Plot
    # Draw line first
    ax.plot(threads_list, tectonic_unique_data, color=tecu_par['color'], linestyle=tecu_par['linestyle'], linewidth=2)
    # Draw point markers
    ax.plot(threads_list, tectonic_unique_data, marker=tecu_par['marker'], 
            markersize=tecu_par['markersize'], markerfacecolor=tecu_par['markerfacecolor'], 
            markeredgecolor=tecu_par['color'], linestyle='None')

    # X-axis limits and equal ticks of 10 from 0 to 60
    ax.set_xlim(0, 62)
    ax.set_xticks([0, 10, 20, 30, 40, 50, 60])
    ax.set_xticklabels(["0", "10", "20", "30", "40", "50", "60"])

    # Y-axis limits starting at 0
    max_val = max(max(ycsb_data), max(tectonic_data), max(tectonic_unique_data))
    ax.set_ylim(bottom=0, top=max_val * 1.15)

    apply_plot_style(ax, 
                     title="YCSB workload c (10 M blind operations)", 
                     xlabel="number of threads", 
                     ylabel="end-to-end latency (s)")

    # Legend elements: show only one entry per tool, using the parallel configuration style as the base
    legend_elements = [
        mlines.Line2D([], [], color=ycsb_par['color'], linestyle=ycsb_par['linestyle'],
                      marker=ycsb_par['marker'], markersize=ycsb_par['markersize'],
                      markerfacecolor=ycsb_par['markerfacecolor'], markeredgecolor=ycsb_par['color'],
                      label=format_label('YCSB')),
                      
        mlines.Line2D([], [], color=tec_par['color'], linestyle=tec_par['linestyle'],
                      marker=tec_par['marker'], markersize=tec_par['markersize'],
                      markerfacecolor=tec_par['markerfacecolor'], markeredgecolor=tec_par['color'],
                      label=format_label('Tectonic')),
                      
        mlines.Line2D([], [], color=tecu_par['color'], linestyle=tecu_par['linestyle'],
                      marker=tecu_par['marker'], markersize=tecu_par['markersize'],
                      markerfacecolor=tecu_par['markerfacecolor'], markeredgecolor=tecu_par['color'],
                      label=format_label('Tectonic Unique'))
    ]
    ax.legend(handles=legend_elements, frameon=False)

    # Save legend separately and then save figure
    save_legend(fig, f"{PLOTS_DIR}/generation_speedup_comparison")
    save_fig(fig, f"{PLOTS_DIR}/generation_speedup_comparison")
    print(f"Plot saved to {PLOTS_DIR}/generation_speedup_comparison.png and .pdf")

    # Copy files to the blind_unique_compare directory
    COMP_DIR = "/home/cc/Tectonic/blind_unique_compare"
    os.makedirs(COMP_DIR, exist_ok=True)
    import shutil
    shutil.copy2(f"{PLOTS_DIR}/generation_speedup_comparison.pdf", f"{COMP_DIR}/generation_speedup_comparison.pdf")
    shutil.copy2(f"{PLOTS_DIR}/generation_speedup_comparison.png", f"{COMP_DIR}/generation_speedup_comparison.png")
    shutil.copy2(f"{PLOTS_DIR}/generation_speedup_comparison_legend.pdf", f"{COMP_DIR}/generation_speedup_comparison_legend.pdf")
    shutil.copy2(f"{PLOTS_DIR}/generation_speedup_comparison_legend.png", f"{COMP_DIR}/generation_speedup_comparison_legend.png")
    shutil.copy2(__file__, f"{COMP_DIR}/plot_blind_unique_compare.py")
    print(f"Standalone copies saved to {COMP_DIR}")

if __name__ == "__main__":
    main()