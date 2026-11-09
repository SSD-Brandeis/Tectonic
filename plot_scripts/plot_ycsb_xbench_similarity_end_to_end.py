#!/usr/bin/env python3
"""
Plot: YCSB vs X-Bench Workload A execution time.

Produces PDF line plots from results.json. The plotted metric is
wall_time_s, which is the elapsed time to execute a full trace against a
database.
"""
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])

import json
import os
import sys

import matplotlib

matplotlib.rcParams["text.usetex"] = True
import matplotlib.font_manager as fm
from matplotlib.lines import Line2D
import matplotlib.pyplot as plt
import numpy as np


RESULTS_PATH = REPO_ROOT + "/data/ycsb_xbench_similarity_all_dbs/results.json"
OUT_DIR = REPO_ROOT + "/data/ycsb_xbench_similarity_all_dbs"

FONT_PATH = REPO_ROOT + "/LinLibertine_Mah.ttf"
if os.path.exists(FONT_PATH):
    fm.fontManager.addfont(FONT_PATH)
    prop = fm.FontProperties(fname=FONT_PATH)
    matplotlib.rcParams["font.family"] = prop.get_name()

SCALES = [2**18, 2**19, 2**20, 2**21, 2**22]
DATABASES = ["rocksdb", "redis", "cassandra", "scylla"]
DB_LABELS = {
    "rocksdb": "RocksDB",
    "redis": "Redis",
    "cassandra": "Cassandra",
    "scylla": "ScyllaDB",
}

YCSB_COLOR = "grey"
X_COLOR = "tab:red"
YCSB_MARKER = "^"
X_MARKER = "s"
YCSB_LINESTYLE = "-"
X_LINESTYLE = "-."
FONT_SIZE = 20
SINGLE_FIGSIZE = (6.4, 4.8)
ALL_DBS_FIGSIZE = (13.2, 9.6)
LEGEND_FIGSIZE = (7.0, 1.1)


def scale_label(scale):
    return f"$2^{{{scale.bit_length() - 1}}}$"


def get_series(db_results, workload):
    xs = []
    ys = []
    for idx, scale in enumerate(SCALES, start=1):
        pair = db_results.get(str(scale))
        if not pair:
            continue
        metrics = pair.get(workload, {})
        if "wall_time_s" not in metrics:
            continue
        xs.append(idx)
        ys.append(metrics["wall_time_s"])
    return xs, ys


def style_axis(ax):
    ax.set_xticks(np.arange(1, len(SCALES) + 1))
    ax.set_xticklabels([scale_label(s) for s in SCALES], fontsize=FONT_SIZE)
    ax.set_xlim(0.7, len(SCALES) + 0.3)
    ax.set_ylim(bottom=0)
    # Ensure 0 is explicitly ticked/labeled on y-axis
    yticks = list(ax.get_yticks())
    if 0.0 not in yticks:
        yticks = [y for y in yticks if y >= 0]
        yticks.insert(0, 0.0)
        ax.set_yticks(yticks)
    ax.set_xlabel("operation count", fontsize=FONT_SIZE)
    ax.set_ylabel("execution time (s)", fontsize=FONT_SIZE)
    ax.tick_params(axis="both", labelsize=FONT_SIZE, colors="black", direction="in")
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)


