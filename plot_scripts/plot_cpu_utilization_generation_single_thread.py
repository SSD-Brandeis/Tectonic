#!/usr/bin/env python3
"""Plot single-core workload-generation CPU utilization."""

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
RESULTS_PATH = f"{OUT_DIR}/generation_single_thread_results.json"
FONT_PATH = f"{ROOT_DIR}/LinLibertine_Mah.ttf"

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


def load_results():
    if not os.path.exists(RESULTS_PATH):
        print(f"no results file found: {RESULTS_PATH}")
        sys.exit(0)
    with open(RESULTS_PATH) as f:
        return json.load(f)


def pad_cpu_log(samples, duration_s, common_end_s):
    duration_s = float(duration_s or 0.0)
    common_end_s = max(float(common_end_s or 0.0), duration_s)
    padded = [{"elapsed_s": 0.0, "cpu_percent": 0.0}]
    for sample in samples:
        elapsed = float(sample.get("elapsed_s", 0.0))
        if elapsed <= duration_s:
            padded.append({"elapsed_s": elapsed, "cpu_percent": float(sample.get("cpu_percent", 0.0))})
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
            buckets.append({"elapsed_s": (current_bucket + 0.5) * bucket_s, "cpu_percent": total / count})
            current_bucket = bucket
            total = 0.0
            count = 0
        total += float(sample["cpu_percent"])
        count += 1
    if count:
        buckets.append({"elapsed_s": (current_bucket + 0.5) * bucket_s, "cpu_percent": total / count})
    return buckets

def style_for_count(style, count):
    styled = dict(style)
    styled["markersize"] = 3.5
    return styled


def plot_line(ax, samples, label, style):
    xs = [sample["elapsed_s"] for sample in samples]
    ys = [sample["cpu_percent"] for sample in samples]
    return ax.plot(xs, ys, label=label, **style_for_count(style, len(xs)))[0]


def force_zero_ticks(ax, x_right):
    ax.set_xlim(left=0.0, right=max(float(x_right or 0.0), 1.0))
    ax.set_ylim(bottom=0.0)
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


def style_axis(ax, x_right):
    ax.set_title(plot_style.format_label("workload generation single thread"))
    ax.set_xlabel(plot_style.format_label("elapsed time (s)"))
    ax.set_ylabel("process cpu utilization\n(\\% of one logical cpu)", labelpad=12)
    force_zero_ticks(ax, x_right)
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


def main():
    configure_font()
    data = load_results()
    generation = data["generation"]
    window_s = max(generation["ycsb"]["duration_s"], generation["tectonic"]["duration_s"])
    ycsb_log = resample_cpu_log(
        pad_cpu_log(generation["ycsb"].get("cpu_log", []), generation["ycsb"]["duration_s"], window_s)
    )
    tectonic_log = resample_cpu_log(
        pad_cpu_log(generation["tectonic"].get("cpu_log", []), generation["tectonic"]["duration_s"], window_s)
    )

    fig, ax = plt.subplots(1, 1, figsize=(4.2, 2.8))
    ycsb_handle = plot_line(ax, ycsb_log, "YCSB", YCSB_STYLE)
    tectonic_handle = plot_line(ax, tectonic_log, "Tectonic", TECTONIC_STYLE)
    for boundary in generation["ycsb"].get("phase_boundaries_s", []):
        ax.axvline(boundary, color="grey", linestyle=":", linewidth=0.8)
    style_axis(ax, window_s)

    base = f"{OUT_DIR}/cpu_utilization_workload_generation_single_thread"
    fig.subplots_adjust(left=0.30, right=0.98, bottom=0.18, top=0.88)
    fig.savefig(f"{base}.pdf", bbox_inches="tight")
    fig.savefig(f"{base}.png", bbox_inches="tight", dpi=300)
    plt.close(fig)

    save_legend([ycsb_handle, tectonic_handle], ["YCSB", "Tectonic"], base)
    print(f"saved: {base}.pdf")
    print(f"saved: {base}.png")


if __name__ == "__main__":
    main()
