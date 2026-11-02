#!/usr/bin/env python3
"""
make_paper_tables_fixed_vs_adaptive.py
======================================
R1.O8 / R2.O2: the three fixed-vs-adaptive KeySet tables of the paper (decided with the authors, 2026-10-31),
recomputed from the raw result files and written to
data/revision/R1.O8-R2.O2_keyset-selection/paper_table_fixed_vs_adaptive.md:

  Table 1  Tec-1..7, fixed VecKeySet            (+ the KeySet Algorithm 1 selects)
  Table 2  KV-Bench I-V, fixed VecHashSetKeySet  (KV-Bench's design)  + KV-Bench's own generator
  Table 3  YCSB A-F, fixed EmptyKeySet           (YCSB's design)      + YCSB's own generator

The original generators' columns are measured as the corrected Fig. 7 (run_scripts/run_fig7_corrected.py: wall
clock, YCSB load + run; peak VmRSS; caches dropped; 5 trials) on the machine of the X-Bench columns, with the same
operation counts: Table 2 kvbench-generator-table2/ (fixedkeyset-2), Table 3 ycsb-generator-table3/ (revision-2).

Every cell is the mean of 5 trials (a row with fewer finished trials is marked provisional). Time: wall clock of
`tectonic-cli generate`, trace written included. Memory: peak RSS (GNU time, KiB / 1024, labelled MB). Means at
full precision, rounded half up at the end: time to 0.01 s, memory to 0.1 MB.

Usage: python3 run_scripts/make_paper_tables_fixed_vs_adaptive.py
"""
import json
from decimal import Decimal, ROUND_HALF_UP
from statistics import mean

EXP = "/home/cc/Tectonic/data/revision/R1.O8-R2.O2_keyset-selection"
OUT = f"{EXP}/paper_table_fixed_vs_adaptive.md"
TRIALS = 5


def runs(path):
    with open(f"{EXP}/{path}") as f:
        return json.load(f)


TABLE4 = "table4/results/runs.json"
MM = "table4-fixed-to-completion-multimachine/results"
# (row label, source file, workload name in the file, trials used, original generator: (file, job, KV-Bench scale)
# or None)
TABLE1 = [
    ("Tec-1", f"{MM}/runs.revision-2.json", "Tec-1", range(1, 6), None),
    ("Tec-2", TABLE4, "Tec-2", range(1, 6), None),
    ("Tec-3", f"{MM}/runs.fixedkeyset-2.json", "Tec-3-0.1x", range(31, 36), None),
    ("Tec-4", TABLE4, "Tec-4", range(1, 6), None),
    ("Tec-5", f"{MM}/runs.fixedkeyset-2.json", "Tec-5-0.1x", range(31, 36), None),
    ("Tec-6", f"{MM}/runs.fixedkeyset-2.json", "Tec-6-0.1x", range(31, 36), None),
    ("Tec-7", TABLE4, "Tec-7", range(1, 6), None),
]
# Table 2 (authors, 2026-11-02): every column with the same operation counts, on fixedkeyset-2, X-Bench 24f2dddf:
# KV-I, II, III, V full size; KV-IV at 0.1x in every column (KV-Bench's generator needs ~2 days per full-size IV).
# KVBench I-V as the KVBench paper defines them (superseded: kvbench-fixed-vec-hashset/ with the earlier specs,
# kvbench-paper-full-fixed-vec-hashset/ with X-Bench 5b727191, the KV-Bench column at 1/20 from the corrected Fig. 7).
KVB = "kvbench-paper-full-fixed-vec-hashset-24f2dddf/results/runs.fixedkeyset-2.json"
KVB_IV = "kvbench-iv-0.1x-fixed-vec-hashset-24f2dddf/results/runs.fixedkeyset-2.json"
KVGEN = "kvbench-generator-table2/results/runs.fixedkeyset-2.json"
TABLE2 = [(f"KV-{n}†", KVB_IV if n == "IV" else KVB, "KVBench-IV-0.1x" if n == "IV" else f"KVBench-{n}", range(1, 6),
           (KVGEN, f"KVBench-{n}/KV-Bench", 10 if n == "IV" else 1)) for n in ("I", "II", "III", "IV", "V")]
