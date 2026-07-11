#!/usr/bin/env python3
"""
Plot: YCSB vs Tectonic Workload A end-to-end runtime.

Produces PDF+PNG line plots from results.json. The plotted metric is
wall_time_s, which is the elapsed time to execute a full trace against a
database.
"""

import json
import os
import sys

import matplotlib

matplotlib.rcParams["text.usetex"] = True
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np


RESULTS_PATH = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_all_dbs/results.json"
OUT_DIR = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_all_dbs"

FONT_PATH = "/home/cc/Tectonic/LinLibertine_Mah.ttf"
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
TEC_COLOR = "tab:red"


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
    ax.set_xticklabels([scale_label(s) for s in SCALES], fontsize=7)
    ax.set_xlim(0.7, len(SCALES) + 0.3)
    ax.set_xlabel("operation count scale", fontsize=7)
    ax.set_ylabel("wall time (s)", fontsize=8)
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(True, axis="y", linewidth=0.35, alpha=0.35)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)


def plot_database(db, db_results):
    fig, ax = plt.subplots(figsize=(3.1, 2.4))
    yx, yy = get_series(db_results, "ycsb")
    tx, ty = get_series(db_results, "tectonic")

    if yx:
        ax.plot(
            yx,
            yy,
            color=YCSB_COLOR,
            marker="o",
            markersize=3.5,
            markerfacecolor="white",
            markeredgecolor=YCSB_COLOR,
            linewidth=1.0,
            label="YCSB",
        )
    if tx:
        ax.plot(
            tx,
            ty,
            color=TEC_COLOR,
            marker="s",
            markersize=3.5,
            markerfacecolor=TEC_COLOR,
            markeredgecolor=TEC_COLOR,
            linewidth=1.0,
            label="Tectonic",
        )

    style_axis(ax)
    ax.set_title(DB_LABELS.get(db, db), fontsize=9)
    fig.tight_layout(pad=0.5)

    base = f"{OUT_DIR}/{db}_end_to_end_wall_time"
    fig.savefig(f"{base}.pdf", bbox_inches="tight")
    fig.savefig(f"{base}.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved: {base}.png")


def plot_all_databases(db_results_all):
    fig, axes = plt.subplots(2, 2, figsize=(6.4, 4.6), sharex=True)
    axes = axes.ravel()

    for ax, db in zip(axes, DATABASES):
        db_results = db_results_all.get(db, {})
        yx, yy = get_series(db_results, "ycsb")
        tx, ty = get_series(db_results, "tectonic")
        if yx:
            ax.plot(
                yx,
                yy,
                color=YCSB_COLOR,
                marker="o",
                markersize=3.0,
                markerfacecolor="white",
                markeredgecolor=YCSB_COLOR,
                linewidth=0.9,
            )
        if tx:
            ax.plot(
                tx,
                ty,
                color=TEC_COLOR,
                marker="s",
                markersize=3.0,
                markerfacecolor=TEC_COLOR,
                markeredgecolor=TEC_COLOR,
                linewidth=0.9,
            )
        style_axis(ax)
        ax.set_title(DB_LABELS.get(db, db), fontsize=8)

    fig.suptitle("YCSB vs Tectonic workload A end-to-end runtime", fontsize=9, y=1.01)
    fig.tight_layout(pad=0.6)

    base = f"{OUT_DIR}/all_dbs_end_to_end_wall_time"
    fig.savefig(f"{base}.pdf", bbox_inches="tight")
    fig.savefig(f"{base}.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved: {base}.png")


def plot_legend():
    fig, ax = plt.subplots(figsize=(2.8, 0.5))
    ax.axis("off")
    handles = [
        mpatches.Patch(facecolor="white", edgecolor=YCSB_COLOR, label="YCSB"),
        mpatches.Patch(facecolor=TEC_COLOR, edgecolor=TEC_COLOR, label="Tectonic"),
    ]
    ax.legend(handles=handles, loc="center", ncol=2, fontsize=9, frameon=False)
    base = f"{OUT_DIR}/end_to_end_wall_time_legend"
    fig.savefig(f"{base}.pdf", bbox_inches="tight")
    fig.savefig(f"{base}.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
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

    print(f"Plotting end-to-end runtime from {RESULTS_PATH}")
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
