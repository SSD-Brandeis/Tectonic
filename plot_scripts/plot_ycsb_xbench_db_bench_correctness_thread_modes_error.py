#!/usr/bin/env python3
"""Plot 1 - accuracy (in %) for the Fig. 11 sequential workload-count experiment.

Same data, series and styling as plot_ycsb_xbench_db_bench_correctness_thread_modes.py,
but the y-axis shows 100 - accuracy_by_operation (%) on a single linear axis.
The legend is identical to workload_accuracy_sequential_<reps>runs_legend.pdf.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

from plot_ycsb_xbench_db_bench_correctness_thread_modes import (
    FONT_SIZE,
    PLOTS_DIR,
    RESULTS_PATH,
    configure_font,
    font,
    load_results,
    repetition_count,
    save_custom_legend,
    setting_runs,
)

SERIES = [
    # (tool, op, color, linestyle, marker)
    ("xbench", "I", "tab:blue", "-.", "s"),
    ("ycsb", "I", "grey", "-", "^"),
    ("ycsb", "P", "grey", "--", "o"),
    ("ycsb", "U", "grey", ":", "d"),
]


def plot_setting(data: dict, setting: str, reps: int) -> None:
    runs = setting_runs(data, setting)
    x_values = np.array([run["record_count"] for run in runs]) / 1000.0

    fig, ax = plt.subplots(figsize=(6.0, 3.8))
    for tool, op, color, linestyle, marker in SERIES:
        error = 100.0 - np.array([run["tools"][tool]["accuracy_by_operation"][op] for run in runs])
        ax.plot(x_values, error, color=color, linestyle=linestyle, marker=marker,
                markersize=15, markeredgewidth=1.2, markerfacecolor="none",
                markeredgecolor=color, linewidth=4.0)

    ax.set_ylim(-1.0, 22.0)
    ax.set_yticks([0, 5, 10, 15, 20])
    ax.set_xlim(left=0.0, right=max(x_values) * 1.05)
    ax.set_xticks([0, 5, 10, 15, 20])
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{x:g}"))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda y, _: f"{y:g}"))
    ax.set_xlabel("operation count (K)", fontproperties=font(FONT_SIZE), labelpad=12)
    ax.set_ylabel(r"1 $-$ accuracy (\%)", fontproperties=font(FONT_SIZE))
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontproperties(font(FONT_SIZE))
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.8)
    ax.tick_params(colors="black", which="both", direction="out", top=False, right=False)

    fig.subplots_adjust(left=0.20, right=0.98, bottom=0.20, top=0.92)

    base = PLOTS_DIR / f"workload_error_{setting}_{reps}runs"
    save_custom_legend(base)
    fig.savefig(f"{base}.pdf", bbox_inches="tight", pad_inches=0.16)
    plt.close(fig)
    print(f"saved: {base}.pdf")


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot 1 - accuracy for the workload-count correctness results.")
    parser.add_argument("--results", default=str(RESULTS_PATH))
    args = parser.parse_args()
    configure_font()
    data = load_results(Path(args.results))
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    plot_setting(data, "sequential", repetition_count(data))


if __name__ == "__main__":
    main()
