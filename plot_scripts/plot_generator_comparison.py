#!/usr/bin/env python3
import os
import json
import csv
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.patches import Patch
import plot_style

# ================= CONFIGURATION VARIABLES =================
# Toggle between 'linear' and 'log' scale for y-axis of latency/performance plots (Figure 1 and Figure 3)
Y_AXIS_SCALE = 'log'
# Toggle between 'linear' and 'log' scale for x-axis (time) of memory footprint line plots (Figure 2 and Figure 4)
TIME_AXIS_SCALE = 'linear'
# Common figure size for latency plots (Figure 1 and Figure 3) to ensure consistency and compact layout
LATENCY_FIGURE_SIZE = (5, 3.6)
# ==========================================================

STATS_DIR = "/home/cc/Tectonic/data/generator_comparison"
PLOTS_DIR = "/home/cc/Tectonic/generator_experiment_plots"
os.makedirs(PLOTS_DIR, exist_ok=True)

ALL_OPS = ['Insert', 'Point Query', 'Update', 'Point Delete', 'Range Query', 'Range Delete']

OP_COLORS = {
    'Insert': '#9467bd',
    'Point Query': '#ff7f0e',
    'Update': '#2ca02c',
    'Point Delete': '#e377c2',
    'Range Query': '#1f77b4',
    'Range Delete': '#8c564b'
}

TOOL_HATCHES = {
    'Tectonic': '',
    'YCSB': '/////',
    'KVbench': '\\\\',
    'KVBench': '\\\\'
}

# Predefined logical operation counts for Workloads I-V
WORKLOAD_OP_COUNTS = {
    "I":   {"Insert": 1000000, "Point Query": 1000000},
    "II":  {"Insert": 500000,  "Point Delete": 100000, "Update": 250000, "Point Query": 150000},
    "III": {"Insert": 1000000, "Update": 500000,  "Point Query": 500000},
    "IV":  {"Insert": 1000000, "Update": 500000,  "Range Delete": 500000},
    "V":   {"Insert": 950000,  "Point Query": 50000}
}

# Complexity coefficients for workload generators
TECTONIC_COEFFICIENTS = {
    "Insert": 1.0,
    "Update": 1.1,
    "Point Query": 1.0,
    "Point Delete": 1.0,
    "Range Query": 2.0,
    "Range Delete": 2.0
}

KVBENCH_COEFFICIENTS = {
    "Insert": 1.0,
    "Update": 30.0,
    "Point Query": 30.0,
    "Point Delete": 30.0,
    "Range Query": 250.0,
    "Range Delete": 250.0
}

def load_json(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, 'r') as f:
        return json.load(f)

def calculate_durations(total_duration, op_counts, coefficients):
    durations = {op: 0.0 for op in ALL_OPS}
    if total_duration is None or total_duration <= 0.0:
        return durations

    total_weighted = 0.0
    for op, count in op_counts.items():
        coef = coefficients.get(op, 1.0)
        total_weighted += count * coef

    if total_weighted <= 0.0:
        return durations

    for op, count in op_counts.items():
        coef = coefficients.get(op, 1.0)
        durations[op] = total_duration * (count * coef) / total_weighted

    return durations

def apply_scale_and_ticks(ax, max_val, is_percentage=False):
    # Set y-axis scale
    if Y_AXIS_SCALE == 'log':
        ax.set_yscale('log')
        ax.set_ylim(bottom=1.0, top=max_val * 1.5 if max_val > 0 else 100.0)
        ax.yaxis.set_major_locator(mticker.LogLocator(base=10.0))
        ax.yaxis.set_major_formatter(mticker.LogFormatterMathtext(base=10.0))
        ax.yaxis.set_minor_locator(mticker.NullLocator())
    else:
        ax.set_yscale('linear')
        ax.set_ylim(bottom=0.0, top=max_val * 1.1 if max_val > 0 else 100.0)
        ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{int(round(y))}"))

    pass

def format_subplot_y_axis(ax, max_val):
    # Memory y-axis always starts strictly at 0
    ax.set_ylim(bottom=0.0, top=max_val * 1.15 if max_val > 0.0 else 100.0)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{int(round(y))}"))

def format_subplot_x_axis(ax):
    ax.set_xscale(TIME_AXIS_SCALE)
    if TIME_AXIS_SCALE == 'log':
        ax.set_xlim(left=1.0)
    else:
        ax.set_xlim(left=0.0)