def plot_database(db, db_results):
    fig, ax = plt.subplots(figsize=SINGLE_FIGSIZE)
    yx, yy = get_series(db_results, "ycsb")
    tx, ty = get_series(db_results, "xbench")

    if yx:
        ax.plot(
            yx,
            yy,
            color=YCSB_COLOR,
            linestyle=YCSB_LINESTYLE,
            marker=YCSB_MARKER,
            markersize=7.0,
            markerfacecolor="white",
            markeredgecolor=YCSB_COLOR,
            linewidth=2.0,
            label="YCSB",
        )
    if tx:
        ax.plot(
            tx,
            ty,
            color=X_COLOR,
            linestyle=X_LINESTYLE,
            marker=X_MARKER,
            markersize=7.0,
            markerfacecolor=X_COLOR,
            markeredgecolor=X_COLOR,
            linewidth=2.0,
            label="X-Bench+",
        )

    style_axis(ax)
    ax.set_title(DB_LABELS.get(db, db), fontsize=FONT_SIZE)
    fig.tight_layout(pad=1.0)

    base = f"{OUT_DIR}/{db}_end_to_end_wall_time"
    fig.savefig(f"{base}.pdf", bbox_inches="tight", pad_inches=0.08)
    fig.savefig(f"{base}.png", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    print(f"  saved: {base}.pdf")
    print(f"  saved: {base}.png")


def plot_all_databases(db_results_all):
    fig, axes = plt.subplots(2, 2, figsize=ALL_DBS_FIGSIZE, sharex=True)
    axes = axes.ravel()

    for ax, db in zip(axes, DATABASES):
        db_results = db_results_all.get(db, {})
        yx, yy = get_series(db_results, "ycsb")
        tx, ty = get_series(db_results, "xbench")
        if yx:
            ax.plot(
                yx,
                yy,
                color=YCSB_COLOR,
                linestyle=YCSB_LINESTYLE,
                marker=YCSB_MARKER,
                markersize=6.5,
                markerfacecolor="white",
                markeredgecolor=YCSB_COLOR,
                linewidth=1.8,
            )
        if tx:
            ax.plot(
                tx,
                ty,
                color=X_COLOR,
                linestyle=X_LINESTYLE,
                marker=X_MARKER,
                markersize=6.5,
                markerfacecolor=X_COLOR,
                markeredgecolor=X_COLOR,
                linewidth=1.8,
            )
        style_axis(ax)
        ax.set_title(DB_LABELS.get(db, db), fontsize=FONT_SIZE)

    fig.suptitle("YCSB vs X-Bench+ workload A execution time", fontsize=FONT_SIZE, y=0.99)
    fig.tight_layout(pad=1.0, rect=(0, 0, 1, 0.96))

    base = f"{OUT_DIR}/all_dbs_end_to_end_wall_time"
    fig.savefig(f"{base}.pdf", bbox_inches="tight", pad_inches=0.08)
    fig.savefig(f"{base}.png", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    print(f"  saved: {base}.pdf")
    print(f"  saved: {base}.png")


def plot_legend():
    fig, ax = plt.subplots(figsize=LEGEND_FIGSIZE)
    ax.axis("off")
    handles = [
        Line2D(
            [0],
            [0],
            color=YCSB_COLOR,
            linestyle=YCSB_LINESTYLE,
            marker=YCSB_MARKER,
            markersize=7,
            markerfacecolor="white",
            markeredgecolor=YCSB_COLOR,
            linewidth=2.0,
            label="YCSB",
        ),
        Line2D(
            [0],
            [0],
            color=X_COLOR,
            linestyle=X_LINESTYLE,
            marker=X_MARKER,
            markersize=7,
            markerfacecolor=X_COLOR,
            markeredgecolor=X_COLOR,
            linewidth=2.0,
            label="X-Bench+",
        ),
    ]
    ax.legend(handles=handles, loc="center", ncol=2, fontsize=FONT_SIZE, frameon=False)
    base = f"{OUT_DIR}/end_to_end_wall_time_legend"
    fig.savefig(f"{base}.pdf", bbox_inches="tight", pad_inches=0.08)
    fig.savefig(f"{base}.png", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    print(f"  saved: {base}.pdf")
    print(f"  saved: {base}.png")


def main():
    if not os.path.exists(RESULTS_PATH):
        print(f"No results file found: {RESULTS_PATH}")
        sys.exit(0)

    data = json.load(open(RESULTS_PATH))
    db_results_all = data.get("results", {})
    if not db_results_all:
        print("No results yet; skipping plot.")
        sys.exit(0)

    print(f"Plotting execution time from {RESULTS_PATH}")
    for db in DATABASES:
        if db not in db_results_all:
            print(f"  skip {db} (no data)")
            continue
        plot_database(db, db_results_all[db])
    plot_all_databases(db_results_all)
    plot_legend()
    print("Done.")


if __name__ == "__main__":
    main()
