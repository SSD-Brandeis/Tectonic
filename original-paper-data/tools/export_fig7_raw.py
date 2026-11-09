#!/usr/bin/env python3
"""Export the data plotted in Fig. 7A-D as one tidy CSV per panel, read directly from the trace JSONs.

Source: x_bench_data1/figure_6_validate/traces/*.json (the files plot_scripts/plot_figure_6_validate.py reads;
rerun_backup/ is not used). Every row is one plotted value; `source` names the JSON field it was read from.

  fig7a_generation_time.csv   workload, tool, phase, seconds
  fig7b_peak_memory.csv       workload, tool, peak_rss_mb, peak_at_s, peak_phase
  fig7c_operation_time.csv    workload, tool, operation, seconds
  fig7d_peak_memory.csv       workload, tool, peak_rss_mb, peak_at_s, peak_phase
  fig7_memlog_raw.csv         every memory sample; each peak in 7B/7D is the max rss_mb of its run
  sha256sums.txt              hashes of the trace files (check with: sha256sum -c sha256sums.txt)

Every value is checked against the archived figure_6_validate_*.csv files; any mismatch aborts.
Usage: python3 -I tools/export_fig7_raw.py [out_dir]   (default: reproduced/fig7/raw_csv)
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "x_bench_data1" / "figure_6_validate"
TRACES = DATA / "traces"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "reproduced" / "fig7" / "raw_csv"
PREFIX = {"YCSB": "ycsb", "KVBench": "kvbench", "X-Bench": "tectonic"}
OPS = ["Insert", "Point Query", "Update", "Point Delete", "Range Query", "Range Delete"]  # plot stacking order
YCSB_WL = ["A", "B", "C", "D", "E", "F"]
KV_WL = ["I", "II", "III", "IV", "V"]


def trace_path(tool, wl):
    return TRACES / f"{PREFIX[tool]}_{wl.lower()}_trace.json"


def load(tool, wl):
    p = trace_path(tool, wl)
    return (json.loads(p.read_text()), p.name) if p.exists() else (None, None)


def write(name, rows):
    with (OUT / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def peak(tool, d):
    """(peak MB, time of peak in s, phase) over all memory samples of the run."""
    runs = [("load", d["load"]["mem_log"]), ("exec", d["run"]["mem_log"])] if tool == "YCSB" else [("whole_run", d["mem_log"])]
    return max(((mb, t, ph) for ph, log in runs for t, mb in log), key=lambda s: s[0])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in ["fig7_timings_raw.csv", "fig7_reductions.csv"]:  # earlier layout
        (OUT / old).unlink(missing_ok=True)

    # Fig. 7A: YCSB A-F, loading + execution phase time per tool
    a = []
    for wl in YCSB_WL:
        for tool in ["YCSB", "KVBench", "X-Bench"]:
            d, f = load(tool, wl)
            if d is None:
                continue
            if tool == "YCSB":
                vals = [("load", d["load"]["total_duration"], "load.total_duration"),
                        ("exec", d["run"]["total_duration"], "run.total_duration")]
            elif tool == "KVBench":
                vals = [("load", d["loading_phase_end_time"], "loading_phase_end_time"),
                        ("exec", d["total_duration"] - d["loading_phase_end_time"],
                         "total_duration - loading_phase_end_time")]
            else:
                e = d["empirical_op_durations"]
                vals = [("load", e["Insert"], "empirical_op_durations.Insert"),
                        ("exec", sum(v for k, v in e.items() if k != "Insert"),
                         "sum of empirical_op_durations except Insert")]
            for phase, s, src in vals:
                a.append({"workload": wl, "tool": tool, "phase": phase, "seconds": s, "trace_file": f, "source": src})

    # Fig. 7B / 7D: peak RSS
    def mem_rows(wls, tools):
        rows = []
        for wl in wls:
            for tool in tools:
                d, f = load(tool, wl)
                if d is None:
                    continue
                mb, t, ph = peak(tool, d)
                rows.append({"workload": wl, "tool": tool, "peak_rss_mb": mb, "peak_at_s": t, "peak_phase": ph,
                             "trace_file": f, "source": "max of mem_log rss (load.mem_log + run.mem_log)"
                             if tool == "YCSB" else "max of mem_log rss"})
        return rows

    b = mem_rows(YCSB_WL, ["YCSB", "KVBench", "X-Bench"])
    dd = mem_rows(KV_WL, ["KVBench", "X-Bench"])

    # Fig. 7C: KVBench I-V, stacked time per operation type
    c = []
    for wl in KV_WL:
        for tool in ["KVBench", "X-Bench"]:
            d, f = load(tool, wl)
            for op in OPS:
                c.append({"workload": wl, "tool": tool, "operation": op, "seconds": d["empirical_op_durations"][op],
                          "trace_file": f, "source": f"empirical_op_durations.{op}"})

    # every memory sample
    log = []
    for wl in YCSB_WL + KV_WL:
        for tool in ["YCSB", "KVBench", "X-Bench"]:
            d, _ = load(tool, wl)
            if d is None:
                continue
            runs = [("load", d["load"]["mem_log"]), ("exec", d["run"]["mem_log"])] if tool == "YCSB" else [("whole_run", d["mem_log"])]
            for ph, samples in runs:
                log += [{"workload": wl, "tool": tool, "phase": ph, "t_s": t, "rss_mb": mb} for t, mb in samples]

    # check against the archived CSVs the plot script wrote
    checks = 0
    def same(x, y, what):
        nonlocal checks
        checks += 1
        if abs(float(x) - float(y)) > 1e-9:
            sys.exit(f"MISMATCH {what}: {x} vs {y}")
    phase = {(r["workload"], r["tool"], r["phase"]): r["seconds"] for r in a}
    for r in csv.DictReader((DATA / "figure_6_validate_phase_breakdown.csv").open()):
        same(r["LoadPhase_Latency_s"], phase[(r["Workload"], r["Tool"], "load")], f"7A load {r['Workload']} {r['Tool']}")
        same(r["ExecutionPhase_Latency_s"], phase[(r["Workload"], r["Tool"], "exec")], f"7A exec {r['Workload']} {r['Tool']}")
    mem = {(r["workload"], r["tool"]): r["peak_rss_mb"] for r in b + dd}
    for r in csv.DictReader((DATA / "figure_6_validate_memory.csv").open()):
        for tool in PREFIX:
            if r[f"{tool}_PeakMem_MB"]:
                same(r[f"{tool}_PeakMem_MB"], mem[(r["Workload"], tool)], f"7B/D {r['Workload']} {tool}")
    ops = {(r["workload"], r["tool"], r["operation"]): r["seconds"] for r in c}
    for r in csv.DictReader((DATA / "figure_6_validate_op_breakdown.csv").open()):
        for op in OPS:
            same(r[op.replace(" ", "_") + "_Latency_s"], ops[(r["Workload"], r["Tool"], op)], f"7C {r['Workload']} {r['Tool']} {op}")
    for (wl, tool), mb in mem.items():
        same(mb, max(s["rss_mb"] for s in log if s["workload"] == wl and s["tool"] == tool), f"memlog {wl} {tool}")

    write("fig7a_generation_time.csv", a)
    write("fig7b_peak_memory.csv", b)
    write("fig7c_operation_time.csv", c)
    write("fig7d_peak_memory.csv", dd)
    write("fig7_memlog_raw.csv", log)
    with (OUT / "sha256sums.txt").open("w") as f:
        for p in sorted(TRACES.glob("*.json")):
            f.write(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(OUT, walk_up=True)}\n")
    print(f"7A {len(a)} rows, 7B {len(b)}, 7C {len(c)}, 7D {len(dd)}, memlog {len(log)}; "
          f"{checks} values checked against archived CSVs, 0 mismatches -> {OUT}")


if __name__ == "__main__":
    main()
