#!/usr/bin/env python3
"""
Regenerate figure_6_validate plots from empirical trace JSON.

Outputs:
  /home/cc/Tectonic/generator_experiment_plots/fig1.pdf
  /home/cc/Tectonic/generator_experiment_plots/fig1_mem.pdf
  /home/cc/Tectonic/generator_experiment_plots/fig3.pdf
  /home/cc/Tectonic/generator_experiment_plots/fig3_mem.pdf
"""

import csv
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.patches import Patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_style


ROOT = Path("/home/cc/Tectonic")
OUT_DIR = ROOT / "data" / "figure_6_validate"
SOURCE_DIR = OUT_DIR / "traces"
PLOTS_DIR = ROOT / "figure_6_validate_plot"

OUT_DIR.mkdir(parents=True, exist_ok=True)
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

ALL_OPS = ["Insert", "Point Query", "Update", "Point Delete", "Range Query", "Range Delete"]
KV_WORKLOADS = ["I", "II", "III", "IV", "V"]
YCSB_WORKLOADS = ["A", "B", "C", "D", "E", "F"]
KVBENCH_YCSB_WORKLOADS = ["A", "B", "C", "D", "E"]

OP_COLORS = {
    "Insert": "#9467bd",
    "Point Query": "#ff7f0e",
    "Update": "#2ca02c",
    "Point Delete": "#e377c2",
    "Range Query": "#1f77b4",
    "Range Delete": "#8c564b",
}

OP_LABELS = {
    "Point Query": "PQ",
    "Point Delete": "PD",
    "Range Delete": "RD",
}

HATCHES = {
    "X-Bench": "",
    "YCSB": "/////",
    "KVBench": "\\\\",
}

LATENCY_FIGURE_SIZE = (5, 3.6)
LEGEND_HANDLE_KWARGS = {"handlelength": 1.0, "handleheight": 0.35}


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def safe_unlink(path: Path) -> None:
    if path.exists() or path.is_symlink():
        path.unlink()


def save_pdf(fig, path: Path) -> None:
    safe_unlink(path)
    fig.savefig(path, bbox_inches="tight")


def save_legend(ax, path_without_ext: Path) -> None:
    legend = ax.get_legend()
    if not legend:
        return

    handles = getattr(legend, "legend_handles", None) or getattr(legend, "legendHandles", [])
    labels = [text.get_text() for text in legend.get_texts()]
    legend.remove()

    ncol = min(4, max(1, len(handles)))
    fig_leg = plt.figure(figsize=(ncol * 2.5, 1.0))
    legend = fig_leg.legend(handles, labels, loc="center", frameon=False, ncol=ncol, **LEGEND_HANDLE_KWARGS)
    fig_leg.canvas.draw()
    bbox = legend.get_window_extent().transformed(fig_leg.dpi_scale_trans.inverted())
    fig_leg.set_size_inches(bbox.width + 0.4, bbox.height + 0.4)

    pdf_path = path_without_ext.with_name(path_without_ext.name + "_legend.pdf")
    png_path = path_without_ext.with_name(path_without_ext.name + "_legend.png")
    safe_unlink(pdf_path)
    safe_unlink(png_path)
    fig_leg.savefig(pdf_path, bbox_inches="tight")
    fig_leg.savefig(png_path, bbox_inches="tight", dpi=300)
    plt.close(fig_leg)


def peak_mem(trace: dict | None) -> float:
    if not trace:
        return 0.0
    return max((float(row[1]) for row in trace.get("mem_log", [])), default=0.0)


def ycsb_peak_mem(trace: dict | None) -> float:
    if not trace:
        return 0.0
    peaks = []
    for phase in ["load", "run"]:
        peaks.append(max((float(row[1]) for row in trace.get(phase, {}).get("mem_log", [])), default=0.0))
    return max(peaks, default=0.0)


def op_durations(trace: dict | None) -> dict[str, float]:
    values = {op: 0.0 for op in ALL_OPS}
    if not trace:
        return values
    raw = trace.get("empirical_op_durations") or trace.get("op_durations") or {}
    for op in ALL_OPS:
        values[op] = float(raw.get(op, 0.0) or 0.0)
    return values


