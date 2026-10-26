#!/usr/bin/env python3
"""
run_ycsb_derivatives_fidelity.py
================================
Revision item R1.O7: "We will add experiments to compare how closely X-Bench mimics the
workloads offered by YCSB-IVS and YCSB-TS".

For every workload, three traces are compared:
  ycsb_derivative        the derivative's own, unmodified Java generator (CoreWorkload of YCSB-IVS /
                         YCSB-TS), recorded with a FileClient-style binding (ycsb-derivatives/), the
                         method of the YCSB traces of Table 5;
  ycsb_derivative_rerun  a second, independent run of the same generator (the noise floor);
  xbench                 X-Bench, from the specs in example-specs/ycsb-ivs/ and example-specs/ycsb-ts/.

Stages (checkpointed, run with --stage <name>, or all in this order):
  generate      the three traces per workload
  analyze       trace properties: op mix per phase window, key popularity, reuse distance,
                value sizes, timestamp positions, time-window widths and starts
  trace-spread  the same distances for further independent draws of both generators
  execute       TRIALS trials per trace with `tectonic-cli execute` on RocksDB (fresh database,
                dropped page cache): throughput, per-phase latency percentiles, bytes read and
                written, end-of-run database state
  report        report.md from all of the above

Output: data/revision/R1.O7_ycsb-derivatives/ (see its README.md).
"""
import os
import re
import sys
import json
import time
import random
import shutil
import string
import hashlib
import argparse
import statistics
import subprocess
import threading

ROOT_DIR = "/home/cc/Tectonic"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"
VALUE_SIZES_BIN = f"{ROOT_DIR}/target/release/examples/rocksdb_value_sizes"

DERIV_DIR = f"{ROOT_DIR}/ycsb-derivatives"
IVS_DIR = f"{ROOT_DIR}/YCSB-IVS"
TS_DIR = f"{ROOT_DIR}/YCSB-TS"

OUT_DIR = f"{ROOT_DIR}/data/revision/R1.O7_ycsb-derivatives"
WORKLOAD_DIR = f"{OUT_DIR}/workloads"
RESULTS_DIR = f"{OUT_DIR}/results"
DBSTATE_DIR = f"{OUT_DIR}/db_state"
LOG_PATH = f"{OUT_DIR}/logs/run.log"
ROCKSDB_DIR = f"{OUT_DIR}/rocksdb_data"

TRIALS = 5
# ycsb_derivative: the derivative's trace X-Bench is compared against; ycsb_derivative_rerun: a second,
# independent run of the derivative's generator (the noise floor); xbench: X-Bench's trace
SIDES = ("ycsb_derivative", "ycsb_derivative_rerun", "xbench")


def _classpath(*jars, cp_file):
    with open(cp_file) as f:
        deps = f.read().strip()
    return ":".join(list(jars) + [deps])


IVS_CP = _classpath(f"{DERIV_DIR}/ivs/ivs-fileclient.jar",
                    f"{IVS_DIR}/core/target/core-0.18.0-SNAPSHOT.jar",
                    cp_file=f"{DERIV_DIR}/ivs/core.classpath")
TS_CP = _classpath(f"{DERIV_DIR}/ts/ts-fileclient.jar",
                   f"{TS_DIR}/core/target/core-0.4.0.jar",
                   cp_file=f"{DERIV_DIR}/ts/core.classpath")

# =============================================================================
#  WORKLOADS
# =============================================================================
# Only workloads exactly as the derivatives ship them.
# YCSB-IVS: workloads/workloada-extend (its only extend workload) driven exactly as the shipped
# experiment_scripts/experiment_mongodb.sh drives it on the primary database: load, then iterations of
# an extend phase (extendproportion=1) followed by a run phase (read/update 0.5/0.5,
# fieldlengthdistribution=histogram built from the record sizes). The script exits in iteration 5 right
# after its extend phase, so it issues 5 extend phases and 4 run phases.
IVS_WORKLOAD_FILE = f"{IVS_DIR}/workloads/workloada-extend"
IVS_EXTEND_PHASES = 5
IVS_RUN_PHASES = 4

# YCSB-TS: one of the three domain workloads shipped in workloads/ (energy, smartFactory,
# medicalMonitoring), unchanged. energy is the one evaluated: it loads the time domain in order and then
# mixes random inserts with time-range queries.
WORKLOADS = {
    "ivs-uniform": {"tool": "ivs", "distribution": "uniform", "scale": 1},
    # the same with 10x records and operations per phase (a 1x trial takes ~2 s); everything else as shipped
    "ivs-uniform-x10": {"tool": "ivs", "distribution": "uniform", "scale": 10},
    "ts-energy": {"tool": "ts", "workload_file": f"{TS_DIR}/workloads/energy"},
}

# =============================================================================
#  UTILITIES
# =============================================================================
_log_fh = None


def log(msg=""):
    print(msg, flush=True)
    if _log_fh:
        _log_fh.write(msg + "\n")
        _log_fh.flush()


