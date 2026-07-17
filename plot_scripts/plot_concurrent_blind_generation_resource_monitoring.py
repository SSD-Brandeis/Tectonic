#!/usr/bin/env python3
"""Plot resource-monitored concurrent blind generation results."""

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
EXPERIMENT_NAME = "concurrent_blind_generation_resource_monitoring"
DATA_DIR = ROOT_DIR / "data" / EXPERIMENT_NAME
RESULTS_PATH = DATA_DIR / f"{EXPERIMENT_NAME}_results.json"
FONT_PATH = ROOT_DIR / "LinLibertine_Mah.ttf"
FONT_SIZE = 24

TOOLS = [
    (
        "ycsb",
        "YCSB",
        {"color": "grey", "linestyle": "-", "marker": "^", "markerfacecolor": "none"},
    ),
    (
        "tectonic",
        "X-Bench",
        {"color": "tab:red", "linestyle": "-.", "marker": "s", "markerfacecolor": "none"},
    ),
    (
        "tectonic_unique",
        "X-Bench unique",
        {"color": "tab:orange", "linestyle": ":", "marker": "o", "markerfacecolor": "none"},
    ),
]


def font(size: int = FONT_SIZE) -> font_manager.FontProperties:
    return font_manager.FontProperties(fname=str(FONT_PATH), size=size)


def configure_font() -> None:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"strict font file not found: {FONT_PATH}")
    font_manager.fontManager.addfont(str(FONT_PATH))
    font_name = font().get_name()
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
    plt.rcParams["font.size"] = FONT_SIZE
    plt.rcParams["axes.titlesize"] = FONT_SIZE
    plt.rcParams["axes.labelsize"] = FONT_SIZE
    plt.rcParams["xtick.labelsize"] = FONT_SIZE
    plt.rcParams["ytick.labelsize"] = FONT_SIZE
    plt.rcParams["legend.fontsize"] = FONT_SIZE


def finite(value: object) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def load_results(path: Path) -> dict:
    if not path.exists():
        print(f"no results file found: {path}", file=sys.stderr)
        sys.exit(1)
    with path.open() as f:
        return json.load(f)


def style_axes(ax, ylabel: str, max_y: float, log_scale: bool = False) -> None:
    ax.set_xlabel("number of threads", fontproperties=font(), labelpad=12)
    ax.set_ylabel(ylabel, fontproperties=font(), labelpad=16)
    ax.set_xlim(left=0, right=62)
    ax.set_xticks([0, 10, 20, 30, 40, 50, 60])
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{x:g}"))
    if log_scale:
        ax.set_yscale("log")
        bottom = 1.0  # 10^0 as per styling rules
        top = 10 ** math.ceil(math.log10(max(max_y * 1.18, 10.0)))
        ax.set_ylim(bottom=bottom, top=top)
        ax.yaxis.set_major_locator(ticker.LogLocator(base=10.0))
        ax.yaxis.set_major_formatter(ticker.LogFormatterMathtext(base=10.0))
        ax.yaxis.set_minor_locator(ticker.NullLocator())
    else:
        top_y = max(max_y * 1.18, 1.0)
        ax.set_ylim(bottom=0, top=top_y)
        ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=5))
        ticks = ax.get_yticks()
        valid_ticks = [t for t in ticks if t >= 0]
        if valid_ticks:
            highest_tick = max(valid_ticks)
            if highest_tick >= max_y:
                ax.set_ylim(bottom=0, top=highest_tick)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{y:g}"))
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontproperties(font())
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)
    ax.tick_params(colors="black", which="both", direction="in")
    ax.grid(False)


def metric_values(data: dict, metric_key: str, tool: str, scale: float) -> list[float]:
    values = data.get("summary", {}).get(metric_key, {}).get(tool, [])
    return [float(value) * scale if finite(value) else float("nan") for value in values]


def plot_metric(data: dict, metric_key: str, ylabel: str, output_name: str, scale: float = 1.0, log_scale: bool = False) -> None:
    threads = [int(value) for value in data.get("summary", {}).get("threads", [])]
    if not threads:
        print("no summary threads to plot", file=sys.stderr)
        return
    fig, ax = plt.subplots(1, 1, figsize=(8.0, 5.4))
    max_y = 0.0
    for tool, _label, style in TOOLS:
        values = metric_values(data, metric_key, tool, scale)
        finite_values = [value for value in values if finite(value)]
        if finite_values:
            max_y = max(max_y, max(finite_values))
        ax.plot(
            threads,
            values,
            color=style["color"],
            linestyle=style["linestyle"],
            marker=style["marker"],
            markersize=20,
            markerfacecolor=style["markerfacecolor"],
            markeredgecolor=style["color"],
            linewidth=2.0,
        )
    style_axes(ax, ylabel, max_y, log_scale)
    fig.subplots_adjust(left=0.21, right=0.96, bottom=0.19, top=0.88)
    plot_dir = Path(data["provenance"]["plot_dir"])
    plot_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(plot_dir / f"{output_name}.pdf", bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)


def save_legend(data: dict) -> None:
    handles = [
        mlines.Line2D(
            [],
            [],
            color=style["color"],
            linestyle=style["linestyle"],
            marker=style["marker"],
            markersize=20,
            markerfacecolor=style["markerfacecolor"],
            markeredgecolor=style["color"],
            linewidth=2.0,
            label=label,
        )
        for _tool, label, style in TOOLS
    ]
    plot_dir = Path(data["provenance"]["plot_dir"])
    plot_dir.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(9.0, 1.0))
    fig.legend(handles=handles, loc="center", ncol=len(handles), frameon=False, prop=font())
    fig.savefig(plot_dir / f"{EXPERIMENT_NAME}_legend.pdf", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot concurrent blind resource monitoring results.")
    parser.add_argument("--results", default=str(RESULTS_PATH))
    args = parser.parse_args()

    configure_font()
    data = load_results(Path(args.results))
    plot_metric(
        data,
        "latency_s",
        "end-to-end latency (s)",
        f"{EXPERIMENT_NAME}_latency",
    )
    plot_metric(
        data,
        "peak_vmhwm_mib",
        "peak memory footprint (MB)",
        f"{EXPERIMENT_NAME}_peak_memory",
        log_scale=True,
    )
    plot_metric(
        data,
        "avg_all_cores_cpu_percent",
        "average cpu usage (%)",
        f"{EXPERIMENT_NAME}_average_cpu",
    )
    plot_metric(
        data,
        "output_bytes",
        "generated output size (GiB)",
        f"{EXPERIMENT_NAME}_output_size",
        scale=1.0 / (1024.0**3),
    )
    save_legend(data)
    print(f"saved plots to {data['provenance']['plot_dir']}")


if __name__ == "__main__":
    main()
