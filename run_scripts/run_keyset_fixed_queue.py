#!/usr/bin/env python3
"""
run_keyset_fixed_queue.py
=========================
R2.O2 (ii): fixed VecKeySet on the Table 4 workloads where it hit the 600 s timeout of the KeySet
experiment, run to completion, one trial each (decided with the authors, 2026-10-26): X-1, X-5,
X-6 at full scale and X-3 at 0.1x (1M unique inserts instead of 10M; everything else unchanged),
plus Algorithm 1 on X-3 0.1x (5 trials) as its baseline. 2026-10-31 (authors): 5 trials of both KeySets for
X-3, X-5 and X-6 at 0.1x (op counts / 10, nothing else), all on fixedkeyset-2, trials 31-35. The runs are spread over several machines
of the same hardware; a machine never runs two of them at the same time (a lock enforces it).

Each machine works through its own queue, `queue/<host>.txt`, one job per line:
`<workload> <keyset> <trial>`. The queue is re-read before every job, so a job that has not started
can be moved to another machine by deleting its line. A job already in `results/runs.<host>.json` is
skipped.

Same run as `run_scripts/run_keyset_fixed_to_completion.py`, without a timeout:
`/usr/bin/time -v xbench-cli generate -w <spec> -o <trace> --keyset <k>`, single-threaded
(XBENCH_PARALLEL_GEN unset), RUST_LOG=xbench=debug, `sync; echo 3 > /proc/sys/vm/drop_caches`
before the run. Wall time and peak RSS from GNU time; VmRSS and VmHWM of the generator sampled every
10 s into `trial<N>.rss.log`, so a run that is stopped still has its peak memory so far.

KV-Bench (2026-10-31, decided with the authors): the same runs on KV-Bench I-V (`example-specs/kvbench/`)
with the fixed KeySet `VecHashSetKeySet` (`--keyset vec-hashset`, KV-Bench's design: a vector plus a hash
set), unchanged, and Algorithm 1 (`--keyset adaptive`), 5 trials each, into
`R1.O8-R2.O2_keyset-selection/kvbench-fixed-vec-hashset/` (`--out-dir`).

YCSB (2026-10-31, decided with the authors): the same runs on YCSB A-F with every operation untracked, as in
YCSB, which computes keys from key numbers (`example-specs/ycsb-untracked/`, written by
`run_scripts/make_ycsb_untracked_specs.py`), with the fixed KeySet
`EmptyKeySet` (`--keyset empty`, YCSB's design: no keys stored) and Algorithm 1, 5 trials each, into
`R1.O8-R2.O2_keyset-selection/ycsb-fixed-empty/`.

Before it starts, the runner checks that no other `xbench-cli` (generate or execute) runs on the machine, so a
machine never runs two experiments at once, also across output folders.

X-2 (2026-11-09): a rerun with range-delete selectivity pareto scale 0.01 instead of 1.0 went to
`R1.O8-R2.O2_keyset-selection/superseded/x2-range-delete-scale0.01/`. Reverted and stopped 2026-11-10 (authors): scale 1.0
is not incorrect and keeps the paper consistent, so the spec is back to scale 1.0 and that rerun is not used.

Usage: python3 run_scripts/run_keyset_fixed_queue.py [--out-dir <dir>] [--binary <xbench-cli>]   (on the run machine, inside tmux)

Output (default --out-dir): data/revision/R1.O8-R2.O2_keyset-selection/table4-fixed-to-completion-multimachine/
  queue/<host>.txt                          the jobs of a machine
  results/runs.<host>.json                  one record per finished run
  runs/<workload>/<keyset>/trial<N>.{stdout,stderr,rss}.log
  logs/console.<host>.out
"""
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import os
import re
import sys
import json
import time
import fcntl
import socket
import hashlib
import subprocess

ROOT_DIR = REPO_ROOT
XBENCH_CLI = f"{ROOT_DIR}/target/release/xbench-cli"
if "--binary" in sys.argv:  # a frozen copy, so a rebuild of target/ during a multi-day queue does not change it
    XBENCH_CLI = os.path.abspath(sys.argv[sys.argv.index("--binary") + 1])
OUT_DIR = f"{ROOT_DIR}/data/revision/R1.O8-R2.O2_keyset-selection/table4-fixed-to-completion-multimachine"
if "--out-dir" in sys.argv:
    OUT_DIR = os.path.abspath(sys.argv[sys.argv.index("--out-dir") + 1])
