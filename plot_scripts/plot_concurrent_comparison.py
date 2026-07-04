#!/usr/bin/env python3
import os
import json
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from plot_style import apply_plot_style, save_fig, format_label, get_seq_par_style, save_legend

STATS_DIR = "/home/cc/Tectonic/data/concurrent_experiment"
PLOTS_DIR = "/home/cc/Tectonic/concurrent_experiment_plots"
os.makedirs(PLOTS_DIR, exist_ok=True)

def load_results():
    stats_file = os.path.join(STATS_DIR, "results.json")
    if not os.path.exists(stats_file):
        raise FileNotFoundError(f"Stats file not found: {stats_file}")
    with open(stats_file, 'r') as f:
        return json.load(f)

def apply_y_ticks_and_max(ax, max_val):
    ax.set_ylim(bottom=0.0, top=max_val * 1.15 if max_val > 0.0 else 10.0)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=False))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{y:.2f}"))

def main():
    try:
        results = load_results()
    except Exception as e:
        print(f"Error loading results: {e}")
        return

    seq_total = results["best_seq_total"]
    par_total = results["best_par_total"]
    speedup = seq_total / par_total if par_total > 0 else 1.0
    
    seq_timeline = results["seq_timeline"]
    par_timeline = results["par_timeline"]
    
    # ------------------ FIGURE 1: Total Latency Bar Chart ------------------
    fig1, ax1 = plt.subplots(figsize=(4, 5))
    
    categories = [format_label("sequential"), format_label("parallel")]
    
    # Apply seq/par styles for Tectonic
    seq_style = get_seq_par_style("Tectonic", "sequential")
    par_style = get_seq_par_style("Tectonic", "parallel")
    
    bar_seq = ax1.bar(categories[0], seq_total, width=0.5,
                     facecolor=seq_style['facecolor'],
                     edgecolor=seq_style['edgecolor'],
                     hatch=seq_style['hatch'],
                     linewidth=0.5,
                     label='Tectonic (seq)')
                     
    bar_par = ax1.bar(categories[1], par_total, width=0.5,
                     facecolor=par_style['facecolor'],
                     edgecolor=par_style['edgecolor'],
                     hatch=par_style['hatch'],
                     linewidth=0.5,
                     label='Tectonic (par)')
    
    ax1.set_xlim(-0.5, 1.5)
    
    bars = [bar_seq[0], bar_par[0]]
    
    # Add exact time and speedup text on top of each bar
    for idx, rect in enumerate(bars):
        height = rect.get_height()
        label = f"{height:.2f} s"
        if idx == 1:
            label += f"\n({speedup:.2f}x speedup)"
        ax1.annotate(label,
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 5),
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=10, fontweight='bold')
                     
    apply_y_ticks_and_max(ax1, max(seq_total, par_total))
    apply_plot_style(ax1, ylabel="total generation time (seconds)")
    ax1.grid(axis='x', visible=False)
    ax1.legend(frameon=False)
    
    save_legend(fig1, os.path.join(PLOTS_DIR, "fig1"))
    save_fig(fig1, os.path.join(PLOTS_DIR, "fig1"))
    
    # ------------------ FIGURE 2: Gantt Chart/Timeline ------------------
    fig2, (ax_seq, ax_par) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    
    workloads = sorted(list(seq_timeline.keys())) # Tec-1 to Tec-7
    y_pos = np.arange(len(workloads))
    
    # Custom harmonious color scheme for the workloads
    colors = ['#3498db', '#9b59b6', '#e67e22', '#1abc9c', '#e74c3c', '#f1c40f', '#34495e']
    wl_colors = {wl: colors[i] for i, wl in enumerate(workloads)}
    
    # 1. Plot Sequential Timeline
    for i, wl in enumerate(workloads):
        start = seq_timeline[wl]["start"]
        duration = seq_timeline[wl]["duration"]
        ax_seq.barh(i, duration, left=start, color=wl_colors[wl], edgecolor='black', height=0.5)
        ax_seq.text(start + duration/2, i, f"{duration:.2f}s", ha='center', va='center', color='white', fontweight='bold', fontsize=9)
        
    apply_plot_style(ax_seq, title=f"sequential generation (total: {seq_total:.2f}s)", ylabel="workloads")
    ax_seq.set_yticks(y_pos)
    ax_seq.set_yticklabels([format_label(wl.lower()) for wl in workloads], fontweight='bold')
    
    # 2. Plot Parallel Timeline
    for i, wl in enumerate(workloads):
        start = par_timeline[wl]["start"]
        duration = par_timeline[wl]["duration"]
        ax_par.barh(i, duration, left=start, color=wl_colors[wl], edgecolor='black', height=0.5)
        ax_par.text(start + duration/2, i, f"{duration:.2f}s", ha='center', va='center', color='white', fontweight='bold', fontsize=9)
        
    apply_plot_style(ax_par, title=f"parallel generation (7 threads: {par_total:.2f}s)", xlabel="latency (seconds)", ylabel="workloads")
    ax_par.set_yticks(y_pos)
    ax_par.set_yticklabels([format_label(wl.lower()) for wl in workloads], fontweight='bold')
    
    # Customize grid & limits
    ax_seq.grid(axis='y', visible=False)
    ax_par.grid(axis='y', visible=False)
    
    # Align the X limits to the sequential time to show the visual speedup gap
    ax_seq.set_xlim(0, seq_total * 1.05)
    ax_par.set_xlim(0, seq_total * 1.05)
    
    save_fig(fig2, os.path.join(PLOTS_DIR, "fig2"))
    
    print("Concurrent generation experiment plots created successfully.")

if __name__ == "__main__":
    main()
