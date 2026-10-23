#!/usr/bin/env python3
"""
run_keyset_fixed_to_completion.py
=================================
R2.O2 (ii): fixed VecKeySet on the Table 4 workloads where it hit the 600 s timeout of the KeySet
experiment (Tec-1, Tec-3, Tec-5, Tec-6), run to completion so that the fixed-vs-adaptive ratio is a
measurement instead of a lower bound.

Same run as `run_scripts/run_keyset_selection.py` (suite table4), without the timeout:
`/usr/bin/time -v tectonic-cli generate -w <spec> -o <trace> --keyset vec`, single-threaded
(TECTONIC_PARALLEL_GEN unset), RUST_LOG=tectonic=debug, `sync; echo 3 > /proc/sys/vm/drop_caches`
before the run, one run at a time. Wall time and peak RSS from GNU time. 1 trial per workload
(decided with the authors, 2026-10-22). The generator's stdout (progress per 10% of a group) is
written to a log file while the run goes, so a long run can be followed.

Output: data/revision/R1.O8-R2.O2_keyset-selection/table4-fixed-to-completion/
  results/runs.json                         one record per run
  runs/<workload>/vec/trial1.{stdout,stderr}.log
"""
import os
import re
import sys
import json
import time
import subprocess

ROOT_DIR = "/home/cc/Tectonic"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"
OUT_DIR = f"{ROOT_DIR}/data/revision/R1.O8-R2.O2_keyset-selection/table4-fixed-to-completion"
RUNS_PATH = f"{OUT_DIR}/results/runs.json"
TRACE_PATH = f"{OUT_DIR}/tmp/trace.txt"
KEYSET = "vec"
TRIAL = 1
# shortest expected first, so results arrive while Tec-3 (10M unique inserts) runs
WORKLOADS = {
    "Tec-1": "x-bench-specs/table4/Tec-1_multi_stage.spec.json",
    "Tec-5": "x-bench-specs/table4/Tec-5_abrupt_shift.spec.json",
    "Tec-6": "x-bench-specs/table4/Tec-6_varied_kv_size.spec.json",
    "Tec-3": "x-bench-specs/table4/Tec-3_varied_sortedness.spec.json",
}


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def op_counts(path):
    counts = {}
    with open(path, "rb") as f:
        for line in f:
            op = line[:line.index(b" ")].decode()
            counts[op] = counts.get(op, 0) + 1
    return counts


def run(workload, spec):
    out_dir = f"{OUT_DIR}/runs/{workload}/{KEYSET}"
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.dirname(TRACE_PATH), exist_ok=True)
    if os.path.exists(TRACE_PATH):
        os.remove(TRACE_PATH)
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)
    cmd = ["/usr/bin/time", "-v", TECTONIC_CLI, "generate", "-w", f"{ROOT_DIR}/{spec}", "-o", TRACE_PATH,
           "--keyset", KEYSET]
    env = {k: v for k, v in os.environ.items() if k != "TECTONIC_PARALLEL_GEN"}  # single-threaded
    env["RUST_LOG"] = "tectonic=debug"  # progress of every group in steps of 10%, on stdout
    stdout_path, stderr_path = f"{out_dir}/trial{TRIAL}.stdout.log", f"{out_dir}/trial{TRIAL}.stderr.log"
    log(f"{workload}: {' '.join(cmd)}")
    t0 = time.perf_counter()
    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        rc = subprocess.run(cmd, stdout=out, stderr=err, env=env).returncode
    wall = time.perf_counter() - t0
    with open(stderr_path) as f:
        stderr = re.sub(r"\x1b\[[0-9;]*m", "", f.read())
    if rc != 0:
        raise RuntimeError(f"{workload} failed with exit code {rc}: {stderr[-2000:]}")
    with open(stdout_path) as f:
        stdout = f.read()
    m = re.search(r"Tectonic_Op_Timings: (\{.*\})", stdout)
    rec = {"workload": workload, "keyset": KEYSET, "trial": TRIAL, "spec": spec, "wall_time_s": wall,
           "timed_out": False,
           "peak_rss_mib": int(re.search(r"Maximum resident set size \(kbytes\): (\d+)", stderr).group(1)) / 1024,
           "gnu_time_elapsed": re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)", stderr).group(1),
           "keysets_used": re.findall(r"\[Tectonic KeySet\] Section (\d+): (\S+)", stderr),
           "op_timings_s": json.loads(m.group(1)) if m else None,
           "trace_bytes": os.path.getsize(TRACE_PATH),
           "op_counts": op_counts(TRACE_PATH),
           "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
    os.remove(TRACE_PATH)
    return rec


def main():
    runs = []
    if os.path.exists(RUNS_PATH):
        with open(RUNS_PATH) as f:
            runs = json.load(f)
    done = {r["workload"] for r in runs}
    todo = [w for w in (sys.argv[1:] or WORKLOADS) if w not in done]
    os.makedirs(os.path.dirname(RUNS_PATH), exist_ok=True)
    for workload in todo:
        rec = run(workload, WORKLOADS[workload])
        runs.append(rec)
        with open(RUNS_PATH, "w") as f:
            json.dump(runs, f, indent=2)
        log(f"{workload}: {rec['wall_time_s']:.1f} s, {rec['peak_rss_mib']:.1f} MiB, {rec['keysets_used']}, "
            f"{rec['op_counts']}")


if __name__ == "__main__":
    main()