HOST = socket.gethostname()
QUEUE_PATH = f"{OUT_DIR}/queue/{HOST}.txt"
RUNS_PATH = f"{OUT_DIR}/results/runs.{HOST}.json"
TRACE_PATH = f"{OUT_DIR}/tmp/trace.txt"
LOCK_PATH = f"{OUT_DIR}/tmp/run.lock"
SAMPLE_INTERVAL_S = 10
WORKLOADS = {
    "X-1": "x-bench-specs/table4/X-1_multi_stage.spec.json",
    "X-2": "x-bench-specs/table4/X-2_interleaved.spec.json",  # pareto scale 1.0, as in the paper (scale 0.01 reverted 2026-11-10)
    "X-3": "x-bench-specs/table4/X-3_varied_sortedness.spec.json",
    "X-3-0.1x": "x-bench-specs/table4/X-3_varied_sortedness_0.1x.spec.json",
    "X-5": "x-bench-specs/table4/X-5_abrupt_shift.spec.json",
    "X-5-0.1x": "x-bench-specs/table4/X-5_abrupt_shift_0.1x.spec.json",
    "X-6": "x-bench-specs/table4/X-6_varied_kv_size.spec.json",
    "X-6-0.1x": "x-bench-specs/table4/X-6_varied_kv_size_0.1x.spec.json",
    "KVBench-I": "example-specs/kvbench/i.spec.json",
    "KVBench-II": "example-specs/kvbench/ii.spec.json",
    "KVBench-III": "example-specs/kvbench/iii.spec.json",
    "KVBench-IV": "example-specs/kvbench/iv.spec.json",
    "KVBench-V": "example-specs/kvbench/v.spec.json",
    "KVBench-IV-0.1x": "example-specs/kvbench-0.1x/iv.spec.json",  # Table 2's KV-IV row at 0.1x (2026-11-02)
    "YCSB-A": "example-specs/ycsb-untracked/a.spec.json",
    "YCSB-B": "example-specs/ycsb-untracked/b.spec.json",
    "YCSB-C": "example-specs/ycsb-untracked/c.spec.json",
    "YCSB-D": "example-specs/ycsb-untracked/d.spec.json",
    "YCSB-E": "example-specs/ycsb-untracked/e.spec.json",
    "YCSB-F": "example-specs/ycsb-untracked/f.spec.json",
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
    """The xbench-cli process GNU time started."""
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


def other_generators():
    """PIDs of `xbench-cli` processes already running on this machine: `generate` of another runner, or
    `execute` of another experiment (2026-10-31: the direct-I/O reruns use the run machines)."""
    pids = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if args[0].endswith(b"xbench-cli"):
            pids.append(int(pid))
    return pids


def run(workload, keyset, trial, binary_sha256):
    out_dir = f"{OUT_DIR}/runs/{workload}/{keyset}"
    os.makedirs(out_dir, exist_ok=True)
    if os.path.exists(TRACE_PATH):
        os.remove(TRACE_PATH)
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)
    cmd = ["/usr/bin/time", "-v", XBENCH_CLI, "generate", "-w", f"{ROOT_DIR}/{WORKLOADS[workload]}",
           "-o", TRACE_PATH, "--keyset", keyset]
    env = {k: v for k, v in os.environ.items() if k != "XBENCH_PARALLEL_GEN"}  # single-threaded
    env["RUST_LOG"] = "xbench=debug"  # progress of every group in steps of 10%, on stdout
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
    m = re.search(r"XBench_Op_Timings: (\{.*\})", stdout)
    rec = {"workload": workload, "keyset": keyset, "trial": trial, "spec": WORKLOADS[workload],
           "spec_sha256": sha256(os.path.join(ROOT_DIR, WORKLOADS[workload])),
           "host": HOST, "binary_sha256": binary_sha256, "wall_time_s": wall, "timed_out": False,
           "peak_rss_mib": int(re.search(r"Maximum resident set size \(kbytes\): (\d+)", stderr).group(1)) / 1024,
           "gnu_time_elapsed": re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)", stderr).group(1),
           "keysets_used": re.findall(r"\[X-Bench KeySet\] Section (\d+): (\S+)", stderr),
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
    if other_generators():
        sys.exit(f"xbench-cli is already running on {HOST} (pid {other_generators()}): one run per machine")
    binary_sha256 = sha256(XBENCH_CLI)
    log(f"{HOST}: {XBENCH_CLI} sha256 {binary_sha256}, queue {QUEUE_PATH}")
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
