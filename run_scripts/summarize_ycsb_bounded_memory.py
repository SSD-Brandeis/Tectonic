#!/usr/bin/env python3
"""
Tables for R2.O2(iii) (YCSB memory under a bounded JVM heap), computed from results/*.json.

Writes data/revision/R2.O2_ycsb-bounded-memory/results/tables.md. Every number in the package's
report.md and README.md is copied from that file. Means over trials; "sd" is the sample standard
deviation (n - 1).
"""

import json
import statistics
from pathlib import Path

EXP_DIR = Path("/home/cc/Tectonic/data/revision/R2.O2_ycsb-bounded-memory")
RESULTS_DIR = EXP_DIR / "results"
WORKLOADS = ["a", "b", "c", "d", "e", "f"]
HEAPS = ["6m", "8m", "16m", "32m", "64m", "128m", "256m", "512m", "1024m", "default"]
COMPARED = ["6m", "8m", "16m", "32m", "default"]  # T10: YCSB settings set against X-Bench
PHASES = ["load", "run"]


def load(name):
    with open(RESULTS_DIR / name) as f:
        return json.load(f)


def mean(vals):
    return sum(vals) / len(vals)


def sd(vals):
    return statistics.stdev(vals) if len(vals) > 1 else 0.0


def cell(ycsb, w, heap, phase, key):
    return mean([t[key] for t in ycsb[w][heap][f"{phase}_trials"]])


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def pct(new, old):
    return f"{(new - old) / old * 100:+.1f}%"


def pipeline(ycsb, w, heap):
    """YCSB's two JVMs (load, then run) per trial: peak = the larger peak, time = the sum. Means."""
    pairs = list(zip(ycsb[w][heap]["load_trials"], ycsb[w][heap]["run_trials"]))
    assert all(l["trial"] == r["trial"] for l, r in pairs)
    return (mean([max(l["peak_rss_mb"], r["peak_rss_mb"]) for l, r in pairs]),
            mean([l["duration_s"] + r["duration_s"] for l, r in pairs]))


