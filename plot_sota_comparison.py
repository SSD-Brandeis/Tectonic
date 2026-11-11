#!/usr/bin/env python3
"""SOTA comparison: end-to-end latency and memory footprint of X-Bench vs.
KVBench (KVBench workloads I-V) and vs. YCSB and KVBench (YCSB workloads A-F).

Each figure shows one metric for one workload suite, with one bar per tool.
Both metrics are measured the same way for every tool:
  latency  wall-clock seconds from generator start to exit. YCSB runs as two
           processes (-load, then -t), so its latency is their sum.
  memory   peak sampled VmRSS of the generator process(es).

The split latency figures divide each latency into a loading and an execution
phase, using the phase boundary recorded in each trace:
  YCSB             loading = -load process, execution = -t process
  KVBench, X-Bench loading = loading_phase_end_time,
                   execution = execution_phase_duration (= latency - loading)

Reads  <data-root>/results/runs.*.json (default; written by
       run_scripts/run_fig7_load_execution.py): every bar is the mean over the
       trials, and every (workload, tool) must have the same number of trials,
       none failed. Loading of KVBench and X-Bench there is when the phase-end
       line reached the runner, on the same clock as the latency.
   or, with --traces, <data-root>/traces/{ycsb,kvbench,xbench}_<workload>_trace.json
       (written by run_end_to_end_latency_and_mem_footprint.py)
Writes <data-root>/plots/ (paper Fig. 7; every figure ends in -new-fig7.pdf)
       latency_{kvbench,ycsb}-new-fig7.pdf        execution/loading split
       latency_{kvbench,ycsb}_total-new-fig7.pdf  single-panel total (log axis)
       memory_{kvbench,ycsb}-new-fig7.pdf, legend-new-fig7.pdf, summary.csv

Run: python3 scripts/plot_sota_comparison.py
Text sizes, canvas and frame size come from plot_style.py (shared by all figures).
"""

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.patches import Patch

# Shared paper style: fonts, text sizes, canvas, bar styles (sets rcParams on import).
import plot_style
from plot_style import (ANNOTATION_FONTSIZE, AXES_MARGINS_IN, BAR_EDGE_WIDTH, FIGSIZE,
                        FONT_SIZE, XLABEL_PAD, YLABEL_TOP_DROP_IN, anchor_ylabel_to_canvas_top,
                        apply_frame_margins, save_figure, save_legend_figure)

REPO_ROOT = Path(__file__).resolve().parent  # the script sits at the repo root
DATA_ROOT = REPO_ROOT / "data" / "revision" / "fig7-load-execution"
FIG_SUFFIX = "-new-fig7"  # paper figure number, ends every output file name

SUITE_WORKLOADS = {
    "kvbench": ["I", "II", "III", "IV", "V"],
    "ycsb": ["A", "B", "C", "D", "E", "F"],
}
SUITE_XLABEL = {"kvbench": "KVBench workload", "ycsb": "YCSB workload"}

# Tools compared on each suite, in bar order: baselines first, X-Bench last.
SUITE_TOOLS = {
    "kvbench": ["KVBench", "X-Bench"],
    "ycsb": ["YCSB", "KVBench", "X-Bench"],
}
# KVBench has no read-modify-write operation, so it cannot express YCSB-F.
UNSUPPORTED = {("ycsb", "F", "KVBench")}

TRACE_PREFIX = {"YCSB": "ycsb", "KVBench": "kvbench", "X-Bench": "xbench"}

# Fill color and hatch per tool come from plot_style.BAR_STYLES, so every figure
# draws a tool the same way (X-Bench: solid tab:blue; baselines: hatched white).
STYLE_KEY = {"YCSB": "YCSB", "KVBench": "KVBench", "X-Bench": "XBench"}
TOOL_COLOR = {tool: plot_style.BAR_STYLES[key]["facecolor"] for tool, key in STYLE_KEY.items()}
TOOL_HATCH = {tool: plot_style.BAR_STYLES[key]["hatch"] for tool, key in STYLE_KEY.items()}
# Unfilled bars need a thin outline to show where each bar ends. Matplotlib
# also draws the hatches in the edge color.
BAR_EDGE = {"edgecolor": "black", "linewidth": BAR_EDGE_WIDTH}
# Solid-filled bars need no outline. Edge color stays black (linewidth 0) so any
# hatch would still draw in black.
TOOL_EDGE = {tool: ({"edgecolor": "black", "linewidth": 0} if TOOL_COLOR[tool] != "white" else BAR_EDGE)
             for tool in TOOL_COLOR}
