#!/usr/bin/env python3
"""
run_keyset_fixed_queue.py
=========================
R2.O2 (ii): fixed VecKeySet on the Table 4 workloads where it hit the 600 s timeout of the KeySet
experiment, run to completion, one trial each (decided with the authors, 2026-10-26): Tec-1, Tec-5,
Tec-6 at full scale and Tec-3 at 0.1x (1M unique inserts instead of 10M; everything else unchanged),
plus Algorithm 1 on Tec-3 0.1x (5 trials) as its baseline. The runs are spread over several machines
of the same hardware; a machine never runs two of them at the same time (a lock enforces it).

Each machine works through its own queue, `queue/<host>.txt`, one job per line:
`<workload> <keyset> <trial>`. The queue is re-read before every job, so a job that has not started
can be moved to another machine by deleting its line. A job already in `results/runs.<host>.json` is
skipped.

Same run as `run_scripts/run_keyset_fixed_to_completion.py`, without a timeout:
`/usr/bin/time -v tectonic-cli generate -w <spec> -o <trace> --keyset <k>`, single-threaded
(TECTONIC_PARALLEL_GEN unset), RUST_LOG=tectonic=debug, `sync; echo 3 > /proc/sys/vm/drop_caches`
before the run. Wall time and peak RSS from GNU time; VmRSS and VmHWM of the generator sampled every
10 s into `trial<N>.rss.log`, so a run that is stopped still has its peak memory so far.

Usage: python3 run_scripts/run_keyset_fixed_queue.py      (on the run machine, inside tmux)

Output: data/revision/R1.O8-R2.O2_keyset-selection/table4-fixed-to-completion-multimachine/
  queue/<host>.txt                          the jobs of a machine
  results/runs.<host>.json                  one record per finished run
  runs/<workload>/<keyset>/trial<N>.{stdout,stderr,rss}.log
  logs/console.<host>.out
"""
import os
import re
import sys
import json
import time
import fcntl
import socket
import hashlib
import subprocess

ROOT_DIR = "/home/cc/Tectonic"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"
OUT_DIR = f"{ROOT_DIR}/data/revision/R1.O8-R2.O2_keyset-selection/table4-fixed-to-completion-multimachine"
HOST = socket.gethostname()
QUEUE_PATH = f"{OUT_DIR}/queue/{HOST}.txt"
RUNS_PATH = f"{OUT_DIR}/results/runs.{HOST}.json"
TRACE_PATH = f"{OUT_DIR}/tmp/trace.txt"
LOCK_PATH = f"{OUT_DIR}/tmp/run.lock"
SAMPLE_INTERVAL_S = 10
WORKLOADS = {
    "Tec-1": "x-bench-specs/table4/Tec-1_multi_stage.spec.json",
    "Tec-3": "x-bench-specs/table4/Tec-3_varied_sortedness.spec.json",
    "Tec-3-0.1x": "x-bench-specs/table4/Tec-3_varied_sortedness_0.1x.spec.json",
    "Tec-5": "x-bench-specs/table4/Tec-5_abrupt_shift.spec.json",
    "Tec-6": "x-bench-specs/table4/Tec-6_varied_kv_size.spec.json",
}


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def op_counts(path):
    counts = {}
    with open(path, "rb") as f:
        for line in f:
            op = line[:line.index(b" ")].decode()
            counts[op] = counts.get(op, 0) + 1
    return counts


def generator_pid(time_pid):
    """The tectonic-cli process GNU time started."""
    for _ in range(100):
        try:
            with open(f"/proc/{time_pid}/task/{time_pid}/children") as f:
                children = f.read().split()
            if children:
                return int(children[0])
        except FileNotFoundError:
            return None
        time.sleep(0.05)
    return None


def memory_kib(pid):
    """VmRSS and VmHWM of a process, None once it has exited."""
    try:
        with open(f"/proc/{pid}/status") as f:
            status = f.read()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return tuple(int(re.search(rf"{k}:\s+(\d+) kB", status).group(1)) for k in ("VmRSS", "VmHWM"))


