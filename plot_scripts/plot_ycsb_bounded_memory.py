#!/usr/bin/env python3
"""
Plots for R2.O2(iii): YCSB Memory Under Bounded JVM Heap
Disentangling Headroom from Working Set

Generates:
  1. plots/ycsb_memory_vs_heap.pdf (and .png)
  2. plots/ycsb_duration_and_gc.pdf (and .png)
  3. plots/memory_comparison_bars.pdf (and .png)
  4. plots/ycsb_heap_breakdown.pdf (and .png), from results/ycsb_heap_breakdown.json

plot_style turns on text.usetex, so a literal percent sign must be written as \\%.
"""

import re
import sys
import json
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

ROOT = Path("/home/cc/Tectonic")
EXP_DIR = ROOT / "data" / "revision" / "R2.O2_ycsb-bounded-memory"
RESULTS_DIR = EXP_DIR / "results"
ENV_DIR = EXP_DIR / "logs" / "env"
PLOTS_DIR = EXP_DIR / "plots"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

# Add plot_style
sys.path.insert(0, str(ROOT / "plot_scripts"))
try:
    import plot_style
except ImportError:
    plot_style = None

# Colors
COLOR_LOAD = "#1f77b4"     # Tab blue
COLOR_RUN = "#ff7f0e"      # Tab orange
COLOR_XBENCH = "#2ca02c"   # Tab green
COLOR_JVM_BASE = "#7f7f7f" # Grey
COLOR_HEADROOM = "#d62728" # Tab red / pink shading

HEAP_LABELS = ["16m", "32m", "64m", "128m", "256m", "512m", "1024m", "Default"]
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


def plot_memory_vs_heap(ycsb_data, xbench_data, base_data):
    fig, ax = plt.subplots(figsize=(7.5, 4.8), dpi=300)

    # Compute mean across workloads A-F for each heap config
    load_means = []
    load_mins = []
    load_maxs = []
    run_means = []
    run_mins = []
    run_maxs = []

    for h in HEAP_LABELS:
        h_key = h.lower() if h != "Default" else "default"
        l_vals = [ycsb_data[w][h_key]["summary"]["load_rss"]["mean"] for w in WORKLOADS]
        r_vals = [ycsb_data[w][h_key]["summary"]["run_rss"]["mean"] for w in WORKLOADS]
        load_means.append(np.mean(l_vals))
        load_mins.append(np.min(l_vals))
        load_maxs.append(np.max(l_vals))
        run_means.append(np.mean(r_vals))
        run_mins.append(np.min(r_vals))
        run_maxs.append(np.max(r_vals))

    x = np.arange(len(HEAP_LABELS))

    # Shaded bands for base JVM and working set
    base_jvm_rss = base_data["java_version_16m"]["mean_rss_mb"]  # ~35.0 MB
    xbench_rss = np.mean([xbench_data[w]["mean_rss_mb"] for w in WORKLOADS])  # ~92.7 MB

    # Plot base JVM runtime line
    ax.axhline(base_jvm_rss, color=COLOR_JVM_BASE, linestyle=":", linewidth=1.5,
               label=f"java -Xmx16m -version ({base_jvm_rss:.1f} MB)")

    # Plot X-Bench baseline
    ax.axhline(xbench_rss, color=COLOR_XBENCH, linestyle="--", linewidth=1.8,
               label=f"X-Bench, load + run in one process ({xbench_rss:.1f} MB)")

    # Plot Load and Run Curves
    ax.plot(x, load_means, marker="o", markersize=7, linewidth=2.2, color=COLOR_LOAD,
            label="YCSB Load Phase (1M inserts)")
    ax.fill_between(x, load_mins, load_maxs, color=COLOR_LOAD, alpha=0.15)

    ax.plot(x, run_means, marker="s", markersize=7, linewidth=2.2, color=COLOR_RUN,
            label="YCSB Run Phase (1M ops)")
    ax.fill_between(x, run_mins, run_maxs, color=COLOR_RUN, alpha=0.15)

    # Annotate Knee of Curve & Headroom
    ax.annotate(f"-Xmx16m: {load_means[0]:.0f} MB (load)",
                xy=(0, load_means[0]), xytext=(0.5, 320),
                arrowprops=dict(facecolor="black", shrink=0.08, width=1, headwidth=6),
                fontsize=9.5, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#e8f4f8", edgecolor="#1f77b4"))

    ax.annotate(f"default heap: +{load_means[7] - load_means[0]:,.0f} MB\nover -Xmx16m (load)",
                xy=(7, load_means[7]), xytext=(4.3, 1150),
                arrowprops=dict(facecolor="black", shrink=0.08, width=1, headwidth=6),
                fontsize=9.5, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#fde8e8", edgecolor="#d62728"))

    ax.set_xticks(x)
    ax.set_xticklabels(HEAP_LABELS, fontsize=10.5)
    ax.set_xlabel("Configured JVM Heap Bound (-Xmx)", fontsize=11, fontweight="bold")
    ax.set_ylabel("Peak Resident Set Size (RSS in MB)", fontsize=11, fontweight="bold")
    ax.set_title("R2.O2(iii): YCSB Peak Memory vs. Bounded JVM Heap Ceiling (1M Scale)",
                 fontsize=12, fontweight="bold", pad=12)

    ax.set_ylim(0, 1550)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(200))
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="upper left", frameon=True, framealpha=0.9, fontsize=9.5)

    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "ycsb_memory_vs_heap.pdf")
    fig.savefig(PLOTS_DIR / "ycsb_memory_vs_heap.png")
    plt.close(fig)
    print(f"Generated {PLOTS_DIR / 'ycsb_memory_vs_heap.pdf'}")


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
    ax1.axvline(2, color="gray", linestyle=":", linewidth=1.2)
    ax1.annotate(f"16m: {durs[2]:.2f} s,\nGC {gc_pcts[2]:.1f}\\% of runtime", xy=(2, durs[2]), xytext=(2.6, 60),
                 arrowprops=dict(facecolor="black", shrink=0.08, width=1, headwidth=5),
                 fontsize=9, bbox=dict(boxstyle="round,pad=0.2", facecolor="#fff", edgecolor="gray"))
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
    fig.savefig(PLOTS_DIR / "ycsb_duration_and_gc.pdf")
    fig.savefig(PLOTS_DIR / "ycsb_duration_and_gc.png")
    plt.close(fig)
    print(f"Generated {PLOTS_DIR / 'ycsb_duration_and_gc.pdf'}")


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
    ax.set_ylabel("Peak Resident Set Size (RSS in MB)", fontsize=11, fontweight="bold")
    ax.set_title("Peak RSS per workload (1M records, 1M operations)",
                 fontsize=11.5, fontweight="bold", pad=12)
    ax.set_ylim(0, 1600)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(200))
    ax.tick_params(axis="y", labelsize=10)
    ax.grid(True, linestyle="--", alpha=0.4, axis="y")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, frameon=False, fontsize=9.5)

    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "memory_comparison_bars.pdf")
    fig.savefig(PLOTS_DIR / "memory_comparison_bars.png")
    plt.close(fig)
    print(f"Generated {PLOTS_DIR / 'memory_comparison_bars.pdf'}")


