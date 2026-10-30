#!/usr/bin/env python3
"""
Plots for R2.O2(iii): YCSB Memory Under Bounded JVM Heap
Disentangling Headroom from Working Set

Generates plots/pdf/<name>.pdf and plots/png/<name>.png for
  xbench_vs_ycsb_capped, xbench_vs_ycsb_capped_log, ycsb_heap_floor_{memory,time}[_log];
  with --superseded also two figures of the 2026-10-26 version: ycsb_duration_and_gc, memory_comparison_bars.
plots/README.md describes each figure and explains its terms (-Xmx, GC, heap, ...).

plot_style turns on text.usetex, so a literal percent sign must be written as \\%.
"""

import re
import sys
import json
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.transforms as mtransforms
import matplotlib.ticker as mticker

ROOT = Path("/home/cc/Tectonic")
EXP_DIR = ROOT / "data" / "revision" / "R2.O2_ycsb-bounded-memory"
RESULTS_DIR = EXP_DIR / "results"
ENV_DIR = EXP_DIR / "logs" / "env"
PLOTS_DIR = EXP_DIR / "plots"
PDF_DIR = PLOTS_DIR / "pdf"
PNG_DIR = PLOTS_DIR / "png"
PDF_DIR.mkdir(parents=True, exist_ok=True)
PNG_DIR.mkdir(parents=True, exist_ok=True)

# Add plot_style
sys.path.insert(0, str(ROOT / "plot_scripts"))
try:
    import plot_style
except ImportError:
    plot_style = None

WORKLOADS = ["a", "b", "c", "d", "e", "f"]


def load_data():
    with open(RESULTS_DIR / "ycsb_bounded_memory.json", "r") as f:
        ycsb_data = json.load(f)
    with open(RESULTS_DIR / "xbench_baseline.json", "r") as f:
        xbench_data = json.load(f)
    with open(RESULTS_DIR / "jvm_base_footprint.json", "r") as f:
        base_data = json.load(f)
    with open(RESULTS_DIR / "ycsb_threshold.json", "r") as f:
        threshold_data = json.load(f)
    return ycsb_data, xbench_data, base_data, threshold_data


def plot_duration_and_gc(ycsb_data, threshold_data):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.5), dpi=300)

    # Data for Workload A including threshold
    # 6m and 8m: one run each (ycsb_threshold.json); 16m and above: mean of 5 trials
    threshold_labels = ["6m", "8m", "16m", "32m", "64m", "128m", "256m", "512m", "1024m", "Default"]
    durs = [
        threshold_data["6m"]["duration_s"],
        threshold_data["8m"]["duration_s"],
    ]
    gc_pcts = [
        threshold_data["6m"]["ycsb_metrics"]["gc_time_pct"],
        threshold_data["8m"]["ycsb_metrics"]["gc_time_pct"],
    ]

    for h in ["16m", "32m", "64m", "128m", "256m", "512m", "1024m", "default"]:
        durs.append(ycsb_data["a"][h]["summary"]["load_duration"]["mean"])
        # GC % from trials
        pcts = [tr["gc_time_pct"] for tr in ycsb_data["a"][h]["load_trials"]]
        gc_pcts.append(np.mean(pcts) if pcts else 0.0)

    x = np.arange(len(threshold_labels))

    # Panel 1: Duration
    ax1.plot(x, durs, marker="o", color="#d62728", linewidth=2.2, markersize=6)
    ax1.set_xticks(x)
    ax1.set_xticklabels(threshold_labels, rotation=35, fontsize=9.5)
    ax1.set_ylabel("Load Duration (seconds)", fontsize=10.5, fontweight="bold")
    ax1.set_xlabel("JVM Heap Ceiling (-Xmx)", fontsize=10.5, fontweight="bold")
    ax1.set_title("(A) Workload A load duration (6m, 8m: 1 run)", fontsize=11, fontweight="bold")
    ax1.grid(True, linestyle="--", alpha=0.4)

    # Panel 2: GC % Time
    ax2.bar(x, gc_pcts, color="#1f77b4", edgecolor="black", width=0.55)
    ax2.set_xticks(x)
    ax2.set_xticklabels(threshold_labels, rotation=35, fontsize=9.5)
    ax2.set_ylabel("GC time (\\% of runtime)", fontsize=10.5, fontweight="bold")
    ax2.set_xlabel("JVM Heap Ceiling (-Xmx)", fontsize=10.5, fontweight="bold")
    ax2.set_title("(B) GC time, YCSB's own counters (\\%)", fontsize=11, fontweight="bold")
    ax2.grid(True, linestyle="--", alpha=0.4, axis="y")
    ax2.set_ylim(0, 100)

    fig.tight_layout()
    save(fig, "ycsb_duration_and_gc")