def run(workload, keyset, trial, binary_sha256):
    out_dir = f"{OUT_DIR}/runs/{workload}/{keyset}"
    os.makedirs(out_dir, exist_ok=True)
    if os.path.exists(TRACE_PATH):
        os.remove(TRACE_PATH)
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)
    cmd = ["/usr/bin/time", "-v", TECTONIC_CLI, "generate", "-w", f"{ROOT_DIR}/{WORKLOADS[workload]}",
           "-o", TRACE_PATH, "--keyset", keyset]
    env = {k: v for k, v in os.environ.items() if k != "TECTONIC_PARALLEL_GEN"}  # single-threaded
    env["RUST_LOG"] = "tectonic=debug"  # progress of every group in steps of 10%, on stdout
    prefix = f"{out_dir}/trial{trial}"
    log(f"{workload} {keyset} trial {trial}: {' '.join(cmd)}")
    t0 = time.perf_counter()
    with open(f"{prefix}.stdout.log", "w") as out, open(f"{prefix}.stderr.log", "w") as err, \
            open(f"{prefix}.rss.log", "w") as rss:
        rss.write("elapsed_s vmrss_kib vmhwm_kib\n")
        proc = subprocess.Popen(cmd, stdout=out, stderr=err, env=env)
        pid = generator_pid(proc.pid)
        while proc.poll() is None:
            mem = memory_kib(pid) if pid else None
            if mem:
                rss.write(f"{time.perf_counter() - t0:.1f} {mem[0]} {mem[1]}\n")
                rss.flush()
            try:
                proc.wait(timeout=SAMPLE_INTERVAL_S)
            except subprocess.TimeoutExpired:
                pass
    wall = time.perf_counter() - t0
    with open(f"{prefix}.stderr.log") as f:
        stderr = re.sub(r"\x1b\[[0-9;]*m", "", f.read())
    if proc.returncode != 0:
        raise RuntimeError(f"{workload} {keyset} failed with exit code {proc.returncode}: {stderr[-2000:]}")
    with open(f"{prefix}.stdout.log") as f:
        stdout = f.read()
    m = re.search(r"Tectonic_Op_Timings: (\{.*\})", stdout)
    rec = {"workload": workload, "keyset": keyset, "trial": trial, "spec": WORKLOADS[workload],
           "host": HOST, "binary_sha256": binary_sha256, "wall_time_s": wall, "timed_out": False,
           "peak_rss_mib": int(re.search(r"Maximum resident set size \(kbytes\): (\d+)", stderr).group(1)) / 1024,
           "gnu_time_elapsed": re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)", stderr).group(1),
           "keysets_used": re.findall(r"\[Tectonic KeySet\] Section (\d+): (\S+)", stderr),
           "op_timings_s": json.loads(m.group(1)) if m else None,
           "trace_bytes": os.path.getsize(TRACE_PATH),
           "op_counts": op_counts(TRACE_PATH),
           "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
    os.remove(TRACE_PATH)
    return rec


def next_job(done):
    with open(QUEUE_PATH) as f:
        for line in f:
            fields = line.split("#", 1)[0].split()
            if not fields:
                continue
            workload, keyset, trial = fields[0], fields[1], int(fields[2])
            if workload not in WORKLOADS:
                sys.exit(f"unknown workload in {QUEUE_PATH}: {workload}")
            if (workload, keyset, trial) not in done:
                return workload, keyset, trial
    return None


def main():
    for d in ("results", "tmp", "logs"):
        os.makedirs(f"{OUT_DIR}/{d}", exist_ok=True)
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"another run is in progress on {HOST}: one run per machine")
    binary_sha256 = sha256(TECTONIC_CLI)
    log(f"{HOST}: {TECTONIC_CLI} sha256 {binary_sha256}, queue {QUEUE_PATH}")
    runs = []
    if os.path.exists(RUNS_PATH):
        with open(RUNS_PATH) as f:
            runs = json.load(f)
    while True:
        job = next_job({(r["workload"], r["keyset"], r["trial"]) for r in runs})
        if job is None:
            log("queue done")
            break
        rec = run(*job, binary_sha256)
        runs.append(rec)
        with open(RUNS_PATH, "w") as f:
            json.dump(runs, f, indent=2)
        log(f"{job[0]} {job[1]} trial {job[2]}: {rec['wall_time_s']:.1f} s, {rec['peak_rss_mib']:.1f} MiB, "
            f"{rec['keysets_used']}, {rec['op_counts']}")


if __name__ == "__main__":
    main()