def new_tables(ycsb, xbench_old, breakdown):
    """T10-T13 (2026-10-27): X-Bench vs. YCSB with a capped heap, X-Bench's memory split, heap floor."""
    md = []
    xb_path, floor_path = RESULTS_DIR / "xbench_breakdown.json", RESULTS_DIR / "ycsb_heap_floor.json"
    if not xb_path.exists():
        return md
    xb = load(xb_path.name)
    xbm = {w: (mean([t["peak_rss_mb"] for t in xb[w]]), mean([t["duration_s"] for t in xb[w]])) for w in WORKLOADS}

    for idx, what, unit in [(0, "peak memory", "MB"), (1, "generation time (load + run)", "s")]:
        md += [f"## T10-{'memory' if idx == 0 else 'time'}: {what} ({unit}), X-Bench vs. YCSB per heap setting", "",
               "X-Bench: `xbench_breakdown.json` (current binary, 5 trials). YCSB: per trial, the larger of the "
               "load and run peaks (memory) or their sum (time), then the mean of 5 trials. Ratio = YCSB / X-Bench.", ""]
        f = (lambda v: f"{v:.1f}") if idx == 0 else (lambda v: f"{v:.2f}")
        rows, ratios = [], {h: [] for h in COMPARED}
        for w in WORKLOADS:
            row = [w.upper(), f(xbm[w][idx])]
            for h in COMPARED:
                v = pipeline(ycsb, w, h)[idx]
                ratios[h].append(v / xbm[w][idx])
                row += [f(v), f"{v / xbm[w][idx]:.2f}x"]
            rows.append(row)
        rows.append(["**mean A-F**", f"**{f(mean([xbm[w][idx] for w in WORKLOADS]))}**"]
                    + sum([[f"**{f(mean([pipeline(ycsb, w, h)[idx] for w in WORKLOADS]))}**",
                            f"{min(ratios[h]):.2f}-{max(ratios[h]):.2f}x"] for h in COMPARED], []))
        md += [table(["Workload", "X-Bench"] + sum([[f"YCSB {h}", "ratio"] for h in COMPARED], []), rows), ""]

    md += [f"## T11: X-Bench resident memory at peak RSS (`xbench_breakdown.json`, binary built {xb['binary_mtime']} UTC, "
           f"HEAD {xb['git_head']}), 5 trials each", "",
           "File-backed = the `tectonic-cli` binary, libc, libstdc++, ...; allocated = all anonymous memory "
           "(X-Bench's data, buffers, stacks). Last column: the 2026-10-25 runs of T7 (older binary).", ""]
    rows = []
    for w in WORKLOADS:
        tr = xb[w]
        g = lambda k: mean([t["resident_at_peak"][k] for t in tr])
        rows.append([w.upper(), f"{xbm[w][0]:.1f}", f"{g('file_backed_mb'):.1f}", f"{g('anon_mb'):.1f}",
                     f"{g('total_mb'):.1f}", f"{xbm[w][1]:.2f}", f"{mean([t['output_size_mb'] for t in tr]):.0f}",
                     ",".join(str(t["exit_code"]) for t in tr), f"{xbench_old[w]['mean_rss_mb']:.1f} / {xbench_old[w]['mean_duration_s']:.2f}"])
    md += [table(["Workload", "Peak RSS", "file-backed", "allocated", "smaps total", "duration (s)", "output (MB)",
                  "exit codes", "T7 RSS / duration"], rows), ""]

    if breakdown:
        md += ["## T12: what the peak is made of (Fig. `memory_breakdown`), mean of A-F x 5 trials", "",
               "YCSB: the phase with the larger mean peak at that setting (`ycsb_heap_breakdown.json`). Java heap in "
               "use = max live heap after a GC (all of the resident heap for a run without GC); headroom = the rest of "
               "the resident Java heap. JVM internals = anonymous memory outside the Java heap.", ""]
        xb_all = [t for w in WORKLOADS for t in xb[w]]
        rows = [["X-Bench", "-", f"{mean([t['peak_rss_mb'] for t in xb_all]):.1f}",
                 f"{mean([t['resident_at_peak']['file_backed_mb'] for t in xb_all]):.1f}", "-",
                 f"{mean([t['resident_at_peak']['anon_mb'] for t in xb_all]):.1f}", "-", "-", "-"]]
        for h in ["16m", "32m", "default"]:
            best = max(PHASES, key=lambda p: mean([t["peak_rss_mb"] for w in WORKLOADS for t in breakdown[w][h][p]]))
            tr = [t for w in WORKLOADS for t in breakdown[w][h][best]]
            res = [t["resident_at_peak"]["java_heap_mb"] for t in tr]
            live = [min(t["live_after_gc_max_mb"] if t["live_after_gc_max_mb"] is not None else r, r) for t, r in zip(tr, res)]
            peaks = [t["peak_rss_mb"] for t in tr]
            rows.append([f"YCSB {h}", best, f"{mean(peaks):.1f}",
                         f"{mean([t['resident_at_peak']['file_backed_mb'] for t in tr]):.1f}",
                         f"{mean([t['resident_at_peak']['other_anon_mb'] for t in tr]):.1f}", "-",
                         f"{mean(live):.1f}", f"{mean(res) - mean(live):.1f}", f"{min(peaks):,.0f}-{max(peaks):,.0f}"])
        md += [table(["Setting", "phase", "peak RSS", "code and libraries (file-backed)", "JVM internals",
                      "X-Bench allocated", "Java heap in use", "Java heap headroom", "single runs (MB)"], rows), ""]

    if floor_path.exists():
        floor = load(floor_path.name)
        md += ["## T13: heap floor, workload A load phase (`ycsb_heap_floor.json` for 4m-12m; main sweep for 16m, 32m, "
               "default), 5 trials each", ""]
        rows = []
        for h in list(floor) + ["16m", "32m", "default"]:
            tr = floor[h] if h in floor else ycsb["a"][h]["load_trials"]
            ok = [t for t in tr if t.get("exit_code", 0) == 0]
            oom = sum(1 for t in tr if t.get("out_of_memory"))
            rows.append([h, f"{len(ok)}/{len(tr)}" + (f" ({oom} OutOfMemoryError)" if oom else ""),
                         f"{mean([t['duration_s'] for t in ok]):.2f} (sd {sd([t['duration_s'] for t in ok]):.2f})" if ok else "-",
                         f"{mean([t['gc_count'] for t in ok]):,.0f}" if ok else "-",
                         f"{mean([t['gc_time_pct'] for t in ok]):.1f}%" if ok else "-",
                         f"{mean([t['peak_rss_mb'] for t in ok]):.1f}" if ok else "-"])
        md += [table(["-Xmx", "completed", "duration (s)", "GC count", "GC time", "peak RSS (MB)"], rows), ""]
    return md


