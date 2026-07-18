#!/usr/bin/env python3
"""
Plot: YCSB vs X-Bench Disk Bytes I/O rate (time-series).
========================================================
Produces PDF figures comparing the rate of bytes read and written over time.
Plotted metrics are read_mb_s and write_mb_s over elapsed_s.

Style requirements (AGENTS.md):
  - Strictly use LinLibertine_Mah.ttf font.
  - Consistent 20 pt font size for titles, labels, ticks, and legends.
  - All x/y axis labels in regular lowercase except YCSB, M, s.
  - Both numeric x and y-axis must start with 0 (explicitly tick/label both zeroes).
  - YCSB: Color 'grey', marker '^'.
  - X-Bench: Color 'tab:red', marker 's' (replacing 'Tectonic' label with 'X-Bench').
  - Read vs Write styling:
      Read: Dotted line style (':')
      Write: Solid/dash-dot line style ('-' for YCSB, '-.' for X-Bench)
  - No grid lines or background decorative lines.
  - All 4 spines visible (closed rectangle).
  - Legend exported to a separate PDF file (*_legend.pdf).
  - Size: (5, 3.6).
  - PDF format only.
"""

import os
import sys
import json
import csv
import numpy as np
import matplotlib

matplotlib.rcParams["text.usetex"] = True
import matplotlib.font_manager as fm
from matplotlib.lines import Line2D
import matplotlib.pyplot as plt

# ── Paths & Config ───────────────────────────────────────────────────────────
FONT_PATH = "/home/cc/Tectonic/LinLibertine_Mah.ttf"
if os.path.exists(FONT_PATH):
    fm.fontManager.addfont(FONT_PATH)
    prop = fm.FontProperties(fname=FONT_PATH)
    matplotlib.rcParams["font.family"] = prop.get_name()

DATA_DIR = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_io_bytes"
SCALES = [262144, 524288, 1048576, 2097152, 4194304]
DATABASES = ["rocksdb", "redis", "cassandra", "scylla"]
DB_LABELS = {
    "rocksdb": "RocksDB",
    "redis": "Redis",
    "cassandra": "Cassandra",
    "scylla": "ScyllaDB",
}

FONT_SIZE = 20
FIGSIZE = (5, 3.6)
LEGEND_FIGSIZE = (7.5, 1.2)

YCSB_COLOR = "grey"
TEC_COLOR = "tab:red"
YCSB_MARKER = "^"
TEC_MARKER = "s"

# ── Helper functions ──────────────────────────────────────────────────────────
def load_csv(path):
    xs, reads, writes = [], [], []
    if not os.path.exists(path):
        return xs, reads, writes
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                xs.append(float(row["elapsed_s"]))
                reads.append(float(row["read_mb_s"]))
                writes.append(float(row["write_mb_s"]))
            except (KeyError, ValueError):
                continue
    return xs, reads, writes

def style_axis(ax):
    # Ensure both axes start at 0
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    
    # Explicitly label the zero on both x and y axis
    xticks = list(ax.get_xticks())
    if 0.0 not in xticks:
        xticks = [x for x in xticks if x >= 0]
        xticks.insert(0, 0.0)
        ax.set_xticks(xticks)
        
    yticks = list(ax.get_yticks())
    if 0.0 not in yticks:
        yticks = [y for y in yticks if y >= 0]
        yticks.insert(0, 0.0)
        ax.set_yticks(yticks)
        
    ax.set_xlabel("time (s)", fontsize=FONT_SIZE)
    ax.set_ylabel("bytes transferred (Mb)", fontsize=FONT_SIZE)
    ax.tick_params(axis="both", labelsize=FONT_SIZE, colors="black", direction="in")
    
    # Closed frame (4 spines visible)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)

