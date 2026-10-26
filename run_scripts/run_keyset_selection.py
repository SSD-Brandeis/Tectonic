#!/usr/bin/env python3
"""
run_keyset_selection.py
=======================
Revision items R1.O8 / R2.O3 / R2.Q3 and R2.O2 (ii):

  "We will add an experiment that generates each of the seven workloads in Table 4 with the
   compatible KeySets, reporting generation time and peak memory. We will show whether the KeySet
   selected by Algorithm 1 achieves the best performance for both metrics."
  "(ii) add a same-language ablation that generates each workload with a 'fixed KeySet' and with
   'adaptive KeySet' selection"

Two workload suites (--suite), each in its own folder under the output directory:
  table4    the seven workloads of Table 4 (Tec-1..Tec-7), as promised;
  branches  one workload per branch of Algorithm 1 (B1..B8), so that every KeySet Algorithm 1 can
            select is selected by one workload (Table 4 only reaches three of them).

Every run is one single-threaded `tectonic-cli generate -w <spec> -o <trace> --keyset <k>`:
  - adaptive: Algorithm 1 selects the KeySet per section (the default of `tectonic-cli`);
  - every compatible KeySet forced for all sections (the fixed baseline of R2.O2 (ii) is VecKeySet).
Wall-clock time and peak RSS (getrusage, via GNU time) of the process; the trace is written to the
same SSD and deleted after the run; the page cache is dropped before every run.

Output: data/revision/R1.O8-R2.O2_keyset-selection/<suite folder>/ (see the README.md there).
"""
import math
import os
import re
import sys
import json
import time
import random
import shutil
import argparse
import statistics
import subprocess

ROOT_DIR = "/home/cc/Tectonic"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"
EXPERIMENT_DIR = f"{ROOT_DIR}/data/revision/R1.O8-R2.O2_keyset-selection"
# set by use_suite()
OUT_DIR = RUNS_PATH = SUMMARY_PATH = REPORT_PATH = LOG_PATH = TRACE_PATH = None
WORKLOADS = SUITE = None

TRIALS = 5
TIMEOUT_S = None  # per suite, see SUITES
SHUFFLE_SEED = 20261022

# Table 4 of the paper: the seven X-Bench workloads, as corrected in x-bench-specs/table4/ (see the README of
# the experiment): Tec-1 written from Table 4, unique inserts in Tec-1..6, hot reads in Tec-7.
TABLE4_WORKLOADS = {
    "Tec-1": ("multi-stage", "x-bench-specs/table4/Tec-1_multi_stage.spec.json"),
    "Tec-2": ("interleaved", "x-bench-specs/table4/Tec-2_interleaved.spec.json"),
    "Tec-3": ("varied sortedness", "x-bench-specs/table4/Tec-3_varied_sortedness.spec.json"),
    "Tec-4": ("slow shift", "x-bench-specs/table4/Tec-4_slow_shift.spec.json"),
    "Tec-5": ("abrupt shift", "x-bench-specs/table4/Tec-5_abrupt_shift.spec.json"),
    "Tec-6": ("varied key/val size", "x-bench-specs/table4/Tec-6_varied_kv_size.spec.json"),
    "Tec-7": ("hot-key", "x-bench-specs/table4/Tec-7_hot_key.spec.json"),
}

# The same seven workloads with upserts (`inserts`) instead of `unique_inserts`, nothing else changed
TABLE4_UPSERT_WORKLOADS = {wl: (label, path.replace("x-bench-specs/table4/", "x-bench-specs/table4-upsert/"))
                           for wl, (label, path) in TABLE4_WORKLOADS.items()}