def default_max_heap():
    """The JVM's default MaxHeapSize on this machine, from logs/env/java_flags_default.txt."""
    m = re.search(r"MaxHeapSize\s+=\s+(\d+)", (ENV_DIR / "java_flags_default.txt").read_text())
    return f"max {int(m[1]) / 2**30:.0f} GiB"


# Reference categorical palette, slots 1-4 in order; hatches as a second cue
BREAKDOWN_PARTS = [
    ("live", "Java heap: live after GC (working set)", "#2a78d6", ""),
    ("headroom", "Java heap: resident, not live (headroom)", "#eb6834", "///"),
    ("anon", "other anonymous: metaspace, code cache, GC, malloc", "#1baf7a", ".."),
    ("file", "file-backed: libjvm.so, CDS archive, libc", "#eda100", "\\\\"),
]


def plot_heap_breakdown(breakdown):
    heaps = list(breakdown["a"].keys())
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), dpi=300, sharey=True)
    for ax, phase in zip(axes, ["load", "run"]):
        parts = {k: [] for k, *_ in BREAKDOWN_PARTS}
        peaks = []
        for h in heaps:
            trials = [t for w in WORKLOADS for t in breakdown[w][h][phase]]
            heap_res = [t["resident_at_peak"]["java_heap_mb"] for t in trials]
            # a run without any GC has no live-heap measurement: count all of its heap as live
            live = [min(t["live_after_gc_max_mb"] if t["live_after_gc_max_mb"] is not None else r, r)
                    for t, r in zip(trials, heap_res)]
            parts["live"].append(np.mean(live))
            parts["headroom"].append(np.mean(heap_res) - np.mean(live))
            parts["anon"].append(np.mean([t["resident_at_peak"]["other_anon_mb"] for t in trials]))
            parts["file"].append(np.mean([t["resident_at_peak"]["file_backed_mb"] for t in trials]))
            peaks.append(np.mean([t["peak_rss_mb"] for t in trials]))
        x = np.arange(len(heaps))
        bottom = np.zeros(len(heaps))
        for key, label, color, hatch in BREAKDOWN_PARTS:
            ax.bar(x, parts[key], 0.6, bottom=bottom, label=label, color=color, hatch=hatch,
                   edgecolor="white", linewidth=1.0)
            bottom += np.array(parts[key])
        for xi, top, peak in zip(x, bottom, peaks):
            ax.annotate(f"{peak:,.0f}", xy=(xi, top), xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=9)
        ax.set_xticks(x)
        ax.set_xticklabels([h if h != "default" else "default" for h in heaps], fontsize=10)
        ax.set_xlabel("JVM heap bound (-Xmx)", fontsize=10.5, fontweight="bold")
        ax.set_title(f"YCSB {phase} phase, mean of A-F", fontsize=11, fontweight="bold")
        ax.tick_params(axis="y", labelsize=10)
        ax.grid(True, linestyle="--", alpha=0.4, axis="y")
    axes[0].set_ylabel("Resident memory at peak RSS (MB)", fontsize=10.5, fontweight="bold")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles[::-1], labels[::-1], loc="lower center", ncol=2, frameon=False, fontsize=9)
    fig.tight_layout(rect=(0, 0.13, 1, 1))
    fig.savefig(PLOTS_DIR / "ycsb_heap_breakdown.pdf")
    fig.savefig(PLOTS_DIR / "ycsb_heap_breakdown.png")
    plt.close(fig)
    print(f"Generated {PLOTS_DIR / 'ycsb_heap_breakdown.pdf'}")


def main():
    print("Generating publication-quality figures for R2.O2(iii)...")
    ycsb_data, xbench_data, base_data, threshold_data = load_data()

    plot_memory_vs_heap(ycsb_data, xbench_data, base_data)
    plot_duration_and_gc(ycsb_data, threshold_data)
    plot_comparison_bars(ycsb_data, xbench_data)
    breakdown_path = RESULTS_DIR / "ycsb_heap_breakdown.json"
    if breakdown_path.exists():
        with open(breakdown_path) as f:
            plot_heap_breakdown(json.load(f))

    print("All figures successfully created in:", PLOTS_DIR)


if __name__ == "__main__":
    main()