# ── Main plotting logic ───────────────────────────────────────────────────────
def plot_db_scale(db, scale):
    ycsb_path = f"{DATA_DIR}/{db}_scale{scale}_ycsb_io_bytes.csv"
    tec_path  = f"{DATA_DIR}/{db}_scale{scale}_tectonic_io_bytes.csv"
    
    yx, yr, yw = load_csv(ycsb_path)
    tx, tr, tw = load_csv(tec_path)
    
    if not yx and not tx:
        return # No data
        
    fig, ax = plt.subplots(figsize=FIGSIZE)
    
    # Compute cumulative sums of MBs
    cum_yr = np.cumsum(yr) if yr else []
    cum_yw = np.cumsum(yw) if yw else []
    cum_tr = np.cumsum(tr) if tr else []
    cum_tw = np.cumsum(tw) if tw else []
    
    # Select markers every N points if timeseries is long
    y_mark_every = max(1, len(yx) // 10)
    t_mark_every = max(1, len(tx) // 10)
    
    # Plot YCSB
    if yx:
        # Read
        ax.plot(yx, cum_yr, color=YCSB_COLOR, linestyle=":", marker=YCSB_MARKER,
                markersize=6, markerfacecolor="white", markeredgecolor=YCSB_COLOR,
                linewidth=1.5, markevery=y_mark_every)
        # Write
        ax.plot(yx, cum_yw, color=YCSB_COLOR, linestyle="-", marker=YCSB_MARKER,
                markersize=6, markerfacecolor="white", markeredgecolor=YCSB_COLOR,
                linewidth=1.8, markevery=y_mark_every)
                
    # Plot X-Bench
    if tx:
        # Read
        ax.plot(tx, cum_tr, color=TEC_COLOR, linestyle=":", marker=TEC_MARKER,
                markersize=6, markerfacecolor="white", markeredgecolor=TEC_COLOR,
                linewidth=1.5, markevery=t_mark_every)
        # Write
        ax.plot(tx, cum_tw, color=TEC_COLOR, linestyle="-.", marker=TEC_MARKER,
                markersize=6, markerfacecolor=TEC_COLOR, markeredgecolor=TEC_COLOR,
                linewidth=1.8, markevery=t_mark_every)
                
    style_axis(ax)
    
    # Title
    db_lbl = DB_LABELS.get(db, db)
    ax.set_title(db_lbl, fontsize=FONT_SIZE)
    fig.tight_layout(pad=1.0)
    
    base_path = f"{DATA_DIR}/{db}_scale{scale}_io_bytes"
    fig.savefig(f"{base_path}.pdf", bbox_inches="tight", pad_inches=0.08)
    fig.savefig(f"{base_path}.png", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    print(f"  saved: {base_path}.pdf")
    print(f"  saved: {base_path}.png")

def plot_legend(scale):
    fig, ax = plt.subplots(figsize=LEGEND_FIGSIZE)
    ax.axis("off")
    
    handles = [
        Line2D([0], [0], color=YCSB_COLOR, linestyle="-", marker=YCSB_MARKER,
               markersize=7, markerfacecolor="white", markeredgecolor=YCSB_COLOR,
               linewidth=2.0, label="YCSB write"),
        Line2D([0], [0], color=YCSB_COLOR, linestyle=":", marker=YCSB_MARKER,
               markersize=7, markerfacecolor="white", markeredgecolor=YCSB_COLOR,
               linewidth=2.0, label="YCSB read"),
        Line2D([0], [0], color=TEC_COLOR, linestyle="-.", marker=TEC_MARKER,
               markersize=7, markerfacecolor=TEC_COLOR, markeredgecolor=TEC_COLOR,
               linewidth=2.0, label="X-Bench write"),
        Line2D([0], [0], color=TEC_COLOR, linestyle=":", marker=TEC_MARKER,
               markersize=7, markerfacecolor="white", markeredgecolor=TEC_COLOR,
               linewidth=2.0, label="X-Bench read"),
    ]
    
    ax.legend(handles=handles, loc="center", ncol=2, fontsize=FONT_SIZE, frameon=False)
    
    base_path = f"{DATA_DIR}/io_bytes_scale{scale}_legend"
    fig.savefig(f"{base_path}.pdf", bbox_inches="tight", pad_inches=0.08)
    fig.savefig(f"{base_path}.png", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    print(f"  saved: {base_path}.pdf")
    print(f"  saved: {base_path}.png")

def main():
    if not os.path.exists(DATA_DIR):
        print(f"Data directory not found: {DATA_DIR}")
        sys.exit(0)
        
    print(f"Plotting bytes I/O time-series from {DATA_DIR}")
    plotted_any = False
    for scale in SCALES:
        # Check if we have any data at this scale
        has_scale_data = False
        for db in DATABASES:
            y_path = f"{DATA_DIR}/{db}_scale{scale}_ycsb_io_bytes.csv"
            t_path = f"{DATA_DIR}/{db}_scale{scale}_tectonic_io_bytes.csv"
            if os.path.exists(y_path) or os.path.exists(t_path):
                has_scale_data = True
                plot_db_scale(db, scale)
                plotted_any = True
        if has_scale_data:
            plot_legend(scale)
            
    if not plotted_any:
        print("No CSV data files found to plot.")
    else:
        print("Done plotting.")

if __name__ == "__main__":
    main()
