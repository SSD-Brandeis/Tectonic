#!/usr/bin/env python3
"""Plot 10x YCSB-E workload-generation CPU utilization and CPU work."""

import json
import math
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


ROOT_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT_DIR / "data/CPU-utilization"
RESULTS_PATH = OUT_DIR / "pidstat_multithread_generation_results.json"
FONT_PATH = ROOT_DIR / "LinLibertine_Mah.ttf"

MULTI_TOOL_ORDER = ["YCSB", "Tectonic"]
SINGLE_TOOL_ORDER = ["YCSB", "Tectonic", "KVBench"]
TOOL_KEYS = {"YCSB": "ycsb", "Tectonic": "tectonic", "KVBench": "kvbench"}
TOOL_LABELS = {"YCSB": "YCSB", "Tectonic": "Tectonic", "KVBench": "KVBench"}
STYLE = {
    "YCSB": {"color": "grey", "linestyle": "-", "marker": "^", "hatch": "///"},
    "Tectonic": {"color": "tab:red", "linestyle": "-.", "marker": "s", "hatch": "\\\\"},
    "KVBench": {"color": "tab:blue", "linestyle": "--", "marker": "v", "hatch": "xx"},
}


def font(size):
    return font_manager.FontProperties(fname=str(FONT_PATH), size=size)


def configure_font():
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
    plt.rcParams["savefig.bbox"] = "standard"
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42
    plt.rcParams["pdf.compression"] = 0
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 12
    plt.rcParams["axes.labelsize"] = 12
    plt.rcParams["xtick.labelsize"] = 10
    plt.rcParams["ytick.labelsize"] = 10


def load_results():
    if not RESULTS_PATH.exists():
        print(f"no results file found: {RESULTS_PATH}")
        sys.exit(0)
    with open(RESULTS_PATH) as f:
        return json.load(f)


def finite(value):
    return value is not None and math.isfinite(float(value))


def apply_tick_font(ax):
    tick_font = font(10)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontproperties(tick_font)


def style_spines(ax):
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)
    ax.tick_params(colors="black", which="both", direction="in")
    apply_tick_font(ax)


def pad_samples(samples, duration_s, common_end_s):
    duration_s = float(duration_s or 0.0)
    common_end_s = max(float(common_end_s or 0.0), duration_s)
    padded = [{"elapsed_s": 0.0, "avg_cpu_percent": 0.0}]
    for sample in samples:
        elapsed = float(sample.get("elapsed_s", 0.0))
        if elapsed <= duration_s:
            padded.append({
                "elapsed_s": elapsed,
                "avg_cpu_percent": min(100.0, max(0.0, float(sample.get("avg_cpu_percent", 0.0)))),
            })
    if padded[-1]["elapsed_s"] < duration_s:
        padded.append({"elapsed_s": duration_s, "avg_cpu_percent": padded[-1]["avg_cpu_percent"]})
    padded.append({"elapsed_s": duration_s, "avg_cpu_percent": 0.0})
    if common_end_s > duration_s:
        padded.append({"elapsed_s": common_end_s, "avg_cpu_percent": 0.0})
    return padded


def line_style(tool, count):
    base = {key: STYLE[tool][key] for key in ("color", "linestyle", "marker")}
    color = STYLE[tool]["color"]
    base.update({
        "markersize": 4,
        "markerfacecolor": color,
        "markeredgecolor": color,
        "linewidth": 1.4,
    })
    if count > 24:
        base["markevery"] = max(1, count // 18)
    return base


def plot_series(ax, samples, tool):
    xs = [sample["elapsed_s"] for sample in samples]
    ys = [sample["avg_cpu_percent"] for sample in samples]
    return ax.plot(xs, ys, label=TOOL_LABELS[tool], **line_style(tool, len(xs)))[0]


def set_numeric_axes(ax, xlabel, ylabel, x_right, y_top=None, force_y_100=False):
    ax.set_xlabel(xlabel, fontproperties=font(12))
    ax.set_ylabel(ylabel, fontproperties=font(12), labelpad=12)
    ax.set_xlim(left=0.0, right=max(float(x_right or 0.0), 1.0))
    if y_top is None:
        y_top = ax.get_ylim()[1]
    ax.set_ylim(bottom=0.0, top=max(float(y_top), 1.0))
    ax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=6))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=5))
    xticks = [tick for tick in ax.get_xticks() if tick >= 0.0]
    yticks = [tick for tick in ax.get_yticks() if 0.0 <= tick <= ax.get_ylim()[1]]
    if not any(abs(tick) < 1e-9 for tick in xticks):
        xticks.insert(0, 0.0)
    if not any(abs(tick) < 1e-9 for tick in yticks):
        yticks.insert(0, 0.0)
    if force_y_100 and not any(abs(tick - 100.0) < 1e-9 for tick in yticks):
        yticks.append(100.0)
    ax.set_xticks(sorted(set(xticks)))
    ax.set_yticks(sorted(set(yticks)))
    style_spines(ax)


