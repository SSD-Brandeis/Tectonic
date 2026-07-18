#!/usr/bin/env python3
import os
import json
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from plot_style import apply_plot_style, format_label, get_seq_par_style

def save_fig(fig, path_without_ext):
    fig.tight_layout()
    fig.savefig(f"{path_without_ext}.pdf", bbox_inches="tight")
    plt.close(fig)

def save_legend(fig_or_ax, path_without_ext):
    handles = []
    labels = []
    
    def extract_from_legend(legend):
        h_list = []
        l_list = []
        if legend:
            leg_handles = getattr(legend, "legend_handles", None) or getattr(legend, "legendHandles", [])
            for handle, text_obj in zip(leg_handles, legend.get_texts()):
                h_list.append(handle)
                l_list.append(text_obj.get_text())
        return h_list, l_list

    if hasattr(fig_or_ax, 'axes') and isinstance(fig_or_ax.axes, list):
        for ax in fig_or_ax.axes:
            legend = ax.get_legend()
            if legend:
                h, l = extract_from_legend(legend)
                for handle, label in zip(h, l):
                    if label not in labels:
                        handles.append(handle)
                        labels.append(label)
                legend.remove()
            else:
                h, l = ax.get_legend_handles_labels()
                for handle, label in zip(h, l):
                    if label not in labels:
                        handles.append(handle)
                        labels.append(label)
                        
        for legend in list(fig_or_ax.legends):
            h, l = extract_from_legend(legend)
            for handle, label in zip(h, l):
                if label not in labels:
                    handles.append(handle)
                    labels.append(label)
            fig_or_ax.legends.remove(legend)
    else:
        legend = fig_or_ax.get_legend()
        if legend:
            h, l = extract_from_legend(legend)
            handles.extend(h)
            labels.extend(l)
            legend.remove()
        elif hasattr(fig_or_ax, 'get_legend_handles_labels'):
            handles, labels = fig_or_ax.get_legend_handles_labels()
            
    if not handles:
        return
        
    ncol = 1
    if len(handles) > 4:
        ncol = min(4, len(handles))
        
    fig_leg = plt.figure(figsize=(ncol * 2.5, 1.0))
    legend = fig_leg.legend(handles, labels, loc='center', frameon=False, ncol=ncol)
    
    fig_leg.canvas.draw()
    bbox = legend.get_window_extent()
    bbox = bbox.transformed(fig_leg.dpi_scale_trans.inverted())
    fig_leg.set_size_inches(bbox.width + 0.4, bbox.height + 0.4)
    
    fig_leg.savefig(f"{path_without_ext}_legend.pdf", bbox_inches='tight')
    plt.close(fig_leg)

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
    fig1, ax1 = plt.subplots(figsize=(4, 4.5))
    
    categories = [format_label("sequential"), format_label("parallel")]
    
    # Apply seq/par styles for Tectonic
    seq_style = get_seq_par_style("Tectonic", "sequential")
    par_style = get_seq_par_style("Tectonic", "parallel")
    
    bar_seq = ax1.bar(categories[0], seq_total, width=0.5,
                     facecolor=seq_style['facecolor'],
                     edgecolor=seq_style['edgecolor'],
                     hatch=seq_style['hatch'],
                     linewidth=0.5,
                     label='X-Bench (seq)')
                     
    bar_par = ax1.bar(categories[1], par_total, width=0.5,
                     facecolor=par_style['facecolor'],
                     edgecolor=par_style['edgecolor'],
                     hatch=par_style['hatch'],
                     linewidth=0.5,
                     label='X-Bench (par)')
    
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
                      
    apply_plot_style(ax1, ylabel="total generation time (s)")
    ax1.set_yticks([0, 20, 40, 60, 80])
    ax1.set_ylim(0, 80)
    ax1.grid(axis='x', visible=False)
    ax1.legend(frameon=False)
    
    save_legend(fig1, os.path.join(PLOTS_DIR, "fig1"))
    save_fig(fig1, os.path.join(PLOTS_DIR, "fig1"))
    
    # ------------------ FIGURE 2: Gantt Chart/Timeline (Split) ------------------
    workloads = sorted(list(seq_timeline.keys())) # Tec-1 to Tec-7
    y_pos = np.arange(len(workloads))
    
    # Custom harmonious color scheme for the workloads
    colors = ['#3498db', '#9b59b6', '#e67e22', '#1abc9c', '#e74c3c', '#f1c40f', '#34495e']
    wl_colors = {wl: colors[i] for i, wl in enumerate(workloads)}
    
    # 1. Plot Sequential Timeline
    fig2_seq, ax_seq = plt.subplots(figsize=(5, 3.6))
    for i, wl in enumerate(workloads):
        start = seq_timeline[wl]["start"]
        duration = seq_timeline[wl]["duration"]
        ax_seq.barh(i, duration, left=start, color=wl_colors[wl], edgecolor='black', height=0.5)
        
    apply_plot_style(ax_seq, ylabel="workload", xlabel="latency (s)")
    ax_seq.set_yticks(y_pos)
    ax_seq.set_yticklabels([wl.split('-')[-1] for wl in workloads], fontweight='bold')
    ax_seq.set_ylim(-0.5, len(workloads) - 0.5)
    ax_seq.grid(axis='y', visible=False)
    ax_seq.set_xlim(0, seq_total * 1.05)
    save_fig(fig2_seq, os.path.join(PLOTS_DIR, "fig2_sequential"))
    
    # 2. Plot Parallel Timeline
    fig2_par, ax_par = plt.subplots(figsize=(5, 3.6))
    for i, wl in enumerate(workloads):
        start = par_timeline[wl]["start"]
        duration = par_timeline[wl]["duration"]
        ax_par.barh(i, duration, left=start, color=wl_colors[wl], edgecolor='black', height=0.5)
        
    apply_plot_style(ax_par, ylabel="workload", xlabel="latency (s)")
    ax_par.set_yticks(y_pos)
    ax_par.set_yticklabels([wl.split('-')[-1] for wl in workloads], fontweight='bold')
    ax_par.set_ylim(-0.5, len(workloads) - 0.5)
    ax_par.grid(axis='y', visible=False)
    ax_par.set_xlim(0, 30.0)
    save_fig(fig2_par, os.path.join(PLOTS_DIR, "fig2_parallel"))
    
    print("Concurrent generation experiment plots created successfully.")

if __name__ == "__main__":
    main()
