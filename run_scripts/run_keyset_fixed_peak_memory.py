#!/usr/bin/env python3
"""
run_keyset_fixed_peak_memory.py
===============================
R2.O2 (ii): time and peak memory of fixed VecKeySet on the Table 4 workloads where it does not finish
in 600 s (Tec-1, Tec-3, Tec-5, Tec-6), without running for days (decided with the authors, 2026-10-22).

Two measurements, both with `tectonic-cli generate --keyset vec`, single-threaded, RUST_LOG=tectonic=debug,
`sync; echo 3 > /proc/sys/vm/drop_caches` before every run, one run at a time:

1. timeout   The real workload (x-bench-specs/table4/), killed after 600 s as in the KeySet experiment.
             Peak RSS so far is read from VmHWM in /proc/<generator pid>/status every 0.5 s and just
             before the kill: a measured lower bound on the full run's peak RSS. The generator's progress
             (stdout) is kept, so the kill point is known. 1 trial (a timed-out configuration is not
             repeated in the KeySet experiment either).
2. proxy     The same spec with `unique_inserts` turned into `inserts` (nothing else changed).
             VecKeySet then holds the same keys but skips the linear `contains` scan of every unique
             insert, so the run finishes. Its peak RSS stands in for the full run's. Validated on Tec-2
             and Tec-4, where fixed VecKeySet finishes with unique inserts: both forms run here.
             Not for Tec-1: its 1M empty point queries also scan every key (hours with either form).
             5 trials, shuffled order per trial.

For every run that finishes, peak RSS comes from GNU time (as in the KeySet experiment) and the last
VmHWM sample is kept as a check of the sampling.

Output: data/revision/R1.O8-R2.O2_keyset-selection/table4-fixed-peak-memory/
  specs-upsert/            the proxy specs
  results/runs.json        one record per run
  runs/<mode>/<workload>/trial<k>.{stdout,stderr}.log
"""
import os
import re
import json
import time
import random
import subprocess

ROOT_DIR = "/home/cc/Tectonic"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"
OUT_DIR = f"{ROOT_DIR}/data/revision/R1.O8-R2.O2_keyset-selection/table4-fixed-peak-memory"
RUNS_PATH = f"{OUT_DIR}/results/runs.json"
TRACE_PATH = f"{OUT_DIR}/tmp/trace.txt"
KEYSET = "vec"
TIMEOUT_S = 600
TRIALS = 5
SEED = 20261022
SPECS = {
    "Tec-1": "x-bench-specs/table4/Tec-1_multi_stage.spec.json",
    "Tec-2": "x-bench-specs/table4/Tec-2_interleaved.spec.json",
    "Tec-3": "x-bench-specs/table4/Tec-3_varied_sortedness.spec.json",
    "Tec-4": "x-bench-specs/table4/Tec-4_slow_shift.spec.json",
    "Tec-5": "x-bench-specs/table4/Tec-5_abrupt_shift.spec.json",
    "Tec-6": "x-bench-specs/table4/Tec-6_varied_kv_size.spec.json",
}
TIMEOUT_WORKLOADS = ["Tec-1", "Tec-3", "Tec-5", "Tec-6"]
# (mode, workload): the proxy for the timed-out workloads, and both forms on the validation workloads
PROXY_CONFIGS = [("upsert", w) for w in ("Tec-2", "Tec-3", "Tec-4", "Tec-5", "Tec-6")] + \
                [("unique", w) for w in ("Tec-2", "Tec-4")]


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def upsert_spec(workload):
    """The workload's spec with every `unique_inserts` renamed to `inserts`."""
    path = f"{OUT_DIR}/specs-upsert/{os.path.basename(SPECS[workload])}"
    with open(f"{ROOT_DIR}/{SPECS[workload]}") as f:
        text = f.read()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text.replace('"unique_inserts"', '"inserts"'))
    return path


