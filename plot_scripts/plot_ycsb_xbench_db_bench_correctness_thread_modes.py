#!/usr/bin/env python3
"""Plot workload-count accuracy for sequential and parallel generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
RESULTS_PATH = ROOT_DIR / "data" / "ycsb_tectonic_correctness_thread_modes" / "results.json"
PLOTS_DIR = ROOT_DIR / "ycsb_tectonic_correctness_plots" / "thread_modes"
FONT_PATH = ROOT_DIR / "LinLibertine_Mah.ttf"
FONT_SIZE = 26

def font(size: int) -> font_manager.FontProperties:
    return font_manager.FontProperties(fname=str(FONT_PATH), size=size)

def configure_font() -> None:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"strict font file not found: {FONT_PATH}")
    font_manager.fontManager.addfont(str(FONT_PATH))
    prop = font(FONT_SIZE)
    font_name = prop.get_name()
    plt.rcParams["font.family"] = font_name
    plt.rcParams["font.serif"] = [font_name]
    plt.rcParams["text.usetex"] = True
    plt.rcParams["font.weight"] = "normal"
    plt.rcParams["axes.titleweight"] = "normal"
    plt.rcParams["axes.labelweight"] = "normal"
    plt.rcParams["axes.grid"] = False
    plt.rcParams["savefig.dpi"] = 300
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42
    plt.rcParams["pdf.compression"] = 0
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = FONT_SIZE
    plt.rcParams["axes.labelsize"] = FONT_SIZE
    plt.rcParams["xtick.labelsize"] = FONT_SIZE
    plt.rcParams["ytick.labelsize"] = FONT_SIZE

def load_results(path: Path) -> dict:
    if not path.exists():
        print(f"results file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with path.open() as f:
        return json.load(f)

def setting_runs(data: dict, setting: str) -> list[dict]:
    runs = [run for run in data.get("runs", []) if run.get("setting") == setting]
    return sorted(runs, key=lambda run: run["record_count"])

def repetition_count(data: dict) -> int:
    if "repetitions" in data:
        return int(data["repetitions"])
    for run in data.get("runs", []):
        if "repetitions" in run:
            return int(run["repetitions"])
    return 1

def style_axis_broken(ax1, ax2) -> None:
    ax2.set_xlabel("operation count (K)", fontproperties=font(FONT_SIZE), labelpad=12)
    
    ax1.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{y:g}"))
    ax2.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{y:g}"))
    ax2.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{x:g}"))
    
    for ax in [ax1, ax2]:
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_fontproperties(font(FONT_SIZE))
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color("black")
            spine.set_linewidth(0.8)
        ax.tick_params(colors="black", which="both", direction="out")

def save_custom_legend(base: Path) -> None:
    handles = [
        mlines.Line2D([], [], label="X-Bench", color="tab:blue", linestyle="-.", marker="s", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="tab:blue", linewidth=4.0),
        mlines.Line2D([], [], label="YCSB-inserts", color="grey", linestyle="-", marker="^", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="grey", linewidth=4.0),
        mlines.Line2D([], [], label="YCSB-PQ", color="grey", linestyle="--", marker="o", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="grey", linewidth=4.0),
        mlines.Line2D([], [], label="YCSB-updates", color="grey", linestyle=":", marker="d", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="grey", linewidth=4.0),
    ]
    fig = plt.figure(figsize=(7.2, 1.2))
    fig.legend(handles=handles, loc="center", ncol=2, frameon=False, prop=font(FONT_SIZE))
    fig.savefig(f"{base}_legend.pdf", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    print(f"saved: {base}_legend.pdf")

def plot_setting(data: dict, setting: str, title: str, reps: int) -> None:
    runs = setting_runs(data, setting)
    x_values = np.array([run["record_count"] for run in runs]) / 1000.0
    
    # Extract accuracies
    tectonic_inserts_acc = np.array([run["tools"]["tectonic"]["accuracy_by_operation"]["I"] for run in runs])
    ycsb_inserts_acc = np.array([run["tools"]["ycsb"]["accuracy_by_operation"]["I"] for run in runs])
    ycsb_queries_acc = np.array([run["tools"]["ycsb"]["accuracy_by_operation"]["P"] for run in runs])
    ycsb_updates_acc = np.array([run["tools"]["ycsb"]["accuracy_by_operation"]["U"] for run in runs])
    
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(6.0, 3.8), 
                                   gridspec_kw={'height_ratios': [5, 1]})
    
    # Plot Tectonic+
    ax1.plot(x_values, tectonic_inserts_acc, label="X-Bench", color="tab:blue", linestyle="-.", marker="s", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="tab:blue", linewidth=4.0)
    ax2.plot(x_values, tectonic_inserts_acc, color="tab:blue", linestyle="-.", marker="s", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="tab:blue", linewidth=4.0)
    
    # Plot YCSB - inserts
    ax1.plot(x_values, ycsb_inserts_acc, label="YCSB-inserts", color="grey", linestyle="-", marker="^", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="grey", linewidth=4.0)
    ax2.plot(x_values, ycsb_inserts_acc, color="grey", linestyle="-", marker="^", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="grey", linewidth=4.0)
    
    # Plot YCSB - point queries
    ax1.plot(x_values, ycsb_queries_acc, label="YCSB-PQ", color="grey", linestyle="--", marker="o", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="grey", linewidth=4.0)
    ax2.plot(x_values, ycsb_queries_acc, color="grey", linestyle="--", marker="o", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="grey", linewidth=4.0)
    
    # Plot YCSB - updates
    ax1.plot(x_values, ycsb_updates_acc, label="YCSB-updates", color="grey", linestyle=":", marker="d", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="grey", linewidth=4.0)
    ax2.plot(x_values, ycsb_updates_acc, color="grey", linestyle=":", marker="d", markersize=15, markeredgewidth=1.2, markerfacecolor="none", markeredgecolor="grey", linewidth=4.0)
    
    top_bottom_lim = 60.0
    top_ticks = [70, 80, 90, 100]
        
    ax1.set_ylim(top_bottom_lim, 105.0)
    ax2.set_ylim(0.0, 10.0)
    
    # Apply standard styles
    style_axis_broken(ax1, ax2)
    
    # Hide the spines between ax1 and ax2
    ax1.spines['bottom'].set_visible(False)
    ax2.spines['top'].set_visible(False)
    
    # Only show x-ticks at the bottom subplot
    ax1.tick_params(top=False, bottom=False, right=False, labelbottom=False)
    ax2.tick_params(top=False, bottom=True, right=False, direction="out")
    
    ax1.set_ylim(top_bottom_lim, 105.0)
    ax2.set_ylim(0.0, 10.0)
    ax1.set_yticks(top_ticks)
    ax2.set_yticks([0])
    
    if len(x_values) > 0:
        ax2.set_xlim(left=0.0, right=max(x_values) * 1.05)
        ax2.set_xticks([0, 5, 10, 15, 20])
        
    # Draw axis break cut-out diagonal ticks
    d = .015
    kwargs = dict(transform=ax1.transAxes, color='black', clip_on=False, linewidth=1.0)
    ax1.plot((-d, +d), (-d, +d), **kwargs)
    ax1.plot((1 - d, 1 + d), (-d, +d), **kwargs)

    kwargs.update(transform=ax2.transAxes)
    ax2.plot((-d, +d), (1 - d, 1 + d), **kwargs)
    ax2.plot((1 - d, 1 + d), (1 - d, 1 + d), **kwargs)
    
    # Center y-axis label vertically on the figure
    fig.text(0.02, 0.5, r"accuracy (\%)", va='center', ha='center', rotation='vertical', fontproperties=font(FONT_SIZE))
    
    fig.subplots_adjust(left=0.20, right=0.98, bottom=0.20, top=0.92, hspace=0.04)
    
    base = PLOTS_DIR / f"workload_accuracy_{setting}_{reps}runs"
    save_custom_legend(base)
    
    fig.savefig(f"{base}.pdf", bbox_inches="tight", pad_inches=0.16)
    plt.close(fig)
    print(f"saved: {base}.pdf")

def plot_accuracy(data: dict) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    reps = repetition_count(data)
    plot_setting(data, "sequential", "sequential", reps)

def main() -> None:
    parser = argparse.ArgumentParser(description="Plot workload-count correctness thread-mode results.")
    parser.add_argument("--results", default=str(RESULTS_PATH))
    args = parser.parse_args()
    configure_font()
    plot_accuracy(load_results(Path(args.results)))

if __name__ == "__main__":
    main()
