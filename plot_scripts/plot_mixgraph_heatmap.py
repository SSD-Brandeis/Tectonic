#!/usr/bin/env python3
"""Plot key-space access heatmaps for db_bench and Tectonic mixgraph runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

ROOT_DIR = Path(__file__).resolve().parents[1]
FONT_PATH = ROOT_DIR / "LinLibertine_Mah.ttf"

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
    
    # Consistent 20 pt font size
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

def save_heatmap(x: list[int], y: list[int], title: str, color: str, out_path: Path) -> None:
    # Strict figure size (5, 3.6)
    fig, ax = plt.subplots(1, 1, figsize=(5, 3.6))

    # Scatter plot with tiny markers (s=0.5) to look like a density heat-map
    ax.scatter(x, y, s=1.0, color=color, alpha=0.6, edgecolors='none')

    # Enforce regular lowercase labeling: "key sequence" and "kv-pair access count"
    ax.set_xlabel("key sequence", fontproperties=font(20), labelpad=8)
    ax.set_ylabel("kv-pair access count", fontproperties=font(20), labelpad=8)
    
    # Tick limits and zeroes
    ax.set_xlim(left=0.0, right=max(x) * 1.05)
    ax.set_ylim(bottom=0.0, top=max(y) * 1.1)

    # Explicitly ensure both 0 labels are printed and ticked
    ax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=4))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=4))
    
    xticks = list(ax.get_xticks())
    if 0.0 not in xticks:
        xticks.append(0.0)
    ax.set_xticks(sorted(set(xticks)))

    yticks = list(ax.get_yticks())
    if 0.0 not in yticks:
        yticks.append(0.0)
    ax.set_yticks(sorted(set(yticks)))

    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda val, _: f"{val:g}"))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda val, _: f"{val:g}"))

    style_spines(ax)
    
    fig.subplots_adjust(left=0.26, right=0.96, bottom=0.24, top=0.94)
    # Save as PDF only
    fig.savefig(out_path, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)

def main() -> None:
    parser = argparse.ArgumentParser(description="Plot mixgraph key access heatmaps.")
    parser.add_argument("--results", required=True, help="Path to heatmap_data.json")
    parser.add_argument("--out-dir", required=True, help="Path to output directory")
    args = parser.parse_args()

    configure_font()

    results_path = Path(args.results)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with results_path.open() as f:
        data = json.load(f)

    # Plot db_bench heatmap (grey)
    db_bench_data = data.get("db_bench", {})
    if db_bench_data:
        x = db_bench_data.get("key_sequence", [])
        y = db_bench_data.get("access_counts", [])
        if x and y:
            save_heatmap(x, y, "db_bench", "grey", out_dir / "mixgraph_heatmap_db_bench.pdf")
            print(f"Saved db_bench heatmap to {out_dir / 'mixgraph_heatmap_db_bench.pdf'}")

    # Plot Tectonic heatmap (tab:red)
    tectonic_data = data.get("tectonic", {})
    if tectonic_data:
        x = tectonic_data.get("key_sequence", [])
        y = tectonic_data.get("access_counts", [])
        if x and y:
            save_heatmap(x, y, "X-Bench", "tab:red", out_dir / "mixgraph_heatmap_tectonic.pdf")
            print(f"Saved tectonic heatmap to {out_dir / 'mixgraph_heatmap_tectonic.pdf'}")

if __name__ == "__main__":
    main()