def plot_fig1_fig2():
    workloads = ["I", "II", "III", "IV", "V"]
    
    # ------------------ FIGURE 1: Latency Breakdown (Subplot A Only) ------------------
    fig1, ax1 = plt.subplots(figsize=LATENCY_FIGURE_SIZE)
    
    x = np.arange(len(workloads))
    width = 0.35
    
    op_breakdown = []
    with open(f"{STATS_DIR}/generator_op_breakdown.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            op_breakdown.append(row)
            
    tec_ops_data = []
    kv_ops_data = []
    max_duration = 0.0
    
    for w in workloads:
        t_row = next((r for r in op_breakdown if r["Workload"] == w and r["Tool"] == "Tectonic+"), None)
        k_row = next((r for r in op_breakdown if r["Workload"] == w and r["Tool"] == "KVbench"), None)
        
        tec_ops = {}
        for op in ALL_OPS:
            col = op.replace(" ", "_") + "_Latency_s"
            tec_ops[op] = float(t_row[col]) if t_row and col in t_row else 0.0
        tec_ops_data.append(tec_ops)
        if t_row:
            max_duration = max(max_duration, float(t_row["Total_Latency_s"]))
            
        kv_ops = {}
        for op in ALL_OPS:
            col = op.replace(" ", "_") + "_Latency_s"
            kv_ops[op] = float(k_row[col]) if k_row and col in k_row else 0.0
        kv_ops_data.append(kv_ops)
        if k_row:
            max_duration = max(max_duration, float(k_row["Total_Latency_s"]))

    # Plot absolute latency bars
    bottom_tec = np.zeros(len(workloads))
    bottom_kv = np.zeros(len(workloads))
    
    for op in ALL_OPS:
        tec_vals = [tec_ops_data[i][op] for i in range(len(workloads))]
        kv_vals = [kv_ops_data[i][op] for i in range(len(workloads))]
        
        # Draw KVbench segment (striped)
        ax1.bar(x - width/2, kv_vals, width, bottom=bottom_kv, color=OP_COLORS[op], edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['KVbench'])
        bottom_kv += kv_vals
 
        # Draw Tectonic segment (solid)
        ax1.bar(x + width/2, tec_vals, width, bottom=bottom_tec, color=OP_COLORS[op], edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['Tectonic'])
        bottom_tec += tec_vals
 
    x_ticks = x
    x_tick_labels = workloads
 
    ax1.set_ylabel(plot_style.format_label('End-to-End Latency (s)'))
    ax1.set_xticks(x_ticks)
    ax1.set_xticklabels(x_tick_labels)
    
    # Combined legend for operation colors and tool hatches
    present_ops = [op for op in ALL_OPS if any(WORKLOAD_OP_COUNTS[w].get(op, 0) > 0 for w in workloads)]
    color_patches = [Patch(facecolor=OP_COLORS[op], label=op.lower()) for op in present_ops]
    tool_patches = [
        Patch(facecolor='#d3d3d3', edgecolor='black', hatch=TOOL_HATCHES['KVbench'], label='KVbench'),
        Patch(facecolor='#d3d3d3', edgecolor='black', hatch=TOOL_HATCHES['Tectonic'], label='Tectonic+')
    ]
    ax1.legend(handles=color_patches + tool_patches, loc='upper left', bbox_to_anchor=(1, 1))
    
    # Apply strict scale constraints and mark max value
    apply_scale_and_ticks(ax1, max_duration)
    
    plot_style.save_legend(ax1, f"{PLOTS_DIR}/fig1")
    
    fig1.tight_layout()
    fig1.savefig(f"{PLOTS_DIR}/fig1.pdf", bbox_inches='tight')
    plt.close(fig1)
    
    # ------------------ FIGURE 2: Resource Footprint (Line plot, 2x3 Subplots) ------------------
    fig2, axs2 = plt.subplots(2, 3, figsize=(15, 10))
    
    subplot_mapping = {
        "I": axs2[0, 0],
        "II": axs2[0, 1],
        "III": axs2[0, 2],
        "IV": axs2[1, 0],
        "V": axs2[1, 1]
    }
    
    axs2[1, 2].axis('off')
    
    for w in workloads:
        w_lower = w.lower()
        tec_data = load_json(f"{STATS_DIR}/tectonic_{w_lower}_trace.json")
        kv_data = load_json(f"{STATS_DIR}/kvbench_{w_lower}_trace.json")
        
        ax = subplot_mapping[w]
        max_mem_subplot = 0.0
        
        if tec_data and "mem_log" in tec_data:
            mem_log = tec_data["mem_log"]
            if mem_log:
                times = [m[0] for m in mem_log]
                rss = [m[1] for m in mem_log]
                ax.plot(times, rss, label='X-Bench', **plot_style.LINE_STYLES['Tectonic'])
                max_mem_subplot = max(max_mem_subplot, max(rss, default=0.0))
                
        if kv_data and "mem_log" in kv_data:
            mem_log = kv_data["mem_log"]
            if mem_log:
                times = [m[0] for m in mem_log]
                rss = [m[1] for m in mem_log]
                ax.plot(times, rss, label='KVBench', **plot_style.LINE_STYLES['KVBench'])
                max_mem_subplot = max(max_mem_subplot, max(rss, default=0.0))
 
        ax.text(0.05, 0.95, plot_style.format_label(f"workload {w.lower()}"), transform=ax.transAxes, va='top', ha='left', fontweight='bold', fontsize=12)
        ax.set_xlabel(plot_style.format_label('time (s)'))
        ax.set_ylabel(plot_style.format_label('memory footprint (MB)'))
        
        format_subplot_x_axis(ax)
        format_subplot_y_axis(ax, max_mem_subplot)
 
    legend_elements = [
        plt.Line2D([0], [0], label='KVBench', **plot_style.LINE_STYLES['KVBench']),
        plt.Line2D([0], [0], label='X-Bench', **plot_style.LINE_STYLES['Tectonic'])
    ]
    axs2[1, 2].legend(handles=legend_elements, loc='center', fontsize=14, frameon=False)
    
    plot_style.save_legend(axs2[1, 2], f"{PLOTS_DIR}/fig2")
    
    plt.tight_layout()
    fig2.savefig(f"{PLOTS_DIR}/fig2.pdf", bbox_inches='tight')
    plt.close(fig2)

def plot_fig3_fig4():
    workloads = ["A", "B", "C", "D", "E", "F"]
    
    # ------------------ FIGURE 3: Phase-Based Performance Breakdown ------------------
    fig3, ax3 = plt.subplots(figsize=LATENCY_FIGURE_SIZE)
    
    x = np.arange(len(workloads))
    width = 0.25  # three bars side-by-side
    
    load_phase_color = 'tab:blue'
    exec_phase_color = 'tab:red'
    
    max_latency = 0.0
    
    phase_data = []
    with open(f"{STATS_DIR}/generator_phase_breakdown.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phase_data.append(row)
            
    for idx, w in enumerate(workloads):
        t_row = next((r for r in phase_data if r["Workload"] == w and r["Tool"] == "Tectonic+"), None)
        y_row = next((r for r in phase_data if r["Workload"] == w and r["Tool"] == "YCSB"), None)
        k_row = next((r for r in phase_data if r["Workload"] == w and r["Tool"] == "KVbench"), None)
        
        # 1. YCSB phase times
        if y_row:
            load_ycsb = float(y_row["LoadPhase_Latency_s"])
            exec_ycsb = float(y_row["ExecutionPhase_Latency_s"])
            total_ycsb = float(y_row["Total_Latency_s"])
            
            ax3.bar(idx - width, load_ycsb, width, color=load_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['YCSB'])
            ax3.bar(idx - width, exec_ycsb, width, bottom=load_ycsb, color=exec_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['YCSB'])
            max_latency = max(max_latency, total_ycsb)
            
        # 2. KVbench phase times (missing for F)
        if k_row and w != "F":
            load_kv = float(k_row["LoadPhase_Latency_s"])
            exec_kv = float(k_row["ExecutionPhase_Latency_s"])
            total_kv = float(k_row["Total_Latency_s"])
            
            ax3.bar(idx, load_kv, width, color=load_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['KVbench'])
            ax3.bar(idx, exec_kv, width, bottom=load_kv, color=exec_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['KVbench'])
            max_latency = max(max_latency, total_kv)

        # 3. Tectonic phase times
        if t_row:
            load_tec = float(t_row["LoadPhase_Latency_s"])
            exec_tec = float(t_row["ExecutionPhase_Latency_s"])
            total_tec = float(t_row["Total_Latency_s"])
            
            ax3.bar(idx + width, load_tec, width, color=load_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['Tectonic'])
            ax3.bar(idx + width, exec_tec, width, bottom=load_tec, color=exec_phase_color, edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['Tectonic'])
            max_latency = max(max_latency, total_tec)

    x_ticks = [idx for idx, w in enumerate(workloads)]
    x_tick_labels = workloads

    phase_patches = [
        Patch(facecolor=load_phase_color, label='loading phase'),
        Patch(facecolor=exec_phase_color, label='execution phase')
    ]
    tool_patches = [
        Patch(facecolor='none', edgecolor='black', hatch=TOOL_HATCHES['YCSB'], label='YCSB'),
        Patch(facecolor='none', edgecolor='black', hatch=TOOL_HATCHES['KVbench'], label='KVbench'),
        Patch(facecolor='none', edgecolor='black', hatch=TOOL_HATCHES['Tectonic'], label='Tectonic+')
    ]
    ax3.legend(handles=phase_patches + tool_patches, loc='upper right')
    
    ax3.set_ylabel(plot_style.format_label('End-to-End Latency (s)'))
    ax3.set_xticks(x_ticks)
    ax3.set_xticklabels(x_tick_labels)
    
    # Apply scale and max line
    apply_scale_and_ticks(ax3, max_latency)
    
    plot_style.save_legend(ax3, f"{PLOTS_DIR}/fig3")
    
    plt.tight_layout()
    fig3.savefig(f"{PLOTS_DIR}/fig3.pdf", bbox_inches='tight')
    plt.close(fig3)
 
    # ------------------ FIGURE 4: Resource Footprint (Line plot, 2x3 Subplots) ------------------
    fig4, axs4 = plt.subplots(2, 3, figsize=(15, 10))
    
    subplot_mapping = {
        "A": axs4[0, 0],
        "B": axs4[0, 1],
        "C": axs4[0, 2],
        "D": axs4[1, 0],
        "E": axs4[1, 1],
        "F": axs4[1, 2]
    }
    
    for w in workloads:
        w_lower = w.lower()
        ycsb_data = load_json(f"{STATS_DIR}/ycsb_{w_lower}_trace.json")
        tec_data = load_json(f"{STATS_DIR}/tectonic_{w_lower}_trace.json")
        kv_data = load_json(f"{STATS_DIR}/kvbench_{w_lower}_trace.json")
        
        ax = subplot_mapping[w]
        max_mem_subplot = 0.0
        
        # 1. Tectonic
        if tec_data and "mem_log" in tec_data:
            mem_log = tec_data["mem_log"]
            if mem_log:
                times = [m[0] for m in mem_log]
                rss = [m[1] for m in mem_log]
                ax.plot(times, rss, label='X-Bench', **plot_style.LINE_STYLES['Tectonic'])
                max_mem_subplot = max(max_mem_subplot, max(rss, default=0.0))
                
        # 2. YCSB (combine load and run phase)
        if ycsb_data:
            load_mem = ycsb_data["load"].get("mem_log", [])
            run_mem = ycsb_data["run"].get("mem_log", [])
            
            combined_times = []
            combined_rss = []
            for m in load_mem:
                combined_times.append(m[0])
                combined_rss.append(m[1])
            load_duration = ycsb_data["load"]["total_duration"]
            for m in run_mem:
                combined_times.append(m[0] + load_duration)
                combined_rss.append(m[1])
                
            if combined_times:
                ax.plot(combined_times, combined_rss, label='YCSB', **plot_style.LINE_STYLES['YCSB'])
                max_mem_subplot = max(max_mem_subplot, max(combined_rss, default=0.0))
                
        # 3. KVbench (missing for F)
        if kv_data and w != "F" and "mem_log" in kv_data:
            mem_log = kv_data["mem_log"]
            if mem_log:
                times = [m[0] for m in mem_log]
                rss = [m[1] for m in mem_log]
                ax.plot(times, rss, label='KVBench', **plot_style.LINE_STYLES['KVBench'])
                max_mem_subplot = max(max_mem_subplot, max(rss, default=0.0))
 
        # Labels & Ticks
        ax.text(0.05, 0.95, plot_style.format_label(f"workload {w.lower()}"), transform=ax.transAxes, va='top', ha='left', fontweight='bold', fontsize=12)
        ax.set_xlabel(plot_style.format_label('time (s)'))
        ax.set_ylabel(plot_style.format_label('memory footprint (MB)'))
        
        format_subplot_x_axis(ax)
        format_subplot_y_axis(ax, max_mem_subplot)
 
    legend_elements = [
        plt.Line2D([0], [0], label='YCSB', **plot_style.LINE_STYLES['YCSB']),
        plt.Line2D([0], [0], label='KVBench', **plot_style.LINE_STYLES['KVBench']),
        plt.Line2D([0], [0], label='X-Bench', **plot_style.LINE_STYLES['Tectonic'])
    ]
    fig4.legend(handles=legend_elements, loc='upper center', ncol=3, bbox_to_anchor=(0.5, 0.98), fontsize=12)
    
    plot_style.save_legend(fig4, f"{PLOTS_DIR}/fig4")
    
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    fig4.savefig(f"{PLOTS_DIR}/fig4.pdf", bbox_inches='tight')
    plt.close(fig4)

def plot_fig1_mem():
    workloads = ["I", "II", "III", "IV", "V"]
    fig1_mem, ax1_mem = plt.subplots(figsize=LATENCY_FIGURE_SIZE)
    
    x = np.arange(len(workloads))
    width = 0.35
    
    raw_data = []
    with open(f"{STATS_DIR}/generator_raw_metrics.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_data.append(row)
            
    tec_mem = []
    kv_mem = []
    
    for w in workloads:
        row = next((r for r in raw_data if r["Workload"] == w and r["WorkloadSet"] == "KVbench"), None)
        if row:
            tec_mem.append(float(row["Tectonic+_PeakMem_MB"]) if row["Tectonic+_PeakMem_MB"] else 0.0)
            kv_mem.append(float(row["KVbench_PeakMem_MB"]) if row["KVbench_PeakMem_MB"] else 0.0)
        else:
            tec_mem.append(0.0)
            kv_mem.append(0.0)
            
    ax1_mem.bar(x - width/2, kv_mem, width, label='KVbench', facecolor='none', edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['KVbench'])
    ax1_mem.bar(x + width/2, tec_mem, width, label='Tectonic+', facecolor='none', edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['Tectonic'])
    
    ax1_mem.set_xticks(x)
    ax1_mem.set_xticklabels(workloads)
    
    plot_style.apply_plot_style(ax1_mem, ylabel=' memory footprint(MB)')
    
    plot_style.save_legend(ax1_mem, f"{PLOTS_DIR}/fig1_mem")
    
    fig1_mem.tight_layout()
    fig1_mem.savefig(f"{PLOTS_DIR}/fig1_mem.pdf", bbox_inches='tight')
    plt.close(fig1_mem)

def plot_fig3_mem():
    workloads = ["A", "B", "C", "D", "E", "F"]
    fig3_mem, ax3_mem = plt.subplots(figsize=LATENCY_FIGURE_SIZE)
    
    x = np.arange(len(workloads))
    width = 0.25  # three bars side-by-side
    
    raw_data = []
    with open(f"{STATS_DIR}/generator_raw_metrics.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_data.append(row)
            
    for idx, w in enumerate(workloads):
        row = next((r for r in raw_data if r["Workload"] == w and r["WorkloadSet"] == "YCSB"), None)
        if row:
            # YCSB peak memory is the maximum VmRSS across load and run phase
            ycsb_load_mem = float(row["YCSB_LoadPeakMem_MB"]) if row["YCSB_LoadPeakMem_MB"] else 0.0
            ycsb_run_mem = float(row["YCSB_RunPeakMem_MB"]) if row["YCSB_RunPeakMem_MB"] else 0.0
            ycsb_val = max(ycsb_load_mem, ycsb_run_mem)
            lbl = 'YCSB' if idx == 0 else None
            ax3_mem.bar(idx - width, ycsb_val, width, label=lbl, facecolor='none', edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['YCSB'])
            
            # KVbench peak memory (missing for F)
            if w != "F" and row["KVbench_PeakMem_MB"]:
                kv_val = float(row["KVbench_PeakMem_MB"])
                lbl = 'KVbench' if idx == 0 else None
                ax3_mem.bar(idx, kv_val, width, label=lbl, facecolor='none', edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['KVbench'])

            # Tectonic+ peak memory
            tec_val = float(row["Tectonic+_PeakMem_MB"]) if row["Tectonic+_PeakMem_MB"] else 0.0
            lbl = 'Tectonic+' if idx == 0 else None
            ax3_mem.bar(idx + width, tec_val, width, label=lbl, facecolor='none', edgecolor='black', linewidth=0.5, hatch=TOOL_HATCHES['Tectonic'])

    x_ticks = [idx for idx, w in enumerate(workloads)]
    x_tick_labels = workloads

    ax3_mem.set_xticks(x_ticks)
    ax3_mem.set_xticklabels(x_tick_labels)
    
    plot_style.apply_plot_style(ax3_mem, ylabel='memory footprint(MB)')
    ax3_mem.tick_params(axis='x', which='both', bottom=False, top=False)
    
    plot_style.save_legend(ax3_mem, f"{PLOTS_DIR}/fig3_mem")
    
    fig3_mem.tight_layout()
    fig3_mem.savefig(f"{PLOTS_DIR}/fig3_mem.pdf", bbox_inches='tight')
    plt.close(fig3_mem)

if __name__ == "__main__":
    plot_fig1_fig2()
    plot_fig3_fig4()
    plot_fig1_mem()
    plot_fig3_mem()
    print("All comparison figures created successfully.")