# Table 3 (authors, 2026-11-02): every column on revision-2, X-Bench 24f2dddf (superseded: ycsb-fixed-empty/ with
# X-Bench ef9e19b3, the YCSB column from the corrected Fig. 7 on fixedkeyset-2)
YCSB = "ycsb-fixed-empty-24f2dddf/results/runs.revision-2.json"
YCSBGEN = "ycsb-generator-table3/results/runs.revision-2.json"
TABLE3 = [(f"YCSB-{w}", YCSB, f"YCSB-{w}", range(1, 6), (YCSBGEN, f"YCSB-{w}/YCSB", None)) for w in "ABCDEF"]


def rnd(x, q):
    return Decimal(repr(x)).quantize(Decimal(q), rounding=ROUND_HALF_UP)


def fmt_time(x):
    return "pending" if x is None else f"{rnd(x, '0.01'):,}"


def fmt_mem(x):
    return "pending" if x is None else f"{rnd(x, '0.1'):,}"


def selected(rs):
    """The KeySet Algorithm 1 selected, per section; one name if all sections (and trials) agree (authors,
    2026-11-02: no section count then)."""
    if not rs:
        return "pending"
    choices = {tuple(tuple(s) for s in r["keysets_used"]) for r in rs}
    assert len(choices) == 1, choices  # the same in every trial
    sections = next(iter(choices))
    short = lambda k: k.removesuffix("KeySet")  # authors, 2026-11-02: VecHashSetKeySet -> VecHashSet (table width)
    names = sorted({short(k) for _, k in sections})
    if len(names) == 1:
        return names[0]
    return ", ".join(f"S{i}: {short(k)}" for i, k in sections)


def original(gen):
    """Mean wall time and peak memory of the original generator (KV-Bench / YCSB) over its finished trials;
    gen = (file, job, KV-Bench scale): a KV-Bench record must have that scale (the X-Bench row's operation counts)."""
    path, job, scale = gen
    try:
        data = runs(path)
    except FileNotFoundError:
        data = []
    rs = [r for r in data if r["job"] == job and r["trial"] in range(1, TRIALS + 1)]
    if scale is not None:
        assert all(r["kvbench_paper_scale"] == scale for r in rs), (job, scale)
    ok = [r for r in rs if not r["failed"]]
    return {"n": len(ok), "failed": len(rs) - len(ok),
            "time": mean(r["wall_time_s"] for r in ok) if ok else None,
            "mem": mean(r["peak_rss_mib"] for r in ok) if ok else None}


def rows(spec, fixed_keyset, with_original=False):
    out = []
    for label, path, workload, trials, gen in spec:
        try:
            data = runs(path)
        except FileNotFoundError:  # not run yet
            data = []
        side = {}
        for k in ("adaptive", fixed_keyset):
            rs = [r for r in data if r["workload"] == workload and r["keyset"] == k and r["trial"] in trials
                  and not r.get("timed_out")]
            side[k] = rs
        a, f = side["adaptive"], side[fixed_keyset]
        out.append({
            "label": label, "n": (len(a), len(f)),
            "time": (mean(r["wall_time_s"] for r in a) if a else None, mean(r["wall_time_s"] for r in f) if f else None),
            "mem": (mean(r["peak_rss_mib"] for r in a) if a else None, mean(r["peak_rss_mib"] for r in f) if f else None),
            "selected": selected(a),
            "source": f"`{path}`, `{workload}`, trials {trials.start}-{trials.stop - 1}",
            "hosts": sorted({r.get("host") or "local" for r in a + f}),
            "original": original(gen) if with_original else None,
        })
    return out


def orig_cells(r):
    o = r["original"]
    if o is None:
        return None
    if o["n"] == 0:
        return ("crashed" if o["failed"] else "pending",) * 2
    mark = "" if o["n"] == TRIALS else f" ({o['n']} of {TRIALS}{', ' + str(o['failed']) + ' crashed' if o['failed'] else ''})"
    return fmt_time(o["time"]) + mark, fmt_mem(o["mem"]) + mark