def has_empirical_op_durations(trace: dict | None) -> bool:
    if not trace:
        return False
    raw = trace.get("empirical_op_durations") or trace.get("op_durations") or {}
    return bool(raw) and any(float(raw.get(op, 0.0) or 0.0) > 0.0 for op in ALL_OPS)


def require_trace(path: Path, label: str) -> dict:
    trace = load_json(path)
    if trace is None:
        raise FileNotFoundError(f"missing required fresh trace for {label}: {path}")
    return trace


def ycsb_phase(trace: dict | None) -> tuple[float, float]:
    if not trace:
        return 0.0, 0.0
    return float(trace["load"]["total_duration"]), float(trace["run"]["total_duration"])


def generator_phase(trace: dict | None) -> tuple[float, float]:
    if not trace:
        return 0.0, 0.0
    total = float(trace.get("total_duration", 0.0) or 0.0)
    load_end = trace.get("loading_phase_end_time")
    if load_end is None:
        return 0.0, total
    load = min(float(load_end), total)
    return load, max(total - load, 0.0)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_empirical_csvs() -> tuple[list[dict], list[dict], list[dict]]:
    data_quality = {
        "source_trace_dir": str(SOURCE_DIR),
        "forbidden_source_dir": str(ROOT / "data" / "generator_comparison"),
        "status": "pass",
        "issues": [],
    }

    op_rows = []
    for workload in KV_WORKLOADS:
        for tool, prefix in [("X-Bench", "tectonic"), ("KVBench", "kvbench")]:
            trace_path = SOURCE_DIR / f"{prefix}_{workload.lower()}_trace.json"
            trace = require_trace(trace_path, f"{tool} workload {workload}")
            if not has_empirical_op_durations(trace):
                data_quality["issues"].append(f"{trace_path} lacks empirical operation timings")
            durations = op_durations(trace)
            row = {
                "Workload": workload,
                "Tool": tool,
                "Total_Latency_s": sum(durations.values()),
            }
            for op in ALL_OPS:
                row[op.replace(" ", "_") + "_Latency_s"] = durations[op]
            op_rows.append(row)

    phase_rows = []
    for workload in YCSB_WORKLOADS:
        ycsb_trace = require_trace(SOURCE_DIR / f"ycsb_{workload.lower()}_trace.json", f"YCSB workload {workload}")
        load_s, exec_s = ycsb_phase(ycsb_trace)
        phase_rows.append(
            {
                "Workload": workload,
                "Tool": "YCSB",
                "LoadPhase_Latency_s": load_s,
                "ExecutionPhase_Latency_s": exec_s,
                "Total_Latency_s": load_s + exec_s,
            }
        )

        if workload in KVBENCH_YCSB_WORKLOADS:
            kv_trace = require_trace(SOURCE_DIR / f"kvbench_{workload.lower()}_trace.json", f"KVBench YCSB-shaped workload {workload}")
            if kv_trace.get("loading_phase_end_time") is None:
                data_quality["issues"].append(f"kvbench_{workload.lower()}_trace.json lacks KVbench_LoadPhase_End")
            load_s, exec_s = generator_phase(kv_trace)
            phase_rows.append(
                {
                    "Workload": workload,
                    "Tool": "KVBench",
                    "LoadPhase_Latency_s": load_s,
                    "ExecutionPhase_Latency_s": exec_s,
                    "Total_Latency_s": load_s + exec_s,
                }
            )

        tec_trace = require_trace(SOURCE_DIR / f"tectonic_{workload.lower()}_trace.json", f"X-Bench YCSB-shaped workload {workload}")
        if not has_empirical_op_durations(tec_trace):
            data_quality["issues"].append(f"tectonic_{workload.lower()}_trace.json lacks empirical operation timings")
        durations = op_durations(tec_trace)
        load_s = durations["Insert"]
        exec_s = sum(v for op, v in durations.items() if op != "Insert")
        phase_rows.append(
            {
                "Workload": workload,
                "Tool": "X-Bench",
                "LoadPhase_Latency_s": load_s,
                "ExecutionPhase_Latency_s": exec_s,
                "Total_Latency_s": load_s + exec_s,
            }
        )

    mem_rows = []
    for workload in KV_WORKLOADS:
        mem_rows.append(
            {
                "WorkloadSet": "KVBench",
                "Workload": workload,
                "X-Bench_PeakMem_MB": peak_mem(require_trace(SOURCE_DIR / f"tectonic_{workload.lower()}_trace.json", f"X-Bench workload {workload} memory")),
                "KVBench_PeakMem_MB": peak_mem(require_trace(SOURCE_DIR / f"kvbench_{workload.lower()}_trace.json", f"KVBench workload {workload} memory")),
                "YCSB_PeakMem_MB": "",
            }
        )
    for workload in YCSB_WORKLOADS:
        mem_rows.append(
            {
                "WorkloadSet": "YCSB",
                "Workload": workload,
                "X-Bench_PeakMem_MB": peak_mem(require_trace(SOURCE_DIR / f"tectonic_{workload.lower()}_trace.json", f"X-Bench YCSB-shaped workload {workload} memory")),
                "KVBench_PeakMem_MB": peak_mem(require_trace(SOURCE_DIR / f"kvbench_{workload.lower()}_trace.json", f"KVBench YCSB-shaped workload {workload} memory")) if workload in KVBENCH_YCSB_WORKLOADS else "",
                "YCSB_PeakMem_MB": ycsb_peak_mem(require_trace(SOURCE_DIR / f"ycsb_{workload.lower()}_trace.json", f"YCSB workload {workload} memory")),
            }
        )

    op_fields = ["Workload", "Tool", "Total_Latency_s"] + [op.replace(" ", "_") + "_Latency_s" for op in ALL_OPS]
    write_csv(OUT_DIR / "figure_6_validate_op_breakdown.csv", op_fields, op_rows)
    write_csv(
        OUT_DIR / "figure_6_validate_phase_breakdown.csv",
        ["Workload", "Tool", "LoadPhase_Latency_s", "ExecutionPhase_Latency_s", "Total_Latency_s"],
        phase_rows,
    )
    write_csv(
        OUT_DIR / "figure_6_validate_memory.csv",
        ["WorkloadSet", "Workload", "X-Bench_PeakMem_MB", "KVBench_PeakMem_MB", "YCSB_PeakMem_MB"],
        mem_rows,
    )

    quality_path = OUT_DIR / "figure_6_validate_data_quality.json"
    if data_quality["issues"]:
        data_quality["status"] = "fail"
        quality_path.write_text(json.dumps(data_quality, indent=2), encoding="utf-8")
        raise RuntimeError("refusing to plot non-empirical data; see figure_6_validate_data_quality.json")

    quality_path.write_text(json.dumps(data_quality, indent=2), encoding="utf-8")
    return op_rows, phase_rows, mem_rows

