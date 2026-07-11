#!/usr/bin/env python3
"""
Plot CPU-utilization time series for YCSB vs Tectonic.
"""

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
RESULTS_PATH = f"{OUT_DIR}/results.json"
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


def series(samples, cpu_count):
    xs = [sample["elapsed_s"] for sample in samples]
    ys = [sample["cpu_percent"] for sample in samples]
    return xs, ys


def style_for_count(style, count):
    styled = dict(style)
    if count > 80:
        styled["markevery"] = max(1, count // 28)
    return styled


def plot_line(ax, samples, label, style, cpu_count):
    xs, ys = series(samples, cpu_count)
    if not xs:
        return None
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


def style_axis(ax, title, x_right):
    ax.set_title(plot_style.format_label(title))
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


def save_single_panel(base, title, x_right, ycsb_log, tectonic_log, cpu_count, boundaries=None):
    fig, ax = plt.subplots(1, 1, figsize=(4.2, 2.8))
    ycsb_handle = plot_line(ax, ycsb_log, "YCSB", YCSB_STYLE, cpu_count)
    tectonic_handle = plot_line(ax, tectonic_log, "Tectonic", TECTONIC_STYLE, cpu_count)
    for boundary in boundaries or []:
        ax.axvline(boundary, color="grey", linestyle=":", linewidth=0.8)
    style_axis(ax, title, x_right)
    fig.subplots_adjust(left=0.30, right=0.98, bottom=0.18, top=0.88)
    fig.savefig(f"{base}.pdf", bbox_inches="tight")
    fig.savefig(f"{base}.png", bbox_inches="tight", dpi=300)
    plt.close(fig)

    handles = [handle for handle in (ycsb_handle, tectonic_handle) if handle is not None]
    labels = ["YCSB", "Tectonic"][:len(handles)]
    if handles:
        save_legend(handles, labels, base)
    print(f"saved: {base}.pdf")
    print(f"saved: {base}.png")


def main():
    configure_font()
    data = load_results()
    cpu_count = max(float(data.get("cpu_count", 1)), 1.0)

    generation = data["generation"]
    execution = data["execution"]
    generation_window_s = max(generation["ycsb"]["duration_s"], generation["tectonic"]["duration_s"])
    execution_window_s = max(execution["ycsb"]["duration_s"], execution["tectonic"]["duration_s"])

    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.8))

    ycsb_gen_log = pad_cpu_log(
        generation["ycsb"].get("cpu_log", []),
        generation["ycsb"]["duration_s"],
        generation_window_s,
    )
    tectonic_gen_log = pad_cpu_log(
        generation["tectonic"].get("cpu_log", []),
        generation["tectonic"]["duration_s"],
        generation_window_s,
    )
    ycsb_exec_log = pad_cpu_log(
        execution["ycsb"].get("cpu_log", []),
        execution["ycsb"]["duration_s"],
        execution_window_s,
    )
    tectonic_exec_log = pad_cpu_log(
        execution["tectonic"].get("cpu_log", []),
        execution["tectonic"]["duration_s"],
        execution_window_s,
    )

    ycsb_gen = plot_line(axes[0], ycsb_gen_log, "YCSB", YCSB_STYLE, cpu_count)
    tectonic_gen = plot_line(axes[0], tectonic_gen_log, "Tectonic", TECTONIC_STYLE, cpu_count)
    for boundary in generation["ycsb"].get("phase_boundaries_s", []):
        axes[0].axvline(boundary, color="grey", linestyle=":", linewidth=0.8)

    ycsb_exec = plot_line(axes[1], ycsb_exec_log, "YCSB", YCSB_STYLE, cpu_count)
    tectonic_exec = plot_line(axes[1], tectonic_exec_log, "Tectonic", TECTONIC_STYLE, cpu_count)

    style_axis(axes[0], "workload generation", generation_window_s)
    style_axis(axes[1], "rocksdb execution", execution_window_s)

    base = f"{OUT_DIR}/cpu_utilization_timeseries"
    fig.subplots_adjust(left=0.16, right=0.98, bottom=0.18, top=0.88, wspace=0.35)
    fig.savefig(f"{base}.pdf", bbox_inches="tight")
    fig.savefig(f"{base}.png", bbox_inches="tight", dpi=300)
    plt.close(fig)

    handles = [handle for handle in (ycsb_gen or ycsb_exec, tectonic_gen or tectonic_exec) if handle is not None]
    labels = ["YCSB", "Tectonic"][:len(handles)]
    if handles:
        save_legend(handles, labels, base)

    print(f"saved: {base}.pdf")
    print(f"saved: {base}.png")

    save_single_panel(
        f"{OUT_DIR}/cpu_utilization_workload_generation",
        "workload generation",
        generation_window_s,
        ycsb_gen_log,
        tectonic_gen_log,
        cpu_count,
        generation["ycsb"].get("phase_boundaries_s", []),
    )
    save_single_panel(
        f"{OUT_DIR}/cpu_utilization_rocksdb_execution",
        "rocksdb execution",
        execution_window_s,
        ycsb_exec_log,
        tectonic_exec_log,
        cpu_count,
    )


if __name__ == "__main__":
    main()