def save_legend(handles, labels, base):
    fig = plt.figure(figsize=(4.2, 0.65))
    fig.legend(handles, labels, loc="center", ncol=len(handles), frameon=False, prop=font(10))
    fig.savefig(f"{base}_legend.pdf", bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def run_for_thread(data, threads):
    for run in data["runs"]:
        if int(run["threads"]) == int(threads):
            return run
    raise KeyError(f"missing run for thread count {threads}")


def has_pidstat_samples(run):
    return all(run[TOOL_KEYS[tool]].get("pidstat_samples") for tool in MULTI_TOOL_ORDER)


def choose_time_series_threads(data):
    preferred = int(data["time_series_threads"])
    preferred_run = run_for_thread(data, preferred)
    if has_pidstat_samples(preferred_run):
        return preferred
    sampled = [int(run["threads"]) for run in data["runs"] if has_pidstat_samples(run)]
    if sampled:
        return max(sampled)
    return preferred


def plot_time_series(data):
    threads = choose_time_series_threads(data)
    run = run_for_thread(data, threads)
    durations = [float(run[TOOL_KEYS[tool]]["duration_s"]) for tool in MULTI_TOOL_ORDER]
    window_s = max(durations)
    samples_by_tool = {
        tool: pad_samples(run[TOOL_KEYS[tool]].get("pidstat_samples", []), run[TOOL_KEYS[tool]]["duration_s"], window_s)
        for tool in MULTI_TOOL_ORDER
    }

    fig, ax = plt.subplots(1, 1, figsize=(5.6, 3.6))
    handles = [plot_series(ax, samples_by_tool[tool], tool) for tool in MULTI_TOOL_ORDER]
    set_numeric_axes(
        ax,
        "elapsed time (s)",
        r"average cpu utilization (\%)",
        window_s,
        y_top=100.0,
        force_y_100=True,
    )
    fig.subplots_adjust(left=0.34, right=0.94, bottom=0.30, top=0.90)
    base = OUT_DIR / "cpu_utilization_pidstat_multithread_timeseries"
    fig.savefig(f"{base}.pdf", bbox_inches="tight", pad_inches=0.20)
    plt.close(fig)
    return handles, [TOOL_LABELS[tool] for tool in MULTI_TOOL_ORDER], threads


def plot_thread_categories(data, metric_key, ylabel, base_name, y_top=None, force_y_100=False):
    thread_counts = [int(t) for t in data["thread_counts"]]
    positions = list(range(len(thread_counts)))
    labels = [str(t) for t in thread_counts]
    values_by_tool = {}
    max_value = 0.0
    for tool in MULTI_TOOL_ORDER:
        values = []
        key = TOOL_KEYS[tool]
        for threads in thread_counts:
            run = run_for_thread(data, threads)
            value = run[key].get(metric_key)
            value = float("nan") if not finite(value) else float(value)
            if finite(value):
                max_value = max(max_value, value)
            values.append(value)
        values_by_tool[tool] = values

    fig, ax = plt.subplots(1, 1, figsize=(5.6, 3.6))
    handles = []
    for tool in MULTI_TOOL_ORDER:
        handle = ax.plot(
            positions,
            values_by_tool[tool],
            label=TOOL_LABELS[tool],
            **line_style(tool, len(thread_counts)),
        )[0]
        handles.append(handle)
    if y_top is None:
        y_top = max_value * 1.15 if max_value > 0 else 1.0
    ax.set_xlabel("number of threads", fontproperties=font(12))
    ax.set_ylabel(ylabel, fontproperties=font(12), labelpad=12)
    ax.set_xlim(left=-0.20, right=max(0.20, len(thread_counts) - 0.80))
    ax.set_ylim(bottom=0.0, top=max(float(y_top), 1.0))
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontproperties=font(10))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=5))
    yticks = [tick for tick in ax.get_yticks() if 0.0 <= tick <= ax.get_ylim()[1]]
    if not any(abs(tick) < 1e-9 for tick in yticks):
        yticks.insert(0, 0.0)
    if force_y_100 and not any(abs(tick - 100.0) < 1e-9 for tick in yticks):
        yticks.append(100.0)
    ax.set_yticks(sorted(set(yticks)))
    style_spines(ax)
    fig.subplots_adjust(left=0.38, right=0.94, bottom=0.30, top=0.90)
    base = OUT_DIR / base_name
    fig.savefig(f"{base}.pdf", bbox_inches="tight", pad_inches=0.20)
    plt.close(fig)
    return handles, [TOOL_LABELS[tool] for tool in MULTI_TOOL_ORDER]