def format_latency_axis(ax, max_value: float) -> None:
    ax.set_yscale("log")
    ax.set_ylim(bottom=1.0, top=max(max_value * 1.6, 10.0))
    ax.yaxis.set_major_locator(mticker.LogLocator(base=10.0))
    ax.yaxis.set_major_formatter(mticker.LogFormatterMathtext(base=10.0))
    ax.yaxis.set_minor_locator(mticker.NullLocator())
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
    ax.tick_params(colors="black", which="both", direction="out")


def format_memory_axis(ax, max_value: float) -> None:
    ax.set_ylim(bottom=0.0, top=max(max_value * 1.2, 2000.0))
    ax.set_yticks([0, 1000, 2000])
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: plot_style.format_number_clean(y)))
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
    ax.tick_params(colors="black", which="both", direction="out")


def row_for(rows: list[dict], workload: str, tool: str) -> dict | None:
    return next((row for row in rows if row["Workload"] == workload and row["Tool"] == tool), None)


def plot_fig1(op_rows: list[dict]) -> None:
    fig, ax = plt.subplots(figsize=LATENCY_FIGURE_SIZE)
    x = np.arange(len(KV_WORKLOADS))
    width = 0.35
    max_total = 0.0

    bottoms = {"KVBench": np.zeros(len(KV_WORKLOADS)), "X-Bench": np.zeros(len(KV_WORKLOADS))}
    offsets = {"KVBench": -width / 2, "X-Bench": width / 2}

    for op in ALL_OPS:
        for tool in ["KVBench", "X-Bench"]:
            vals = []
            for workload in KV_WORKLOADS:
                row = row_for(op_rows, workload, tool)
                vals.append(float(row[op.replace(" ", "_") + "_Latency_s"]) if row else 0.0)
            ax.bar(
                x + offsets[tool],
                vals,
                width,
                bottom=bottoms[tool],
                color=OP_COLORS[op],
                edgecolor="black",
                linewidth=0.5,
                hatch=HATCHES[tool],
            )
            bottoms[tool] += vals
            max_total = max(max_total, max(bottoms[tool], default=0.0))

    ax.set_ylabel(plot_style.format_label("end-to-end latency (s)"))
    ax.set_xticks(x)
    ax.set_xticklabels(KV_WORKLOADS)
    present_ops = [op for op in ALL_OPS if any(float(row[op.replace(" ", "_") + "_Latency_s"]) > 0 for row in op_rows)]
    handles = [Patch(facecolor=OP_COLORS[op], edgecolor="black", label=OP_LABELS.get(op, op.lower())) for op in present_ops]
    handles += [
        Patch(facecolor="white", edgecolor="black", hatch=HATCHES["KVBench"], label="KVBench"),
        Patch(facecolor="tab:red", edgecolor="black", hatch=HATCHES["X-Bench"], label="X-Bench"),
    ]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1, 1), **LEGEND_HANDLE_KWARGS)
    format_latency_axis(ax, max_total)
    save_legend(ax, PLOTS_DIR / "fig1")
    fig.tight_layout()
    save_pdf(fig, PLOTS_DIR / "fig1.pdf")
    plt.close(fig)


