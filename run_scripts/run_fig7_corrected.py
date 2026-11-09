#!/usr/bin/env python3
"""
run_fig7_corrected.py
=====================
Fig. 7 (generation time and peak memory of YCSB, KV-Bench and X-Bench) run again, corrected (decided with the
authors, 2026-10-31). Fig. 7's harness, `run_figure_6_validate_full.py`, ran each generator once without
dropping the page cache, and its KV-Bench time splits came from timing code that is in no branch of KV-Bench.
Here:
  - the same generators, commands and arguments, through the harness's own functions (`run_ycsb`,
    `run_kvbench`, `run_xbench`), unchanged;
  - `sync; echo 3 > /proc/sys/vm/drop_caches` before every generator process (YCSB: before load and before
    run), wrapped around the harness's `run_command`;
  - TRIALS trials, one generator process at a time, nothing else on the machine;
  - reported: total wall time of generating the workload (YCSB: load + run process) and peak VmRSS (sampled
    every 10 ms; YCSB: the larger of the two processes). No load/execution or per-operation split.

Generators, all unmodified:
  YCSB      0.18.0-SNAPSHOT, github.com/AlexanderHott/YCSB 0bedec5 (the harness's vendor/YCSB), FileClient,
            workloads a-f, recordcount = operationcount = 1,000,000;
  KV-Bench  github.com/SSD-Brandeis/KV-WorkloadGenerator `refactor` 699bc10 with one bug fix (authors' decision,
            2026-11-02: data/revision/fig7-corrected/kvbench-minimal-fix/load_gen_update_oob.patch; without it II
            reads past the insert pool and crashes in every branch), built as its README says. Fig. 7's arguments
            for its YCSB-shaped A-E; I-V as the KVBench paper's Table 5 at 1/SCALE of Fig. 7's operation counts
            (run_scripts/make_kvbench_paper_specs.py), so that IV, which sorts every key on each range delete,
            finishes. A crash is recorded, not retried;
  X-Bench   xbench-cli of this repo, adaptive KeySet (default), 1 thread: example-specs/ycsb-untracked/ (YCSB
            A-F, as Table 3) and example-specs/kvbench-1-20/ (I-V at 1/20; written by make_kvbench_paper_specs.py
            with the same operation counts as the KV-Bench arguments; until 2026-11-02 21:00 UTC this 1/20 copy
            was example-specs/kvbench/). Fig. 7 used example-specs/ycsb_untracked/,
            which no longer exists.

The KV-Bench I-V and YCSB A-F generator runs are also the generator columns of Tables 2 and 3
(`run_scripts/make_paper_tables_fixed_vs_adaptive.py`).

Output: data/revision/fig7-corrected/results/runs.<host>.json, logs/generators.<host>.log (commands, output).
Table 2's KV-Bench generator column (authors, 2026-11-02: the same operation counts as Table 2's X-Bench columns):
  --kv-scales I=1 II=1 III=1 IV=10 V=1 --out-dir data/revision/R1.O8-R2.O2_keyset-selection/kvbench-generator-table2
runs KV-Bench I-V at those scales (1 = full size, 10 = 0.1x) instead of 1/FIG7_SCALE; every record has its own
kvbench_paper_scale and kvbench_args, and KV-Bench's printed operation totals (kvbench_op_counts).

Usage (on the run machine): python3 run_scripts/run_fig7_corrected.py [--only JOB ...] [--trials N] [--out-dir D]
                            [--kv-scales W=S ...]
"""
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import os
import sys
import json
import time
import socket
import hashlib
import argparse
import subprocess
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_figure_6_validate_full as f7  # noqa: E402  (Fig. 7's harness, unchanged)
import make_kvbench_paper_specs as kvspecs  # noqa: E402

f7.KVBENCH_ARGS = kvspecs.KVBENCH_ARGS  # KVBench I-V: the paper's Table 5 at 1/kvspecs.FIG7_SCALE (YCSB-shaped A-E unchanged)
KVBENCH_PATCH = f"{f7.ROOT}/data/revision/fig7-corrected/kvbench-minimal-fix/load_gen_update_oob.patch"
# 2026-11-02 (authors): a second fix, for range queries (YCSB-E), applied after the first full run; its sha256 is
# recorded once the KV-Bench checkout has it (the YCSB-E/KV-Bench reruns, --redo-failed)
KVBENCH_PATCH_RQ = f"{f7.ROOT}/data/revision/fig7-corrected/kvbench-minimal-fix/load_gen_rq_fixes.patch"

OUT_DIR = REPO_ROOT + "/data/revision/fig7-corrected"
TRIALS = 5
XBENCH_YCSB_SPEC = "example-specs/ycsb-untracked/{}.spec.json"
XBENCH_KV_SPEC = "example-specs/kvbench-1-20/{}.spec.json"  # 1/20, as the KVBench arguments