def plot_single_thread_cpu_time(data):
    run = data["single_thread"]
    values = [float(run[TOOL_KEYS[tool]]["cpu_seconds_per_million_ops"]) for tool in SINGLE_TOOL_ORDER]
    positions = list(range(len(SINGLE_TOOL_ORDER)))
    labels = [TOOL_LABELS[tool] for tool in SINGLE_TOOL_ORDER]
    y_top = max(values) * 1.15 if values else 1.0

    fig, ax = plt.subplots(1, 1, figsize=(4.8, 3.4))
    for pos, tool, value in zip(positions, SINGLE_TOOL_ORDER, values):
        style = STYLE[tool]
        ax.bar(
            pos,
            value,
            width=0.55,
            color="white",
            edgecolor=style["color"],
            hatch=style["hatch"],
            linewidth=1.0,
        )
    ax.set_xlabel("generator", fontproperties=font(12))
    ax.set_ylabel(r"cpu time (s / M ops)", fontproperties=font(12), labelpad=12)
    ax.set_xlim(left=-0.55, right=len(positions) - 0.45)
    ax.set_ylim(bottom=0.0, top=max(y_top, 1.0))
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontproperties=font(10))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=5))
    yticks = [tick for tick in ax.get_yticks() if 0.0 <= tick <= ax.get_ylim()[1]]
    if not any(abs(tick) < 1e-9 for tick in yticks):
        yticks.insert(0, 0.0)
    ax.set_yticks(sorted(set(yticks)))
    style_spines(ax)
    fig.subplots_adjust(left=0.32, right=0.94, bottom=0.30, top=0.90)
    base = OUT_DIR / "cpu_utilization_single_thread_cpu_time"
    fig.savefig(f"{base}.pdf", bbox_inches="tight", pad_inches=0.20)
    plt.close(fig)


def plot_single_thread_cpu_utilization(data):
    run = data["single_thread"]
    values = [
        float(run[TOOL_KEYS[tool]]["cpu_seconds"]) / float(run[TOOL_KEYS[tool]]["duration_s"]) * 100.0
        for tool in SINGLE_TOOL_ORDER
    ]
    positions = list(range(len(SINGLE_TOOL_ORDER)))
    labels = [TOOL_LABELS[tool] for tool in SINGLE_TOOL_ORDER]
    y_top = max(values) * 1.15 if values else 1.0

    fig, ax = plt.subplots(1, 1, figsize=(4.8, 3.4))
    for pos, tool, value in zip(positions, SINGLE_TOOL_ORDER, values):
        style = STYLE[tool]
        ax.bar(
            pos,
            value,
            width=0.55,
            color="white",
            edgecolor=style["color"],
            hatch=style["hatch"],
            linewidth=1.0,
        )
    ax.set_xlabel("generator", fontproperties=font(12))
    ax.set_ylabel(r"one-core cpu utilization (\%)", fontproperties=font(12), labelpad=12)
    ax.set_xlim(left=-0.55, right=len(positions) - 0.45)
    ax.set_ylim(bottom=0.0, top=max(y_top, 1.0))
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontproperties=font(10))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=5))
    yticks = [tick for tick in ax.get_yticks() if 0.0 <= tick <= ax.get_ylim()[1]]
    if not any(abs(tick) < 1e-9 for tick in yticks):
        yticks.insert(0, 0.0)
    if not any(abs(tick - 100.0) < 1e-9 for tick in yticks):
        yticks.append(100.0)
    ax.set_yticks(sorted(set(yticks)))
    style_spines(ax)
    fig.subplots_adjust(left=0.36, right=0.94, bottom=0.30, top=0.90)
    base = OUT_DIR / "cpu_utilization_single_thread_cpu_utilization"
    fig.savefig(f"{base}.pdf", bbox_inches="tight", pad_inches=0.20)
    plt.close(fig)


def main():
    configure_font()
    data = load_results()
    handles, labels, plotted_threads = plot_time_series(data)
    plot_thread_categories(
        data,
        "common_window_avg_cpu_percent",
        r"average cpu utilization (\%)",
        "cpu_utilization_pidstat_multithread_by_threads",
        y_top=100.0,
        force_y_100=True,
    )
    plot_thread_categories(
        data,
        "cpu_seconds_per_million_ops",
        r"cpu time (s / M ops)",
        "cpu_utilization_cpu_time_by_threads",
    )
    plot_single_thread_cpu_time(data)
    plot_single_thread_cpu_utilization(data)
    save_legend(handles, labels, OUT_DIR / "cpu_utilization_pidstat_multithread")
    print(f"time-series thread count: {plotted_threads}")
    print(f"saved: {OUT_DIR / 'cpu_utilization_pidstat_multithread_timeseries.pdf'}")
    print(f"saved: {OUT_DIR / 'cpu_utilization_pidstat_multithread_by_threads.pdf'}")
    print(f"saved: {OUT_DIR / 'cpu_utilization_cpu_time_by_threads.pdf'}")
    print(f"saved: {OUT_DIR / 'cpu_utilization_single_thread_cpu_time.pdf'}")
    print(f"saved: {OUT_DIR / 'cpu_utilization_single_thread_cpu_utilization.pdf'}")


if __name__ == "__main__":
    main()
