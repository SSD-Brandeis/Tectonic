#!/usr/bin/env python3
"""
run_fig12_direct_io_wal_off.py
==============================
Fig. 12 of the paper (RocksDB, YCSB-A at 10x: I/O over time, latency per operation type, bytes read and
written) run again the way the 2026-11-03 reruns of R1.O7 and R1.O9 ran (user, 2026-11-04): the same
executor and harness, with RocksDB's direct I/O on and the WAL off, 5 trials per trace.

The workloads are Fig. 12's (run_scripts/run_rocksdb_similarity_ycsba_10x.sh):
  ycsb    YCSB's shipped workloads/workloada, unchanged, with recordcount = operationcount = 10,000,000
          (FileClient binding of rocksdb-benchmark-harness/vendor/YCSB): load, then run, in one trace
  xbench  x-bench-specs/fig12/workload-a.paper-run.spec.json (Fig. 12's spec as the paper ran it) at scale 10
          (`tectonic-cli generate -s 10`): 10M inserts, then 5M point queries and 5M updates

Executor and harness, as run_ycsb_derivatives_fidelity.py and run_mixgraph_fidelity.py:
  `tectonic-cli execute -d rocksdb -t 1 -c statistics=1;direct_io=1;disable_wal=1`; per trial a fresh
  database, the trace copied to /dev/shm, the page cache dropped, `iostat` of sda (which holds the database)
  for every second. Fig. 12's original harness (rocksdb-benchmark-harness) has no option for the WAL.
Trials: TRIALS rounds; every round runs one trial of each trace, and the order alternates from round to round.

Stages: generate (both traces, once), execute (resumable: done trials are kept), all.
Output: data/revision/Fig12_ycsb-a-x10/ (workloads/, results/generation.json) and its direct-io-wal-off/
        (results/execution.json, db_state/<side>/trial<n>/, logs/, progress.log).
Plots : python3 plot_scripts/plot_r1o7_fig12_style.py --fig12
"""
import os
import sys
import json
import time
import shutil
import argparse
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_ycsb_derivatives_fidelity as r1o7  # noqa: E402

ROOT_DIR = r1o7.ROOT_DIR
TECTONIC_CLI = os.environ.get("TECTONIC_CLI", f"{ROOT_DIR}/bin/tectonic-cli-bb407953")  # has disable_wal
OUT_DIR = f"{ROOT_DIR}/data/revision/Fig12_ycsb-a-x10"
WORKLOAD_DIR = f"{OUT_DIR}/workloads"
GEN_PATH = f"{OUT_DIR}/results/generation.json"
RUN_DIR = f"{OUT_DIR}/direct-io-wal-off"
EXEC_PATH = f"{RUN_DIR}/results/execution.json"
DBSTATE_DIR = f"{RUN_DIR}/db_state"
ROCKSDB_DIR = f"{RUN_DIR}/rocksdb_data"
SHM_TRACE = "/dev/shm/fig12_trace.txt"
EXECUTOR_CONFIG = "statistics=1;direct_io=1;disable_wal=1"
NAME = "ycsb-a-x10"
SIDES = ("ycsb", "xbench")
TRIALS = 5