# (job, tool, workload set, workload); the order of one trial, as Fig. 7: YCSB A-F, then KV-Bench I-V
JOBS = []
for w in f7.YCSB_WORKLOADS:
    JOBS.append((f"YCSB-{w}/YCSB", "YCSB", "ycsb", w))
    if w in f7.KVBENCH_YCSB_ARGS:
        JOBS.append((f"YCSB-{w}/KV-Bench", "KV-Bench", "ycsb", w))
    JOBS.append((f"YCSB-{w}/X-Bench", "X-Bench", "ycsb", w))
for w in f7.KV_WORKLOADS:
    JOBS.append((f"KVBench-{w}/KV-Bench", "KV-Bench", "kvbench", w))
    JOBS.append((f"KVBench-{w}/X-Bench", "X-Bench", "kvbench", w))
JOB_NAMES = [j[0] for j in JOBS]

_run_command = f7.run_command


def run_command_caches_dropped(*args, **kwargs):
    """The harness's run_command, after writing back and dropping the page cache, dentries and inodes."""
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)
    log = kwargs.get("log", args[3] if len(args) > 3 else None)
    if log:
        log.write("caches dropped (sync; echo 3 > /proc/sys/vm/drop_caches)")
    global last_stdout
    raw = _run_command(*args, **kwargs)
    last_stdout = raw["stdout"]
    return raw


last_stdout = []  # stdout of the last generator process (run_command_caches_dropped)


f7.run_command = run_command_caches_dropped  # run_ycsb / run_kvbench / run_xbench look it up at call time


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head(path):
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def kvbench_counts(stdout):
    """KV-Bench's own operation totals from its stdout ("Total operation count = N" and the *_count fields of
    "Workload_parameters:"). The harness deletes the output file when the run ends, so it is not counted."""
    out = {}
    for line in stdout:
        if line.startswith("Total operation count = "):
            out["total"] = int(line.split("=")[1])
        elif line.startswith("Workload_parameters:"):
            for kv in line.split(":", 1)[1].split(","):
                k, _, v = kv.partition("=")
                if k.strip().endswith("_count"):
                    out[k.strip()] = int(v)
    return out