# One workload per branch of Algorithm 1 (adaptive_keyset in tectonic/src/lib.rs). Each is one section of
# two groups: "load" (2M inserts; unique inserts where the branch needs contains checks) and "run" (the
# operations that make the branch's requirements, no inserts so the loaded keys stay sorted).
BRANCH_WORKLOADS = {
    "B1": ("no requirement -> EmptyKeySet", "example-specs/keyset-branches/B1_empty.spec.json"),
    "B2": ("sort + delete + contains -> VecOptionHashSetKeySet",
           "example-specs/keyset-branches/B2_vec-option-hashset.spec.json"),
    "B3": ("sort + delete -> VecOptionKeySet", "example-specs/keyset-branches/B3_vec-option.spec.json"),
    "B4": ("sort + contains -> VecHashSetKeySet", "example-specs/keyset-branches/B4_vec-hashset.spec.json"),
    "B5": ("delete -> VecKeySet", "example-specs/keyset-branches/B5_vec-delete.spec.json"),
    "B6": ("contains + random -> VecBloomFilterKeySet", "example-specs/keyset-branches/B6_vec-bloom.spec.json"),
    "B7": ("contains -> BloomFilterKeySet", "example-specs/keyset-branches/B7_bloom.spec.json"),
    "B8": ("random -> VecKeySet", "example-specs/keyset-branches/B8_vec-random.spec.json"),
}

# suite -> (folder, description, workloads, timeout per run in s)
SUITES = {
    "table4": ("table4", "the seven workloads of Table 4", TABLE4_WORKLOADS, 600),
    "branches": ("algorithm1-branches", "one workload per branch of Algorithm 1", BRANCH_WORKLOADS, 600),
    "table4-upsert": ("table4-upsert", "the seven workloads of Table 4 with upserts instead of unique inserts, "
                      "adaptive and fixed VecKeySet only", TABLE4_UPSERT_WORKLOADS, 600),
}
# suites that run only some of the compatible KeySets (besides adaptive)
SUITE_KEYSETS = {"table4-upsert": ("vec",)}


def use_suite(name):
    global OUT_DIR, RUNS_PATH, SUMMARY_PATH, REPORT_PATH, LOG_PATH, TRACE_PATH, WORKLOADS, SUITE, TIMEOUT_S
    folder, _, WORKLOADS, TIMEOUT_S = SUITES[name]
    SUITE = name
    OUT_DIR = f"{EXPERIMENT_DIR}/{folder}"
    RUNS_PATH = f"{OUT_DIR}/results/runs.json"
    SUMMARY_PATH = f"{OUT_DIR}/results/summary.json"
    REPORT_PATH = f"{OUT_DIR}/report.md"
    LOG_PATH = f"{OUT_DIR}/logs/run.log"
    TRACE_PATH = f"{OUT_DIR}/tmp/trace.txt"

# The seven KeySets of Algorithm 1: `--keyset` value -> name printed by tectonic-cli
KEYSETS = {
    "empty": "EmptyKeySet",
    "vec": "VecKeySet",
    "bloom": "BloomFilterKeySet",
    "vec-bloom": "VecBloomFilterKeySet",
    "vec-hashset": "VecHashSetKeySet",
    "vec-option": "VecOptionKeySet",
    "vec-option-hashset": "VecOptionHashSetKeySet",
}
FIXED_KEYSET = "vec"

# What each KeySet supports (tectonic/src/keyset.rs). EmptyKeySet answers every contains check with
# "no" and BloomFilterKeySet cannot return keys, so neither supports random access.
CAPABILITIES = {
    "empty": set(),
    "vec": {"random", "delete", "sort", "contains"},
    "bloom": {"contains"},
    "vec-bloom": {"random", "delete", "sort", "contains"},
    "vec-hashset": {"random", "delete", "sort", "contains"},
    "vec-option": {"random", "delete", "sort"},
    "vec-option-hashset": {"random", "delete", "sort", "contains"},
}

OPS = ["inserts", "unique_inserts", "updates", "merges", "point_deletes", "empty_point_deletes", "range_deletes",
       "point_queries", "empty_point_queries", "range_queries", "untracked_point_queries", "untracked_point_deletes",
       "untracked_range_queries"]


def section_requirements(section):
    """The requirements Algorithm 1 derives from a section's operations (tectonic/src/lib.rs)."""
    has = {op for g in section["groups"] for op in OPS if op in g and g[op].get("op_count", 0)}
    # range queries need sorted keys only in StartEnd format (has_query_range); StartCount
    # (the default) only needs a random start key (has_query_range_count)
    start_end_rq = any(g.get("range_queries", {}).get("op_count", 0)
                       and g["range_queries"].get("range_format", "StartCount") == "StartEnd"
                       for g in section["groups"])
    req = set()
    if has & {"updates", "merges", "point_deletes", "range_deletes", "point_queries", "range_queries"}:
        req.add("random")
    if has & {"point_deletes", "range_deletes"}:
        req.add("delete")
    if start_end_rq or "range_deletes" in has:
        req.add("sort")
    if not section.get("skip_key_contains_check") and has & {"unique_inserts", "empty_point_queries",
                                                            "empty_point_deletes"}:
        req.add("contains")
    return req


