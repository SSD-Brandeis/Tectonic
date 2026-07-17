#!/usr/bin/env python3
"""Plot db_bench vs Tectonic mixgraph experiment results."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

ROOT_DIR = Path(__file__).resolve().parents[1]
FONT_PATH = ROOT_DIR / "LinLibertine_Mah.ttf"

# Styles for benchmark generators
# X-Bench (Tectonic): Color: tab:red. Since it is sequential, hollow bar (white facecolor) with distinct hatch ///.
# db_bench (represented as YCSB-style): Color: grey. Hollow bar with distinct hatch \\\
TOOLS = [
    ("native_db_bench", "db_bench", {"facecolor": "white", "edgecolor": "grey", "hatch": r"\\\\"}),
    ("tectonic", "X-Bench", {"facecolor": "white", "edgecolor": "tab:red", "hatch": "///"}),
]

def font(size: int) -> font_manager.FontProperties:
    return font_manager.FontProperties(fname=str(FONT_PATH), size=size)

def configure_font() -> None:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"strict font file not found: {FONT_PATH}")
    font_manager.fontManager.addfont(str(FONT_PATH))
    prop = font(20)
    font_name = prop.get_name()
    plt.rcParams["font.family"] = font_name
    plt.rcParams["font.sans-serif"] = [font_name]
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
    
    # Enforce 20 pt font size globally for titles, labels, ticks, and legends
    plt.rcParams["font.size"] = 20
    plt.rcParams["axes.labelsize"] = 20
    plt.rcParams["xtick.labelsize"] = 20
    plt.rcParams["ytick.labelsize"] = 20

def style_spines(ax) -> None:
    tick_font = font(20)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontproperties(tick_font)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)
    ax.tick_params(colors="black", which="both", direction="in")

def set_y_axis(ax, ylabel: str, y_top: float) -> None:
    ax.set_ylabel(ylabel, fontproperties=font(20), labelpad=10)
    ax.set_ylim(bottom=0.0, top=max(y_top, 1.0))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=5))
    
    # Explicitly ensure 0 is ticked
    yticks = list(ax.get_yticks())
    if 0.0 not in yticks:
        yticks.append(0.0)
    ax.set_yticks(sorted(set(yticks)))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{y:g}"))

def legend_handles():
    return [
        mpatches.Patch(
            facecolor=style["facecolor"],
            edgecolor=style["edgecolor"],
            hatch=style["hatch"],
            label=label,
            linewidth=1.0,
        )
        for _, label, style in TOOLS
    ]

def save_legend(out_dir: Path, base_name: str) -> None:
    handles = legend_handles()
    fig = plt.figure(figsize=(5, 1.0))
    fig.legend(handles=handles, loc="center", ncol=len(handles), frameon=False, prop=font(20))
    fig.savefig(out_dir / f"{base_name}_legend.pdf", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)

def main() -> None:
    parser = argparse.ArgumentParser(description="Plot mixgraph experiment results.")
    parser.add_argument("--results", required=True, help="Path to results.json")
    parser.add_argument("--out-dir", required=True, help="Path to output directory")
    parser.add_argument("--base-name", default="mixgraph_test_throughput", help="Base name of output PDFs")
    args = parser.parse_args()

    configure_font()

    results_path = Path(args.results)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with results_path.open() as f:
        data = json.load(f)

    runs = data.get("runs", [])
    if not runs:
        print("Error: No runs found in results file.")
        sys.exit(1)

    # Reorganize by tool
    run_map = {run["tool"]: run for run in runs}

    # Plot figure size: (5, 3.6) strictly
    fig, ax = plt.subplots(1, 1, figsize=(5, 3.6))

    positions = [0, 1]
    widths = 0.5
    max_throughput = 0.0

    for i, (tool_key, tool_label, style) in enumerate(TOOLS):
        run = run_map.get(tool_key, {})
        throughput = run.get("throughput_ops_sec", 0.0)
        max_throughput = max(max_throughput, throughput)
        
        ax.bar(
            [positions[i]],
            [throughput],
            widths,
            facecolor=style["facecolor"],
            edgecolor=style["edgecolor"],
            hatch=style["hatch"],
            linewidth=1.0,
        )

    # Enforce regular lowercase labeling (no capital letters except YCSB, M, s)
    ax.set_xlabel("benchmark tool", fontproperties=font(20), labelpad=8)
    ax.set_xticks(positions)
    # Convert tool label names to lowercase for the plot (unless it matches YCSB etc.)
    ax.set_xticklabels([tool[1] for tool in TOOLS])
    
    # Tick limits and y label formatting
    ax.set_xlim(left=-0.6, right=1.6)
    
    # Ensure x-axis zero is handled correctly if it's ordinal (keep centered with pad)
    set_y_axis(ax, "throughput (ops/s)", max_throughput * 1.2)
    style_spines(ax)

    fig.subplots_adjust(left=0.26, right=0.96, bottom=0.24, top=0.94)
    # Save in PDF format only
    fig.savefig(out_dir / f"{args.base_name}.pdf", bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)

    # Save separate legend
    save_legend(out_dir, args.base_name)

    print(f"Plots saved to {out_dir / args.base_name}.pdf and {out_dir / (args.base_name + '_legend.pdf')}")

if __name__ == "__main__":
    main()