def plot_comparison_bars(ycsb_data, xbench_data):
    fig, ax = plt.subplots(figsize=(8.5, 4.5), dpi=300)

    x = np.arange(len(WORKLOADS))
    width = 0.26

    # 1. YCSB Default (Load phase peak RSS)
    ycsb_def = [ycsb_data[w]["default"]["summary"]["load_rss"]["mean"] for w in WORKLOADS]
    # 2. YCSB Bounded 16m (Load phase peak RSS)
    ycsb_16m = [ycsb_data[w]["16m"]["summary"]["load_rss"]["mean"] for w in WORKLOADS]
    # 3. X-Bench Baseline (Peak RSS)
    xb_vals = [xbench_data[w]["mean_rss_mb"] for w in WORKLOADS]

    b1 = ax.bar(x - width, ycsb_def, width, label=f"YCSB load phase, default heap ({default_max_heap()})",
                color="#e74c3c", edgecolor="black", linewidth=0.7, hatch="///")
    b2 = ax.bar(x, ycsb_16m, width, label="YCSB load phase, -Xmx16m",
                color="#3498db", edgecolor="black", linewidth=0.7, hatch="\\\\")
    b3 = ax.bar(x + width, xb_vals, width, label="X-Bench, load + run in one process",
                color="#2ecc71", edgecolor="black", linewidth=0.7)

    # Add value labels on top of bars
    for bars in [b1, b2, b3]:
        for bar in bars:
            height = bar.get_height()
            ax.annotate(f"{height:.0f}",
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=8.5, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([f"Workload {w.upper()}" for w in WORKLOADS], fontsize=10.5, fontweight="bold")
    ax.set_ylabel("Memory footprint (MB)", fontsize=11, fontweight="bold")
    ax.set_title("Memory footprint per workload (1M records, 1M operations)",
                 fontsize=11.5, fontweight="bold", pad=12)
    ax.set_ylim(0, 1600)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(200))
    ax.tick_params(axis="y", labelsize=10)
    ax.grid(True, linestyle="--", alpha=0.4, axis="y")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, frameon=False, fontsize=9.5)

    fig.tight_layout()
    save(fig, "memory_comparison_bars")


def default_max_heap():
    """The JVM's default MaxHeapSize on this machine, from logs/env/java_flags_default.txt."""
    m = re.search(r"MaxHeapSize\s+=\s+(\d+)", (ENV_DIR / "java_flags_default.txt").read_text())
    return f"max {int(m[1]) / 2**30:.0f} GiB"


# Figures 1-3 below compare YCSB with a capped Java heap against X-Bench. Styles follow the paper's
# Fig. 7 (plot_figure_6_validate.py): X-Bench solid tab:red, YCSB white with black hatching.
CAPS = ["6m", "8m", "16m"]  # main figure: 6m is the smallest cap at which YCSB runs (4m fails, T13)
CAP_LABELS = {"6m": "YCSB, heap capped at 6 MB", "8m": "YCSB, heap capped at 8 MB",
              "16m": "YCSB, heap capped at 16 MB"}
BAR_STYLE = {
    "xbench": dict(facecolor="tab:red", edgecolor="black", linewidth=0.5),
    "6m": dict(facecolor="#bbbbbb", edgecolor="black", linewidth=0.5, hatch="\\\\\\"),
    "8m": dict(facecolor="white", edgecolor="black", linewidth=0.5, hatch="---"),
    "16m": dict(facecolor="white", edgecolor="black", linewidth=0.5, hatch="/////"),
    "default": dict(facecolor="white", edgecolor="black", linewidth=0.5, hatch="xxx"),
}


FIG_FONT = {"font.size": 10, "axes.labelsize": 10.5, "xtick.labelsize": 10, "ytick.labelsize": 10}