# Latency bars: a black divider at the end of the loading phase.
PHASE_DIVIDER_COLOR = "black"
PHASE_DIVIDER_WIDTH = 2.0

# Color of the "!" that marks a workload a tool cannot express.
UNSUPPORTED_COLOR = "#404040"

# Memory is recorded in MB (VmRSS / 1024) and plotted in GB.
MB_PER_GB = 1024
# Top of the memory axis per suite, in GB.
MEMORY_TOP_GB = {"kvbench": 2.1, "ycsb": 2}
# Top of the log latency axis per suite, in seconds. KVBench's IV takes about 2,000 s.
LATENCY_TOP = {"kvbench": 1e4, "ycsb": 1e2}

# Split latency figures: execution phase (top panel) over loading phase (bottom
# panel), sharing the x-axis. Each panel has its own linear y-range, in seconds.
# Same canvas as the single-panel figures: drawn at FIGSIZE and cropped to the
# box of the single-panel latency figure, with the plot frame at the same left edge.
SPLIT_PHASES = ["execution", "load"]  # top to bottom
# Both panels span 0-30 s. Bars taller than that (KVBench's execution of II and IV,
# and its loading of I) are clipped at the top and labeled with their value.
SPLIT_YTOP = {"execution": 30, "load": 30}
SPLIT_YTICK_STEP = 15  # ticks 0, 15, 30 (authors, 2026-11-11)
SPLIT_PANEL_LABEL = {"execution": "execution", "load": "loading"}
SPLIT_HSPACE_IN = 0.22  # room for a clipped-bar value above the lower panel
# Panel labels ("execution", "loading"): inside each panel at its top right,
# italic, dark grey; inset from the frame corner as a fraction of the panel.
SPLIT_PANEL_LABEL_INSET = 0.03
# Panels whose top corners are taken by tall bars get the label centered above a
# workload with short bars instead: KVBench loading, where I (32 s), III (29 s),
# IV (27 s, full size) and V (26 s) are tall and only II's bars are short.
SPLIT_PANEL_LABEL_ABOVE = {("kvbench", "load"): "II"}
# 15 pt: at 18 pt they ran into the tallest bars (KVBench IV, YCSB E).
SPLIT_PANEL_LABEL_COLOR = "dimgrey"
SPLIT_PANEL_LABEL_FONTSIZE = 15
# Value written above a clipped bar, outside the frame; small enough to fit in TOP_PAD.
CLIPPED_VALUE_FONTSIZE = ANNOTATION_FONTSIZE
# X-Bench's execution phase is short (about 0.2-11 s), so its value is written
# above each bar in the execution panel (vertical, to fit the narrow bar slot).
VALUE_LABEL_TOOL = "X-Bench"
VALUE_LABEL_FONTSIZE = ANNOTATION_FONTSIZE
VALUE_LABEL_SHIFT_PT = 3  # rightward shift of the X-Bench values, in points
GROUP_WIDTH = 0.8


def load_trace(path, tool):
    """(latency_s, load_s, memory_mb) from one generator trace.

    Loading phase: YCSB's separate -load run; for KVBench and X-Bench, the
    trace's loading_phase_end_time. Execution phase = latency - load_s, which is
    the trace's execution_phase_duration.
    """
    trace = json.loads(path.read_text(encoding="utf-8"))
    if trace["tool"] != tool:
        raise ValueError(f"{path}: tool is {trace['tool']!r}, expected {tool!r}")
    runs = [trace["load"], trace["run"]] if tool == "YCSB" else [trace]
    latency = sum(float(run["total_duration"]) for run in runs)
    memory = max(float(mb) for run in runs for _, mb in run["mem_log"])
    if tool == "YCSB":
        load = float(trace["load"]["total_duration"])
    else:
        load = float(trace["loading_phase_end_time"])
        execution = float(trace["execution_phase_duration"])
        if abs(load + execution - latency) > 1e-6:
            raise ValueError(f"{path}: loading + execution != total_duration")
    return latency, load, memory