def main():
    ycsb = load("ycsb_bounded_memory.json")
    xbench = load("xbench_baseline.json")
    base = load("jvm_base_footprint.json")
    threshold = load("ycsb_threshold.json")
    breakdown_path = RESULTS_DIR / "ycsb_heap_breakdown.json"
    breakdown = load(breakdown_path.name) if breakdown_path.exists() else None
    if breakdown and not (set(breakdown) == set(WORKLOADS) and all(
            len(cells.get(p, [])) == 5 for w in WORKLOADS for cells in breakdown[w].values() for p in PHASES)):
        breakdown = None  # still running

    md = ["# R2.O2(iii) tables", "",
          "Generated by `run_scripts/summarize_ycsb_bounded_memory.py` from `results/*.json`. "
          "Means over trials; sd = sample standard deviation. RSS in MB (MiB), durations in s.", ""]

    # T1: peak RSS per workload and heap, per phase
    for phase in PHASES:
        md += [f"## T1-{phase}: YCSB {phase} phase, peak RSS (MB), mean of 5 trials", ""]
        rows = []
        for w in WORKLOADS:
            rows.append([w.upper()] + [f"{cell(ycsb, w, h, phase, 'peak_rss_mb'):.1f}" for h in HEAPS])
        rows.append(["**mean A-F**"] + [f"**{mean([cell(ycsb, w, h, phase, 'peak_rss_mb') for w in WORKLOADS]):.1f}**"
                                         for h in HEAPS])
        md += [table(["Workload"] + HEAPS, rows), ""]

    # T2: duration per workload and heap, per phase, with change vs. default
    for phase in PHASES:
        md += [f"## T2-{phase}: YCSB {phase} phase, wall-clock duration (s), mean of 5 trials", ""]
        rows = []
        for w in WORKLOADS + ["mean"]:
            ws = WORKLOADS if w == "mean" else [w]
            d = {h: mean([cell(ycsb, x, h, phase, "duration_s") for x in ws]) for h in HEAPS}
            name = "**mean A-F**" if w == "mean" else w.upper()
            rows.append([name] + [f"{d[h]:.2f}" for h in HEAPS]
                        + [pct(d["16m"], d["default"]), pct(d["32m"], d["default"])])
        md += [table(["Workload"] + HEAPS + ["16m vs. default", "32m vs. default"], rows), ""]

    # T3: GC (YCSB's own counters: count and % of runtime)
    for phase in PHASES:
        md += [f"## T3-{phase}: YCSB {phase} phase, GC count and GC time (% of runtime), mean of 5 trials", ""]
        gc_heaps = ["6m", "8m", "16m", "32m", "64m", "1024m", "default"]
        rows = []
        for w in WORKLOADS:
            rows.append([w.upper()] + [f"{cell(ycsb, w, h, phase, 'gc_count'):,.0f} / {cell(ycsb, w, h, phase, 'gc_time_pct'):.1f}%"
                                       for h in gc_heaps])
        md += [table(["Workload"] + gc_heaps, rows), ""]

    # T4: throughput (YCSB's [OVERALL] Throughput)
    md += ["## T4: YCSB throughput (ops/s, YCSB's own [OVERALL]), mean A-F of the 5-trial means", ""]
    rows = []
    for phase in PHASES:
        rows.append([phase] + [f"{mean([cell(ycsb, w, h, phase, 'throughput_ops_sec') for w in WORKLOADS]):,.0f}" for h in HEAPS])
    md += [table(["Phase"] + HEAPS, rows), ""]

    # T5: threshold runs (workload A load, one run each) next to the 5-trial cells
    md += ["## T5: workload A load phase below and at the knee", "",
           "4m, 6m, 8m: one run each (`ycsb_threshold.json`, `logs/threshold/`); 16m, 32m, default: mean of 5 trials.", ""]
    rows = []
    for h in ["4m", "6m", "8m"]:
        t = threshold[h]
        m = t["ycsb_metrics"]
        status = "failed (exit 1)" if t["exit_code"] else "completed"
        rows.append([h, status, f"{t['duration_s']:.2f}",
                     f"{m['throughput_ops_sec']:,.0f}" if m["throughput_ops_sec"] else "-",
                     f"{m['gc_count']:,}", f"{m['gc_time_ms']:,.0f}", f"{m['gc_time_pct']:.1f}%", f"{t['peak_rss_mb']:.1f}"])
    for h in ["16m", "32m", "default"]:
        rows.append([h, "completed (5/5)", f"{cell(ycsb, 'a', h, 'load', 'duration_s'):.2f}",
                     f"{cell(ycsb, 'a', h, 'load', 'throughput_ops_sec'):,.0f}",
                     f"{cell(ycsb, 'a', h, 'load', 'gc_count'):,.0f}", f"{cell(ycsb, 'a', h, 'load', 'gc_time_ms'):,.0f}",
                     f"{cell(ycsb, 'a', h, 'load', 'gc_time_pct'):.1f}%", f"{cell(ycsb, 'a', h, 'load', 'peak_rss_mb'):.1f}"])
    md += [table(["-Xmx", "Status", "Duration (s)", "Throughput (ops/s)", "GCs", "GC time (ms)", "GC time %", "Peak RSS (MB)"], rows), ""]

    # T6: JVM without a workload, and X-Bench
    md += ["## T6: JVM without a workload (5 trials each)", ""]
    rows = []
    for name, d in base.items():
        vals = [t["peak_rss_mb"] for t in d["trials"]]
        rows.append([f"`{name}`", f"{mean(vals):.1f} (sd {sd(vals):.2f})",
                     f"{mean([t['duration_s'] for t in d['trials']]):.3f}"])
    md += [table(["Command", "Peak RSS (MB)", "Duration (s)"], rows), ""]

    md += ["## T7: X-Bench (`tectonic-cli generate`, one process: load + run, 2M operations), 5 trials each", ""]
    rows = []
    for w in WORKLOADS:
        tr = xbench[w]["trials"]
        rss = [t["peak_rss_mb"] for t in tr]
        dur = [t["duration_s"] for t in tr]
        rows.append([w.upper(), f"{mean(rss):.1f} (sd {sd(rss):.2f})", f"{mean(dur):.2f} (sd {sd(dur):.2f})",
                     f"{mean([t['output_size_mb'] for t in tr]):.0f}", ",".join(str(t["exit_code"]) for t in tr)])
    all_rss = [t["peak_rss_mb"] for w in WORKLOADS for t in xbench[w]["trials"]]
    all_dur = [t["duration_s"] for w in WORKLOADS for t in xbench[w]["trials"]]
    rows.append(["**mean A-F**", f"**{mean(all_rss):.1f}**", f"**{mean(all_dur):.2f}**", "", ""])
    md += [table(["Workload", "Peak RSS (MB)", "Duration (s)", "Output (MB)", "Exit codes"], rows), ""]

    # Comparable durations: YCSB load + run (two JVMs) vs. X-Bench (one process, both phases)
    md += ["## T8: generation time for the same work (load + run phases)", "",
           "YCSB: load-phase JVM + run-phase JVM, sum of their means. X-Bench: one process generating both phases.", ""]
    rows = []
    for w in WORKLOADS:
        y = {h: cell(ycsb, w, h, "load", "duration_s") + cell(ycsb, w, h, "run", "duration_s") for h in ["16m", "32m", "default"]}
        rows.append([w.upper(), f"{y['default']:.2f}", f"{y['32m']:.2f}", f"{y['16m']:.2f}", f"{xbench[w]['mean_duration_s']:.2f}"])
    md += [table(["Workload", "YCSB default", "YCSB 32m", "YCSB 16m", "X-Bench"], rows), ""]

    if breakdown:
        bh = list(breakdown["a"].keys())
        md += ["## T9: headroom vs. working set, measured (`ycsb_heap_breakdown.json`, mean of 5 trials, mean A-F)", "",
               "Separate runs with `-Xlog:gc,gc+heap+exit`. Resident memory at peak RSS from `/proc/<pid>/smaps`: "
               "Java heap = the mappings in the heap's reserved range (from the GC exit log); file-backed = "
               "libjvm.so, the CDS archive (classes.jsa), libc, ...; other anonymous = metaspace, code cache, GC "
               "data structures, malloc, thread stacks. Live heap after GC = heap occupancy right after a collection "
               "(max over the run).", ""]

        def bmean(w_list, heap, phase, f):
            return mean([f(t) for w in w_list for t in breakdown[w][heap][phase]])

        for phase in PHASES:
            rows = []
            for h in bh:
                g = lambda f: bmean(WORKLOADS, h, phase, f)
                live = [t["live_after_gc_max_mb"] for w in WORKLOADS for t in breakdown[w][h][phase] if t["live_after_gc_max_mb"] is not None]
                n_nogc = sum(1 for w in WORKLOADS for t in breakdown[w][h][phase] if t["live_after_gc_max_mb"] is None)
                rows.append([h,
                             f"{g(lambda t: t['peak_rss_mb']):.1f}",
                             f"{g(lambda t: t['resident_at_peak']['java_heap_mb']):.1f}",
                             f"{g(lambda t: t['resident_at_peak']['file_backed_mb']):.1f}",
                             f"{g(lambda t: t['resident_at_peak']['other_anon_mb']):.1f}",
                             f"{g(lambda t: t['heap_used_max_mb']):.0f}",
                             (f"{mean(live):.1f}" if live else "-") + (f" ({n_nogc} runs without GC)" if n_nogc else ""),
                             f"{g(lambda t: t['gc_pauses']):,.0f}",
                             f"{g(lambda t: t['ru_minflt']):,.0f}",
                             f"{g(lambda t: t['duration_s']):.2f}"])
            md += [f"### T9-{phase}", "",
                   table(["-Xmx", "Peak RSS", "resident Java heap", "file-backed", "other anonymous",
                          "max heap used", "max live heap after GC", "GC pauses", "minor page faults", "duration (s)"], rows), ""]

        md += ["### T9-per-workload: resident Java heap / max live heap after GC (MB) at default and 16m", ""]
        rows = []
        for w in WORKLOADS:
            row = [w.upper()]
            for phase in PHASES:
                for h in ["default", "16m"]:
                    tr = breakdown[w][h][phase]
                    live = [t["live_after_gc_max_mb"] for t in tr if t["live_after_gc_max_mb"] is not None]
                    row.append(f"{mean([t['resident_at_peak']['java_heap_mb'] for t in tr]):.0f} / "
                               + (f"{mean(live):.0f}" if live else "no GC"))
            rows.append(row)
        md += [table(["Workload", "load default", "load 16m", "run default", "run 16m"], rows), ""]

        md += ["### T9-check: peak RSS of the instrumented runs vs. the main sweep (mean A-F)", ""]
        rows = []
        for phase in PHASES:
            for h in bh:
                a = bmean(WORKLOADS, h, phase, lambda t: t["peak_rss_mb"])
                b = mean([cell(ycsb, w, h, phase, "peak_rss_mb") for w in WORKLOADS])
                s = bmean(WORKLOADS, h, phase, lambda t: t["resident_at_peak"]["total_mb"])
                rows.append([phase, h, f"{b:.1f}", f"{a:.1f}", f"{s:.1f}"])
        md += [table(["Phase", "-Xmx", "main sweep", "instrumented runs", "smaps snapshot total"], rows), ""]

    md += new_tables(ycsb, xbench, breakdown)
    (RESULTS_DIR / "tables.md").write_text("\n".join(md))
    print(f"Wrote {RESULTS_DIR / 'tables.md'}")


if __name__ == "__main__":
    main()