def markdown(title, fixed_name, rs, generator=None, generator_scale=""):
    g = f" {generator} generator{generator_scale}" if generator else ""
    head = ["Workload", "Selected KeySet", "Generation time, adaptive (s)", "Generation time, fixed (s)"]
    head += [f"Generation time,{g} (s)"] if generator else []
    head += ["Memory, adaptive (MB)", "Memory, fixed (MB)"]
    head += [f"Memory,{g} (MB)"] if generator else []
    L = [f"## {title}", "", "| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    for r in rs:
        ft = fmt_time(r["time"][1])
        fm = fmt_mem(r["mem"][1])
        mark = "" if r["n"] == (TRIALS, TRIALS) else f" (provisional: {r['n'][0]} + {r['n'][1]} trials)"
        o = orig_cells(r)
        cells = [f"{r['label']}{mark}", r["selected"], fmt_time(r["time"][0]), ft] + ([o[0]] if o else [])
        cells += [fmt_mem(r["mem"][0]), fm] + ([o[1]] if o else [])
        L.append("| " + " | ".join(cells) + " |")
    L += ["", f"Fixed KeySet: `{fixed_name}` in every section.", ""]
    return L


def latex(caption, label, rs, generator=None, generator_scale=""):
    k = 3 if generator else 2
    cols = ["Adaptive", "Fixed"] + ([generator] if generator else [])
    # \small, no outer column padding (@{}), 3 pt between columns, and the generator's scale on its own header
    # line, so the table is as narrow as its contents (one column of the ACM two-column layout: 241 pt)
    L = ["```latex", r"\begin{table}[t]", r"\small", r"\setlength{\tabcolsep}{3pt}", r"\centering",
         rf"\caption{{{caption}}}", rf"\label{{{label}}}",
         r"\begin{tabular}{@{}ll" + "r" * (2 * k) + "@{}}", r"\toprule",
         rf" & & \multicolumn{{{k}}}{{c}}{{Generation time (s)}} & \multicolumn{{{k}}}{{c}}{{Memory (MB)}} \\",
         rf"\cmidrule(lr){{3-{2 + k}}} \cmidrule(lr){{{3 + k}-{2 + 2 * k}}}",
         "Workload & Selected KeySet & " + " & ".join(cols + cols) + r" \\"]
    if generator_scale.strip():
        scale = [""] * 2 + [generator_scale.strip()]
        L.append(" & & " + " & ".join(scale + scale) + r" \\")
    L.append(r"\midrule")
    for r in rs:
        ft = fmt_time(r["time"][1])
        fm = fmt_mem(r["mem"][1])
        o = orig_cells(r)
        cells = [r["label"].replace("†", r"$^\dagger$"), r["selected"], fmt_time(r["time"][0]), ft] + ([o[0]] if o else [])
        cells += [fmt_mem(r["mem"][0]), fm] + ([o[1]] if o else [])
        L.append(" & ".join(cells) + r" \\")
    L += [r"\bottomrule", r"\end{tabular}", r"\end{table}", "```", ""]
    return L


def sources(rs):
    L = ["| Row | Source (adaptive and fixed) | Machine |", "| --- | --- | --- |"]
    for r in rs:
        L.append(f"| {r['label']} | {r['source']} | {', '.join(r['hosts'])} |")
    return L + [""]


def main():
    t1, t2, t3 = rows(TABLE1, "vec"), rows(TABLE2, "vec-hashset", True), rows(TABLE3, "empty", True)
    provisional = [r["label"] for r in t1 + t2 + t3 if r["n"] != (TRIALS, TRIALS)]
    L = ["# Paper tables: adaptive KeySet vs. fixed KeySet (R1.O8, R2.O2)", "",
         "Written by `run_scripts/make_paper_tables_fixed_vs_adaptive.py` from the raw result files (rerun it to "
         "update). Every cell is the mean of 5 trials"
         + (f"; provisional rows (fewer finished trials): {', '.join(provisional)}." if provisional else "."), "",
         "- **Adaptive KeySet:** Algorithm 1, per section (`--keyset adaptive`).",
         "- **Fixed KeySet:** one KeySet for every section: `VecKeySet` (Table 1), KV-Bench's `VecHashSetKeySet` "
         "(Table 2), YCSB's `EmptyKeySet` (Table 3).",
         "- Single-threaded `tectonic-cli generate`; time is the wall clock including writing the trace; memory "
         "is peak RSS (KiB / 1024, labelled MB). Means at full precision, rounded half up: 0.01 s, 0.1 MB.",
         "- Each row compares the two KeySets on one machine (2x Xeon Gold 6126, 187 GB, same SSD model).", ""]
    L += markdown("Table 1: X-Bench workloads Tec-1..7, fixed VecKeySet", "VecKeySet", t1)
    L += ["Tec-3, Tec-5 and Tec-6 are at 0.1x scale (every `op_count` / 10, nothing else changed: "
          "`x-bench-specs/table4/*_0.1x.spec.json`): with the fixed VecKeySet they take 85+ h (Tec-3 stopped at 50% "
          "after 84.7 h), 27-40 h and 27 h at full scale. Tec-1, Tec-2, Tec-4 and Tec-7 are at full scale.", ""]
    L += latex("Generation time and memory footprint of X-Bench workloads with the adaptive KeySet and the fixed "
               "VecKeySet (mean of 5 trials; Tec-3, Tec-5, Tec-6 at 0.1x scale).", "tab:fixed-vs-adaptive-tec", t1)
    L += markdown("Table 2: KV-Bench I-V, fixed VecHashSetKeySet (KV-Bench's design)", "VecHashSetKeySet", t2,
                  "KV-Bench")
    L += ["† KV-I to KV-V: KV-Bench workloads I to V.", "",
          "Specs: KVBench I-V as the KVBench paper defines them (Table 5: operation percentages and distributions; "
          "KV-Bench's defaults where it gives none), with Fig. 7's operation counts "
          "(`example-specs/kvbench/`, written by `run_scripts/make_kvbench_paper_specs.py`, 2026-11-02).", "",
          "**Every column of a row has the same operation counts** (authors, 2026-11-02): KV-I, II, III, V at full size; "
          "**KV-IV at 0.1x in every column** (every operation count / 10: `example-specs/kvbench-0.1x/iv.spec.json`, "
          "KV-Bench `-I 100000 -U 50000 -R 50000`), because KV-Bench's generator sorts every key on each range delete "
          "and needs about 2 days for one full-size IV (137,387 of 500,000 range deletes after 15 h).", "",
          "KV-Bench generator: KV-Bench's own C++ generator (`load_gen`, branch `refactor` 699bc10, built as its README "
          "says) with two bug fixes (`../fig7-corrected/kvbench-minimal-fix/`: an update past the key pool, which "
          "crashed workload II in every branch; range queries, which KV-I-V do not have), measured as the corrected "
          "Fig. 7: wall clock of the process, peak VmRSS sampled every 10 ms, caches dropped "
          "(`kvbench-generator-table2/README.md`). All columns on fixedkeyset-2.", ""]
    L += latex("Generation time and memory footprint of KV-Bench workloads with the adaptive KeySet, "
               "KV-Bench's VecHashSetKeySet, and KV-Bench's generator (mean of 5 trials; KV-IV at 0.1x scale). "
               "$^\\dagger$KV-I to KV-V: KV-Bench workloads I to V.", "tab:fixed-vs-adaptive-kvbench", t2, "KV-Bench")
    L += markdown("Table 3: YCSB A-F, fixed EmptyKeySet (YCSB's design)", "EmptyKeySet", t3, "YCSB")
    L += ["Specs: `example-specs/ycsb-untracked/`, every operation untracked as in YCSB (details: "
          "`ycsb-fixed-empty/README.md`), so Algorithm 1 selects `EmptyKeySet` too and the two columns differ only "
          "by run-to-run noise.", ""]
    L += ["YCSB generator: YCSB 0.18.0-SNAPSHOT (`rocksdb-benchmark-harness/vendor/YCSB` 0bedec5, unmodified; "
          "CoreWorkload + FileClient), workloads a-f with 1M records and 1M operations, measured as the corrected "
          "Fig. 7: load + run wall clock, the larger peak VmRSS of the two processes, caches dropped "
          "(`ycsb-generator-table3/README.md`). All columns on revision-2 (authors, 2026-11-02).", ""]
    L += latex("Generation time and memory footprint of YCSB workloads with the adaptive KeySet, YCSB's "
               "EmptyKeySet, and YCSB's generator (mean of 5 trials).", "tab:fixed-vs-adaptive-ycsb", t3, "YCSB")
    L += ["## Sources", "", "Paths relative to this folder.", ""]
    for title, rs in (("Table 1", t1), ("Table 2", t2), ("Table 3", t3)):
        L += [f"**{title}**", ""] + sources(rs)
    L += ["Binaries: Table 1: `table4/` rows ran on the local machine on 2026-10-22 with the binary of that day (see "
          "`README.md`), the multimachine runs with 3b8e082 (sha256 `0cf9d251…`). Tables 2 and 3: `24f2dddf…` "
          "(2026-11-02: `5b727191…`, the X-Bench binary of the corrected Fig. 7, plus `Scatter::apply` computing its "
          "multiplier once instead of for every number; same output, `ycsb-fixed-empty-24f2dddf/README.md`). Table 1's "
          "specs do not use `scatter`.", ""]
    with open(OUT, "w") as f:
        f.write("\n".join(L))
    print(OUT)


if __name__ == "__main__":
    main()