def compatible_keysets(spec):
    """KeySets that support every requirement of every section (one KeySet is forced for all sections)."""
    reqs = set().union(*(section_requirements(s) for s in spec["sections"]))
    return [k for k, caps in CAPABILITIES.items() if reqs <= caps], sorted(reqs)


# =============================================================================
#  UTILITIES
# =============================================================================
_log_fh = None


def log(msg=""):
    print(msg, flush=True)
    if _log_fh:
        _log_fh.write(msg + "\n")
        _log_fh.flush()


def write_json_atomic(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def drop_caches():
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)


def op_counts(path):
    counts = {}
    with open(path, "rb") as f:
        for line in f:
            op = line[:line.index(b" ")].decode()
            counts[op] = counts.get(op, 0) + 1
    return counts


def generate(workload, keyset, trial, validate):
    spec = f"{ROOT_DIR}/{WORKLOADS[workload][1]}"
    if os.path.exists(TRACE_PATH):
        os.remove(TRACE_PATH)
    drop_caches()
    cmd = ["/usr/bin/time", "-v", TECTONIC_CLI, "generate", "-w", spec, "-o", TRACE_PATH, "--keyset", keyset]
    env = {k: v for k, v in os.environ.items() if k != "TECTONIC_PARALLEL_GEN"}  # single-threaded
    env["RUST_LOG"] = "tectonic=debug"  # progress of every group in steps of 10%, for runs that time out
    t0 = time.perf_counter()
    # own process group, so that a timeout kills tectonic-cli and not only /usr/bin/time
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                         start_new_session=True)
    try:
        stdout, stderr = p.communicate(timeout=TIMEOUT_S)
        timed_out = False
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, 9)
        _, stderr = p.communicate()
        timed_out = True
    wall = time.perf_counter() - t0
    stderr = re.sub(r"\x1b\[[0-9;]*m", "", stderr or "")
    out_dir = f"{OUT_DIR}/runs/{workload}/{keyset}"
    os.makedirs(out_dir, exist_ok=True)
    with open(f"{out_dir}/trial{trial}.stderr.log", "w") as f:
        f.write(stderr)
    rec = {"workload": workload, "keyset": keyset, "trial": trial, "wall_time_s": wall, "timed_out": timed_out,
           "trace_bytes": os.path.getsize(TRACE_PATH) if os.path.exists(TRACE_PATH) else 0,
           "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
    if timed_out:
        groups = re.findall(r"\[Generating\] Section (\d+) \| Group (\d+)", stderr)
        pct = re.findall(r"Generating operation \d+ \((\d+)%\)", stderr.split("[Generating]")[-1])
        rec["progress_at_timeout"] = {"section": int(groups[-1][0]), "group": int(groups[-1][1]),
                                      "group_percent_done": int(pct[-1]) if pct else 0} if groups else None
    else:
        if p.returncode != 0:
            log(stderr[-3000:])
            raise RuntimeError(f"{workload} {keyset} trial {trial} failed with exit code {p.returncode}")
        m = re.search(r"Maximum resident set size \(kbytes\): (\d+)", stderr)
        rec["peak_rss_mib"] = int(m.group(1)) / 1024
        m = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)", stderr)
        rec["gnu_time_elapsed"] = m.group(1)
        m = re.search(r"Tectonic_Op_Timings: (\{.*\})", stdout or "")  # printed to stdout
        rec["op_timings_s"] = json.loads(m.group(1)) if m else None
        rec["keysets_used"] = re.findall(r"\[Tectonic KeySet\] Section (\d+): (\S+)", stderr)
        if validate:
            rec["op_counts"] = op_counts(TRACE_PATH)
    if os.path.exists(TRACE_PATH):
        os.remove(TRACE_PATH)
    return rec