def vmhwm_kib(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            m = re.search(r"VmHWM:\s+(\d+) kB", f.read())
        return int(m.group(1)) if m else None
    except OSError:
        return None


def run(mode, workload, trial, timeout):
    spec = upsert_spec(workload) if mode == "upsert" else f"{ROOT_DIR}/{SPECS[workload]}"
    out_dir = f"{OUT_DIR}/runs/{mode}/{workload}"
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.dirname(TRACE_PATH), exist_ok=True)
    if os.path.exists(TRACE_PATH):
        os.remove(TRACE_PATH)
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)
    cmd = ["/usr/bin/time", "-v", TECTONIC_CLI, "generate", "-w", spec, "-o", TRACE_PATH, "--keyset", KEYSET]
    env = {k: v for k, v in os.environ.items() if k != "TECTONIC_PARALLEL_GEN"}  # single-threaded
    env["RUST_LOG"] = "tectonic=debug"  # progress of every group in steps of 10%, on stdout
    stdout_path, stderr_path = f"{out_dir}/trial{trial}.stdout.log", f"{out_dir}/trial{trial}.stderr.log"
    log(f"{mode} {workload} trial {trial}: {' '.join(cmd)}")
    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        t0 = time.perf_counter()
        p = subprocess.Popen(cmd, stdout=out, stderr=err, env=env, start_new_session=True)
        gen_pid, hwm, timed_out = None, None, False
        while p.poll() is None:
            if gen_pid is None:
                children = subprocess.run(["pgrep", "-P", str(p.pid)], stdout=subprocess.PIPE, text=True).stdout.split()
                gen_pid = int(children[0]) if children else None
            if gen_pid is not None:
                hwm = vmhwm_kib(gen_pid) or hwm
            if timeout and time.perf_counter() - t0 >= timeout:
                hwm = vmhwm_kib(gen_pid) or hwm  # last reading, just before the kill
                os.killpg(p.pid, 9)
                p.wait()
                timed_out = True
                break
            time.sleep(0.5)
        wall = time.perf_counter() - t0
    with open(stderr_path) as f:
        stderr = re.sub(r"\x1b\[[0-9;]*m", "", f.read())
    with open(stdout_path) as f:
        stdout = re.sub(r"\x1b\[[0-9;]*m", "", f.read())
    rec = {"mode": mode, "workload": workload, "keyset": KEYSET, "trial": trial,
           "spec": os.path.relpath(spec, ROOT_DIR), "wall_time_s": wall, "timed_out": timed_out,
           "vmhwm_mib_last_sample": hwm / 1024 if hwm else None,
           "keysets_used": re.findall(r"\[Tectonic KeySet\] Section (\d+): (\S+)", stderr),
           "last_group": (re.findall(r"\[Generating\] Section \d+ \| Group \d+", stderr) or [None])[-1],
           "last_progress_line": (re.findall(r"Generating operation \d+ \(\d+%\)", stdout) or [None])[-1],
           "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not timed_out:
        if p.returncode != 0:
            raise RuntimeError(f"{mode} {workload} trial {trial} failed: {stderr[-2000:]}")
        rec["peak_rss_mib"] = int(re.search(r"Maximum resident set size \(kbytes\): (\d+)", stderr).group(1)) / 1024
        rec["trace_bytes"] = os.path.getsize(TRACE_PATH)
    if os.path.exists(TRACE_PATH):
        os.remove(TRACE_PATH)
    return rec


def main():
    runs = []
    if os.path.exists(RUNS_PATH):
        with open(RUNS_PATH) as f:
            runs = json.load(f)
    done = {(r["mode"], r["workload"], r["trial"]) for r in runs}
    todo = [("timeout", w, 1) for w in TIMEOUT_WORKLOADS]
    for trial in range(1, TRIALS + 1):
        configs = list(PROXY_CONFIGS)
        random.Random(SEED + trial).shuffle(configs)
        todo += [(mode, w, trial) for mode, w in configs]
    os.makedirs(os.path.dirname(RUNS_PATH), exist_ok=True)
    for mode, workload, trial in todo:
        if (mode, workload, trial) in done:
            continue
        rec = run(mode, workload, trial, TIMEOUT_S if mode == "timeout" else None)
        runs.append(rec)
        with open(RUNS_PATH, "w") as f:
            json.dump(runs, f, indent=2)
        log(f"  -> {rec['wall_time_s']:.1f} s{' TIMEOUT' if rec['timed_out'] else ''}, "
            f"peak RSS {rec.get('peak_rss_mib')}, VmHWM {rec['vmhwm_mib_last_sample']}, {rec['last_progress_line']}")


if __name__ == "__main__":
    main()
