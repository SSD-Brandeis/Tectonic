#!/usr/bin/env python3
"""
Plot: YCSB vs Tectonic Workload A Similarity — All Four Databases
=================================================================
Produces 4 PDF figures (one per database).
Each figure has 3 subplots (insert, point query, update).
Each subplot: box plots at each scale (2^18..2^22) for YCSB vs Tectonic.

Box plot statistics drawn from harness/tectonic-cli percentile data:
  whislo = p0   (minimum)
  q1     = p25
  med    = p50  (median)
  q3     = p75
  whishi = p99  (99th percentile)

Styles (AGENTS.md):
  YCSB     : grey, hollow (facecolor white), hatch ///
  Tectonic : tab:red, solid filled
  Legend   : separate PDF (_legend.pdf)
  Font     : LinLibertine_Mah.ttf, text.usetex=True
  Labels   : lowercase except YCSB (always uppercase)
"""

import os, sys, json
import matplotlib
matplotlib.rcParams['text.usetex'] = True
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import matplotlib.ticker as ticker
import numpy as np

# ── Font ──────────────────────────────────────────────────────────────────────
fm.fontManager.addfont('/home/cc/Tectonic/LinLibertine_Mah.ttf')
prop = fm.FontProperties(fname='/home/cc/Tectonic/LinLibertine_Mah.ttf')
matplotlib.rcParams['font.family'] = prop.get_name()

RESULTS_PATH = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_all_dbs/results.json"
OUT_DIR      = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_all_dbs"

# ── Styles ────────────────────────────────────────────────────────────────────
YCSB_COLOR = 'grey'
TEC_COLOR  = 'tab:red'
BOX_WIDTH  = 0.35

# Operations to plot and their display labels (lowercase per AGENTS.md)
OPS = [
    ("insert",      "insert"),
    ("point_query", "point query"),
    ("update",      "update"),
]

SCALES    = [2**18, 2**19, 2**20, 2**21, 2**22]
DATABASES = ["rocksdb", "redis", "cassandra", "scylla"]
DB_LABELS = {
    "rocksdb":   "RocksDB",
    "redis":     "Redis",
    "cassandra": "Cassandra",
    "scylla":    "ScyllaDB",
}

# =============================================================================
def make_box_stats(lat_dict, op_key):
    """Build bxp stats dict from percentile data for one operation."""
    d = lat_dict.get(op_key, {})
    if not d:
        return None
    return {
        "whislo": d.get("p0",  0.0),
        "q1":     d.get("p25", 0.0),
        "med":    d.get("p50", 0.0),
        "q3":     d.get("p75", 0.0),
        "whishi": d.get("p99", 0.0),
        "fliers": [],
    }

def scale_label(s):
    exp = s.bit_length() - 1
    return f"$2^{{{exp}}}$"

def plot_database(db, db_results):
    """
    Plot one database: 3 subplots (insert / point query / update).
    Each subplot: grouped box plots per scale.
    """
    fig, axes = plt.subplots(1, 3, figsize=(8.0, 2.6))

    available_scales = sorted(int(k) for k in db_results.keys())
    n_scales = len(SCALES)

    # x-positions: one integer per scale, YCSB offset left, Tectonic offset right
    x_centers = np.arange(1, n_scales + 1, dtype=float)
    off = BOX_WIDTH * 0.6

    for ax, (op_key, op_label) in zip(axes, OPS):
        ycsb_boxes = []
        tec_boxes  = []
        ycsb_pos   = []
        tec_pos    = []

        for i, scale in enumerate(SCALES):
            skey = str(scale)
            if skey not in db_results:
                continue
            pair = db_results[skey]

            ys = make_box_stats(pair.get("ycsb",  {}).get("latency_us", {}), op_key)
            ts = make_box_stats(pair.get("tectonic", {}).get("latency_us", {}), op_key)

            if ys:
                ycsb_boxes.append(ys)
                ycsb_pos.append(x_centers[i] - off)
            if ts:
                tec_boxes.append(ts)
                tec_pos.append(x_centers[i] + off)

        # Draw YCSB boxes (hollow, grey, hatch ///)
        if ycsb_boxes:
            bp_y = ax.bxp(ycsb_boxes, positions=ycsb_pos,
                          widths=BOX_WIDTH, patch_artist=True,
                          showfliers=False, manage_ticks=False)
            for patch in bp_y["boxes"]:
                patch.set_facecolor("white")
                patch.set_edgecolor(YCSB_COLOR)
                patch.set_hatch("///")
                patch.set_linewidth(0.8)
            for element in ["whiskers","caps","medians"]:
                for line in bp_y[element]:
                    line.set_color(YCSB_COLOR)
                    line.set_linewidth(0.8)

        # Draw Tectonic boxes (solid red)
        if tec_boxes:
            bp_t = ax.bxp(tec_boxes, positions=tec_pos,
                          widths=BOX_WIDTH, patch_artist=True,
                          showfliers=False, manage_ticks=False)
            for patch in bp_t["boxes"]:
                patch.set_facecolor(TEC_COLOR)
                patch.set_edgecolor(TEC_COLOR)
                patch.set_linewidth(0.8)
            for element in ["whiskers","caps","medians"]:
                for line in bp_t[element]:
                    line.set_color("white" if element == "medians" else TEC_COLOR)
                    line.set_linewidth(0.8)

        # X axis: one tick per scale
        ax.set_xticks(x_centers)
        ax.set_xticklabels([scale_label(s) for s in SCALES], fontsize=7)
        ax.set_xlim(0.3, n_scales + 0.7)
        ax.set_xlabel("operation count", fontsize=7)
        ax.set_ylim(bottom=0)
        ax.tick_params(axis='y', labelsize=7)
        ax.set_title(op_label, fontsize=8)

    axes[0].set_ylabel(r"latency ($\mu$s)", fontsize=8)
    fig.suptitle(
        r"YCSB vs Tectonic workload A latency — " + DB_LABELS.get(db, db),
        fontsize=9, y=1.01
    )
    plt.tight_layout(pad=0.5)

    base = f"{OUT_DIR}/{db}_similarity"
    fig.savefig(f"{base}.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"  saved: {base}.pdf")

def plot_legend():
    fig, ax = plt.subplots(figsize=(2.8, 0.5))
    ax.axis("off")
    handles = [
        mpatches.Patch(facecolor="white", edgecolor=YCSB_COLOR,
                       hatch="///", label="YCSB"),
        mpatches.Patch(facecolor=TEC_COLOR, edgecolor=TEC_COLOR,
                       label="Tectonic"),
    ]
    ax.legend(handles=handles, loc="center", ncol=2, fontsize=9, frameon=False)
    base = f"{OUT_DIR}/similarity_legend"
    fig.savefig(f"{base}.pdf", bbox_inches="tight")
    plt.close(fig)
    print(f"  saved: {base}.pdf")

# =============================================================================
def main():
    if not os.path.exists(RESULTS_PATH):
        print(f"No results file found: {RESULTS_PATH}")
        sys.exit(0)

    data = json.load(open(RESULTS_PATH))
    db_results_all = data.get("results", {})

    if not db_results_all:
        print("No results yet — skipping plot.")
        sys.exit(0)

    print(f"Plotting from {RESULTS_PATH}")
    for db in DATABASES:
        if db not in db_results_all or not db_results_all[db]:
            print(f"  skip {db} (no data yet)")
            continue
        print(f"  plotting {db}...")
        plot_database(db, db_results_all[db])

    plot_legend()
    print("Done.")

if __name__ == "__main__":
    main()
