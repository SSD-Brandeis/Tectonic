#!/usr/bin/env python3
"""Plot db_bench vs Tectonic RocksDB experiment results."""

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
OUT_DIR = ROOT_DIR / "data" / "db_bench_exp_test" / "end_to_end"
RESULTS_PATH = OUT_DIR / "results.json"
FONT_PATH = ROOT_DIR / "LinLibertine_Mah.ttf"

TOOLS = [
    ("native_db_bench", r"db\_bench", {"facecolor": "white", "edgecolor": "0.25", "hatch": "///"}),
    ("tectonic", "Tectonic", {"facecolor": "tab:red", "edgecolor": "tab:red", "hatch": ""}),
]


def font(size: int) -> font_manager.FontProperties:
    return font_manager.FontProperties(fname=str(FONT_PATH), size=size)


def configure_font() -> None:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"strict font file not found: {FONT_PATH}")
    font_manager.fontManager.addfont(str(FONT_PATH))
    prop = font(12)
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
        print(f"no results file found: {path}")
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


def set_y_axis(ax, ylabel: str, y_top: float) -> None:
    ax.set_ylabel(ylabel, fontproperties=font(12), labelpad=10)
    ax.set_ylim(bottom=0.0, top=max(y_top, 1.0))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=5))
    yticks = [tick for tick in ax.get_yticks() if 0.0 <= tick <= ax.get_ylim()[1]]
    if not any(abs(tick) < 1e-9 for tick in yticks):
        yticks.insert(0, 0.0)
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


def save_legend(base: Path) -> None:
    handles = legend_handles()
    fig = plt.figure(figsize=(3.6, 0.6))
    fig.legend(handles=handles, loc="center", ncol=len(handles), frameon=False, prop=font(10))
    fig.savefig(f"{base}_legend.pdf", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def metric_value(run: dict, tool_key: str, metric_key: str, scale: float) -> float:
    value = run[tool_key].get(metric_key)
    return float(value) * scale if finite(value) else float("nan")


def plot_metric(data: dict, metric_key: str, ylabel: str, base_name: str, scale: float = 1.0) -> None:
    runs = data.get("runs", [])
    if not runs:
        print("no runs to plot")
        return
    labels = [str(run["workload"]) for run in runs]
    positions = list(range(len(runs)))
    width = 0.34
    offsets = [-width / 2, width / 2]
    max_value = 0.0

    fig, ax = plt.subplots(1, 1, figsize=(5.4, 3.2))
    for offset, (tool_key, tool_label, style) in zip(offsets, TOOLS):
        values = [metric_value(run, tool_key, metric_key, scale) for run in runs]
        finite_values = [value for value in values if finite(value)]
        if finite_values:
            max_value = max(max_value, max(finite_values))
        ax.bar(
            [pos + offset for pos in positions],
            values,
            width,
            label=tool_label,
            facecolor=style["facecolor"],
            edgecolor=style["edgecolor"],
            hatch=style["hatch"],
            linewidth=1.0,
        )

    ax.set_xlabel(r"db\_bench workload", fontproperties=font(12), labelpad=8)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlim(left=-0.6, right=max(positions) + 0.6)
    set_y_axis(ax, ylabel, max_value * 1.18)
    style_spines(ax)
    fig.subplots_adjust(left=0.26, right=0.96, bottom=0.24, top=0.94)
    fig.savefig(OUT_DIR / f"{base_name}.pdf", bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot db_bench experiment results.")
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
        "cpu_seconds_per_million_ops",
        r"cpu time (s / M ops)",
        "db_bench_exp_test_cpu_time",
    )
    plot_metric(
        data,
        "wall_seconds_per_million_ops",
        r"wall time (s / M ops)",
        "db_bench_exp_test_wall_time",
    )
    plot_metric(
        data,
        "peak_vmhwm_kb",
        r"peak memory footprint (MB)",
        "db_bench_exp_test_peak_memory",
        scale=1.0 / 1024.0,
    )
    save_legend(OUT_DIR / "db_bench_exp_test")
    print(f"saved plots to {OUT_DIR}")


if __name__ == "__main__":
    main()
