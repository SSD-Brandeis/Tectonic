#!/usr/bin/env python3
import os
import json
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import plot_style

# Paths
DATA_DIR = "/home/cc/Tectonic/data/ycsb_tectonic_correctness"
PLOTS_DIR = "/home/cc/Tectonic/ycsb_tectonic_correctness_plots"
os.makedirs(PLOTS_DIR, exist_ok=True)

RESULTS_FILE = os.path.join(DATA_DIR, "results.json")

def generate_plot(runs, base_scale, output_name, title_suffix, broken=False):
    scales = []
    
    # Expected counts
    expected_inserts = []
    expected_queries = []
    expected_updates = []
    
    # YCSB counts
    ycsb_inserts = []
    ycsb_queries = []
    ycsb_updates = []
    
    # Tectonic counts
    tectonic_inserts = []
    tectonic_queries = []
    tectonic_updates = []
    
    for r in runs:
        scales.append(r["scale"])
        
        expected_inserts.append(r["expected"]["I"])
        expected_queries.append(r["expected"]["P"])
        expected_updates.append(r["expected"]["U"])
        
        ycsb_inserts.append(r["ycsb"]["I"])
        ycsb_queries.append(r["ycsb"]["P"])
        ycsb_updates.append(r["ycsb"]["U"])
        
        tectonic_inserts.append(r["tectonic"]["I"])
        tectonic_queries.append(r["tectonic"]["P"])
        tectonic_updates.append(r["tectonic"]["U"])
        
    scales = np.array(scales)
    op_counts = base_scale * scales # convert scale to operation count
    
    # Convert lists to numpy arrays
    expected_inserts = np.array(expected_inserts)
    expected_queries = np.array(expected_queries)
    expected_updates = np.array(expected_updates)
    
    ycsb_inserts = np.array(ycsb_inserts)
    ycsb_queries = np.array(ycsb_queries)
    ycsb_updates = np.array(ycsb_updates)
    
    tectonic_inserts = np.array(tectonic_inserts)
    tectonic_queries = np.array(tectonic_queries)
    tectonic_updates = np.array(tectonic_updates)
    
    # Calculate accuracy in percentage: 100 * (1 - abs(actual - expected) / expected)
    ycsb_inserts_acc = 100.0 * (1.0 - np.abs(ycsb_inserts - expected_inserts) / expected_inserts)
    ycsb_queries_acc = 100.0 * (1.0 - np.abs(ycsb_queries - expected_queries) / expected_queries)
    ycsb_updates_acc = 100.0 * (1.0 - np.abs(ycsb_updates - expected_updates) / expected_updates)
    
    tectonic_inserts_acc = 100.0 * (1.0 - np.abs(tectonic_inserts - expected_inserts) / expected_inserts)
    tectonic_queries_acc = 100.0 * (1.0 - np.abs(tectonic_queries - expected_queries) / expected_queries)
    tectonic_updates_acc = 100.0 * (1.0 - np.abs(tectonic_updates - expected_updates) / expected_updates)
    
    # Ground truth expected accuracy line is always 100%
    expected_acc = np.ones_like(op_counts) * 100.0
    
    if broken:
        # Create two subplots sharing the x-axis (split/broken y-axis)
        fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(10, 8), 
                                       gridspec_kw={'height_ratios': [3, 1]})
        fig.subplots_adjust(hspace=0.08) # small gap between subplots
        
        # Plot Ground Truth - orange dashed line on both axes
        ax1.plot(op_counts, expected_acc, label="ground truth (100 percent)", color="tab:orange", linestyle="--")
        ax2.plot(op_counts, expected_acc, color="tab:orange", linestyle="--")
        
        # Standard Tectonic Style: Color: tab:red, Line style: -., Marker: s, hollow on both axes
        ax1.plot(op_counts, tectonic_inserts_acc, label="tectonic", color="tab:red", linestyle="-.", marker="s", markersize=8, markerfacecolor="none")
        ax2.plot(op_counts, tectonic_inserts_acc, color="tab:red", linestyle="-.", marker="s", markersize=8, markerfacecolor="none")
        
        # Standard YCSB Style: Color: grey, Line style: -, Marker: ^, hollow on both axes.
        ax1.plot(op_counts, ycsb_inserts_acc, label="YCSB - Inserts", color="grey", linestyle="-", marker="^", markersize=8, markerfacecolor="none")
        ax2.plot(op_counts, ycsb_inserts_acc, color="grey", linestyle="-", marker="^", markersize=8, markerfacecolor="none")
        
        ax1.plot(op_counts, ycsb_queries_acc, label="YCSB - Point Queries", color="grey", linestyle="--", marker="o", markersize=8, markerfacecolor="none")
        ax2.plot(op_counts, ycsb_queries_acc, color="grey", linestyle="--", marker="o", markersize=8, markerfacecolor="none")
        
        ax1.plot(op_counts, ycsb_updates_acc, label="YCSB - Updates", color="grey", linestyle=":", marker="d", markersize=8, markerfacecolor="none")
        ax2.plot(op_counts, ycsb_updates_acc, color="grey", linestyle=":", marker="d", markersize=8, markerfacecolor="none")
        
        # Configure limits and ticks dynamically based on large vs small scale:
        if "small" in output_name:
            top_bottom_lim = 84.0
            top_ticks = [85, 100]
        else:
            top_bottom_lim = 94.0
            top_ticks = [95, 100]
            
        ax1.set_ylim(top_bottom_lim, 102.0)
        ax2.set_ylim(0.0, 10.0)
        
        # Hide the spines between ax1 and ax2
        ax1.spines['bottom'].set_visible(False)
        ax2.spines['top'].set_visible(False)
        
        # Only show x-ticks at the bottom subplot
        ax1.xaxis.tick_top()
        ax1.tick_params(labeltop=False)
        ax2.xaxis.tick_bottom()
        
        # Apply standard styles
        plot_style.apply_plot_style(ax1, title=f"workload generation accuracy ({title_suffix})")
        plot_style.apply_plot_style(ax2, xlabel="operation count")
        
        # Set y-limits and ticks again to prevent apply_plot_style overrides
        ax1.set_ylim(top_bottom_lim, 102.0)
        ax2.set_ylim(0.0, 10.0)
        
        # Set ticks exactly as requested: 85/95 and 100 on top, 0 on bottom
        ax1.set_yticks(top_ticks)
        ax2.set_yticks([0])
        
        # Explicitly set x-ticks starting from 0.0
        if base_scale == 10000 and "small" in title_suffix:
            ax2.set_xticks([0, 2000, 4000, 6000, 8000, 10000])
            ax2.set_xlim(left=0.0, right=10500)
        else:
            ax2.set_xticks([0, 20000, 40000, 60000, 80000, 100000, 120000, 140000, 160000])
            ax2.set_xlim(left=0.0, right=165000)
            
        # Draw axis break cut-out diagonal ticks
        d = .015
        kwargs = dict(transform=ax1.transAxes, color='black', clip_on=False, linewidth=1.0)
        ax1.plot((-d, +d), (-d, +d), **kwargs)
        ax1.plot((1 - d, 1 + d), (-d, +d), **kwargs)

        kwargs.update(transform=ax2.transAxes)
        ax2.plot((-d, +d), (1 - d, 1 + d), **kwargs)
        ax2.plot((1 - d, 1 + d), (1 - d, 1 + d), **kwargs)
        
        # Center y-axis label vertically on the figure
        fig.text(0.02, 0.5, "workload accuracy (percentage)", va='center', ha='center', rotation='vertical', fontsize=20, fontname="Linux Libertine O")
        
        # Legend
        ax1.legend(loc="lower right")
        
    else:
        # Create single panel figure
        fig, ax = plt.subplots(figsize=(10, 8))
        
        # Plot Ground Truth - orange dashed line
        ax.plot(op_counts, expected_acc, label="ground truth (100 percent)", color="tab:orange", linestyle="--")
        
        # Standard Tectonic Style: Color: tab:red, Line style: -., Marker: s, hollow
        ax.plot(op_counts, tectonic_inserts_acc, label="tectonic", color="tab:red", linestyle="-.", marker="s", markersize=8, markerfacecolor="none")
        
        # Standard YCSB Style: Color: grey, Line style: -, Marker: ^, hollow.
        ax.plot(op_counts, ycsb_inserts_acc, label="YCSB - Inserts", color="grey", linestyle="-", marker="^", markersize=8, markerfacecolor="none")
        ax.plot(op_counts, ycsb_queries_acc, label="YCSB - Point Queries", color="grey", linestyle="--", marker="o", markersize=8, markerfacecolor="none")
        ax.plot(op_counts, ycsb_updates_acc, label="YCSB - Updates", color="grey", linestyle=":", marker="d", markersize=8, markerfacecolor="none")
        
        # Formatting
        plot_style.apply_plot_style(ax, 
                                    title=f"workload generation accuracy ({title_suffix})", 
                                    xlabel="operation count", 
                                    ylabel="workload accuracy (percentage)")
        
        # Enforce exact ticks for y-axis: 0, 50, 100
        ax.set_yticks([0, 50, 100])
        ax.set_ylim(bottom=0.0, top=110.0) # buffer to show 100% clearly
        
        # Enforce left limit start at 0
        ax.set_xlim(left=0.0)
        
        # Explicitly set x-ticks starting from 0.0
        if base_scale == 10000 and "small" in title_suffix:
            ax.set_xticks([0, 2000, 4000, 6000, 8000, 10000])
            ax.set_xlim(left=0.0, right=10500)
        else:
            ax.set_xticks([0, 20000, 40000, 60000, 80000, 100000, 120000, 140000, 160000])
            ax.set_xlim(left=0.0, right=165000)
            
        # Legend
        ax.legend(loc="lower right")
        
    # Save the legend separately and clean the main plot
    output_path = os.path.join(PLOTS_DIR, output_name)
    plot_style.save_legend(fig, output_path)
    
    # Save the main plot (PDF and PNG)
    plot_style.save_fig(fig, output_path)
    print(f"Generated correctness plot at {output_path}.pdf / .png")
    print(f"Generated legend at {output_path}_legend.pdf / .png")

def main():
    if not os.path.exists(RESULTS_FILE):
        print(f"[ERROR] Results file {RESULTS_FILE} not found. Please run the experiment first.")
        return
        
    with open(RESULTS_FILE, "r") as f:
        data = json.load(f)
        
    base_scale = data["base_scale"]
    
    # Standard linear y-axis plots (y-ticks: 0, 50, 100)
    generate_plot(data["large_scale_runs"], base_scale, "correctness_large", "large scale", broken=False)
    generate_plot(data["small_scale_runs"], base_scale, "correctness_small", "small scale", broken=False)
    
    # Broken y-axis plots (y-ticks: 0, 95, 100 or 0, 85, 100)
    generate_plot(data["large_scale_runs"], base_scale, "correctness_large_broken", "large scale broken", broken=True)
    generate_plot(data["small_scale_runs"], base_scale, "correctness_small_broken", "small scale broken", broken=True)

if __name__ == "__main__":
    main()