# Fig. 12's generation, as run_rocksdb_similarity_ycsba_10x.sh
OP_COUNT = 10_000_000
XBENCH_SCALE = 10
YCSB_DIR = f"{ROOT_DIR}/rocksdb-benchmark-harness/vendor/YCSB"
M2 = os.path.expanduser("~/.m2/repository")
YCSB_CP = ":".join([
    f"{YCSB_DIR}/file/conf", f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar",
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar"])
# The spec as the paper's Fig. 12 run used it (2026-07-17; rocksdb-benchmark-harness commit 1761d67): updates write
# 109 bytes, YCSB-A's one 100-byte field `{fieldN=...}`. Commit b941aab (2026-09-21, "minor") later set them to
# 1090 in the harness's copy; the first run of this script used that copy (superseded/update-1090/).
XBENCH_SPEC = f"{ROOT_DIR}/x-bench-specs/fig12/workload-a.paper-run.spec.json"
TRACES = {side: f"{WORKLOAD_DIR}/{side}.txt" for side in SIDES}

log = r1o7.log


def progress(msg):
    with open(f"{RUN_DIR}/progress.log", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def op_counts(path):
    counts = {}
    with open(path, "rb") as f:
        for line in f:
            op = line[:line.find(b" ")].decode()
            counts[op] = counts.get(op, 0) + 1
    return counts


def stage_generate(args):
    os.makedirs(WORKLOAD_DIR, exist_ok=True)
    gen = r1o7.load_json(GEN_PATH, {})
    for side in SIDES:
        if side in gen and os.path.exists(TRACES[side]) and not args.force:
            log(f"  {side}: trace exists, kept")
            continue
        r1o7.banner(f"GENERATE {side}")
        cost = r1o7.GenCost()
        if side == "ycsb":
            parts = [f"{WORKLOAD_DIR}/.ycsb-load.part", f"{WORKLOAD_DIR}/.ycsb-run.part"]
            for part, phase in zip(parts, ("-load", "-t")):
                if os.path.exists(part):
                    os.remove(part)  # the FileClient appends
                cost.add(*r1o7.timed_run(
                    ["java", "-cp", YCSB_CP, "site.ycsb.Client", "-db", "site.ycsb.db.FileClient",
                     "-P", "workloads/workloada", "-p", f"file.output={part}", "-p", f"recordcount={OP_COUNT}",
                     "-p", f"operationcount={OP_COUNT}", phase],
                    cwd=YCSB_DIR, stdout_path=f"{WORKLOAD_DIR}/ycsb{phase}.out"))
            with open(TRACES[side] + ".tmp", "wb") as out:
                for part in parts:
                    with open(part, "rb") as f:
                        shutil.copyfileobj(f, out, 1 << 24)
                    os.remove(part)
            os.replace(TRACES[side] + ".tmp", TRACES[side])
            source = {"ycsb_dir": YCSB_DIR, "workload": "workloads/workloada (unchanged)",
                      "recordcount": OP_COUNT, "operationcount": OP_COUNT,
                      "ycsb_commit": subprocess.run(["git", "-C", YCSB_DIR, "rev-parse", "HEAD"],
                                                    stdout=subprocess.PIPE, text=True).stdout.strip()}
        else:
            shutil.copy2(XBENCH_SPEC, f"{WORKLOAD_DIR}/xbench.spec.json")
            cost.add(*r1o7.timed_run([TECTONIC_CLI, "generate", "-w", XBENCH_SPEC, "-s", str(XBENCH_SCALE),
                                      "-o", TRACES[side]], stdout_path=f"{WORKLOAD_DIR}/xbench_generate.out"))
            source = {"spec": XBENCH_SPEC, "scale": XBENCH_SCALE, "tectonic_cli": TECTONIC_CLI,
                      "tectonic_cli_sha256": r1o7.sha256_file(TECTONIC_CLI)}
        gen[side] = {"trace": TRACES[side], "source": source, "cost": cost.as_dict(),
                     "op_counts": op_counts(TRACES[side]), "bytes": os.path.getsize(TRACES[side]),
                     "sha256": r1o7.sha256_file(TRACES[side]), "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
        r1o7.write_json_atomic(GEN_PATH, gen)
        log(f"  -> {gen[side]['op_counts']}, {gen[side]['bytes'] / 2 ** 30:.2f} GiB, {cost.as_dict()}")
        progress(f"generated {side}: {gen[side]['op_counts']}")


def execute_trial(side, trial):
    if os.path.exists(ROCKSDB_DIR):
        shutil.rmtree(ROCKSDB_DIR)
    os.makedirs(ROCKSDB_DIR)
    out_dir = f"{DBSTATE_DIR}/{side}/trial{trial}"
    os.makedirs(out_dir, exist_ok=True)
    shutil.copyfile(TRACES[side], SHM_TRACE)
    r1o7.drop_caches()
    cmd = [TECTONIC_CLI, "execute", "-i", SHM_TRACE, "-d", "rocksdb", "-p", ROCKSDB_DIR, "-t", "1",
           "-c", EXECUTOR_CONFIG]
    log(f"  $ {' '.join(cmd)}   (trace: {TRACES[side]})")
    iostat = r1o7.Iostat(r1o7.IOSTAT_DEVICE)
    t0 = time.perf_counter()
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    wall = time.perf_counter() - t0
    subprocess.run(["sync"], check=True)  # writes still in the page cache belong to this run
    time.sleep(2)
    samples = iostat.stop()
    os.remove(SHM_TRACE)
    with open(f"{out_dir}/iostat.csv", "w") as f:
        f.write("second,read_MBps,write_MBps\n")
        for t, (rd, wr) in enumerate(samples):
            f.write(f"{t},{rd},{wr}\n")
    with open(f"{out_dir}/cli_raw.log", "w") as f:
        f.write(r.stdout)
    if r.returncode != 0 or "[ERROR]" in r.stderr:
        log(r.stderr[-3000:])
        r1o7.fail(f"execute failed for {side} trial {trial}")
    ops, overall, read_stats, rocks, windows = r1o7.parse_execute_output(r.stdout)
    tickers = r1o7.parse_rocksdb_statistics(r.stdout)
    if not tickers:
        r1o7.fail(f"no RocksDB statistics for {side} trial {trial}")
    shutil.copy2(f"{ROCKSDB_DIR}/LOG", f"{out_dir}/rocksdb_LOG.txt")
    sst = [f for f in os.listdir(ROCKSDB_DIR) if f.endswith(".sst")]
    rocks["SST total size (MB)"] = sum(os.path.getsize(f"{ROCKSDB_DIR}/{f}") for f in sst) / 2 ** 20
    rocks["SST file count"] = len(sst)
    return {"trial": trial, "wall_time_s": wall, "end_to_end_time_s": overall.get("End to End Time"),
            "overall": overall, "operations": ops, "windows": windows, "read_stats": read_stats,
            "rocksdb": rocks, "rocksdb_statistics": tickers,
            "device_io": {"device": r1o7.IOSTAT_DEVICE, "read_MBps": [x for x, _ in samples],
                          "write_MBps": [y for _, y in samples]},
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}


def stage_execute(args):
    gen = r1o7.load_json(GEN_PATH, {})
    for side in SIDES:
        if side not in gen or r1o7.sha256_file(TRACES[side]) != gen[side]["sha256"]:
            r1o7.fail(f"trace of {side} missing or changed; run --stage generate")
    results = r1o7.load_json(EXEC_PATH, {})
    done = results.setdefault(NAME, {})
    # --redo side:trial: a trial disturbed by other I/O on sda (2026-11-05: the host's apt timers) runs again; its
    # record and database state move to ../superseded/disturbed-trials/
    for item in args.redo:
        side, trial = item.split(":")[0], int(item.split(":")[1])
        old = [t for t in done.get(side, []) if t["trial"] == trial]
        if not old:
            continue
        dst = f"{OUT_DIR}/superseded/disturbed-trials"
        os.makedirs(f"{dst}/db_state/{side}", exist_ok=True)
        moved = r1o7.load_json(f"{dst}/execution.json", {})
        moved.setdefault(NAME, {}).setdefault(side, []).append(old[0])
        r1o7.write_json_atomic(f"{dst}/execution.json", moved)
        shutil.move(f"{DBSTATE_DIR}/{side}/trial{trial}", f"{dst}/db_state/{side}/trial{trial}")
        done[side] = [t for t in done[side] if t["trial"] != trial]
        r1o7.write_json_atomic(EXEC_PATH, results)
        progress(f"redo {side} trial {trial}: old one moved to superseded/disturbed-trials/")
    for rnd in range(1, args.trials + 1):
        order = SIDES if rnd % 2 else SIDES[::-1]
        for pos, side in enumerate(order, 1):
            trials = done.setdefault(side, [])
            if any(t["trial"] == rnd for t in trials):
                continue
            r1o7.banner(f"EXECUTE round {rnd}/{args.trials}: {pos}. {side}")
            rec = execute_trial(side, rnd)
            rec["position"] = pos
            if f"{side}:{rnd}" in args.redo:
                rec["redo"] = True  # run after the 5 rounds, not in its round's slot
            trials.append(rec)
            r1o7.write_json_atomic(EXEC_PATH, results)
            msg = f"round {rnd} {side}: {rec['end_to_end_time_s']:.1f} s, " \
                  f"{rec['overall'].get('Throughput (using start and end time)', 0):.0f} ops/s, " \
                  f"wal.bytes {rec['rocksdb_statistics'].get('rocksdb.wal.bytes')}"
            log(f"  -> {msg}")
            progress(msg)
    if os.path.exists(ROCKSDB_DIR):
        shutil.rmtree(ROCKSDB_DIR)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["generate", "execute", "all"], required=True)
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--force", action="store_true", help="generate: regenerate existing traces")
    ap.add_argument("--redo", nargs="+", default=[], metavar="SIDE:TRIAL",
                    help="execute: run these trials again (e.g. xbench:4, one disturbed by other I/O on sda)")
    args = ap.parse_args()
    for d in (f"{OUT_DIR}/results", f"{RUN_DIR}/results", f"{RUN_DIR}/logs"):
        os.makedirs(d, exist_ok=True)
    r1o7._log_fh = open(f"{RUN_DIR}/logs/run.log", "a")
    r1o7.banner(f"run_fig12_direct_io_wal_off.py  stage={args.stage}  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if not os.access(TECTONIC_CLI, os.X_OK):
        r1o7.fail(f"no executable {TECTONIC_CLI}")
    if args.stage in ("generate", "all"):
        stage_generate(args)
    if args.stage in ("execute", "all"):
        stage_execute(args)


if __name__ == "__main__":
    main()
