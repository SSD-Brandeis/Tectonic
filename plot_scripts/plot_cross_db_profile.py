#!/usr/bin/env python3
import os
import json
import re
import matplotlib.pyplot as plt
import numpy as np
from plot_style import DB_COLORS, DB_HATCHES, apply_plot_style, save_fig, format_label, save_legend

STATS_DIR = "/home/cc/Tectonic/data/cross_db_profile"
PLOTS_DIR = "/home/cc/Tectonic/experiment_plots_cross_db_profile"
os.makedirs(PLOTS_DIR, exist_ok=True)

# Regex patterns
THROUGHPUT_PAT = re.compile(r"\[Overall\] Throughput \(using start and end time\):\s*([\d\.]+)\s*ops/sec")
PHASE_PAT = re.compile(r"\[\[\*\*\*Stats for (.*?)\*\*\*\]\]")
OVERALL_PAT = re.compile(r"\[\[\*\*\*Overall Stats\*\*\*\]\]")

def parse_log_file(filepath):
    """
    Parses a Tectonic benchmark log file and extracts throughput values.
    Returns a dictionary of phase_name -> throughput (float)
    """
    results = {}
    current_phase = "Overall"
    
    if not os.path.exists(filepath):
        return results

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            
            # Check for phase headers
            m_phase = PHASE_PAT.search(line)
            if m_phase:
                current_phase = m_phase.group(1).strip()
                continue
                
            m_overall = OVERALL_PAT.search(line)
            if m_overall:
                current_phase = "Overall"
                continue
                
            # Check for throughput
            m_tp = THROUGHPUT_PAT.search(line)
            if m_tp:
                tp_val = float(m_tp.group(1))
                results[current_phase] = tp_val
                
    return results