# =============================================================================
#  STAGES
# =============================================================================
def configurations():
    configs = []
    for wl, (_, spec_path) in WORKLOADS.items():
        with open(f"{ROOT_DIR}/{spec_path}") as f:
            spec = json.load(f)
        keysets, _ = compatible_keysets(spec)
        keysets = [k for k in keysets if k in SUITE_KEYSETS.get(SUITE, keysets)]
        configs += [(wl, "adaptive")] + [(wl, k) for k in keysets]
    return configs


def stage_run(args):
    runs = load_json(RUNS_PATH, [])
    done = {(r["workload"], r["keyset"], r["trial"]) for r in runs}
    configs = configurations()
    for trial in range(1, args.trials + 1):
        order = list(configs)
        random.Random(SHUFFLE_SEED + trial).shuffle(order)
        for wl, ks in order:
            if (wl, ks, trial) in done:
                continue
            if any(r["workload"] == wl and r["keyset"] == ks and r["timed_out"] for r in runs):
                continue  # a configuration that timed out once is not repeated
            log(f"[trial {trial}] {wl} --keyset {ks}")
            rec = generate(wl, ks, trial, validate=(trial == 1))
            runs.append(rec)
            write_json_atomic(RUNS_PATH, runs)
            log(f"   -> {rec['wall_time_s']:.2f} s, {rec.get('peak_rss_mib', float('nan')):.1f} MiB"
                f"{' TIMEOUT' if rec['timed_out'] else ''}  {rec.get('keysets_used')}")


def mean_sd(values):
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)


def value_range(values):
    values = [v for v in values if v is not None]
    return (min(values), max(values)) if values else None


def distinct(r1, r2):
    """Two configurations differ when their trial ranges do not overlap."""
    return r1[1] < r2[0] or r2[1] < r1[0]


def ties(forced, best, metric):
    """KeySets indistinguishable from the best one: trial ranges overlap the best's."""
    return sorted(k for k, v in forced.items() if not distinct(v[metric + "_range"], forced[best][metric + "_range"]))


def dominating(forced, sel):
    """KeySets that beat `sel` in one metric and are not worse in the other (ties: overlapping ranges)."""
    def better(a, b, m):  # a is distinctly better than b in metric m
        return distinct(a[m + "_range"], b[m + "_range"]) and a[m][0] < b[m][0]
    out = []
    for k, v in forced.items():
        if k == sel:
            continue
        b = [better(v, forced[sel], m) for m in ("time_s", "peak_rss_mib")]
        w = [better(forced[sel], v, m) for m in ("time_s", "peak_rss_mib")]
        if any(b) and not any(w):
            out.append(k)
    return out


def progress(row):
    p = row.get("progress_at_timeout")
    if not p:
        return ""
    # only the group is known: the generator logs progress within a group to stdout, which a killed run loses
    return f"; killed in group {p['group']}"


def single_keyset(names):
    keys = {k for k, v in KEYSETS.items() if v in names}
    return next(iter(keys)) if len(keys) == 1 else None