def run_job(tool, wset, w, log):
    if tool == "YCSB":
        rec = f7.run_ycsb(w, log)
        load, run = rec["load"], rec["run"]
        return {"wall_time_s": load["total_duration"] + run["total_duration"],
                "load_wall_time_s": load["total_duration"], "run_wall_time_s": run["total_duration"],
                "peak_rss_mib": max(f7.peak_mem(load), f7.peak_mem(run)),
                "load_peak_rss_mib": f7.peak_mem(load), "run_peak_rss_mib": f7.peak_mem(run),
                "command": [load["command"], run["command"]], "op_counts": [load["op_counts"], run["op_counts"]]}
    if tool == "KV-Bench":
        args = (f7.KVBENCH_YCSB_ARGS if wset == "ycsb" else f7.KVBENCH_ARGS)[w]
        rec = f7.run_kvbench(w, args, log, wset)
        return {"wall_time_s": rec["total_duration"], "peak_rss_mib": f7.peak_mem(rec), "command": rec["command"],
                "kvbench_op_counts": kvbench_counts(last_stdout)}
    else:
        spec = (XBENCH_YCSB_SPEC if wset == "ycsb" else XBENCH_KV_SPEC).format(w.lower())
        rec = f7.run_xbench(w, str(f7.ROOT / spec), log, wset)
    return {"wall_time_s": rec["total_duration"], "peak_rss_mib": f7.peak_mem(rec), "command": rec["command"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", choices=JOB_NAMES, default=JOB_NAMES)
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--redo-failed", action="store_true",
                    help="run the failed records of the --only jobs again; the replaced records go to failed_replaced.<host>.json")
    ap.add_argument("--kv-scales", nargs="+", metavar="W=S",
                    help="KV-Bench I-V at 1/S of Fig. 7's operation counts per workload, e.g. I=1 IV=10 (Table 2)")
    args = ap.parse_args()
    kv_scales = {w: kvspecs.FIG7_SCALE for w in f7.KV_WORKLOADS}
    if args.kv_scales:
        given = dict(x.split("=") for x in args.kv_scales)
        assert set(given) == set(f7.KV_WORKLOADS), f"--kv-scales needs every one of {f7.KV_WORKLOADS}"
        kv_scales = {w: int(given[w]) for w in f7.KV_WORKLOADS}
        f7.KVBENCH_ARGS = {w: kvspecs.kvbench_args(kv_scales[w])[w] for w in f7.KV_WORKLOADS}
        # X-Bench's KV-Bench specs here are the 1/20 ones (XBENCH_KV_SPEC): only KV-Bench's generator in this mode
        assert not [j for j in args.only if j.startswith("KVBench-") and j.endswith("/X-Bench")], \
            "--kv-scales runs KV-Bench's generator only: pass --only KVBench-<W>/KV-Bench ..."
    os.environ.pop("XBENCH_PARALLEL_GEN", None)  # X-Bench single-threaded, as Fig. 7
    host = socket.gethostname()
    os.makedirs(f"{args.out_dir}/results", exist_ok=True)
    runs_path = f"{args.out_dir}/results/runs.{host}.json"
    runs = json.load(open(runs_path)) if os.path.exists(runs_path) else []
    if args.redo_failed:
        redo = [r for r in runs if r["failed"] and r["job"] in args.only]
        replaced_path = f"{args.out_dir}/results/failed_replaced.{host}.json"
        old_replaced = json.load(open(replaced_path)) if os.path.exists(replaced_path) else []
        with open(replaced_path, "w") as f:
            json.dump(old_replaced + redo, f, indent=2)
        runs = [r for r in runs if r not in redo]
    log = f7.HumanLog(Path(f"{args.out_dir}/logs/generators.{host}.log"), append=True)
    versions = {"xbench_xbench_cli_sha256": sha256(f7.XBENCH_CLI)}
    # 2026-11-02: Table 3's rerun on revision-2, which has no KV-Bench, runs YCSB/X-Bench jobs only
    if any(tool == "KV-Bench" for job, tool, _, _ in JOBS if job in args.only) or os.path.exists(f7.KVBENCH_CLI):
        versions.update({
                "kvbench_load_gen_sha256": sha256(f7.KVBENCH_CLI), "kvbench_commit": git_head(f7.KVBENCH_CLI.parent.parent),
                "kvbench_patch_sha256": sha256(KVBENCH_PATCH), "kvbench_paper_scale": kvspecs.FIG7_SCALE,
                "kvbench_checkout_diff_sha256": hashlib.sha256(subprocess.run(
                    ["git", "-C", str(f7.KVBENCH_CLI.parent.parent), "diff"], capture_output=True).stdout).hexdigest(),
                "kvbench_args": f7.KVBENCH_ARGS})
    versions.update({
                "ycsb_commit": git_head(f7.YCSB_DIR),
                "ycsb_core_jar_sha256": sha256(f7.YCSB_DIR / "core" / "target" / "core-0.18.0-SNAPSHOT.jar"),
                "ycsb_file_binding_jar_sha256": sha256(f7.YCSB_DIR / "file" / "target" / "file-binding-0.18.0-SNAPSHOT.jar"),
                "java": subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.splitlines()[0]})
    if "kvbench_load_gen_sha256" in versions and os.path.exists(KVBENCH_PATCH_RQ):
        versions["kvbench_patch_rq_sha256"] = sha256(KVBENCH_PATCH_RQ)
    log.write(f"\n=== run_fig7_corrected.py on {host}, {time.strftime('%Y-%m-%d %H:%M:%S')} UTC: {versions}")
    done = {(r["job"], r["trial"]) for r in runs}
    for trial in range(1, args.trials + 1):
        for job, tool, wset, w in JOBS:
            if job not in args.only or (job, trial) in done:
                continue
            log.write(f"\n##### {job} trial {trial}/{args.trials}")
            rec = {"job": job, "tool": tool, "workload_set": wset, "workload": w, "trial": trial, "host": host,
                   "caches_dropped": True, **versions}
            if wset == "kvbench":  # the scale and arguments of this run (KV-Bench I-V; X-Bench uses XBENCH_KV_SPEC)
                rec["kvbench_paper_scale"] = kv_scales[w]
                rec["kvbench_args"] = f7.KVBENCH_ARGS[w] if tool == "KV-Bench" else None
            try:
                rec.update(run_job(tool, wset, w, log))
                rec["failed"] = False
            except Exception as e:  # a generator crash (exit code in the message); recorded, not retried
                rec.update({"failed": True, "error": repr(e)[:2000]})
            rec["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
            runs.append(rec)
            with open(runs_path + ".tmp", "w") as f:
                json.dump(runs, f, indent=2)
            os.replace(runs_path + ".tmp", runs_path)
            log.write(f"##### {job} trial {trial}: " + ("FAILED " + rec["error"] if rec["failed"] else
                      f"{rec['wall_time_s']:.2f} s, {rec['peak_rss_mib']:.1f} MiB"))
    log.close()


if __name__ == "__main__":
    main()
