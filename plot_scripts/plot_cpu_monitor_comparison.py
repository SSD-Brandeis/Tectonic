#!/usr/bin/env python3
"""Plot workload-generation CPU utilization from several monitoring tools."""

import argparse
import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

import plot_style


ROOT_DIR = "/home/cc/Tectonic"
OUT_DIR = f"{ROOT_DIR}/data/CPU-utilization"
DEFAULT_RESULTS_PATH = f"{OUT_DIR}/monitor_comparison_results.json"
FONT_PATH = f"{ROOT_DIR}/LinLibertine_Mah.ttf"
DEFAULT_TOOLS = ["procfs", "pidstat", "top"]

TOOL_LABELS = {
    "procfs": "procfs",
    "pidstat": "pidstat",
    "top": "top",
}

YCSB_STYLE = {
    "color": "grey",
    "linestyle": "-",
    "marker": "^",
    "markersize": 4,
    "markerfacecolor": "none",
    "markeredgecolor": "grey",
    "linewidth": 1.4,
}
TECTONIC_STYLE = {
    "color": "tab:red",
    "linestyle": "-.",
    "marker": "s",
    "markersize": 4,
    "markerfacecolor": "none",
    "markeredgecolor": "tab:red",
    "linewidth": 1.4,
}


def configure_font():
    if not os.path.exists(FONT_PATH):
        raise FileNotFoundError(f"strict font file not found: {FONT_PATH}")
    font_manager.fontManager.addfont(FONT_PATH)
    prop = font_manager.FontProperties(fname=FONT_PATH)
    plt.rcParams["font.family"] = prop.get_name()
    plt.rcParams["text.usetex"] = True
    plt.rcParams["font.weight"] = "normal"
    plt.rcParams["axes.titleweight"] = "normal"
    plt.rcParams["axes.labelweight"] = "normal"
    plt.rcParams["axes.grid"] = False
    plt.rcParams["savefig.dpi"] = 300
    plt.rcParams["font.size"] = 12
    plt.rcParams["axes.labelsize"] = 12
    plt.rcParams["axes.titlesize"] = 12
    plt.rcParams["xtick.labelsize"] = 10
    plt.rcParams["ytick.labelsize"] = 10


def load_results(path):
    if not os.path.exists(path):
        print(f"no results file found: {path}")
        sys.exit(0)
    with open(path) as f:
        return json.load(f)


def output_tag(data):
    tag = data.get("output_tag")
    if tag:
        return tag
    workload = data.get("workload", "unknown")
    scale = data.get("scale", "unknown")
    cpu_mode = data.get("cpu_mode", "unknown")
    cpu_tag = "allcpus" if cpu_mode == "all_available_cpus" else "singlecpu"
    return f"workload_generation_workload{workload}_scale{scale}_{cpu_tag}"


def pad_cpu_log(samples, duration_s, common_end_s):
    duration_s = float(duration_s or 0.0)
    common_end_s = max(float(common_end_s or 0.0), duration_s)
    padded = [{"elapsed_s": 0.0, "cpu_percent": 0.0}]
    for sample in samples:
        elapsed = float(sample.get("elapsed_s", 0.0))
        if elapsed <= duration_s:
            padded.append({
                "elapsed_s": elapsed,
                "cpu_percent": max(0.0, float(sample.get("cpu_percent", 0.0))),
            })
    if padded[-1]["elapsed_s"] < duration_s:
        padded.append({"elapsed_s": duration_s, "cpu_percent": padded[-1]["cpu_percent"]})
    padded.append({"elapsed_s": duration_s, "cpu_percent": 0.0})
    if common_end_s > duration_s:
        padded.append({"elapsed_s": common_end_s, "cpu_percent": 0.0})
    return padded


def resample_cpu_log(samples, bucket_s=1.0):
    if not samples:
        return []
    buckets = []
    current_bucket = None
    total = 0.0
    count = 0
    for sample in samples:
        elapsed = float(sample["elapsed_s"])
        bucket = int(elapsed // bucket_s)
        if current_bucket is None:
            current_bucket = bucket
        if bucket != current_bucket:
            buckets.append({
                "elapsed_s": (current_bucket + 0.5) * bucket_s,
                "cpu_percent": total / count,
            })
            current_bucket = bucket
            total = 0.0
            count = 0
        total += float(sample["cpu_percent"])
        count += 1
    if count:
        buckets.append({
            "elapsed_s": (current_bucket + 0.5) * bucket_s,
            "cpu_percent": total / count,
        })
    return buckets


def style_for_count(style, count):
    styled = dict(style)
    styled["markersize"] = 3.5
    if count > 20:
        styled["markevery"] = max(1, count // 18)
    return styled


def plot_line(ax, samples, label, style):
    xs = [sample["elapsed_s"] for sample in samples]
    ys = [sample["cpu_percent"] for sample in samples]
    return ax.plot(xs, ys, label=label, **style_for_count(style, len(xs)))[0]


def force_zero_ticks(ax, x_right, y_top=None):
    ax.set_xlim(left=0.0, right=max(float(x_right or 0.0), 1.0))
    ax.set_ylim(bottom=0.0)
    if y_top is not None:
        ax.set_ylim(top=max(float(y_top), 1.0))
    ax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=6))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=6))
    xticks = [tick for tick in ax.get_xticks() if tick >= 0.0]
    yticks = [tick for tick in ax.get_yticks() if tick >= 0.0]
    if not any(abs(tick) < 1e-9 for tick in xticks):
        xticks.insert(0, 0.0)
    if not any(abs(tick) < 1e-9 for tick in yticks):
        yticks.insert(0, 0.0)
    ax.set_xticks(sorted(set(xticks)))
    ax.set_yticks(sorted(set(yticks)))


