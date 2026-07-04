#!/usr/bin/env python3
import os
import sys
import json
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager
import argparse
import re

# Font configuration
FONT_PATH = "/home/cc/Tectonic/LinLibertine_Mah.ttf"
if not os.path.exists(FONT_PATH):
    raise FileNotFoundError(f"Strict font file not found: {FONT_PATH}")

font_manager.fontManager.addfont(FONT_PATH)
prop = font_manager.FontProperties(fname=FONT_PATH)
plt.rcParams["font.family"] = prop.get_name()
plt.rcParams["font.weight"] = "bold"
plt.rcParams["font.size"] = 12
plt.rcParams["axes.titlesize"] = 14
plt.rcParams["axes.labelsize"] = 12
plt.rcParams["xtick.labelsize"] = 10
plt.rcParams["ytick.labelsize"] = 10
plt.rcParams["legend.fontsize"] = 10

def to_small_caps(text):
    mapping = {
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
        'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
        'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ', 'u': 'ᴜ',
        'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ'
    }
    units = {'s', 'us', 'ms', 'ns', 'sec', 'seconds', 'ops/s', 'ops/sec', 'mb', 'kb', 'gb', 'b'}
    
    def replace_word(match):
        word = match.group(0)
        if word.lower() in units:
            return word
        return "".join(mapping.get(c, c) for c in word.lower())
        
    return re.sub(r'[a-zA-Z]+', replace_word, text)

# Add parent directory to path to import plot_style
sys.path.append("/home/cc/Tectonic/plot_scripts")
import plot_style

# Cohesive Palette for Tectonic vs YCSB
COLORS = {
    "Tectonic": "tab:red",
    "YCSB": "grey"
}

def generate_box_plots(results_file, out_dir, is_test):
    if not os.path.exists(results_file):
        print(f"Error: Results file not found at {results_file}")
        sys.exit(1)
        
    with open(results_file, "r") as f:
        results = json.load(f)
        
    for db, db_data in results.items():
        print(f"Generating plot for {db}...")
        
        # Sort scales numerically
        scales_str = sorted(db_data.keys(), key=lambda x: int(x))
        scales = [int(s) for s in scales_str]
        
        fig, ax = plt.subplots(figsize=(8, 6))
        
        # Prepare bxp structures for Tectonic and YCSB
        tec_boxes = []
        ycsb_boxes = []
        
        for scale_str in scales_str:
            scale_data = db_data[scale_str]
            for gen in ["tectonic", "ycsb"]:
                gen_data = scale_data[gen]
                metrics = gen_data.get("metrics", {})
                op_metrics = metrics.get("Point Query") or metrics.get("Update") or metrics.get("Insert") or {}
                
                min_val = op_metrics.get("Minimum Latency", 0.0)
                p25_val = op_metrics.get("25th Percentile Latency", 0.0)
                p50_val = op_metrics.get("50th Percentile Latency", 0.0)
                p75_val = op_metrics.get("75th Percentile Latency", 0.0)
                p99_val = op_metrics.get("99th Percentile Latency", 0.0)
                mean_val = op_metrics.get("Average Latency", 0.0)
                
                box = {
                    "whislo": min_val,
                    "q1": p25_val,
                    "med": p50_val,
                    "q3": p75_val,
                    "whishi": p99_val,
                    "mean": mean_val,
                    "fliers": []
                }
                
                if gen == "tectonic":
                    tec_boxes.append(box)
                else:
                    ycsb_boxes.append(box)
                    
        x_positions = np.arange(len(scales)) * 2.0
        tec_pos = x_positions - 0.35
        ycsb_pos = x_positions + 0.35
        
        # Draw Tectonic boxes
        bp_tec = ax.bxp(
            tec_boxes,
            positions=tec_pos,
            widths=0.5,
            showmeans=True,
            meanline=True,
            patch_artist=True,
            boxprops=dict(facecolor="tab:red", edgecolor="tab:red", linewidth=1.0),
            whiskerprops=dict(color="black", linewidth=1.0),
            capprops=dict(color="black", linewidth=1.0),
            medianprops=dict(color="red", linewidth=1.5),
            meanprops=dict(color="yellow", linewidth=1.0, linestyle="--")
        )
        
        # Draw YCSB boxes
        bp_ycsb = ax.bxp(
            ycsb_boxes,
            positions=ycsb_pos,
            widths=0.5,
            showmeans=True,
            meanline=True,
            patch_artist=True,
            boxprops=dict(facecolor="white", edgecolor="grey", hatch="///", linewidth=1.0),
            whiskerprops=dict(color="black", linewidth=1.0),
            capprops=dict(color="black", linewidth=1.0),
            medianprops=dict(color="red", linewidth=1.5),
            meanprops=dict(color="yellow", linewidth=1.0, linestyle="--")
        )
        
        # Configure axes
        ax.set_xticks(x_positions)
        if is_test:
            x_labels = [str(s) for s in scales]
        else:
            x_labels = []
            for s in scales:
                power = int(round(np.log2(s)))
                x_labels.append(f"$2^{{{power}}}$")
                
        ax.set_xticklabels(x_labels)
        
        db_title = db.upper() if db != "rocksdb" else "RocksDB"
        if db == "scylla": db_title = "ScyllaDB"
        if db == "cassandra": db_title = "Cassandra"
        if db == "redis": db_title = "Redis"
        
        db_name_clean = db.lower()
        
        # Add legend
        from matplotlib.patches import Patch
        legend_patches = [
            Patch(facecolor="tab:red", edgecolor="tab:red", label=plot_style.format_label("Tectonic")),
            Patch(facecolor="white", edgecolor="grey", hatch="///", label=plot_style.format_label("YCSB"))
        ]
        ax.legend(handles=legend_patches, frameon=False, loc="upper right")
        
        plot_style.apply_plot_style(ax, 
                                 title=f"{db_title} Execution Latency Profile", 
                                 xlabel="Operation Count Scale", 
                                 ylabel="Database Execution Latency (us)")
        
        # Save legend separately
        plot_style.save_legend(fig, os.path.join(out_dir, f"{db_name_clean}_profile_comparison"))
        
        # Save figures
        plot_style.save_fig(fig, os.path.join(out_dir, f"{db_name_clean}_profile_comparison"))
        
        print(f"Plot saved for {db} successfully.")

def main():
    parser = argparse.ArgumentParser(description="Plot Tectonic vs YCSB performance evaluation results.")
    parser.add_argument("--test", action="store_true", help="Plot test scale results instead of production.")
    args = parser.parse_args()
    
    stats_dir = "/home/cc/Tectonic/data/ycsb_tectonic_profile"
    results_file = f"{stats_dir}/results.json"
    if not os.path.exists(results_file):
        partial_file = f"{stats_dir}/results_partial.json"
        if os.path.exists(partial_file):
            print(f"results.json not found, falling back to {partial_file}")
            results_file = partial_file
    
    out_dir = stats_dir
    
    print("=== Generating Grouped Box Plots ===")
    generate_box_plots(results_file, out_dir, args.test)
    print("All plots generated successfully!")

if __name__ == "__main__":
    main()
