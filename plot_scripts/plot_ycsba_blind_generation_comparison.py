#!/usr/bin/env python3
"""Plot YCSB-A blind workload generation metrics."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


ROOT_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT_DIR / "data" / "db_bench_exp_test" / "ycsba_blind"
RESULTS_PATH = OUT_DIR / "ycsba_blind_generation_results.json"
FONT_PATH = ROOT_DIR / "LinLibertine_Mah.ttf"

TOOLS = [
    (
        "db_bench_style",
        r"db\_bench style",
        {"color": "0.25", "linestyle": "-", "marker": "o", "markerfacecolor": "white"},
    ),
    (
        "tectonic",
        "Tectonic",
        {"color": "tab:red", "linestyle": "-.", "marker": "s", "markerfacecolor": "white"},
    ),
]


def font(size: int) -> font_manager.FontProperties:
    return font_manager.FontProperties(fname=str(FONT_PATH), size=size)


def configure_font() -> None:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"strict font file not found: {FONT_PATH}")
    font_manager.fontManager.addfont(str(FONT_PATH))
    font_name = font(12).get_name()
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
    plt.rcParams["font.size"] = 12
    plt.rcParams["axes.labelsize"] = 12
    plt.rcParams["xtick.labelsize"] = 10
    plt.rcParams["ytick.labelsize"] = 10


def finite(value: object) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def load_results(path: Path) -> dict:
    if not path.exists():
        print(f"no generation results file found: {path}")
        sys.exit(0)
    with path.open() as f:
        return json.load(f)


def style_spines(ax) -> None:
    tick_font = font(10)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontproperties(tick_font)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)
    ax.tick_params(colors="black", which="both", direction="in")


def set_axes(ax, ylabel: str, max_x: float, max_y: float) -> None:
    ax.set_xlabel("scale factor", fontproperties=font(12), labelpad=8)
    ax.set_ylabel(ylabel, fontproperties=font(12), labelpad=10)
    ax.set_xlim(left=0.0, right=max(max_x * 1.08, 0.01))
    ax.set_ylim(bottom=0.0, top=max(max_y * 1.18, 1.0))
    xticks = [0.0]
    for tick in ax.get_xticks():
        if 0.0 <= tick <= ax.get_xlim()[1]:
            xticks.append(float(tick))
    for scale in ax._tectonic_scales:
        xticks.append(scale)
    ax.set_xticks(sorted(set(round(tick, 6) for tick in xticks)))
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{x:g}"))
    yticks = [0.0]
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=5))
    for tick in ax.get_yticks():
        if 0.0 <= tick <= ax.get_ylim()[1]:
            yticks.append(float(tick))
    ax.set_yticks(sorted(set(yticks)))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{y:g}"))
    style_spines(ax)


def metric_value(run: dict, tool_key: str, metric_key: str, scale: float) -> float:
    value = run[tool_key].get(metric_key)
    return float(value) * scale if finite(value) else float("nan")


def plot_metric(data: dict, metric_key: str, ylabel: str, base_name: str, scale: float = 1.0) -> list:
    runs = data.get("runs", [])
    if not runs:
        print("no runs to plot")
        return []
    scales = [float(run["scale"]) for run in runs]
    max_y = 0.0
    handles = []
    fig, ax = plt.subplots(1, 1, figsize=(5.4, 3.2))
    ax._tectonic_scales = scales
    for tool_key, tool_label, style in TOOLS:
        values = [metric_value(run, tool_key, metric_key, scale) for run in runs]
        finite_values = [value for value in values if finite(value)]
        if finite_values:
            max_y = max(max_y, max(finite_values))
        line = ax.plot(
            scales,
            values,
            label=tool_label,
            color=style["color"],
            linestyle=style["linestyle"],
            marker=style["marker"],
            markersize=6,
            markerfacecolor=style["markerfacecolor"],
            markeredgecolor=style["color"],
            linewidth=1.8,
        )[0]
        handles.append(line)

    set_axes(ax, ylabel, max(scales), max_y)
    fig.subplots_adjust(left=0.24, right=0.96, bottom=0.24, top=0.94)
    fig.savefig(OUT_DIR / f"{base_name}.pdf", bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    return handles


def save_legend(base: Path) -> None:
    handles = [
        mlines.Line2D(
            [],
            [],
            color=style["color"],
            linestyle=style["linestyle"],
            marker=style["marker"],
            markersize=6,
            markerfacecolor=style["markerfacecolor"],
            markeredgecolor=style["color"],
            linewidth=1.8,
            label=label,
        )
        for _, label, style in TOOLS
    ]
    fig = plt.figure(figsize=(4.8, 0.6))
    fig.legend(handles=handles, loc="center", ncol=len(handles), frameon=False, prop=font(10))
    fig.savefig(f"{base}_legend.pdf", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot YCSB-A blind generation comparison results.")
    parser.add_argument("--results", default=str(RESULTS_PATH))
    args = parser.parse_args()

    configure_font()
    results_path = Path(args.results)
    data = load_results(results_path)
    global OUT_DIR
    OUT_DIR = results_path.parent
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    plot_metric(
        data,
        "duration_s",
        "workload generation time (s)",
        "ycsba_blind_generation_wall_time",
    )
    plot_metric(
        data,
        "peak_vmhwm_kb",
        "peak memory footprint (MB)",
        "ycsba_blind_generation_peak_memory",
        scale=1.0 / 1024.0,
    )
    plot_metric(
        data,
        "peak_threads",
        "peak thread count",
        "ycsba_blind_generation_peak_threads",
    )
    save_legend(OUT_DIR / "ycsba_blind_generation")
    print(f"saved YCSB-A blind generation plots to {OUT_DIR}")


if __name__ == "__main__":
    main()
