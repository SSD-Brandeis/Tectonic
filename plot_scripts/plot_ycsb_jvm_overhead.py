#!/usr/bin/env python3
import os
import json
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
# Add parent directory to path to import plot_style
import sys
sys.path.append("/home/cc/Tectonic/plot_scripts")
import plot_style
from plot_style import apply_plot_style, save_fig, save_legend, format_label

STATS_DIR = "/home/cc/Tectonic/data/ycsb_jvm_overhead"
PLOTS_DIR = "/home/cc/Tectonic/ycsb_jvm_overhead_plots"
os.makedirs(PLOTS_DIR, exist_ok=True)

GEN_STATS_DIR = "/home/cc/Tectonic/data/generator_comparison"

TOOL_HATCHES = {
    'Tectonic': '',
    'YCSB': '///',
    'KVbench': '\\\\',
    'KVBench': '\\\\'
}

def load_json(filepath):
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return None

def main():
    # ------------------ FIGURE 1: JVM Overhead Breakdown ------------------
    jvm_def = load_json(f"{STATS_DIR}/jvm_default.json")
    jvm_small = load_json(f"{STATS_DIR}/jvm_small.json")
    ycsb_def_a = load_json(f"{STATS_DIR}/ycsb_default_a_trace.json")
    ycsb_small_a = load_json(f"{STATS_DIR}/ycsb_constrained_a_trace.json")

    fig1, ax1 = plt.subplots(figsize=(8, 5))
    
    if jvm_def:
        times, rss = zip(*jvm_def["mem_log"])
        ax1.plot(times, rss, label="JVM Default (Sleep)", color="#9467bd", linestyle="--", linewidth=2)
    if jvm_small:
        times, rss = zip(*jvm_small["mem_log"])
        ax1.plot(times, rss, label="JVM Small Heap (Sleep)", color="#17becf", linestyle="--", linewidth=2)
    if ycsb_def_a:
        times, rss = zip(*ycsb_def_a["load"]["mem_log"])
        ax1.plot(times, rss, label="YCSB Default (Load Workload A)", **{**plot_style.LINE_STYLES['YCSB'], "linestyle": ":"})
    if ycsb_small_a:
        times, rss = zip(*ycsb_small_a["load"]["mem_log"])
        ax1.plot(times, rss, label="YCSB Constrained JVM (Load)", **{**plot_style.LINE_STYLES['YCSB'], "linestyle": "-"})
        
    apply_plot_style(
        ax1, 
        title="ycsb memory footprint: jvm default vs. constrained heap",
        xlabel="time (seconds)",
        ylabel="memory footprint (rss in mb)"
    )
    ax1.set_ylim(bottom=0)
    ax1.legend(loc="upper left")
    
    save_legend(fig1, f"{PLOTS_DIR}/jvm_overhead_breakdown")
    save_fig(fig1, f"{PLOTS_DIR}/jvm_overhead_breakdown")
    print(f"Saved jvm_overhead_breakdown to {PLOTS_DIR}")

    # ------------------ FIGURE 2: Phase-Based Latency Comparison ------------------
    workloads = ["A", "B", "C", "D", "E", "F"]
    fig2, ax2 = plt.subplots(figsize=(10, 6))
    
    width = 0.25
    load_phase_color = '#a1c4fd'
    exec_phase_color = '#38f9d7'
    
    max_latency = 0.0
    
    for idx, w in enumerate(workloads):
        w_lower = w.lower()
        tec_data = load_json(f"{GEN_STATS_DIR}/tectonic_{w_lower}_trace.json")
        kv_data = load_json(f"{GEN_STATS_DIR}/kvbench_{w_lower}_trace.json")
        ycsb_constrained = load_json(f"{STATS_DIR}/ycsb_constrained_{w_lower}_trace.json")
        
        # 1. Tectonic
        if tec_data:
            total_tec = tec_data["total_duration"]
            load_tec = tec_data["loading_phase_end_time"]
            if load_tec is None:
                load_tec = total_tec
            exec_tec = total_tec - load_tec
            ax2.bar(idx - width, load_tec, width, color=load_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['Tectonic'])
            ax2.bar(idx - width, exec_tec, width, bottom=load_tec, color=exec_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['Tectonic'])
            max_latency = max(max_latency, total_tec)
            
        # 2. YCSB Constrained
        if ycsb_constrained:
            load_ycsb = ycsb_constrained["load"]["total_duration"]
            exec_ycsb = ycsb_constrained["run"]["total_duration"]
            total_ycsb = load_ycsb + exec_ycsb
            ax2.bar(idx, load_ycsb, width, color=load_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['YCSB'])
            ax2.bar(idx, exec_ycsb, width, bottom=load_ycsb, color=exec_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['YCSB'])
            max_latency = max(max_latency, total_ycsb)
            
        # 3. KVbench (missing for F)
        if kv_data and w != "F":
            total_kv = kv_data["total_duration"]
            load_kv = kv_data["loading_phase_end_time"]
            if load_kv is None:
                load_kv = total_kv
            exec_kv = total_kv - load_kv
            ax2.bar(idx + width, load_kv, width, color=load_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['KVbench'])
            ax2.bar(idx + width, exec_kv, width, bottom=load_kv, color=exec_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['KVbench'])
            max_latency = max(max_latency, total_kv)

    # Set X ticks
    x_ticks = []
    x_tick_labels = []
    for idx, w in enumerate(workloads):
        if w == "F":
            x_ticks.extend([idx - width, idx])
            x_tick_labels.extend([f"Tec\n({w})", f"YCSB\n({w})"])
        else:
            x_ticks.extend([idx - width, idx, idx + width])
            x_tick_labels.extend([f"Tec\n({w})", f"YCSB\n({w})", f"KV\n({w})"])

    phase_patches = [
        Patch(facecolor=load_phase_color, label='Loading Phase'),
        Patch(facecolor=exec_phase_color, label='Execution Phase')
    ]
    tool_patches = [
        Patch(facecolor='#d3d3d3', edgecolor='black', hatch=TOOL_HATCHES['Tectonic'], label='Tectonic'),
        Patch(facecolor='#d3d3d3', edgecolor='black', hatch=TOOL_HATCHES['YCSB'], label='YCSB (Constrained JVM)'),
        Patch(facecolor='#d3d3d3', edgecolor='black', hatch=TOOL_HATCHES['KVbench'], label='KVbench')
    ]
    ax2.legend(handles=phase_patches + tool_patches, loc='upper right')
    
    apply_plot_style(
        ax2,
        title="latency comparison under jvm constraints (workloads a-f)",
        xlabel="generator and workload",
        ylabel="end-to-end latency (s)"
    )
    ax2.set_xticks(x_ticks)
    ax2.set_xticklabels(x_tick_labels, fontsize=8)
    ax2.set_ylim(bottom=0, top=max_latency * 1.15)
    
    save_legend(fig2, f"{PLOTS_DIR}/ycsb_constrained_latency_breakdown")
    save_fig(fig2, f"{PLOTS_DIR}/ycsb_constrained_latency_breakdown")
    print(f"Saved ycsb_constrained_latency_breakdown to {PLOTS_DIR}")

    # ------------------ FIGURE 3: Resource Footprint (2x3 Grid) ------------------
    fig3, axs3 = plt.subplots(2, 3, figsize=(15, 10))
    subplot_mapping = {
        "A": (0, 0), "B": (0, 1), "C": (0, 2),
        "D": (1, 0), "E": (1, 1), "F": (1, 2)
    }
    
    for w in workloads:
        row, col = subplot_mapping[w]
        ax = axs3[row, col]
        w_lower = w.lower()
        
        # Load datasets
        tectonic_data = load_json(f"{GEN_STATS_DIR}/tectonic_{w_lower}_trace.json")
        kvbench_data = load_json(f"{GEN_STATS_DIR}/kvbench_{w_lower}_trace.json")
        ycsb_default = load_json(f"{STATS_DIR}/ycsb_default_{w_lower}_trace.json")
        ycsb_constrained = load_json(f"{STATS_DIR}/ycsb_constrained_{w_lower}_trace.json")
        
        # Plot Tectonic (blind)
        if tectonic_data:
            t_log = tectonic_data["mem_log"]
            times = [pt[0] for pt in t_log]
            rss = [pt[1] for pt in t_log]
            ax.plot(times, rss, label="Tectonic (Blind)", **plot_style.LINE_STYLES['Tectonic'])
            
        # Plot KVbench (A-E only)
        if kvbench_data and w != "F":
            kv_log = kvbench_data["mem_log"]
            times = [pt[0] for pt in kv_log]
            rss = [pt[1] for pt in kv_log]
            ax.plot(times, rss, label="KVbench", **plot_style.LINE_STYLES['KVBench'])
            
        # Plot YCSB Default (Concatenated)
        if ycsb_default:
            load_log = ycsb_default["load"]["mem_log"]
            run_log = ycsb_default["run"]["mem_log"]
            load_dur = ycsb_default["load"]["total_duration"]
            times = [pt[0] for pt in load_log] + [pt[0] + load_dur for pt in run_log]
            rss = [pt[1] for pt in load_log] + [pt[1] for pt in run_log]
            ax.plot(times, rss, label="YCSB (Default JVM)", **{**plot_style.LINE_STYLES['YCSB'], "linestyle": ":"})
            
        # Plot YCSB Constrained (Concatenated)
        if ycsb_constrained:
            load_log = ycsb_constrained["load"]["mem_log"]
            run_log = ycsb_constrained["run"]["mem_log"]
            load_dur = ycsb_constrained["load"]["total_duration"]
            times = [pt[0] for pt in load_log] + [pt[0] + load_dur for pt in run_log]
            rss = [pt[1] for pt in load_log] + [pt[1] for pt in run_log]
            ax.plot(times, rss, label="YCSB (Constrained JVM)", **{**plot_style.LINE_STYLES['YCSB'], "linestyle": "-"})
            
        apply_plot_style(
            ax,
            title=f"workload {w.lower()}",
            xlabel="time (seconds)",
            ylabel="memory rss (mb)"
        )
        ax.set_ylim(bottom=0)
        ax.legend(loc="upper right", fontsize=8)
        
    save_legend(fig3, f"{PLOTS_DIR}/ycsb_constrained_resource_grid")
    
    plt.tight_layout()
    save_fig(fig3, f"{PLOTS_DIR}/ycsb_constrained_resource_grid")
    print(f"Saved ycsb_constrained_resource_grid to {PLOTS_DIR}")

if __name__ == "__main__":
    main()