def save(fig, name):
    fig.savefig(PDF_DIR / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(PNG_DIR / f"{name}.png", bbox_inches="tight")
    plt.close(fig)
    print(f"Generated {PDF_DIR / (name + '.pdf')} and png/{name}.png")


def ycsb_pipeline(ycsb_data, w, heap):
    """YCSB generates a workload with two JVMs, load then run. Per trial, the pipeline's peak memory
    is the larger of the two peaks and its time is the sum; returns the means over trials."""
    pairs = list(zip(ycsb_data[w][heap]["load_trials"], ycsb_data[w][heap]["run_trials"]))
    assert all(l["trial"] == r["trial"] for l, r in pairs)
    mem = [max(l["peak_rss_mb"], r["peak_rss_mb"]) for l, r in pairs]
    secs = [l["duration_s"] + r["duration_s"] for l, r in pairs]
    return np.mean(mem), np.mean(secs)


def xbench_means(xbench, w):
    trials = xbench[w]
    return np.mean([t["peak_rss_mb"] for t in trials]), np.mean([t["duration_s"] for t in trials])


def plot_xbench_vs_ycsb(ycsb_data, xbench, log=False):
    """Figure 1: peak memory and generation time per workload, X-Bench vs. YCSB with a capped heap."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
    x = np.arange(len(WORKLOADS))
    width = 0.8 / (len(CAPS) + 1)
    for ax, idx, ylabel in [(axes[0], 0, "memory footprint (MB)"), (axes[1], 1, "generation time (s)")]:
        series = [("xbench", "X-Bench", [xbench_means(xbench, w)[idx] for w in WORKLOADS])]
        series += [(h, CAP_LABELS[h], [ycsb_pipeline(ycsb_data, w, h)[idx] for w in WORKLOADS]) for h in CAPS]
        for i, (key, label, vals) in enumerate(series):
            ax.bar(x + (i - len(CAPS) / 2) * width, vals, width, label=label, **BAR_STYLE[key])
        top = max(max(v) for *_, v in series)
        if log:  # y-axes start strictly at 10^0; every bar is above it
            ax.set_yscale("log")
            ax.set_ylim(1.0, top * 2)
        else:
            ax.set_ylim(0, top * 1.1)
        ax.set_xticks(x)
        ax.set_xticklabels([w.upper() for w in WORKLOADS])
        ax.set_xlabel(plot_style.format_label("YCSB workload") if plot_style else "YCSB workload")
        ax.set_ylabel(plot_style.format_label(ylabel) if plot_style else ylabel)
        ax.grid(True, linestyle="--", alpha=0.4, axis="y")
        ax.set_axisbelow(True)
    axes[0].set_title("(A) memory footprint", fontsize=11)
    axes[1].set_title("(B) generation time", fontsize=11)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.tight_layout()
    fig.legend(handles, labels, loc="upper center", ncol=4, frameon=False, fontsize=9.5,
               bbox_to_anchor=(0.5, 0.0), handlelength=1.6, handleheight=0.9)
    save(fig, "xbench_vs_ycsb_capped_log" if log else "xbench_vs_ycsb_capped")


HEAP_FLOOR_FONT = {"font.size": 17, "axes.labelsize": 17, "xtick.labelsize": 16, "ytick.labelsize": 16,
                   "legend.fontsize": 16}


def plot_heap_floor(floor, ycsb_data, xbench, log=False):
    """Figure 3: the two promised metrics of workload A as YCSB's heap cap shrinks, one figure each
    (ycsb_heap_floor_memory, ycsb_heap_floor_time), with X-Bench as a line. YCSB as in Figure 1 (per trial
    the larger peak of load and run, the sum of their times), every cap of the main sweep; line = mean of the
    5 trials (no min-max shading: the default heap's spread fans it out from 1024). 4m from the floor runs (the load fails); 12m is left out
    (load phase only). The default heap (no -Xmx, the paper's setting) is the last point of the line."""
    caps = ["6m", "8m", "16m", "32m", "64m", "128m", "256m", "512m", "1024m", "default"]
    x_fail, x = 1, np.arange(2, len(caps) + 2)  # x = 0 is the origin, labelled 0
    labels = ["0", "4"] + [h[:-1] for h in caps[:-1]] + [f"default\n({default_max_heap()[4:]})"]
    metrics = [(0, "memory", "memory footprint (MB)", "upper left"), (1, "time", "generation time (s)", "upper right")]
    for idx, name, ylabel, legend_loc in metrics:
        with plt.rc_context(HEAP_FLOOR_FONT):
            fig, ax = plt.subplots(figsize=(5.8, 3.6))
            per_trial = np.array([[max(l["peak_rss_mb"], r["peak_rss_mb"]) if idx == 0 else l["duration_s"] + r["duration_s"]
                                   for l, r in zip(ycsb_data["a"][h]["load_trials"], ycsb_data["a"][h]["run_trials"])]
                                  for h in caps])
            ycsb_mean, hi = per_trial.mean(axis=1), per_trial.max(axis=1)
            xb = np.array([t["peak_rss_mb"] if idx == 0 else t["duration_s"] for t in xbench["a"]])
            # the y-axis ends at the first tick at or above the highest trial: 10^k on log, a round tick on linear
            if log:  # the y-axis starts strictly at 10^0; every value is above it
                ax.set_yscale("log")
                bottom, top = 1.0, 10.0 ** np.ceil(np.log10(hi.max()))
            else:
                ax.set_ylim(0.0, hi.max())
                bottom, top = 0.0, min(t for t in ax.get_yticks() if t >= hi.max())
            ax.axhspan(xb.min(), xb.max(), color="tab:red", alpha=0.15, linewidth=0)
            ax.axhline(xb.mean(), color="tab:red", linestyle="--", linewidth=1.8, label="X-Bench")
            ax.plot(x, ycsb_mean, color="black", marker="o", markersize=5, linewidth=1.8, label="YCSB")
            label_box = dict(boxstyle="round,pad=0.12", facecolor="white", edgecolor="none", alpha=0.85)
            # the note goes below X-Bench if that leaves at least 40% of the axis height, else just above it
            below = np.log10(xb.min() / bottom) / np.log10(top / bottom) if log else (xb.min() - bottom) / (top - bottom)
            ax.annotate("fails:\nout of\nmemory", xy=(x_fail, bottom if below >= 0.4 else xb.max()), xytext=(0, 4),
                        textcoords="offset points",
                        ha="center", va="bottom", fontsize=14, bbox=label_box, zorder=5)
            ax.set_ylim(bottom, top)
            ax.set_xlim(0, x[-1] + 0.5)
            ax.set_xticks([0, x_fail, *x])
            ax.set_xticklabels(labels, fontsize=14)  # 11 categories: 16 pt runs 512, 1024 and default together
            default_label = ax.get_xticklabels()[-1]
            default_label.set_fontsize(12)
            default_label.set_transform(default_label.get_transform()  # clear of the 1024 label
                                        + mtransforms.ScaledTranslation(7 / 72, 0, fig.dpi_scale_trans))
            ax.set_xlabel("YCSB Java heap cap, -Xmx (MB)")  # not format_label: it lowercases Java and -Xmx
            ax.set_ylabel(plot_style.format_label(ylabel) if plot_style else ylabel)
            ax.grid(True, linestyle="--", alpha=0.4)
            ax.set_axisbelow(True)
            handles, legend_labels = ax.get_legend_handles_labels()
            ax.legend(handles[::-1], legend_labels[::-1], loc=legend_loc, frameon=False, handlelength=1.8,
                      borderaxespad=0.3)
            fig.tight_layout(pad=0.2)
            save(fig, f"ycsb_heap_floor_{name}" + ("_log" if log else ""))


def main():
    print("Generating figures for R2.O2(iii)...")
    ycsb_data, xbench_data, base_data, threshold_data = load_data()
    with open(RESULTS_DIR / "xbench_breakdown.json") as f:
        xbench = json.load(f)
    with open(RESULTS_DIR / "ycsb_heap_floor.json") as f:
        floor = json.load(f)

    with plt.rc_context(FIG_FONT):  # plot_style's 22 pt suits single small panels, not these
        plot_xbench_vs_ycsb(ycsb_data, xbench)
        plot_xbench_vs_ycsb(ycsb_data, xbench, log=True)
        plot_heap_floor(floor, ycsb_data, xbench)
        plot_heap_floor(floor, ycsb_data, xbench, log=True)

        if "--superseded" in sys.argv:
            # figures of the 2026-10-26 version, replaced by the ones above
            plot_duration_and_gc(ycsb_data, threshold_data)
            plot_comparison_bars(ycsb_data, xbench_data)

    print("All figures created in:", PDF_DIR, "and", PNG_DIR)


if __name__ == "__main__":
    main()