def stage_report(args):
    runs = load_json(RUNS_PATH, [])
    summary = {}
    for wl, (label, spec_path) in WORKLOADS.items():
        with open(f"{ROOT_DIR}/{spec_path}") as f:
            spec = json.load(f)
        keysets, reqs = compatible_keysets(spec)
        rows = {}
        for ks in ["adaptive"] + keysets:
            rs = [r for r in runs if r["workload"] == wl and r["keyset"] == ks]
            if not rs:
                continue
            ok = [r for r in rs if not r["timed_out"]]
            rows[ks] = {"trials": len(ok), "timed_out": any(r["timed_out"] for r in rs),
                        "time_s": mean_sd([r["wall_time_s"] for r in ok]),
                        "peak_rss_mib": mean_sd([r["peak_rss_mib"] for r in ok]),
                        "time_s_range": value_range([r["wall_time_s"] for r in ok]),
                        "peak_rss_mib_range": value_range([r["peak_rss_mib"] for r in ok]),
                        "keysets_used": ok[0]["keysets_used"] if ok else None,
                        "progress_at_timeout": next((r.get("progress_at_timeout") for r in rs if r["timed_out"]),
                                                    None),
                        "op_counts": next((r["op_counts"] for r in ok if "op_counts" in r), None)}
        if "adaptive" not in rows:
            continue
        selected = [name for _, name in rows["adaptive"]["keysets_used"]]
        forced = {k: v for k, v in rows.items() if k != "adaptive" and not v["timed_out"]}
        # None when every forced KeySet timed out (e.g. table4-upsert Tec-1, where only VecKeySet is forced)
        best_time = min(forced, key=lambda k: forced[k]["time_s"][0]) if forced else None
        best_mem = min(forced, key=lambda k: forced[k]["peak_rss_mib"][0]) if forced else None
        summary[wl] = {"label": label, "spec": spec_path, "requirements": reqs, "compatible": keysets,
                       "algorithm1_selects": selected, "rows": rows,
                       "best_time": best_time, "best_memory": best_mem,
                       "tied_time": ties(forced, best_time, "time_s") if forced else [],
                       "tied_memory": ties(forced, best_mem, "peak_rss_mib") if forced else [],
                       "dominated_by": (dominating(forced, sel_key) if (sel_key := single_keyset(selected)) in forced
                                        else None)}
    write_json_atomic(SUMMARY_PATH, summary)

    L = ["# KeySet selection (R1.O8, R2.O3, R2.Q3) and fixed vs. adaptive KeySet (R2.O2 (ii))", "",
         f"Suite `{SUITE}`: {SUITES[SUITE][1]}.", "",
         f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')} by `run_scripts/run_keyset_selection.py`. "
         f"Single-threaded `tectonic-cli generate`, {TRIALS} trials per configuration, mean ± stdev; "
         "time is wall clock of the whole process including writing the trace; memory is peak RSS. "
         f"Two configurations are called different only when the ranges [min, max] of their {TRIALS} trials "
         "do not overlap; \"fastest\" / \"least memory\" mark the lowest mean, and every KeySet whose range "
         "overlaps it is tied with it. "
         f"A run is stopped after {TIMEOUT_S:,} s; a configuration stopped once is not repeated, and its row "
         "shows the group it was killed in (group 0 is the first group of the section); how far it got within "
         "the group was not recorded, and it has no peak RSS (GNU time is killed with the generator). "
         "A ratio to a timed-out run is a lower bound, floor(timeout / adaptive mean).", "",
         "## 1. Every compatible KeySet per workload", ""]
    for wl, s in summary.items():
        sel = ", ".join(sorted(set(s["algorithm1_selects"])))
        L += [f"### {wl} ({s['label']}): requires {', '.join(s['requirements']) or 'nothing'}; "
              f"Algorithm 1 selects {sel}", "",
              "| KeySet | Time (s) | Peak RSS (MiB) | Trials |", "| --- | --- | --- | --- |"]
        for ks, row in s["rows"].items():
            name = "adaptive (Algorithm 1)" if ks == "adaptive" else KEYSETS[ks]
            if row["timed_out"]:
                L.append(f"| {name} | > {TIMEOUT_S:,} (timeout{progress(row)}) | - | 0 |")
                continue
            t, m = row["time_s"], row["peak_rss_mib"]
            marks = []
            if ks == s["best_time"]:
                marks.append("fastest")
            elif ks in s["tied_time"]:
                marks.append("tied fastest")
            if ks == s["best_memory"]:
                marks.append("least memory")
            elif ks in s["tied_memory"]:
                marks.append("tied least memory")
            L.append(f"| {name}{' (' + ', '.join(marks) + ')' if marks else ''} | {t[0]:.2f} ± {t[1]:.2f} | "
                     f"{m[0]:,.1f} ± {m[1]:,.1f} | {row['trials']} |")
        L.append("")

    L += ["## 2. Is the KeySet Algorithm 1 selects the best?", "",
          "| Workload | Algorithm 1 selects | Fastest (lowest mean; tied) | Least memory (lowest mean; tied) | "
          "Selected vs. fastest (time) | Selected vs. least memory (RSS) | Selected is best in time / memory | "
          "KeySets better in one metric and not worse in the other |",
          "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for wl, s in summary.items():
        sel_keys = {k for k, v in KEYSETS.items() if v in s["algorithm1_selects"]}
        rows = s["rows"]
        sel = next(iter(sel_keys)) if len(sel_keys) == 1 else None
        dt = dm = verdict = "-"
        if sel and sel in rows and s["best_time"]:
            verdict = (f"{'yes' if sel in s['tied_time'] else 'no'} / "
                       f"{'yes' if sel in s['tied_memory'] else 'no'}")
            bt, bm = rows[s["best_time"]]["time_s"][0], rows[s["best_memory"]]["peak_rss_mib"][0]
            dt = f"{(rows[sel]['time_s'][0] - bt) / bt * 100:+.1f}%"
            dm = f"{(rows[sel]['peak_rss_mib'][0] - bm) / bm * 100:+.1f}%"
        tied = lambda best, group: (KEYSETS[best] + "".join(
            f"; {KEYSETS[k]}" for k in group if k != best)) if best else "- (every forced KeySet timed out)"
        L.append(f"| {wl} | {', '.join(KEYSETS[k] for k in sorted(sel_keys))} | {tied(s['best_time'], s['tied_time'])} | "
                 f"{tied(s['best_memory'], s['tied_memory'])} | {dt} | {dm} | {verdict} | "
                 f"{', '.join(KEYSETS[k] for k in s['dominated_by']) if s['dominated_by'] else 'none'} |")

    L += ["", f"## 3. Fixed ({KEYSETS[FIXED_KEYSET]}) vs. adaptive KeySet (R2.O2 (ii))", "",
          "| Workload | Adaptive KeySet | Adaptive time (s) | Fixed time (s) | Time diff | Adaptive RSS (MiB) | "
          "Fixed RSS (MiB) | RSS diff |",
          "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for wl, s in summary.items():
        a, f_ = s["rows"]["adaptive"], s["rows"].get(FIXED_KEYSET)
        a_ks = ", ".join(sorted(set(s["algorithm1_selects"])))
        if not f_:
            continue
        if f_["timed_out"]:
            L.append(f"| {wl} | {a_ks} | {a['time_s'][0]:.2f} ± {a['time_s'][1]:.2f} | > {TIMEOUT_S:,} (timeout{progress(f_)}) | "
                     f"> {math.floor(TIMEOUT_S / a['time_s'][0]):,}x | {a['peak_rss_mib'][0]:,.1f} | - | - |")
            continue
        diff = lambda m: (f"{(f_[m][0] - a[m][0]) / a[m][0] * 100:+.1f}%"
                          + ("" if distinct(a[m + "_range"], f_[m + "_range"]) else " (ranges overlap)"))
        L.append(f"| {wl} | {a_ks} | {a['time_s'][0]:.2f} ± {a['time_s'][1]:.2f} | "
                 f"{f_['time_s'][0]:.2f} ± {f_['time_s'][1]:.2f} | {diff('time_s')} | "
                 f"{a['peak_rss_mib'][0]:,.1f} | {f_['peak_rss_mib'][0]:,.1f} | {diff('peak_rss_mib')} |")

    L += ["", "## 4. Validation: operations per type in the trace (trial 1)", "",
          "| Workload | KeySet | Operations |", "| --- | --- | --- |"]
    for wl, s in summary.items():
        for ks, row in s["rows"].items():
            if row["op_counts"]:
                L.append(f"| {wl} | {ks} | {', '.join(f'{k}={v:,}' for k, v in sorted(row['op_counts'].items()))} |")
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(L) + "\n")
    log(f"report: {REPORT_PATH}")


def main():
    global _log_fh
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=list(SUITES), required=True)
    ap.add_argument("--stage", choices=["run", "report", "all"], default="all")
    ap.add_argument("--trials", type=int, default=TRIALS)
    args = ap.parse_args()
    use_suite(args.suite)
    for d in ("results", "logs", "tmp", "runs", "specs"):
        os.makedirs(f"{OUT_DIR}/{d}", exist_ok=True)
    for wl, (_, spec_path) in WORKLOADS.items():
        name = os.path.basename(spec_path)
        shutil.copy2(f"{ROOT_DIR}/{spec_path}", f"{OUT_DIR}/specs/{name if name.startswith(wl) else f'{wl}_{name}'}")
    _log_fh = open(LOG_PATH, "a")
    log(f"=== run_keyset_selection.py --suite {args.suite} --stage {args.stage}  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if args.stage in ("run", "all"):
        stage_run(args)
    if args.stage in ("report", "all"):
        stage_report(args)


if __name__ == "__main__":
    main()