def plot_fig1_mem(mem_rows: list[dict]) -> None:
    fig, ax = plt.subplots(figsize=LATENCY_FIGURE_SIZE)
    rows = [row for row in mem_rows if row["WorkloadSet"] == "KVBench"]
    x = np.arange(len(KV_WORKLOADS))
    width = 0.35
    kv_vals = [float(next(row for row in rows if row["Workload"] == w)["KVBench_PeakMem_MB"]) for w in KV_WORKLOADS]
    xb_vals = [float(next(row for row in rows if row["Workload"] == w)["X-Bench_PeakMem_MB"]) for w in KV_WORKLOADS]

    ax.bar(x - width / 2, kv_vals, width, label="KVBench", facecolor="none", edgecolor="black", linewidth=0.5, hatch=HATCHES["KVBench"])
    ax.bar(x + width / 2, xb_vals, width, label="X-Bench", facecolor="none", edgecolor="black", linewidth=0.5, hatch=HATCHES["X-Bench"])
    ax.set_ylabel(plot_style.format_label("memory footprint (MB)"))
    ax.set_xticks(x)
    ax.set_xticklabels(KV_WORKLOADS)
    ax.legend(loc="upper left", bbox_to_anchor=(1, 1), **LEGEND_HANDLE_KWARGS)
    format_memory_axis(ax, max(kv_vals + xb_vals))
    save_legend(ax, PLOTS_DIR / "fig1_mem")
    fig.tight_layout()
    save_pdf(fig, PLOTS_DIR / "fig1_mem.pdf")
    plt.close(fig)


def plot_fig3(phase_rows: list[dict]) -> None:
    fig, ax = plt.subplots(figsize=LATENCY_FIGURE_SIZE)
    x = np.arange(len(YCSB_WORKLOADS))
    width = 0.25
    load_color = "tab:blue"
    exec_color = "tab:red"
    offsets = {"YCSB": -width, "KVBench": 0.0, "X-Bench": width}
    max_total = 0.0

    for idx, workload in enumerate(YCSB_WORKLOADS):
        for tool in ["YCSB", "KVBench", "X-Bench"]:
            row = row_for(phase_rows, workload, tool)
            if not row:
                continue
            load_s = float(row["LoadPhase_Latency_s"])
            exec_s = float(row["ExecutionPhase_Latency_s"])
            ax.bar(idx + offsets[tool], load_s, width, color=load_color, edgecolor="black", linewidth=0.5, hatch=HATCHES[tool])
            ax.bar(
                idx + offsets[tool],
                exec_s,
                width,
                bottom=load_s,
                color=exec_color,
                edgecolor="black",
                linewidth=0.5,
                hatch=HATCHES[tool],
            )
            max_total = max(max_total, load_s + exec_s)

    handles = [
        Patch(facecolor=load_color, edgecolor="black", label="loading phase"),
        Patch(facecolor=exec_color, edgecolor="black", label="execution phase"),
        Patch(facecolor="white", edgecolor="black", hatch=HATCHES["YCSB"], label="YCSB"),
        Patch(facecolor="white", edgecolor="black", hatch=HATCHES["KVBench"], label="KVBench"),
        Patch(facecolor="tab:red", edgecolor="black", hatch=HATCHES["X-Bench"], label="X-Bench"),
    ]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1, 1), **LEGEND_HANDLE_KWARGS)
    ax.set_ylabel(plot_style.format_label("end-to-end latency (s)"))
    ax.set_xticks(x)
    ax.set_xticklabels(YCSB_WORKLOADS)
    format_latency_axis(ax, max_total)
    save_legend(ax, PLOTS_DIR / "fig3")
    fig.tight_layout()
    save_pdf(fig, PLOTS_DIR / "fig3.pdf")
    plt.close(fig)