def load_results(trace_dir, prefixes):
    """{suite: {workload: {tool: {"latency": s, "load": s, "memory": MB}}}}"""
    results = {}
    for suite, workloads in SUITE_WORKLOADS.items():
        results[suite] = {}
        for workload in workloads:
            results[suite][workload] = {}
            for tool in SUITE_TOOLS[suite]:
                if (suite, workload, tool) in UNSUPPORTED:
                    continue
                path = trace_dir / f"{prefixes[tool]}_{workload.lower()}_trace.json"
                latency, load, memory = load_trace(path, tool)
                results[suite][workload][tool] = {"latency": latency, "load": load, "memory": memory}
    return results


# Tool names in the run records of run_fig7_load_execution.py.
RUNS_TOOL = {"YCSB": "YCSB", "KV-Bench": "KVBench", "X-Bench": "X-Bench"}


def load_results_from_runs(results_dir):
    """Same shape as load_results, from results/runs.*.json: the mean over the
    trials of wall time (latency), loading and peak sampled VmRSS (MiB)."""
    records = []
    for path in sorted(results_dir.glob("runs.*.json")):
        records += json.loads(path.read_text(encoding="utf-8"))
    by_cell = {}
    for r in records:
        if r.get("failed"):
            raise ValueError(f"failed run: {r['job']} trial {r['trial']}")
        tool = RUNS_TOOL[r["tool"]]
        by_cell.setdefault((r["workload_set"], r["workload"], tool), {})[r["trial"]] = r
    results, trials = {}, set()
    for suite, workloads in SUITE_WORKLOADS.items():
        results[suite] = {}
        for workload in workloads:
            results[suite][workload] = {}
            for tool in SUITE_TOOLS[suite]:
                if (suite, workload, tool) in UNSUPPORTED:
                    continue
                runs = list(by_cell.get((suite, workload, tool), {}).values())
                if not runs:
                    raise ValueError(f"no runs for {suite} {workload} {tool}")
                for r in runs:
                    if abs(r["loading_s"] + r["execution_s"] - r["wall_time_s"]) > 1e-6:
                        raise ValueError(f"{r['job']} trial {r['trial']}: loading + execution != wall")
                trials.add(len(runs))
                n = len(runs)
                results[suite][workload][tool] = {
                    "latency": sum(r["wall_time_s"] for r in runs) / n,
                    "load": sum(r["loading_s"] for r in runs) / n,
                    "memory": sum(r["peak_rss_mib"] for r in runs) / n,
                }
    if len(trials) != 1:
        raise ValueError(f"unequal trial counts across bars: {sorted(trials)}")
    print(f"{trials.pop()} trials per bar")
    return results


def draw_bars(ax, results, suite, metric, label_clipped=None, label_tool=None):
    """label_clipped: write the value of bars taller than the y-axis, which must
    already be set. "above": above the frame (top panel); "inside": right of the
    bar, inside the frame (lower panel, where the space above is the gap).
    label_tool: write the value above every bar of this tool."""
    tools = SUITE_TOOLS[suite]
    width = GROUP_WIDTH / len(tools)
    for i, tool in enumerate(tools):
        offset = (i - (len(tools) - 1) / 2) * width
        for x, workload in enumerate(SUITE_WORKLOADS[suite]):
            values = results[suite][workload].get(tool)
            if values is None:
                # Flag an unsupported workload instead of leaving a silent gap.
                ax.text(x + offset, 0.02, "!", transform=ax.get_xaxis_transform(),
                        ha="center", va="bottom", fontsize=30, color=UNSUPPORTED_COLOR)
                continue
            if metric == "memory":
                height = values["memory"] / MB_PER_GB
            elif metric == "execution":
                height = values["latency"] - values["load"]
            else:
                height = values[metric]
            ax.bar(x + offset, height, width,
                   facecolor=TOOL_COLOR[tool], hatch=TOOL_HATCH[tool], **TOOL_EDGE[tool])
            top = ax.get_ylim()[1]
            if tool == label_tool:
                # Shifted right of the bar center so it clears the neighboring bar.
                ax.annotate(f"{height:.2f}", (x + offset, height), xytext=(VALUE_LABEL_SHIFT_PT, 2),
                            textcoords="offset points", ha="center", va="bottom",
                            rotation=90, fontsize=VALUE_LABEL_FONTSIZE)
            if label_clipped and round(height) > top:
                # Clipped bar: write its true value above the bar, outside the
                # plot frame. A bar that rounds to the axis top (e.g. 20.2 s on
                # a 20 s axis) is drawn to the top, unlabeled.
                if label_clipped == "above":
                    ax.annotate(f"{height:.0f}", (x + offset, top), xytext=(0, -0.5),
                                textcoords="offset points", ha="center", va="bottom",
                                fontsize=CLIPPED_VALUE_FONTSIZE, annotation_clip=False)
                else:
                    ax.annotate(f"{height:.0f}", (x + offset + width / 2, top), xytext=(2, -2),
                                textcoords="offset points", ha="left", va="top",
                                fontsize=CLIPPED_VALUE_FONTSIZE)
            if metric == "latency":
                # The divider marks when the loading phase ended; above it is
                # the execution phase.
                ax.hlines(values["load"], x + offset - width / 2, x + offset + width / 2,
                          color=PHASE_DIVIDER_COLOR, linewidth=PHASE_DIVIDER_WIDTH)