def style_axis(ax, title, x_right, y_top=None):
    ax.set_title(plot_style.format_label(title))
    ax.set_xlabel(plot_style.format_label("elapsed time (s)"))
    ax.set_ylabel("process cpu utilization\n(\\% of one logical cpu)", labelpad=14)
    force_zero_ticks(ax, x_right, y_top=y_top)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)
    ax.tick_params(colors="black", which="both", direction="in")


def save_legend(handles, labels, base):
    fig = plt.figure(figsize=(3.4, 0.65))
    fig.legend(handles, labels, loc="center", ncol=len(handles), frameon=False)
    fig.savefig(f"{base}_legend.pdf", bbox_inches="tight")
    fig.savefig(f"{base}_legend.png", bbox_inches="tight", dpi=300)
    plt.close(fig)


def bucket_size(window_s):
    if window_s <= 10.0:
        return 0.1
    return 1.0


def tool_logs(generation, tool, window_s):
    ycsb = generation["ycsb"]
    tectonic = generation["tectonic"]
    bucket_s = bucket_size(window_s)
    ycsb_log = resample_cpu_log(
        pad_cpu_log(ycsb.get("monitors", {}).get(tool, []), ycsb["duration_s"], window_s),
        bucket_s=bucket_s,
    )
    tectonic_log = resample_cpu_log(
        pad_cpu_log(tectonic.get("monitors", {}).get(tool, []), tectonic["duration_s"], window_s),
        bucket_s=bucket_s,
    )
    return ycsb_log, tectonic_log


def y_axis_top(*logs):
    max_y = max((sample["cpu_percent"] for log in logs for sample in log), default=100.0)
    if max_y <= 105.0:
        return 105.0
    return max_y * 1.08


def main():
    parser = argparse.ArgumentParser(description="Plot workload-generation CPU monitor comparison results.")
    parser.add_argument("--results", default=DEFAULT_RESULTS_PATH)
    args = parser.parse_args()

    configure_font()
    data = load_results(args.results)
    generation = data["generation"]
    tag = output_tag(data)
    tools = [tool for tool in data.get("monitors", DEFAULT_TOOLS) if tool != "ps"]
    window_s = max(generation["ycsb"]["duration_s"], generation["tectonic"]["duration_s"])
    legend_handles = None
    legend_labels = None

    for tool in tools:
        ycsb_log, tectonic_log = tool_logs(generation, tool, window_s)
        fig, ax = plt.subplots(1, 1, figsize=(4.2, 2.8))
        ycsb_handle = plot_line(ax, ycsb_log, "YCSB", YCSB_STYLE)
        tectonic_handle = plot_line(ax, tectonic_log, "Tectonic", TECTONIC_STYLE)
        for boundary in generation["ycsb"].get("phase_boundaries_s", []):
            ax.axvline(boundary, color="grey", linestyle=":", linewidth=0.8)
        style_axis(ax, f"workload generation {TOOL_LABELS[tool]}", window_s, y_top=y_axis_top(ycsb_log, tectonic_log))

        base = f"{OUT_DIR}/cpu_monitor_{tool}_{tag}"
        fig.subplots_adjust(left=0.30, right=0.98, bottom=0.18, top=0.88)
        fig.savefig(f"{base}.pdf", bbox_inches="tight")
        fig.savefig(f"{base}.png", bbox_inches="tight", dpi=300)
        plt.close(fig)
        print(f"saved: {base}.pdf")
        print(f"saved: {base}.png")

        if legend_handles is None:
            legend_handles = [ycsb_handle, tectonic_handle]
            legend_labels = ["YCSB", "Tectonic"]

    if legend_handles:
        save_legend(legend_handles, legend_labels, f"{OUT_DIR}/cpu_monitor_{tag}")


if __name__ == "__main__":
    main()