def plot_fig3_mem(mem_rows: list[dict]) -> None:
    fig, ax = plt.subplots(figsize=LATENCY_FIGURE_SIZE)
    rows = [row for row in mem_rows if row["WorkloadSet"] == "YCSB"]
    x = np.arange(len(YCSB_WORKLOADS))
    width = 0.25
    max_value = 0.0

    for idx, workload in enumerate(YCSB_WORKLOADS):
        row = next(row for row in rows if row["Workload"] == workload)
        y_val = float(row["YCSB_PeakMem_MB"] or 0.0)
        kv_val = float(row["KVBench_PeakMem_MB"] or 0.0)
        xb_val = float(row["X-Bench_PeakMem_MB"] or 0.0)
        ax.bar(idx - width, y_val, width, label="YCSB" if idx == 0 else None, facecolor="none", edgecolor="black", linewidth=0.5, hatch=HATCHES["YCSB"])
        if kv_val > 0.0:
            ax.bar(idx, kv_val, width, label="KVBench" if idx == 0 else None, facecolor="none", edgecolor="black", linewidth=0.5, hatch=HATCHES["KVBench"])
        ax.bar(idx + width, xb_val, width, label="X-Bench" if idx == 0 else None, facecolor="none", edgecolor="black", linewidth=0.5, hatch=HATCHES["X-Bench"])
        max_value = max(max_value, y_val, kv_val, xb_val)

    ax.set_ylabel(plot_style.format_label("memory footprint (MB)"))
    ax.set_xticks(x)
    ax.set_xticklabels(YCSB_WORKLOADS)
    ax.legend(loc="upper left", bbox_to_anchor=(1, 1), **LEGEND_HANDLE_KWARGS)
    format_memory_axis(ax, max_value)
    save_legend(ax, PLOTS_DIR / "fig3_mem")
    fig.tight_layout()
    save_pdf(fig, PLOTS_DIR / "fig3_mem.pdf")
    plt.close(fig)


def main() -> int:
    op_rows, phase_rows, mem_rows = export_empirical_csvs()
    plot_fig1(op_rows)
    plot_fig1_mem(mem_rows)
    plot_fig3(phase_rows)
    plot_fig3_mem(mem_rows)

    provenance = {
        "experiment": "figure_6_validate",
        "source_trace_dir": str(SOURCE_DIR),
        "data_dir": str(OUT_DIR),
        "plot_dir": str(PLOTS_DIR),
        "plots": ["fig1.pdf", "fig1_mem.pdf", "fig3.pdf", "fig3_mem.pdf"],
        "metric_definition": {
            "fig1": "stacked empirical operation timings from Tectonic_Op_Timings and KVbench_Op_Timings",
            "fig1_mem": "peak VmRSS sampled while each generator process was running",
            "fig3": "YCSB load/run wall time; KVBench load end from KVbench_LoadPhase_End; X-Bench phase split from empirical insert vs non-insert operation timings",
            "fig3_mem": "peak VmRSS sampled while each generator process was running",
        },
    }
    (OUT_DIR / "figure_6_validate_provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(f"wrote empirical CSVs to {OUT_DIR}")
    print(f"wrote plots to {PLOTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