def style_x_axis(ax, suite):
    workloads = SUITE_WORKLOADS[suite]
    ax.set_xticks(range(len(workloads)))
    ax.set_xticklabels(workloads)
    ax.set_xlim(-0.5, len(workloads) - 0.5)
    ax.set_xlabel(SUITE_XLABEL[suite], labelpad=XLABEL_PAD)


def style_y_axis_latency(ax, top):
    ax.set_yscale("log")
    ax.set_ylim(1, top)
    ax.yaxis.set_major_locator(mticker.LogLocator(base=10, numticks=12))  # every decade
    ax.yaxis.set_major_formatter(mticker.LogFormatterMathtext(base=10))
    ax.yaxis.set_minor_locator(mticker.NullLocator())
    ax.set_ylabel("end-to-end latency (s)", loc="top")


def style_y_axis_memory(ax, top):
    ax.set_ylim(0, top)
    ax.yaxis.set_major_locator(mticker.MultipleLocator(1))
    # Level with "end-to-end latency (s)" in the split latency figures.
    anchor_ylabel_to_canvas_top(ax, "memory footprint (GB)")


def plot_latency(results, suite, top, out_path):
    """Returns the crop box, so the split figure can use the same canvas."""
    fig, ax = plt.subplots(figsize=FIGSIZE)
    draw_bars(ax, results, suite, "latency")
    style_x_axis(ax, suite)
    style_y_axis_latency(ax, top)
    apply_frame_margins(fig)
    return save_figure(fig, ax, out_path)


def plot_memory(results, suite, top, out_path):
    fig, ax = plt.subplots(figsize=FIGSIZE)
    draw_bars(ax, results, suite, "memory")
    style_x_axis(ax, suite)
    style_y_axis_memory(ax, top)
    apply_frame_margins(fig)
    save_figure(fig, ax, out_path)


def style_y_axis_linear(ax, top, show_top_tick=True):
    ax.set_ylim(0, top)
    ticks = list(range(0, int(top) + 1, SPLIT_YTICK_STEP))
    if not show_top_tick and ticks[-1] == top:
        ticks = ticks[:-1]
    ax.yaxis.set_major_locator(mticker.FixedLocator(ticks))
    ax.yaxis.set_minor_locator(mticker.NullLocator())