def main():
    print("=== Step 1: Parsing stats logs ===")
    databases = ["rocksdb", "redis", "cassandra", "scylla"]
    
    ycsb_wls = ["a", "b", "c", "d", "e", "f"]
    kv_wls = ["i", "ii", "iii", "iv", "v"]
    db_bench_wls = ["1", "2", "3", "4", "4b", "5"]
    
    # Store all parsed data
    # { db: { workload_name: { phase: throughput } } }
    data = {db: {} for db in databases}
    
    for db in databases:
        # Parse YCSB
        for wl in ycsb_wls:
            wl_name = f"ycsb_{wl}"
            log_path = os.path.join(STATS_DIR, f"{db}_{wl_name}.log")
            data[db][wl_name] = parse_log_file(log_path)
            
        # Parse KVBench
        for wl in kv_wls:
            wl_name = f"kvbench_{wl}"
            log_path = os.path.join(STATS_DIR, f"{db}_{wl_name}.log")
            data[db][wl_name] = parse_log_file(log_path)
            
        # Parse db_bench
        for wl in db_bench_wls:
            wl_name = f"db_bench_{wl}"
            log_path = os.path.join(STATS_DIR, f"{db}_{wl_name}.log")
            data[db][wl_name] = parse_log_file(log_path)
            
        # Parse Tectonic
        log_path = os.path.join(STATS_DIR, f"{db}_tectonic_1.log")
        data[db]["tectonic_1"] = parse_log_file(log_path)

    # Save summary json
    summary_path = os.path.join(STATS_DIR, "summary.json")
    with open(summary_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Summary JSON saved to {summary_path}")

    # Plot helper function
    def render_bar_chart(ax, title, categories, db_names, category_labels, get_tp_func):
        """
        Renders a grouped bar chart on ax.
        """
        x = np.arange(len(categories))
        width = 0.20
        offsets = [-1.5 * width, -0.5 * width, 0.5 * width, 1.5 * width]
        
        for idx, db in enumerate(db_names):
            y_vals = []
            for cat in categories:
                y_vals.append(get_tp_func(db, cat))
                
            db_label = "ScyllaDB" if db == "scylla" else db.capitalize()
            if db_label == "Rocksdb":
                db_label = "RocksDB"
                
            # Plot bars
            ax.bar(
                x + offsets[idx],
                y_vals,
                width,
                label=format_label(db_label.lower()),
                color=DB_COLORS[db_label],
                edgecolor="black",
                linewidth=0.8,
                hatch=DB_HATCHES[db_label]
            )
            
        ax.set_xticks(x)
        ax.set_xticklabels([format_label(l) for l in category_labels])
        apply_plot_style(ax, title, xlabel="workload type", ylabel="throughput (ops/sec)")
        ax.legend(frameon=False)

    print("=== Step 2: Generating individual and combined plots ===")
    
    # 1. YCSB Plot
    fig_ycsb, ax_ycsb = plt.subplots(figsize=(8, 5))
    def ycsb_tp(db, wl):
        # We look for "Overall" throughput of the ycsb workload
        return data[db].get(f"ycsb_{wl}", {}).get("Overall", 0.0)
    render_bar_chart(
        ax_ycsb, 
        "ycsb workload performance (single threaded)", 
        ycsb_wls, 
        databases, 
        [w.lower() for w in ycsb_wls], 
        ycsb_tp
    )
    save_legend(fig_ycsb, os.path.join(PLOTS_DIR, "ycsb_throughput"))
    save_fig(fig_ycsb, os.path.join(PLOTS_DIR, "ycsb_throughput"))

    # 2. KVBench Plot
    fig_kv, ax_kv = plt.subplots(figsize=(8, 5))
    def kv_tp(db, wl):
        return data[db].get(f"kvbench_{wl}", {}).get("Overall", 0.0)
    render_bar_chart(
        ax_kv, 
        "kvbench workload performance (single threaded)", 
        kv_wls, 
        databases, 
        [w.lower() for w in kv_wls], 
        kv_tp
    )
    save_legend(fig_kv, os.path.join(PLOTS_DIR, "kvbench_throughput"))
    save_fig(fig_kv, os.path.join(PLOTS_DIR, "kvbench_throughput"))

    # 3. db_bench Plot
    fig_dbb, ax_dbb = plt.subplots(figsize=(8, 5))
    def dbb_tp(db, wl):
        return data[db].get(f"db_bench_{wl}", {}).get("Overall", 0.0)
    render_bar_chart(
        ax_dbb, 
        "db_bench workload performance (single threaded)", 
        db_bench_wls, 
        databases, 
        db_bench_wls, 
        dbb_tp
    )
    save_legend(fig_dbb, os.path.join(PLOTS_DIR, "db_bench_throughput"))
    save_fig(fig_dbb, os.path.join(PLOTS_DIR, "db_bench_throughput"))

    # 4. Tectonic Plot
    fig_tec, ax_tec = plt.subplots(figsize=(8, 5))
    tectonic_phases = ["write heavy", "read heavy", "balanced"]
    def tec_tp(db, phase):
        return data[db].get("tectonic_1", {}).get(phase, 0.0)
    render_bar_chart(
        ax_tec, 
        "tectonic workload performance by phase (single threaded)", 
        tectonic_phases, 
        databases, 
        tectonic_phases, 
        tec_tp
    )
    save_legend(fig_tec, os.path.join(PLOTS_DIR, "tectonic_throughput"))
    save_fig(fig_tec, os.path.join(PLOTS_DIR, "tectonic_throughput"))

    # 5. Combined 2x2 Plot
    fig_comb, axs = plt.subplots(2, 2, figsize=(16, 10))
    render_bar_chart(axs[0, 0], "(a) ycsb workloads", ycsb_wls, databases, [w.lower() for w in ycsb_wls], ycsb_tp)
    render_bar_chart(axs[0, 1], "(b) kvbench workloads", kv_wls, databases, [w.lower() for w in kv_wls], kv_tp)
    render_bar_chart(axs[1, 0], "(c) db_bench workloads", db_bench_wls, databases, db_bench_wls, dbb_tp)
    render_bar_chart(axs[1, 1], "(d) tectonic workload phases", tectonic_phases, databases, tectonic_phases, tec_tp)
    
    fig_comb.suptitle(format_label("cross-database benchmark performance suite (single threaded)"), fontsize=16, fontweight="bold", y=0.98)
    save_legend(fig_comb, os.path.join(PLOTS_DIR, "combined_db_profile_throughputs"))
    save_fig(fig_comb, os.path.join(PLOTS_DIR, "combined_db_profile_throughputs"))
    
    print("Plotting complete! Saved all figures to: " + PLOTS_DIR)

if __name__ == "__main__":
    main()