def banner(title):
    log(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")


def fail(msg):
    log(f"  [FATAL] {msg}")
    sys.exit(1)


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


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def read_props(path):
    props = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
    return props


def timed_run(cmd, cwd=None, stdout_path=None):
    """Runs cmd under /usr/bin/time -v. Returns (wall seconds, peak RSS in KiB)."""
    shown = ["$CP" if str(c) in (IVS_CP, TS_CP) else str(c) for c in cmd]
    log(f"  $ {' '.join(shown)}")
    full = ["/usr/bin/time", "-v"] + [str(c) for c in cmd]
    out = open(stdout_path, "w") if stdout_path else subprocess.DEVNULL
    t0 = time.perf_counter()
    r = subprocess.run(full, cwd=cwd, stdout=out, stderr=subprocess.PIPE, text=True)
    wall = time.perf_counter() - t0
    if stdout_path:
        out.close()
    if r.returncode != 0:
        log(r.stderr[-3000:])
        fail(f"command failed with exit code {r.returncode}")
    m = re.search(r"Maximum resident set size \(kbytes\): (\d+)", r.stderr)
    return wall, int(m.group(1)) if m else None


class GenCost:
    """Accumulates the cost of all processes that generate one trace."""

    def __init__(self):
        self.wall_s = 0.0
        self.peak_rss_kib = 0
        self.processes = 0

    def add(self, wall, rss):
        self.wall_s += wall
        self.peak_rss_kib = max(self.peak_rss_kib, rss or 0)
        self.processes += 1

    def as_dict(self):
        return {"wall_s": round(self.wall_s, 3), "peak_rss_mib": round(self.peak_rss_kib / 1024, 1),
                "processes": self.processes}


def count_lines(path):
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            n += chunk.count(b"\n")
    return n


# =============================================================================
#  REFERENCE TRACES (the derivatives' own Java generators)
# =============================================================================
def ivs_params(wl):
    props = read_props(IVS_WORKLOAD_FILE)
    s = wl["scale"]
    return {
        "recordcount": int(props["recordcount"]) * s,
        # experiment_mongodb.sh: extendoperationcount="10000"; operationcount from the workload file
        "extendoperationcount": 10000 * s,
        "operationcount": int(props["operationcount"]) * s,
        "fieldlength": int(props["fieldlength"]),
        "extendfieldlength": int(props["extendfieldlength"]),
        "distribution": wl["distribution"],
        "extend_phases": IVS_EXTEND_PHASES,
        "run_phases": IVS_RUN_PHASES,
    }


def ivs_histogram(sizes_csv, histogram_path):
    """get_key_sizes() of experiment_mongodb.sh, run verbatim with awk."""
    awk = r'''
        BEGIN { block = 100; OFS = "\t" }
        NR == 1 { next }
        {
            size = $2 + 0
            bucket = int(size / (block * 10 ))
            histogram[bucket]++
            if (bucket > max_bucket) max_bucket = bucket
        }
        END {
            print "BlockSize", block > "''' + histogram_path + r'''"
            for (i = 0; i <= max_bucket; i++) {
                count = (i in histogram) ? histogram[i] : 0
                print i, count >> "''' + histogram_path + r'''"
            }
        }'''
    subprocess.run(["awk", "-F,", awk, sizes_csv], check=True)


def generate_ivs_reference(name, wl, trace_path, work_dir):
    p = ivs_params(wl)
    os.makedirs(work_dir, exist_ok=True)
    sizes_bin = f"{work_dir}/ivs_sizes.bin"
    for f in (trace_path, sizes_bin):
        if os.path.exists(f):
            os.remove(f)
    cost = GenCost()
    phases = []

    def ycsb(phase, extra, load=False):
        cmd = ["java", "-cp", IVS_CP, "site.ycsb.Client", "-db", "site.ycsb.db.IVSFileClient",
               "-P", IVS_WORKLOAD_FILE,
               "-p", f"file.output={trace_path}", "-p", f"ivs.sizes={sizes_bin}",
               "-p", f"recordcount={p['recordcount']}"]
        for k, v in extra.items():
            cmd += ["-p", f"{k}={v}"]
        cmd.append("-load" if load else "-t")
        before = count_lines(trace_path) if os.path.exists(trace_path) else 0
        cost.add(*timed_run(cmd, cwd=IVS_DIR, stdout_path=f"{work_dir}/ycsb_{phase}.out"))
        phases.append({"phase": phase, "first_line": before, "lines": count_lines(trace_path) - before})

    ycsb("load", {}, load=True)
    for r in range(1, p["extend_phases"] + 1):
        # Extend phase parameters of experiment_mongodb.sh
        ycsb(f"extend{r}", {
            "extendproportion": 1, "readproportion": 0, "updateproportion": 0,
            "scanproportion": 0, "insertproportion": 0, "readmodifywriteproportion": 0,
            "requestdistribution": p["distribution"], "operationcount": p["extendoperationcount"],
        })
        if r > p["run_phases"]:
            break  # experiment_mongodb.sh: `if [ $iteration -eq 5 ]; then exit`
        # Value size of every record after the extend phase -> histogram (get_key_sizes)
        sizes_csv = f"{work_dir}/key_sizes_round{r}.csv"
        hist = f"{work_dir}/histogram_round{r}.txt"
        cost.add(*timed_run(["java", "-cp", IVS_CP, "site.ycsb.db.IVSFileClient", sizes_bin, sizes_csv]))
        if os.path.exists(hist):
            os.remove(hist)
        ivs_histogram(sizes_csv, hist)
        os.remove(sizes_csv)
        # Run phase parameters of experiment_mongodb.sh
        ycsb(f"run{r}", {
            "extendproportion": 0, "readproportion": 0.5, "updateproportion": 0.5,
            "scanproportion": 0, "insertproportion": 0, "readmodifywriteproportion": 0,
            "requestdistribution": p["distribution"], "operationcount": p["operationcount"],
            "fieldlengthdistribution": "histogram", "fieldlengthhistogram": hist,
        })
    os.remove(sizes_bin)
    return cost, phases, p


def generate_ts_reference(name, wl, trace_path, work_dir):
    os.makedirs(work_dir, exist_ok=True)
    if os.path.exists(trace_path):
        os.remove(trace_path)
    cost = GenCost()
    phases = []
    # predefinedtagstoragefile hands the tag values used by the load phase to the run phase
    for phase, flag in (("load", "-load"), ("run", "-t")):
        before = count_lines(trace_path) if os.path.exists(trace_path) else 0
        cmd = ["java", "-cp", TS_CP, "com.yahoo.ycsb.Client", "-db", "com.yahoo.ycsb.db.TSFileClient",
               "-P", wl["workload_file"], "-p", f"file.output={trace_path}", flag]
        cost.add(*timed_run(cmd, cwd=work_dir, stdout_path=f"{work_dir}/ycsb_{phase}.out"))
        phases.append({"phase": phase, "first_line": before, "lines": count_lines(trace_path) - before})
    return cost, phases, read_props(wl["workload_file"])


# =============================================================================
#  X-BENCH SPECS
# =============================================================================
SPEC_DIRS = {"ivs": f"{ROOT_DIR}/example-specs/ycsb-ivs", "ts": f"{ROOT_DIR}/example-specs/ycsb-ts"}

YCSB_KEY = {"segmented": {"separator": "", "segments": [
    "usertable:user", {"uniform": {"len": 19, "character_set": "numeric"}}]}}
# YCSB CoreWorkload's zipfian request distribution (scrambled, ZipfianGenerator ranks over 10^10 items)
YCSB_SCRAMBLED_ZIPFIAN = {"scramble": True, "ycsb_zipfian": True, "zipf": {"s": 0.99, "n": 10000000000}}
UNIFORM_SELECTION = {"uniform": {"min": 0, "max": 1}}


def ycsb_map_len(fields, field_len):
    """Length of FileClient's "{field0=v, field1=v, ...}" rendering of a record."""
    return 2 + sum(len(f"field{i}=") + field_len for i in range(fields)) + 2 * (fields - 1)


def histogram_generator_pmf(histogram_path):
    """Exact output distribution of YCSB's HistogramGenerator.nextValue() for a histogram file:
    number = nextInt(area); for i < B-1: number -= c_i, return (i+1)*bs once number <= 0;
    otherwise return (B-1)*bs."""
    with open(histogram_path) as f:
        bs = int(f.readline().split("\t")[1])
        counts = [int(line.split("\t")[1]) for line in f if line.strip()]
    area = sum(counts)
    pmf, cum = {}, 0
    for i in range(len(counts) - 1):
        lo = cum
        cum += counts[i]
        # number in [0, area) returns at step i iff lo < number <= cum (number <= 0 at i == 0)
        p = (cum - lo + (1 if i == 0 else 0)) / area
        if p > 0:
            pmf[(i + 1) * bs] = pmf.get((i + 1) * bs, 0) + p
    rest = 1 - sum(pmf.values())
    if rest > 1e-12:
        pmf[(len(counts) - 1) * bs] = pmf.get((len(counts) - 1) * bs, 0) + rest
    return pmf


def ivs_spec(p, run_field_len_pmfs):
    """YCSB-IVS as X-Bench: one section, so every phase shares the valid keys.
    The extend operation (read the field, write back orig + incr) is an append-merge whose operand is
    the one-field map FileClient records; run-phase updates draw their field length from the
    distribution HistogramGenerator produces for that round."""
    sel = UNIFORM_SELECTION if p["distribution"] == "uniform" else YCSB_SCRAMBLED_ZIPFIAN
    groups = [{
        "name": "load",
        "inserts": {"op_count": p["recordcount"], "key": YCSB_KEY,
                    "val": {"uniform": {"len": ycsb_map_len(10, p["fieldlength"])}}},
    }]
    for r in range(1, p["extend_phases"] + 1):
        groups.append({
            "name": f"extend{r}",
            "merges": {"op_count": p["extendoperationcount"], "selection": sel,
                       "val": {"uniform": {"len": ycsb_map_len(1, p["extendfieldlength"])}}},
        })
        if r > p["run_phases"]:
            break
        pmf = run_field_len_pmfs[r - 1]
        if len(pmf) == 1:
            val = {"uniform": {"len": ycsb_map_len(1, next(iter(pmf)))}}
        else:
            val = {"weighted": [{"weight": w, "value": {"uniform": {"len": ycsb_map_len(1, L)}}}
                                for L, w in sorted(pmf.items())]}
        groups.append({
            "name": f"run{r}",
            "point_queries": {"op_count": p["operationcount"] // 2, "selection": sel},
            "updates": {"op_count": p["operationcount"] - p["operationcount"] // 2,
                        "selection": sel, "val": val},
        })
    return {"sections": [{"groups": groups}]}


def ts_mean_value_len(trace):
    """Mean length, rounded, of the measurements (Java's Double.toString) in a YCSB-TS trace."""
    n = total = 0
    with open(trace, "rb") as f:
        for line in f:
            if line[:2] == b"I ":
                total += len(line) - line.rindex(b" ") - 2
                n += 1
    return round(total / n)


def ts_spec(name, props, value_len):
    """YCSB-TS as X-Bench, in the key layout of TSFileClient: metric:timestamp:tag=value.
    Kept to what determines the database's work: timestamps are YCSB-TS's own (consecutive from
    insertstart while the load fills the time domain, randomfloorfilling, otherwise uniform over
    [insertstart, insertend)); the tag value is one of predefinedtagvalueamount random strings of
    tagvaluelength characters, picked uniformly, a new set per phase like YCSB-TS (predefinedtagvalues);
    the measurement is a numeric string of the mean length YCSB-TS writes; every time-range query
    (scan/avg/count/sum) is a window of uniform length in [minscanlength, maxscanlength] that ends by
    insertend."""
    insertstart, insertend = int(props["insertstart"]), int(props["insertend"])
    recordcount, opcount = int(props["recordcount"]), int(props["operationcount"])
    prefix = f"{props['metric']}:"

    # an unseeded hot_range has its own random strings, so load and run get different sets
    def point_key(ts):
        tag_value = {"hot_range": {"len": int(props["tagvaluelength"]),
                                   "amount": int(props["predefinedtagvalueamount"]), "probability": 1.0}}
        return {"segmented": {"separator": "", "segments": [
            prefix, ts, f":{props['tagprefix']}0=", tag_value]}}

    uniform_ts = {"number": {"value": {"uniform": {"min": insertstart, "max": insertend}}, "width": 13}}
    floor_filling = props.get("randomfloorfilling", "false") == "true" or props.get("randomamountperts") != "tsrandom"
    if floor_filling and recordcount <= insertend - insertstart:
        load_ts = {"counter": {"start": insertstart, "width": 13}}
    else:
        load_ts = uniform_ts
    val = {"uniform": {"len": value_len, "character_set": "numeric"}}

    weights = {op: int(props.get(f"{op}proportion", 0)) for op in ("read", "insert", "scan", "avg", "count", "sum")}
    total = sum(weights.values())
    range_ops = round(opcount * (weights["scan"] + weights["avg"] + weights["count"] + weights["sum"]) / total)
    window = {
        "op_count": range_ops,
        "key": {"segmented": {"separator": "", "segments": [prefix, uniform_ts]}},
        "span": {"uniform": {"min": int(props["minscanlength"]), "max": int(props["maxscanlength"])}},
        "span_max_end": insertend,
    }
    return {"sections": [{"groups": [
        {"name": "load", "inserts": {"op_count": recordcount, "key": point_key(load_ts), "val": val}},
        {"name": "run", "inserts": {"op_count": opcount - range_ops, "key": point_key(uniform_ts), "val": val},
         "untracked_range_queries": window},
    ]}]}


def write_spec(name, wl, spec):
    os.makedirs(SPEC_DIRS[wl["tool"]], exist_ok=True)
    path = f"{SPEC_DIRS[wl['tool']]}/{name.split('-', 1)[1]}.spec.json"
    with open(path, "w") as f:
        json.dump({"$schema": "../../workload_schema.json", **spec}, f, indent=2)
        f.write("\n")
    return path


def generate_xbench(spec_path, trace_path):
    if os.path.exists(trace_path):
        os.remove(trace_path)
    # snapshot of the spec the trace was generated from, next to the trace
    shutil.copy2(spec_path, os.path.join(os.path.dirname(trace_path), "xbench.spec.json"))
    cost = GenCost()
    cost.add(*timed_run([TECTONIC_CLI, "generate", "-w", spec_path, "-o", trace_path]))
    return cost


# =============================================================================
#  STAGE: GENERATE
# =============================================================================
GEN_PATH = f"{RESULTS_DIR}/generation.json"


def regenerate_xbench(name, gen):
    """Rewrites the spec and regenerates only the X-Bench trace; the derivative's traces stay fixed.
    Analysis and execution results of the workload are invalidated."""
    rec, wl = gen[name], WORKLOADS[name]
    banner(f"REGENERATE X-Bench {name}")
    if wl["tool"] == "ivs":
        pmfs = [{int(k): v for k, v in pmf.items()} for pmf in rec["xbench_update_field_len_pmfs"]]
        spec = ivs_spec(rec["params"], pmfs)
    else:
        rec.pop("xbench_value_len_pmf", None)
        rec["xbench_value_len"] = ts_mean_value_len(rec["ycsb_derivative_rerun"]["trace"])
        spec = ts_spec(name, rec["params"], rec["xbench_value_len"])
    spec_path = write_spec(name, wl, spec)
    xb_trace = rec["xbench"]["trace"]
    xcost = generate_xbench(spec_path, xb_trace)
    rec["xbench"] = {"trace": xb_trace, "spec": spec_path, "cost": xcost.as_dict(),
                     "lines": count_lines(xb_trace), "bytes": os.path.getsize(xb_trace),
                     "sha256": sha256_file(xb_trace)}
    log(f"  x-bench  : {rec['xbench']['lines']:,} lines, {rec['xbench']['cost']}")
    write_json_atomic(GEN_PATH, gen)
    analysis = load_json(ANALYSIS_PATH, {})
    analysis.pop(name, None)
    write_json_atomic(ANALYSIS_PATH, analysis)
    results = load_json(EXEC_PATH, {})
    if name in results:
        results[name]["xbench"] = []
        write_json_atomic(EXEC_PATH, results)


def stage_generate(args):
    gen = load_json(GEN_PATH, {})
    for name in args.workloads:
        wl = WORKLOADS[name]
        if name in gen and args.regen_xbench:
            regenerate_xbench(name, gen)
            continue
        if name in gen and not args.force:
            log(f"  [generate] {name}: done, skipping")
            continue
        banner(f"GENERATE {name}")
        wdir = f"{WORKLOAD_DIR}/{name}"
        os.makedirs(wdir, exist_ok=True)
        rec = {"workload": wl}
        gen_ref = generate_ivs_reference if wl["tool"] == "ivs" else generate_ts_reference
        # Two independent runs of the derivative's own generator: "ycsb_derivative" is the trace X-Bench is
        # compared against, "ycsb_derivative_rerun" measures how much two runs of the derivative itself differ.
        for side, trace in (("ycsb_derivative_rerun", f"{wdir}/ycsb-derivative-rerun.txt"), ("ycsb_derivative", f"{wdir}/ycsb-derivative.txt")):
            cost, phases, p = gen_ref(name, wl, trace, f"{wdir}/generation/{side.replace('_', '-')}")
            rec[side] = {"trace": trace, "cost": cost.as_dict(), "phases": phases,
                         "lines": count_lines(trace), "bytes": os.path.getsize(trace),
                         "sha256": sha256_file(trace)}
        rec["params"] = p
        if wl["tool"] == "ivs":
            # Run-phase update sizes come from YCSB-IVS's histogram of the database state, which X-Bench
            # cannot observe: take them from the rerun, never from the trace X-Bench is compared against.
            pmfs = [histogram_generator_pmf(f"{wdir}/generation/ycsb-derivative-rerun/histogram_round{r}.txt")
                    for r in range(1, p["run_phases"] + 1)]
            rec["xbench_update_field_len_pmfs"] = [{str(k): v for k, v in pmf.items()} for pmf in pmfs]
            spec = ivs_spec(p, pmfs)
        else:
            rec["xbench_value_len"] = ts_mean_value_len(rec["ycsb_derivative_rerun"]["trace"])
            spec = ts_spec(name, p, rec["xbench_value_len"])
        xb_trace = f"{wdir}/xbench.txt"
        spec_path = write_spec(name, wl, spec)
        xcost = generate_xbench(spec_path, xb_trace)
        rec["xbench"] = {"trace": xb_trace, "spec": spec_path, "cost": xcost.as_dict(),
                         "lines": count_lines(xb_trace), "bytes": os.path.getsize(xb_trace),
                         "sha256": sha256_file(xb_trace)}
        log(f"  reference : {rec['ycsb_derivative']['lines']:,} lines, {rec['ycsb_derivative']['cost']}")
        log(f"  rerun     : {rec['ycsb_derivative_rerun']['lines']:,} lines, {rec['ycsb_derivative_rerun']['cost']}")
        log(f"  x-bench  : {rec['xbench']['lines']:,} lines, {rec['xbench']['cost']}")
        gen[name] = rec
        write_json_atomic(GEN_PATH, gen)


# =============================================================================
#  DISTRIBUTION COMPARISON (exact, over value -> count maps)
# =============================================================================
def _numeric(counter):
    """value -> count maps lose their numeric keys in a JSON round trip."""
    return {(float(k) if "." in k else int(k)) if isinstance(k, str) else k: v for k, v in counter.items()}


def dist_summary(counter):
    counter = _numeric(counter)
    n = sum(counter.values())
    if n == 0:
        return {"n": 0}
    xs = sorted(counter)
    mean = sum(x * c for x, c in counter.items()) / n
    out = {"n": n, "mean": mean, "min": xs[0], "max": xs[-1]}
    targets = {"p50": 0.5, "p90": 0.9, "p99": 0.99, "p999": 0.999}
    cum, ti = 0, sorted(targets.items(), key=lambda t: t[1])
    for x in xs:
        cum += counter[x]
        while ti and cum >= ti[0][1] * n:
            out[ti[0][0]] = x
            ti.pop(0)
    return out


def compare_dists(a, b):
    """Two-sample Kolmogorov-Smirnov statistic and Wasserstein-1 distance between the empirical
    distributions given as value -> count maps; W1 is also reported relative to the mean of a."""
    a, b = _numeric(a), _numeric(b)
    na, nb = sum(a.values()), sum(b.values())
    if na == 0 or nb == 0:
        return {"ks": None, "w1": None, "w1_rel": None}
    xs = sorted(set(a) | set(b))
    fa = fb = 0.0
    ks = w1 = 0.0
    for i, x in enumerate(xs):
        fa += a.get(x, 0) / na
        fb += b.get(x, 0) / nb
        ks = max(ks, abs(fa - fb))
        if i + 1 < len(xs):
            w1 += abs(fa - fb) * (xs[i + 1] - x)
    mean_a = sum(x * c for x, c in a.items()) / na
    return {"ks": ks, "w1": w1, "w1_rel": w1 / mean_a if mean_a else None}


def counter_from_values(values):
    c = {}
    for v in values:
        c[v] = c.get(v, 0) + 1
    return c


# =============================================================================
#  STAGE: TRACE ANALYSIS
# =============================================================================
ANALYSIS_PATH = f"{RESULTS_DIR}/trace_analysis.json"


def analyze_ivs_trace(path, phases):
    """Op mix per phase, key popularity and written value sizes."""
    bounds = []
    for ph in phases:
        bounds.append((ph["first_line"] + ph["lines"], ph["phase"]))
    per_phase = {ph["phase"]: {} for ph in phases}
    access = {"P": {}, "U": {}, "M": {}}
    vlen = {"I": {}, "U": {}, "M": {}}
    # Temporal locality: key accesses since the previous access to the same key
    last_access, reuse, n_access = {}, {}, 0
    bi = 0
    with open(path, "rb") as f:
        for ln, line in enumerate(f):
            while ln >= bounds[bi][0]:
                bi += 1
            op, rest = line[:1].decode(), line[2:].rstrip(b"\n")
            ph = per_phase[bounds[bi][1]]
            ph[op] = ph.get(op, 0) + 1
            sp = rest.find(b" ")
            key = rest if sp < 0 else rest[:sp]
            if op in access:
                d = access[op]
                d[key] = d.get(key, 0) + 1
                prev = last_access.get(key)
                if prev is not None:
                    dist = n_access - prev
                    reuse[dist] = reuse.get(dist, 0) + 1
                last_access[key] = n_access
                n_access += 1
            if op in vlen and sp >= 0:
                L = len(rest) - sp - 1
                vlen[op][L] = vlen[op].get(L, 0) + 1
    out = {"per_phase_ops": per_phase, "value_len": {op: dist_summary(c) for op, c in vlen.items()},
           "value_len_counter": {op: c for op, c in vlen.items()},
           "reuse_distance": dist_summary(reuse), "reuse_distance_counter": reuse,
           "first_accesses": len(last_access)}
    pop = {}
    for op_set, label in ((("P", "U", "M"), "all"), (("P",), "P"), (("U",), "U"), (("M",), "M")):
        per_key = {}
        for op in op_set:
            for k, c in access[op].items():
                per_key[k] = per_key.get(k, 0) + c
        counts = sorted(per_key.values(), reverse=True)
        total = sum(counts)
        pop[label] = {
            "ops": total, "distinct_keys": len(counts),
            "top1_share": counts[0] / total if total else 0,
            "top10_share": sum(counts[:10]) / total if total else 0,
            "top100_share": sum(counts[:100]) / total if total else 0,
            "top1pct_keys_share": sum(counts[:max(1, len(counts) // 100)]) / total if total else 0,
            # number of keys accessed exactly k times
            "access_count_counter": counter_from_values(counts),
        }
    # Loaded keys the phase never touched count as accessed zero times
    loaded = per_phase[phases[0]["phase"]].get("I", 0)
    for label in pop:
        zeros = loaded - pop[label]["distinct_keys"]
        if zeros > 0:
            pop[label]["access_count_counter"][0] = zeros
    out["popularity"] = pop
    return out


def analyze_ts_trace(path, phases):
    """Op mix, timestamp spread and order of inserts, time-window widths of range queries."""
    bounds = [(ph["first_line"] + ph["lines"], ph["phase"]) for ph in phases]
    per_phase = {ph["phase"]: {} for ph in phases}
    ts = {ph["phase"]: [] for ph in phases}
    windows, window_starts = [], []
    vlen = {}
    ordered = {ph["phase"]: [0, 0] for ph in phases}
    prev = {ph["phase"]: None for ph in phases}
    bi = 0
    with open(path, "rb") as f:
        for ln, line in enumerate(f):
            while ln >= bounds[bi][0]:
                bi += 1
            phase = bounds[bi][1]
            parts = line.rstrip(b"\n").split(b" ")
            op = parts[0].decode()
            per_phase[phase][op] = per_phase[phase].get(op, 0) + 1
            if op == "I":
                key = parts[1]
                t = int(key.split(b":")[1])
                ts[phase].append(t)
                if prev[phase] is not None:
                    ordered[phase][0] += key > prev[phase]
                    ordered[phase][1] += 1
                prev[phase] = key
                L = len(parts[2])
                vlen[L] = vlen.get(L, 0) + 1
            elif op == "S":
                start = int(parts[1].split(b":")[1])
                windows.append(int(parts[2].split(b":")[1]) - start)
                window_starts.append(start)
    out = {"per_phase_ops": per_phase, "value_len": dist_summary(vlen), "value_len_counter": vlen,
           "insert_in_key_order": {ph: (o[0] / o[1] if o[1] else None) for ph, o in ordered.items()}}
    # Insert timestamps as a fraction of the timestamp range of their own phase, at 1e-4 resolution.
    # The absolute range of X-Bench's timestamps differs from YCSB-TS's, so positions are relative.
    out["insert_position_counter"] = {}
    for ph, v in ts.items():
        lo, hi = min(v), max(v)
        out["insert_position_counter"][ph] = counter_from_values(round((t - lo) / (hi - lo), 4) for t in v)
    out["insert_ts_range"] = {ph: [min(v), max(v)] if v else None for ph, v in ts.items()}
    # Where the loaded block lies among the run-phase inserts
    load_hi = max(ts["load"])
    out["run_inserts_before_load_end"] = sum(1 for t in ts["run"] if t <= load_hi) / len(ts["run"])
    lo, hi = min(ts["run"]), max(ts["run"])
    out["range_window_fraction"] = dist_summary(
        counter_from_values(round(w / (hi - lo), 5) for w in windows))
    # Window width in ms, at 1 s resolution
    out["range_window_counter"] = counter_from_values(round(w, -3) for w in windows)
    out["range_window_start_counter"] = counter_from_values(
        round((int(t) - lo) / (hi - lo), 4) for t in window_starts)
    return out


def window_op_counts(path, windows):
    """Operations of each type in every phase window of a trace."""
    counts = [{} for _ in windows]
    ends = [first + lines for _, first, lines in windows]
    wi = 0
    with open(path, "rb") as f:
        for ln, line in enumerate(f):
            while wi < len(ends) and ln >= ends[wi]:
                wi += 1
            if wi == len(ends):
                break
            op = line[:line.index(b" ")].decode()
            counts[wi][op] = counts[wi].get(op, 0) + 1
    return {name: c for (name, _, _), c in zip(windows, counts)}


def compare_op_mix(a, b):
    """Largest and mean absolute difference of the fraction of an operation type in a window."""
    diffs = []
    for w, ca in a.items():
        cb = b.get(w, {})
        na, nb = sum(ca.values()) or 1, sum(cb.values()) or 1
        for op in set(ca) | set(cb):
            diffs.append(abs(ca.get(op, 0) / na - cb.get(op, 0) / nb))
    return {"max_abs_diff": max(diffs), "mean_abs_diff": sum(diffs) / len(diffs), "windows": len(a)}


def stage_analyze(args):
    gen = load_json(GEN_PATH, {})
    analysis = load_json(ANALYSIS_PATH, {})
    for name in args.workloads:
        if name not in gen:
            continue
        if name in analysis and not args.force:
            continue
        banner(f"ANALYZE {name}")
        rec = gen[name]
        phases = rec["ycsb_derivative"]["phases"]
        fn = analyze_ivs_trace if rec["workload"]["tool"] == "ivs" else analyze_ts_trace
        res = {side: fn(rec[side]["trace"], phases) for side in SIDES}
        windows = phase_windows(rec)
        for side in SIDES:
            res[side]["window_ops"] = window_op_counts(rec[side]["trace"], windows)
        cmp = {}
        for other in ("xbench", "ycsb_derivative_rerun"):
            c = cmp.setdefault(other, {})
            ref, oth = res["ycsb_derivative"], res[other]
            c["op_mix_per_window"] = compare_op_mix(ref["window_ops"], oth["window_ops"])
            if rec["workload"]["tool"] == "ivs":
                c["reuse_distance"] = compare_dists(ref["reuse_distance_counter"], oth["reuse_distance_counter"])
                for label in ref["popularity"]:
                    c[f"access_count_per_key[{label}]"] = compare_dists(
                        ref["popularity"][label]["access_count_counter"],
                        oth["popularity"][label]["access_count_counter"])
                for op in ("I", "U", "M"):
                    c[f"value_len[{op}]"] = compare_dists(ref["value_len_counter"][op], oth["value_len_counter"][op])
            else:
                for ph in ref["insert_position_counter"]:
                    c[f"insert_position[{ph}]"] = compare_dists(ref["insert_position_counter"][ph],
                                                                oth["insert_position_counter"][ph])
                c["range_window_width"] = compare_dists(ref["range_window_counter"], oth["range_window_counter"])
                c["range_window_start"] = compare_dists(ref["range_window_start_counter"],
                                                        oth["range_window_start_counter"])
                c["value_len"] = compare_dists(ref["value_len_counter"], oth["value_len_counter"])
        res["comparison"] = cmp
        for k, v in cmp["xbench"].items():
            v2 = cmp["ycsb_derivative_rerun"][k]
            if "ks" in v:
                log(f"  {k:32s} vs X-Bench KS={v['ks']:.4f} W1/mean={v['w1_rel'] or 0:.4f} | "
                    f"vs YCSB rerun KS={v2['ks']:.4f} W1/mean={v2['w1_rel'] or 0:.4f}")
            else:
                log(f"  {k:32s} vs X-Bench max|diff|={v['max_abs_diff']:.4f} | "
                    f"vs YCSB rerun max|diff|={v2['max_abs_diff']:.4f}")
        analysis[name] = res
        write_json_atomic(ANALYSIS_PATH, analysis)


# =============================================================================
#  STAGE: TRACE-SPREAD (spread of trace-level distances over independent draws)
# =============================================================================
TRACE_SPREAD_PATH = f"{RESULTS_DIR}/trace_distance_spread.json"
TRACE_SPREAD_DRAWS = 5


def trace_distances(rec, ref, path):
    """Trace-level distances of the trace at path to the analyzed reference trace."""
    tool = rec["workload"]["tool"]
    phases = rec["ycsb_derivative"]["phases"]
    a = (analyze_ivs_trace if tool == "ivs" else analyze_ts_trace)(path, phases)
    a["window_ops"] = window_op_counts(path, phase_windows(rec))
    d = {"op_mix_per_window": compare_op_mix(ref["window_ops"], a["window_ops"])["max_abs_diff"]}
    if tool == "ivs":
        for label in ref["popularity"]:
            d[f"access_count_per_key[{label}]"] = compare_dists(
                ref["popularity"][label]["access_count_counter"], a["popularity"][label]["access_count_counter"])["ks"]
        d["reuse_distance"] = compare_dists(ref["reuse_distance_counter"], a["reuse_distance_counter"])["ks"]
        for op in ("U",):
            d[f"value_len[{op}]"] = compare_dists(ref["value_len_counter"][op], a["value_len_counter"][op])["ks"]
    else:
        for ph in ref["insert_position_counter"]:
            d[f"insert_position[{ph}]"] = compare_dists(ref["insert_position_counter"][ph],
                                                        a["insert_position_counter"][ph])["ks"]
        d["range_window_width"] = compare_dists(ref["range_window_counter"], a["range_window_counter"])["ks"]
        d["range_window_start"] = compare_dists(ref["range_window_start_counter"],
                                                a["range_window_start_counter"])["ks"]
        d["value_len"] = compare_dists(ref["value_len_counter"], a["value_len_counter"])["ks"]
    return d


def stage_trace_spread(args):
    """Draws TRACE_SPREAD_DRAWS more traces from the derivative's generator and from X-Bench and reports, per
    property, the range of their distances to the reference trace: X-Bench mimics the derivative as
    closely as the derivative mimics itself when the two ranges coincide."""
    gen = load_json(GEN_PATH, {})
    analysis = load_json(ANALYSIS_PATH, {})
    spread = load_json(TRACE_SPREAD_PATH, {})
    for name in args.workloads:
        if name not in gen or name not in analysis or (name in spread and not args.force):
            continue
        banner(f"TRACE-SPREAD {name}")
        rec, wl = gen[name], WORKLOADS[name]
        ref = analysis[name]["ycsb_derivative"]
        fdir = f"{WORKLOAD_DIR}/{name}/trace_spread_tmp"
        os.makedirs(fdir, exist_ok=True)
        draws = {"ycsb_derivative": [], "xbench": []}
        for i in range(TRACE_SPREAD_DRAWS):
            path = f"{fdir}/ycsb_derivative{i}.txt"
            if wl["tool"] == "ivs":
                generate_ivs_reference(name, wl, path, f"{fdir}/ycsb_derivative{i}")
            else:
                generate_ts_reference(name, wl, path, f"{fdir}/ycsb_derivative{i}")
            draws["ycsb_derivative"].append(trace_distances(rec, ref, path))
            os.remove(path)
            path = f"{fdir}/xbench{i}.txt"
            generate_xbench(rec["xbench"]["spec"], path)
            draws["xbench"].append(trace_distances(rec, ref, path))
            os.remove(path)
        shutil.rmtree(fdir)
        out = {}
        for side, ds in draws.items():
            out[side] = {k: {"min": min(d[k] for d in ds), "median": statistics.median(d[k] for d in ds),
                             "max": max(d[k] for d in ds)} for k in ds[0]}
        spread[name] = out
        for k in out["xbench"]:
            x, r = out["xbench"][k], out["ycsb_derivative"][k]
            log(f"  {k:32s} X-Bench {x['min']:.4f}..{x['max']:.4f} | YCSB {r['min']:.4f}..{r['max']:.4f}")
        write_json_atomic(TRACE_SPREAD_PATH, spread)


# =============================================================================
#  STAGE: EXECUTE (RocksDB, TRIALS per trace)
# =============================================================================
EXEC_PATH = f"{RESULTS_DIR}/execution.json"


def drop_caches():
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)  # fail loudly: a trial on a warm page cache is not comparable


STAT_LINE = re.compile(r'\[([^\]]+)\]\s+([^:]+):\s+([\d\.]+)')


def parse_stat_block(text):
    ops, overall = {}, {}
    for line in text.splitlines():
        m = STAT_LINE.match(line.strip())
        if m:
            op, metric, val = m.groups()
            (overall if op == "Overall" else ops.setdefault(op, {}))[metric.strip()] = float(val)
    return ops, overall


def parse_execute_output(stdout):
    # "[[***Stats for <window>***]]" blocks precede the final "[[***Overall Stats***]]" block
    overall_at = stdout.find("[[***Overall Stats***]]")
    ops, overall = parse_stat_block(stdout[overall_at:] if overall_at >= 0 else stdout)
    windows = {}
    for m in re.finditer(r"\[\[\*\*\*Stats for (.+?)\*\*\*\]\]\n(.*?)(?=\n\n|\[\[\*\*\*)",
                         stdout[:overall_at] if overall_at >= 0 else stdout, re.S):
        w_ops, w_overall = parse_stat_block(m.group(2))
        windows[m.group(1)] = {"operations": w_ops, "overall": w_overall}
    read_stats = {}
    for label in ("Point query hits", "Point query bytes returned", "Append bytes read",
                  "Range query keys returned", "Range query bytes returned"):
        m = re.search(rf"^{label}: (\d+)$", stdout, re.M)
        if m:
            read_stats[label] = int(m.group(1))
    rocks = {}
    unit = {"GB": 1024.0, "MB": 1.0, "KB": 1 / 1024.0, "B": 1 / (1024 * 1024)}
    m = re.search(r"Cumulative writes:.*?ingest:\s*([\d\.]+)\s*(GB|MB|KB|B)", stdout)
    if m:
        rocks["Cumulative writes ingest (MB)"] = float(m.group(1)) * unit[m.group(2)]
    m = re.search(r"Cumulative WAL:.*?written:\s*([\d\.]+)\s*(GB|MB|KB|B)", stdout)
    if m:
        rocks["Cumulative WAL written (MB)"] = float(m.group(1)) * unit[m.group(2)]
    m = re.search(r"Flush\(GB\):\s*cumulative\s+([\d\.]+)", stdout)
    if m:
        rocks["Flush(GB) cumulative"] = float(m.group(1))
    m = re.search(r"Cumulative compaction:\s*([\d\.]+)\s*GB write.*?([\d\.]+)\s*GB read.*?([\d\.]+)\s*seconds",
                  stdout)
    if m:
        rocks["Cumulative compaction GB write"] = float(m.group(1))
        rocks["Cumulative compaction GB read"] = float(m.group(2))
        rocks["Cumulative compaction seconds"] = float(m.group(3))
    return ops, overall, read_stats, rocks, windows


def parse_rocksdb_statistics(stdout):
    """The tickers of RocksDB's statistics ("rocksdb.<name> COUNT : <n>"), printed by the executor with
    `-c statistics=1` at the end of the run."""
    at = stdout.find("=== RocksDB Statistics ===")
    if at < 0:
        return {}
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"^(rocksdb\.[\w.\-]+) COUNT : (\d+)$", stdout[at:], re.M)}


# Fig. 12 (C) of the paper: bytes RocksDB reads and writes, from its statistics. The paper's read is
# rocksdb.bytes.read (Get) + rocksdb.compact.read.bytes; YCSB-A has no scans, so iterator reads
# (rocksdb.db.iter.bytes.read) are added for the range queries of YCSB-TS.
FIG12_READ_TICKERS = ("rocksdb.bytes.read", "rocksdb.db.iter.bytes.read", "rocksdb.compact.read.bytes")
FIG12_WRITE_TICKERS = ("rocksdb.wal.bytes", "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes")
GIB = 1024 ** 3


TS_RUN_WINDOWS = 10  # the single YCSB-TS run phase is reported in windows of equal operation counts


def phase_windows(rec):
    """(name, first line, line count) of every reported phase window, the same for all traces of a
    workload: the YCSB-IVS phases, or YCSB-TS's load and its run phase split into equal windows."""
    windows = []
    for ph in rec["ycsb_derivative"]["phases"]:
        if rec["workload"]["tool"] == "ts" and ph["phase"] == "run":
            n = ph["lines"]
            for w in range(TS_RUN_WINDOWS):
                lo, hi = n * w // TS_RUN_WINDOWS, n * (w + 1) // TS_RUN_WINDOWS
                windows.append((f"run.w{w + 1}", ph["first_line"] + lo, hi - lo))
        else:
            windows.append((ph["phase"], ph["first_line"], ph["lines"]))
    return windows


def marked_trace(rec, side):
    """The trace with executor stat markers around every phase window, so that latency percentiles
    are reported per window. Written once next to the trace."""
    src = rec[side]["trace"]
    dst = src[:-len(".txt")] + ".marked.txt"
    if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
        return dst
    windows = phase_windows(rec)
    ends = {first + lines: name for name, first, lines in windows}
    starts = {first for _, first, _ in windows}
    with open(src, "rb") as fin, open(dst + ".tmp", "wb") as fout:
        for ln, line in enumerate(fin):
            if ln in ends:
                fout.write(f"FE G {ends[ln]}\n".encode())
            if ln in starts:
                fout.write(b"FS G\n")
            fout.write(line)
        if ln + 1 in ends:
            fout.write(f"FE G {ends[ln + 1]}\n".encode())
    os.replace(dst + ".tmp", dst)
    return dst


IOSTAT_DEVICE = "sda"  # holds ROCKSDB_DIR; Fig. 12 (A) of the paper plots its MB/s over time
SHM_TRACE = "/dev/shm/r1o7_trace.txt"  # the executor reads its trace from RAM, so the device sees only RocksDB


class Iostat:
    """`iostat -m -x -d -y <device> 1` in the background: read and write MB/s of every second."""

    def __init__(self, device):
        self.samples = []
        self.proc = subprocess.Popen(["iostat", "-m", "-x", "-d", "-y", device, "1"], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True)
        self.thread = threading.Thread(target=self._read, args=(device,), daemon=True)
        self.thread.start()

    def _read(self, device):
        cols = None
        for line in self.proc.stdout:
            parts = line.split()
            if parts and parts[0] == "Device":
                cols = parts
            elif cols and parts and parts[0] == device:
                self.samples.append((float(parts[cols.index("rMB/s")]), float(parts[cols.index("wMB/s")])))

    def stop(self):
        self.proc.terminate()
        self.proc.wait()
        self.thread.join(timeout=5)
        return self.samples


def execute_trial(name, side, trace, trial, merge_mode, state_dir=None):
    if os.path.exists(ROCKSDB_DIR):
        shutil.rmtree(ROCKSDB_DIR)
    os.makedirs(ROCKSDB_DIR)
    out_dir = f"{state_dir or f'{DBSTATE_DIR}/{name}'}/{side}/trial{trial}"
    os.makedirs(out_dir, exist_ok=True)
    scan_log = f"{out_dir}/range_query_keys.txt"
    shutil.copyfile(trace, SHM_TRACE)
    drop_caches()
    cmd = [TECTONIC_CLI, "execute", "-i", SHM_TRACE, "-d", "rocksdb", "-p", ROCKSDB_DIR, "-t", "1",
           "--merge-mode", merge_mode, "-c", f"scan_log={scan_log};statistics=1"]
    log(f"  $ {' '.join(cmd)}   (trace: {trace})")
    iostat = Iostat(IOSTAT_DEVICE)
    t0 = time.perf_counter()
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    wall = time.perf_counter() - t0
    subprocess.run(["sync"], check=True)  # writes still in the page cache belong to this run
    time.sleep(2)
    io_samples = iostat.stop()
    os.remove(SHM_TRACE)
    with open(f"{out_dir}/iostat.csv", "w") as f:
        f.write("second,read_MBps,write_MBps\n")
        for t, (rd, wr) in enumerate(io_samples):
            f.write(f"{t},{rd},{wr}\n")
    device_io = {"device": IOSTAT_DEVICE,
                 "read_MBps": [rd for rd, _ in io_samples], "write_MBps": [wr for _, wr in io_samples]}
    with open(f"{out_dir}/cli_raw.log", "w") as f:
        f.write(r.stdout)
    if r.returncode != 0 or "[ERROR]" in r.stderr:
        log(r.stderr[-3000:])
        fail(f"execute failed for {name} {side} trial {trial}")
    ops, overall, read_stats, rocks, windows = parse_execute_output(r.stdout)
    tickers = parse_rocksdb_statistics(r.stdout)
    if not tickers:
        fail(f"no RocksDB statistics in the output of {name} {side} trial {trial}")
    for lf in os.listdir(ROCKSDB_DIR):
        if lf == "LOG":
            shutil.copy2(f"{ROCKSDB_DIR}/{lf}", f"{out_dir}/rocksdb_LOG.txt")
    sst = [f for f in os.listdir(ROCKSDB_DIR) if f.endswith(".sst")]
    rocks["SST total size (MB)"] = sum(os.path.getsize(f"{ROCKSDB_DIR}/{f}") for f in sst) / (1024 * 1024)
    rocks["SST file count"] = len(sst)

    # End-of-run state: value size of every live key
    sizes = {}
    p = subprocess.run([VALUE_SIZES_BIN, ROCKSDB_DIR], stdout=subprocess.PIPE, check=True)
    for line in p.stdout.splitlines():
        L = int(line.rsplit(b",", 1)[1])
        sizes[L] = sizes.get(L, 0) + 1
    with open(scan_log) as f:
        scans = counter_from_values(int(x) for x in f)
    if trial > 1:
        os.remove(scan_log)
    return {
        "trial": trial, "wall_time_s": wall,
        "end_to_end_time_s": overall.get("End to End Time"),
        "overall": overall, "operations": ops, "windows": windows, "read_stats": read_stats, "rocksdb": rocks,
        "device_io": device_io, "rocksdb_statistics": tickers,
        "final_value_size_counter": sizes, "final_value_size": dist_summary(sizes),
        "range_query_keys_counter": scans, "range_query_keys": dist_summary(scans),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def stage_execute(args):
    gen = load_json(GEN_PATH, {})
    results = load_json(EXEC_PATH, {})
    for name in args.workloads:
        if name not in gen:
            continue
        # IVS's extend appends to the stored value; YCSB-TS has no merges
        merge_mode = "append"
        for side in SIDES:
            done = results.setdefault(name, {}).setdefault(side, [])
            if args.force:
                done.clear()
            # --redo side:trial replaces that trial, e.g. one disturbed by other I/O on the device
            for trial in sorted(int(r.split(":")[1]) for r in args.redo if r.split(":")[0] == side):
                banner(f"EXECUTE {name} | {side} | redo trial {trial}/{args.trials}")
                done[trial - 1] = execute_trial(name, side, marked_trace(gen[name], side), trial, merge_mode)
                write_json_atomic(EXEC_PATH, results)
            for trial in range(len(done) + 1, args.trials + 1):
                banner(f"EXECUTE {name} | {side} | trial {trial}/{args.trials}")
                rec = execute_trial(name, side, marked_trace(gen[name], side), trial, merge_mode)
                done.append(rec)
                write_json_atomic(EXEC_PATH, results)
                log(f"  -> {rec['end_to_end_time_s']:.3f}s  read_stats={rec['read_stats']}")
    if os.path.exists(ROCKSDB_DIR):
        shutil.rmtree(ROCKSDB_DIR)


# The execute stage runs the trials of one trace after another: YCSB, then the YCSB rerun, then X-Bench.
# The order check runs one trial of every trace per round and rotates the order each round, so every trace
# runs first, second and third; a difference that comes from the order shows up as one by position.
ORDER_PATH = f"{RESULTS_DIR}/order_check.json"
ORDER_STATE_DIR = f"{DBSTATE_DIR}/order_check"


def stage_order_check(args):
    gen = load_json(GEN_PATH, {})
    results = load_json(ORDER_PATH, {})
    for name in args.workloads:
        if name not in gen:
            continue
        done = results.setdefault(name, {})
        if args.force:
            done.clear()
        for rnd in range(1, args.trials + 1):
            order = SIDES[(rnd - 1) % len(SIDES):] + SIDES[:(rnd - 1) % len(SIDES)]
            for pos, side in enumerate(order, 1):
                if any(r["round"] == rnd for r in done.get(side, [])):
                    continue
                banner(f"ORDER CHECK {name} | round {rnd}/{args.trials} | {pos}. {side}")
                rec = execute_trial(name, side, marked_trace(gen[name], side), rnd, "append",
                                    state_dir=f"{ORDER_STATE_DIR}/{name}")
                rec.update({"round": rnd, "position": pos})
                done.setdefault(side, []).append(rec)
                write_json_atomic(ORDER_PATH, results)
                log(f"  -> {rec['end_to_end_time_s']:.3f}s")
    if os.path.exists(ROCKSDB_DIR):
        shutil.rmtree(ROCKSDB_DIR)


# =============================================================================
#  STAGE: REPORT
# =============================================================================
SUMMARY_PATH = f"{RESULTS_DIR}/summary.json"
REPORT_PATH = f"{OUT_DIR}/report.md"


def mean_sd(values):
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)


def pct_diff(ref, xb):
    if ref in (None, 0) or xb is None:
        return None
    return (xb - ref) / ref * 100


READ_METRICS = ("Point query bytes returned", "Append bytes read", "Range query keys returned",
                "Range query bytes returned")
WRITE_METRICS = ("Cumulative writes ingest (MB)", "Cumulative WAL written (MB)", "Flush(GB) cumulative",
                 "Cumulative compaction GB write", "Cumulative compaction GB read", "SST total size (MB)")
LATENCY_METRICS = {"p50": "50th Percentile Latency", "p99": "99th Percentile Latency", "mean": "Average Latency"}
OTHERS = (("xbench", "X-Bench"), ("ycsb_derivative_rerun", "YCSB rerun"))
FIG12_PERCENTILES = (("p0", "Minimum Latency"), ("p25", "25th Percentile Latency"), ("p50", "50th Percentile Latency"),
                     ("p75", "75th Percentile Latency"), ("p99", "99th Percentile Latency"))


def window_latencies(trials):
    """Mean over trials of the latency percentiles of every operation type in every phase window."""
    out = {}
    for w in trials[0].get("windows", {}):
        for op in trials[0]["windows"][w]["operations"]:
            for label, metric in LATENCY_METRICS.items():
                vals = [t["windows"][w]["operations"].get(op, {}).get(metric) for t in trials]
                vals = [v for v in vals if v is not None]
                if vals:
                    out.setdefault(w, {}).setdefault(op, {})[label] = statistics.mean(vals)
    return out


def stage_report(args):
    gen = load_json(GEN_PATH, {})
    analysis = load_json(ANALYSIS_PATH, {})
    results = load_json(EXEC_PATH, {})
    summary = {}
    for name in WORKLOADS:
        if name not in results or not all(results[name].get(side) for side in SIDES):
            continue
        s = {}
        for side in SIDES:
            trials = results[name][side]
            t = {"trials": len(trials),
                 "end_to_end_time_s": mean_sd([r["end_to_end_time_s"] for r in trials]),
                 "throughput_ops_s": mean_sd([r["overall"].get("Throughput (using start and end time)")
                                              for r in trials])}
            for k in READ_METRICS:
                t[k] = mean_sd([r["read_stats"].get(k) for r in trials])
            for k in WRITE_METRICS:
                t[k] = mean_sd([r["rocksdb"].get(k) for r in trials])
            t["final_value_size"] = trials[0]["final_value_size"]
            t["range_query_keys"] = trials[0]["range_query_keys"]
            t["window_latency"] = window_latencies(trials)
            # Fig. 12 of the paper: bytes from RocksDB's statistics, per-operation latency of the whole run
            t["tickers"] = {k: mean_sd([r["rocksdb_statistics"][k] for r in trials])
                            for k in FIG12_READ_TICKERS + FIG12_WRITE_TICKERS}
            t["rocksdb_read_GB"] = mean_sd([sum(r["rocksdb_statistics"][k] for k in FIG12_READ_TICKERS) / GIB
                                            for r in trials])
            t["rocksdb_write_GB"] = mean_sd([sum(r["rocksdb_statistics"][k] for k in FIG12_WRITE_TICKERS) / GIB
                                             for r in trials])
            t["run_latency_us"] = {
                op: {label: statistics.mean(r["operations"][op][metric] for r in trials)
                     for label, metric in FIG12_PERCENTILES}
                for op in trials[0]["operations"]}
            s[side] = t
        ref = results[name]["ycsb_derivative"]
        for other, _ in OTHERS:
            oth = results[name][other]
            c = {"throughput_diff_pct": pct_diff(s["ycsb_derivative"]["throughput_ops_s"][0], s[other]["throughput_ops_s"][0]),
                 "time_diff_pct": pct_diff(s["ycsb_derivative"]["end_to_end_time_s"][0], s[other]["end_to_end_time_s"][0]),
                 "final_value_size": compare_dists(ref[0]["final_value_size_counter"], oth[0]["final_value_size_counter"]),
                 "range_query_keys": compare_dists(ref[0]["range_query_keys_counter"], oth[0]["range_query_keys_counter"])}
            # Relative difference of every latency percentile of every operation in every window
            lat = {label: [] for label in LATENCY_METRICS}
            for w, ops in s["ycsb_derivative"]["window_latency"].items():
                for op, m in ops.items():
                    for label, v in m.items():
                        o = s[other]["window_latency"].get(w, {}).get(op, {}).get(label)
                        if v and o is not None:
                            lat[label].append(abs(o - v) / v * 100)
            c["window_latency_abs_diff_pct"] = {
                label: {"median": statistics.median(v), "max": max(v), "n": len(v)} for label, v in lat.items() if v}
            s[f"{other}_cmp"] = c
        if name in analysis:
            s["trace_comparison"] = analysis[name]["comparison"]
        summary[name] = s
    write_json_atomic(SUMMARY_PATH, summary)
    write_report(summary, analysis)
    log(f"  report: {REPORT_PATH}")


def fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, (list, tuple)):
        m, sd = v
        if m is None:
            return "-"
        return f"{m:,.{nd}f} ± {sd:,.{nd}f}"
    return f"{v:,.{nd}f}"


def write_report(summary, analysis):
    L = ["# X-Bench vs. YCSB-IVS and YCSB-TS (R1.O7)", "",
         f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')} by `run_scripts/run_ycsb_derivatives_fidelity.py`.", "",
         "For every workload, the trace of the derivative's own Java generator (*YCSB derivative*) is compared with "
         "X-Bench's trace and with a second, independent run of the derivative's generator (*YCSB rerun*), which "
         "shows how much the derivative differs from itself. Execution: `tectonic-cli execute` on RocksDB, "
         "1 client thread, `--merge-mode append`, fresh database and dropped page cache per trial, "
         f"{TRIALS} trials per trace; mean ± stdev.", ""]

    L += ["## 1. Workload properties: statistical distance to the YCSB derivative's trace", "",
          "KS: two-sample Kolmogorov-Smirnov statistic; W1/mean: Wasserstein-1 distance relative to the mean; "
          "op mix: largest absolute difference of the fraction of an operation type in any phase window.", "",
          "| Workload | Property | X-Bench | YCSB rerun |", "| --- | --- | --- | --- |"]
    for name, s in summary.items():
        tc = s.get("trace_comparison", {})
        for k, v in tc.get("xbench", {}).items():
            v2 = tc["ycsb_derivative_rerun"][k]
            if "ks" in v:
                cell = lambda d: f"KS {fmt(d['ks'], 4)}, W1/mean {fmt(d['w1_rel'], 4)}"
            else:
                cell = lambda d: f"max {fmt(d['max_abs_diff'], 4)} over {d['windows']} windows"
            L.append(f"| {name} | {k} | {cell(v)} | {cell(v2)} |")

    trace_spread = load_json(TRACE_SPREAD_PATH, {})
    if trace_spread:
        L += ["", f"Spread over {TRACE_SPREAD_DRAWS} further independent draws of each generator (KS, or op-mix max "
              "difference, to the same YCSB derivative trace):", "",
              "| Workload | Property | X-Bench draws min / median / max | YCSB draws min / median / max |",
              "| --- | --- | --- | --- |"]
        for name, f in trace_spread.items():
            for k, x in f["xbench"].items():
                r = f["ycsb_derivative"][k]
                L.append(f"| {name} | {k} | {x['min']:.4f} / {x['median']:.4f} / {x['max']:.4f} | "
                         f"{r['min']:.4f} / {r['median']:.4f} / {r['max']:.4f} |")

    L += ["", "## 2. Throughput on RocksDB", "",
          "| Workload | YCSB derivative (ops/s) | X-Bench (ops/s) | Diff | YCSB rerun (ops/s) | Diff |",
          "| --- | --- | --- | --- | --- | --- |"]
    for name, s in summary.items():
        L.append(f"| {name} | {fmt(s['ycsb_derivative']['throughput_ops_s'], 0)} | {fmt(s['xbench']['throughput_ops_s'], 0)} | "
                 f"{fmt(s['xbench_cmp']['throughput_diff_pct'])}% | {fmt(s['ycsb_derivative_rerun']['throughput_ops_s'], 0)} | "
                 f"{fmt(s['ycsb_derivative_rerun_cmp']['throughput_diff_pct'])}% |")

    L += ["", "## 2b. Fig. 12 metrics: bytes RocksDB reads and writes, and whole-run latency per operation", "",
          "Bytes from RocksDB's statistics (`-c statistics=1`), as Fig. 12 (C) of the paper: read = "
          + " + ".join(f"`{k}`" for k in FIG12_READ_TICKERS) + "; write = "
          + " + ".join(f"`{k}`" for k in FIG12_WRITE_TICKERS) + ". The paper's read has no "
          "`rocksdb.db.iter.bytes.read` (YCSB-A has no scans); it is added for the range queries of YCSB-TS. "
          f"`iostat` MB/s of `{IOSTAT_DEVICE}` over time are in `db_state/<workload>/<trace>/trial<N>/iostat.csv` "
          "and plotted in `plots/`. Latency: the executor's percentiles of the whole run. Mean ± stdev over the "
          "trials.", "",
          "| Workload | Metric | YCSB derivative | X-Bench | Diff | YCSB rerun | Diff |",
          "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, s in summary.items():
        for side_key, label in [("rocksdb_read_GB", "read (GB)"), ("rocksdb_write_GB", "write (GB)")]:
            ref = s["ycsb_derivative"][side_key]
            L.append(f"| {name} | {label} | {fmt(ref, 3)} | {fmt(s['xbench'][side_key], 3)} | "
                     f"{fmt(pct_diff(ref[0], s['xbench'][side_key][0]))}% | {fmt(s['ycsb_derivative_rerun'][side_key], 3)} | "
                     f"{fmt(pct_diff(ref[0], s['ycsb_derivative_rerun'][side_key][0]))}% |")
        for k in FIG12_READ_TICKERS + FIG12_WRITE_TICKERS:
            ref = s["ycsb_derivative"]["tickers"][k]
            mb = lambda v: (v[0] / 2**20, v[1] / 2**20)
            L.append(f"| {name} | `{k}` (MB) | {fmt(mb(ref), 1)} | {fmt(mb(s['xbench']['tickers'][k]), 1)} | "
                     f"{fmt(pct_diff(ref[0], s['xbench']['tickers'][k][0]))}% | "
                     f"{fmt(mb(s['ycsb_derivative_rerun']['tickers'][k]), 1)} | "
                     f"{fmt(pct_diff(ref[0], s['ycsb_derivative_rerun']['tickers'][k][0]))}% |")
        for op, ref in s["ycsb_derivative"]["run_latency_us"].items():
            xb = s["xbench"]["run_latency_us"].get(op, {})
            rr = s["ycsb_derivative_rerun"]["run_latency_us"].get(op, {})
            show = lambda d: " / ".join(f"{d[k]:.1f}" for k, _ in FIG12_PERCENTILES) if d else "-"
            L.append(f"| {name} | {op} latency p0/p25/p50/p75/p99 (µs) | {show(ref)} | {show(xb)} | "
                     f"p50 {fmt(pct_diff(ref['p50'], xb['p50']) if xb else None)}% | {show(rr)} | "
                     f"p50 {fmt(pct_diff(ref['p50'], rr['p50']) if rr else None)}% |")

    L += ["", "## 3. Per-phase latency percentiles", "",
          "Absolute relative difference to the YCSB derivative of the mean (over trials) p50, p99 and average "
          "latency of every operation type in every phase window (YCSB-IVS: load and every extend/run phase; "
          f"YCSB-TS: load and the run phase in {TS_RUN_WINDOWS} windows); median and maximum over all of them.", "",
          "| Workload | Percentile | X-Bench median / max | YCSB rerun median / max | Windows × ops |",
          "| --- | --- | --- | --- | --- |"]
    for name, s in summary.items():
        for label in LATENCY_METRICS:
            x = s["xbench_cmp"]["window_latency_abs_diff_pct"].get(label)
            r = s["ycsb_derivative_rerun_cmp"]["window_latency_abs_diff_pct"].get(label)
            if x and r:
                L.append(f"| {name} | {label} | {x['median']:.2f}% / {x['max']:.2f}% | "
                         f"{r['median']:.2f}% / {r['max']:.2f}% | {x['n']} |")

    L += ["", "## 4. Bytes read and written on RocksDB (mean over trials)", "",
          "| Workload | Metric | YCSB derivative | X-Bench | Diff | YCSB rerun | Diff |",
          "| --- | --- | --- | --- | --- | --- | --- |"]
    scale = {"Point query bytes returned": (1 / 2**20, "MB"), "Append bytes read": (1 / 2**20, "MB"),
             "Range query keys returned": (1e-6, "M keys"), "Range query bytes returned": (1 / 2**20, "MB")}
    for name, s in summary.items():
        for k in READ_METRICS + WRITE_METRICS:
            r = s["ycsb_derivative"].get(k)
            if not r or r[0] in (None, 0):
                continue
            f, unit = scale.get(k, (1, ""))
            cells = []
            for other, _ in OTHERS:
                o = s[other].get(k)
                cells += [fmt(o[0] * f if o[0] is not None else None), f"{fmt(pct_diff(r[0], o[0]))}%"]
            L.append(f"| {name} | `{k}`{' (' + unit + ')' if unit else ''} | {fmt(r[0] * f)} | {' | '.join(cells)} |")

    L += ["", "## 5. End-of-run database state and keys per range query (trial 1)", "",
          "| Workload | Distribution | Trace | n | mean | p50 | p99 | max | KS vs. YCSB derivative | W1/mean |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, s in summary.items():
        for label, key in (("value size (B)", "final_value_size"), ("keys per range query", "range_query_keys")):
            if s["ycsb_derivative"].get(key, {}).get("n", 0) == 0:
                continue
            for side, nm in (("ycsb_derivative", "YCSB derivative"), ("xbench", "X-Bench"), ("ycsb_derivative_rerun", "YCSB rerun")):
                d = s[side][key]
                c = s.get(f"{side}_cmp", {}).get(key, {})
                L.append(f"| {name} | {label} | {nm} | {d['n']:,} | {d['mean']:,.1f} | {d.get('p50', '-')} | "
                         f"{d.get('p99', '-')} | {d['max']:,} | {fmt(c.get('ks'), 4)} | {fmt(c.get('w1_rel'), 4)} |")

    L += ["", "## 6. Operations per phase", "",
          "| Workload | Phase | YCSB derivative | X-Bench | YCSB rerun |", "| --- | --- | --- | --- | --- |"]
    for name in summary:
        a = analysis.get(name)
        if not a:
            continue
        for ph in a["ycsb_derivative"]["per_phase_ops"]:
            cells = [", ".join(f"{k}={v:,}" for k, v in sorted(a[side]["per_phase_ops"].get(ph, {}).items()))
                     for side in ("ycsb_derivative", "xbench", "ycsb_derivative_rerun")]
            L.append(f"| {name} | {ph} | {' | '.join(cells)} |")
    order = load_json(ORDER_PATH, {})
    if order:
        L += ["", "## 7. Order check", "",
              "Sections 2–4 run all trials of one trace, then all of the next, always in the order YCSB derivative, "
              "YCSB rerun, X-Bench. Here each round runs one trial of every trace and the order rotates every round, "
              "so each trace runs first, second and third (`--stage order-check`, `results/order_check.json`). "
              "Mean ± stdev over the rounds; Diff to the YCSB derivative. Last column: the mean over all three "
              "traces of the trials run first / second / third in their round.", "",
              "| Workload | Metric | YCSB derivative | X-Bench | Diff | YCSB rerun | Diff | 1st / 2nd / 3rd in round |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, sides in order.items():
            if not all(sides.get(side) for side in SIDES):
                continue
            metrics = [("throughput (ops/s)", lambda r: r["overall"].get("Throughput (using start and end time)"), 0),
                       ("RocksDB read (GB)", lambda r: sum(r["rocksdb_statistics"][k] for k in FIG12_READ_TICKERS) / GIB, 3),
                       ("RocksDB write (GB)", lambda r: sum(r["rocksdb_statistics"][k] for k in FIG12_WRITE_TICKERS) / GIB, 3)]
            for op in sides["ycsb_derivative"][0]["operations"]:
                metrics.append((f"{op} p50 (µs)", lambda r, op=op: r["operations"][op]["50th Percentile Latency"], 1))
            for label, get, nd in metrics:
                vals = {side: mean_sd([get(r) for r in sides[side]]) for side in SIDES}
                ref = vals["ycsb_derivative"][0]
                by_pos = [statistics.mean(get(r) for side in SIDES for r in sides[side] if r["position"] == pos)
                          for pos in range(1, len(SIDES) + 1)]
                L.append(f"| {name} | {label} | {fmt(vals['ycsb_derivative'], nd)} | {fmt(vals['xbench'], nd)} | "
                         f"{fmt(pct_diff(ref, vals['xbench'][0]))}% | {fmt(vals['ycsb_derivative_rerun'], nd)} | "
                         f"{fmt(pct_diff(ref, vals['ycsb_derivative_rerun'][0]))}% | "
                         f"{' / '.join(fmt(v, nd) for v in by_pos)} |")
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(L) + "\n")


# =============================================================================
#  MAIN
# =============================================================================
def main():
    global _log_fh
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["generate", "analyze", "trace-spread", "execute", "order-check", "report",
                                        "all"],
                    default="all")
    ap.add_argument("--workloads", nargs="+", default=list(WORKLOADS))
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--redo", nargs="+", default=[], metavar="SIDE:TRIAL",
                    help="execute stage: rerun these trials of --workloads, e.g. xbench:1")
    ap.add_argument("--regen-xbench", action="store_true",
                    help="rewrite the specs and regenerate only the X-Bench traces of --workloads")
    args = ap.parse_args()

    for d in (OUT_DIR, WORKLOAD_DIR, RESULTS_DIR, DBSTATE_DIR, os.path.dirname(LOG_PATH)):
        os.makedirs(d, exist_ok=True)
    _log_fh = open(LOG_PATH, "a")
    banner(f"run_ycsb_derivatives_fidelity.py  stage={args.stage}  {time.strftime('%Y-%m-%d %H:%M:%S')}")

    if args.stage in ("generate", "all"):
        stage_generate(args)
    if args.stage in ("analyze", "all"):
        stage_analyze(args)
    if args.stage in ("trace-spread", "all"):
        stage_trace_spread(args)
    if args.stage in ("execute", "all"):
        stage_execute(args)
    if args.stage == "order-check":
        stage_order_check(args)
    if args.stage in ("report", "all"):
        stage_report(args)


if __name__ == "__main__":
    main()