def plot_latency_split(results, suite, canvas, out_path):
    """Latency split by phase: execution (top panel) over loading (bottom panel).

    canvas: crop box of the single-panel latency figure, in inches.
    """
    fig, axes = plt.subplots(len(SPLIT_PHASES), 1, sharex=True, figsize=FIGSIZE)
    for ax, phase in zip(axes, SPLIT_PHASES):
        # Both panels show their top tick, 30 (authors, 2026-11-11).
        style_y_axis_linear(ax, SPLIT_YTOP[phase], show_top_tick=True)
        draw_bars(ax, results, suite, phase,
                  label_clipped="above",
                  label_tool=VALUE_LABEL_TOOL if phase == "execution" else None)
    style_x_axis(axes[-1], suite)

    w, h = fig.get_size_inches()
    m = AXES_MARGINS_IN
    panel_h = (h - m["top"] - m["bottom"] - SPLIT_HSPACE_IN) / len(axes)
    fig.subplots_adjust(left=m["left"] / w, right=1 - m["right"] / w,
                        bottom=m["bottom"] / h, top=1 - m["top"] / h,
                        hspace=SPLIT_HSPACE_IN / panel_h)
    pad = plt.rcParams["savefig.pad_inches"]
    # Panel labels: inside each panel at its top right (centered above a
    # workload where SPLIT_PANEL_LABEL_ABOVE says), italic, dark grey.
    for ax, phase in zip(axes, SPLIT_PHASES):
        above = SPLIT_PANEL_LABEL_ABOVE.get((suite, phase))
        if above:
            x = (SUITE_WORKLOADS[suite].index(above) + 0.5) / len(SUITE_WORKLOADS[suite])
        else:
            x = 1 - SPLIT_PANEL_LABEL_INSET
        ax.text(x, 1 - SPLIT_PANEL_LABEL_INSET,
                rf"\textit{{{SPLIT_PANEL_LABEL[phase]}}}", transform=ax.transAxes,
                ha="center" if above else "right", va="top", color=SPLIT_PANEL_LABEL_COLOR,
                fontsize=SPLIT_PANEL_LABEL_FONTSIZE)
    # Overall label: axes.labelpad left of the widest tick label (as a normal
    # y-label), its top YLABEL_TOP_DROP_IN below the top edge of the canvas.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    ticks_x0 = min(t.get_window_extent(renderer).x0
                   for ax in axes for t in ax.get_yticklabels() if t.get_visible() and t.get_text())
    x = (ticks_x0 - plt.rcParams["axes.labelpad"] * fig.dpi / 72) / fig.dpi / w
    ylabel = fig.text(x, (canvas.y1 - pad - YLABEL_TOP_DROP_IN) / h,
                      "end-to-end latency (s)", rotation=90, ha="right", va="top")

    # Height and right edge of the single-panel canvas; the left edge is the
    # usual pad left of the y-label, so no blank strip is left there.
    from matplotlib.transforms import Bbox
    left = ylabel.get_window_extent(renderer).x0 / fig.dpi - pad
    fig.savefig(out_path, bbox_inches=Bbox.from_extents(left, canvas.y0, canvas.x1, canvas.y1))
    plt.close(fig)
    print(f"wrote {out_path}")


def plot_legend(out_path):
    handles = [Patch(facecolor=TOOL_COLOR[tool], hatch=TOOL_HATCH[tool], label=tool, **TOOL_EDGE[tool])
               for tool in SUITE_TOOLS["ycsb"]]
    save_legend_figure(handles, out_path, handlelength=1.6, handleheight=0.8)


def write_summary(results, out_path):
    """The number behind every bar, plus each tool's ratio to X-Bench."""
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["suite", "workload", "tool", "latency_s", "load_s", "execution_s",
                         "memory_mb", "latency_vs_xbench", "memory_vs_xbench"])
        for suite, by_workload in results.items():
            for workload, by_tool in by_workload.items():
                xbench = by_tool["X-Bench"]
                for tool, v in by_tool.items():
                    writer.writerow([suite, workload, tool,
                                     f"{v['latency']:.3f}", f"{v['load']:.3f}",
                                     f"{v['latency'] - v['load']:.3f}", f"{v['memory']:.1f}",
                                     f"{v['latency'] / xbench['latency']:.2f}",
                                     f"{v['memory'] / xbench['memory']:.2f}"])
    print(f"wrote {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Plot the SOTA comparison (latency and memory).")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT,
                        help="directory containing results/ or traces/ "
                             "(default: data/revision/fig7-load-execution)")
    parser.add_argument("--traces", action="store_true",
                        help="read traces/*_trace.json instead of results/runs.*.json")
    parser.add_argument("--xbench-prefix", default=TRACE_PREFIX["X-Bench"],
                        help="file-name prefix of the X-Bench traces (default: xbench)")
    args = parser.parse_args()

    if args.traces:
        prefixes = {**TRACE_PREFIX, "X-Bench": args.xbench_prefix}
        results = load_results(args.data_root / "traces", prefixes)
    else:
        results = load_results_from_runs(args.data_root / "results")

    out = args.data_root / "plots"
    out.mkdir(parents=True, exist_ok=True)

    for suite in SUITE_WORKLOADS:
        canvas = plot_latency(results, suite, LATENCY_TOP[suite],
                              out / f"latency_{suite}_total{FIG_SUFFIX}.pdf")
        plot_latency_split(results, suite, canvas, out / f"latency_{suite}{FIG_SUFFIX}.pdf")
        plot_memory(results, suite, MEMORY_TOP_GB[suite], out / f"memory_{suite}{FIG_SUFFIX}.pdf")
    plot_legend(out / f"legend{FIG_SUFFIX}.pdf")
    write_summary(results, out / "summary.csv")


if __name__ == "__main__":
    main()
