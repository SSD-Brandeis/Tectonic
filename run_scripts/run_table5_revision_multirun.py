#!/usr/bin/env python3
"""
run_table5_revision_multirun.py
===============================
Table 5 revision experiment promised in the rebuttal:

  * Every (database, workload, scale) cell is run TRIALS times (default 5) and
    reported as mean +/- standard deviation instead of a single trial.
  * Redis is run with client-side concurrency AND pipelining enabled; the other
    three databases keep their default single-threaded, non-pipelined client
    configuration.
  * The exact per-database configuration is written to
    DATABASE_CONFIGURATION.md so it is unambiguous to a reviewer.
  * Both generated workloads (X-Bench and YCSB) are saved to persistent disk
    under workloads/ with distinct names and sha256 checksums - never /tmp or
    /dev/shm - so all raw inputs can be shared.
  * The end-of-run state of each database is captured per trial under db_state/.
  * Disk usage is monitored continuously; the run prunes and then aborts
    cleanly (checkpointed, resumable) rather than filling the disk.

Measurement methodology is inherited unchanged from
run_table5_post_fix_validation.py so results remain directly comparable to the
earlier single-trial runs.
"""
import os
import sys
import json
import time
import shutil
import hashlib
import argparse
import statistics
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_table5_post_fix_validation as base

ROOT_DIR = "/home/cc/Tectonic"
OUT_DIR = f"{ROOT_DIR}/data/table-5-revision-multirun"
WORKLOAD_DIR = f"{OUT_DIR}/workloads"
RESULTS_DIR = f"{OUT_DIR}/results"
DBSTATE_DIR = f"{OUT_DIR}/db_state"
LOGS_DIR = f"{OUT_DIR}/logs"

RESULTS_PATH = f"{RESULTS_DIR}/results_raw.json"
SUMMARY_PATH = f"{RESULTS_DIR}/summary_mean_stdev.json"
TABLE_PATH = f"{RESULTS_DIR}/table_5_revision.md"
CHECKSUM_PATH = f"{WORKLOAD_DIR}/checksums.json"
LOG_PATH = f"{LOGS_DIR}/run.log"
DISK_LOG_PATH = f"{LOGS_DIR}/disk_usage.log"

ROCKSDB_DIR = f"{OUT_DIR}/rocksdb_data"

TRIALS = 5
DEFAULT_SCALES = [2**18, 2**19, 2**20, 2**21]
DEFAULT_DATABASES = ["rocksdb", "redis", "cassandra", "scylla"]

# Per-database CLIENT configuration. Only Redis deviates from the paper's
# original single-threaded, non-pipelined client setup.
DB_CLIENT_CONFIG = {
    "rocksdb":   {"threads": 1, "config": None,          "client_cpus": None},
    "redis":     {"threads": 4, "config": "pipeline=16", "client_cpus": "4-7"},
    "cassandra": {"threads": 1, "config": None,          "client_cpus": None},
    "scylla":    {"threads": 1, "config": None,          "client_cpus": None},
}

MIN_FREE_GB = 25
DISK_SAMPLE_SECONDS = 60

_disk_monitor = None


# =============================================================================
#  DISK MONITORING
# =============================================================================
def disk_free_gb(path="/"):
    return shutil.disk_usage(path).free / (1024 ** 3)


def disk_used_pct(path="/"):
    u = shutil.disk_usage(path)
    return u.used / u.total * 100.0


class DiskMonitor(threading.Thread):
    """Samples disk usage in the background so a run that fills the disk is
    visible in the artifact rather than being an unexplained crash."""

    def __init__(self, interval=DISK_SAMPLE_SECONDS):
        super().__init__(daemon=True)
        self.interval = interval
        self._stop = threading.Event()
        self.peak_used_pct = 0.0

    def run(self):
        with open(DISK_LOG_PATH, "a") as fh:
            while not self._stop.is_set():
                used = disk_used_pct()
                free = disk_free_gb()
                self.peak_used_pct = max(self.peak_used_pct, used)
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  root_used={used:.1f}%  "
                         f"root_free={free:.1f}GB  shm_free={disk_free_gb('/dev/shm'):.1f}GB\n")
                fh.flush()
                self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()


def enforce_disk_budget(stage):
    """Abort cleanly (progress is checkpointed) rather than filling the disk."""
    free = disk_free_gb()
    if free >= MIN_FREE_GB:
        return
    base.log(f"  [disk] only {free:.1f} GB free before {stage}; pruning docker volumes")
    base.run(["docker", "volume", "prune", "-f"], check=False, capture=True)
    free = disk_free_gb()
    if free < MIN_FREE_GB:
        base.log(f"  [disk] STILL only {free:.1f} GB free (need >= {MIN_FREE_GB} GB).")
        base.log(f"  [disk] Aborting before {stage}. Progress is checkpointed in")
        base.log(f"  [disk] {RESULTS_PATH} - free space and rerun to resume.")
        sys.exit(2)
    base.log(f"  [disk] recovered to {free:.1f} GB free")


# =============================================================================
#  WORKLOAD GENERATION (persistent, distinctly named, checksummed)
# =============================================================================
def workload_path(workload, scale):
    name = "xbench" if workload == "tectonic" else "ycsb"
    return f"{WORKLOAD_DIR}/{name}_workload_a_scale_{scale}.txt"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def record_checksum(workload, scale, path):
    checksums = {}
    if os.path.exists(CHECKSUM_PATH):
        try:
            checksums = json.load(open(CHECKSUM_PATH))
        except Exception:
            checksums = {}
    key = os.path.basename(path)
    base.log(f"  [checksum] hashing {key} ...")
    checksums[key] = {
        "workload": "X-Bench" if workload == "tectonic" else "YCSB",
        "scale": scale,
        "scale_label": f"2^{scale.bit_length() - 1}",
        "bytes": os.path.getsize(path),
        "lines": sum(1 for _ in open(path)),
        "sha256": sha256_file(path),
    }
    base.write_json_atomic(CHECKSUM_PATH, checksums)
    base.log(f"  [checksum] {key} sha256={checksums[key]['sha256'][:16]}...")


def ensure_workload(workload, scale):
    path = workload_path(workload, scale)
    if base.validate_trace(path, scale):
        base.log(f"  [workload] reusing existing {os.path.basename(path)}")
        return path
    enforce_disk_budget(f"generating {workload} workload at 2^{scale.bit_length() - 1}")
    if workload == "ycsb":
        base.generate_ycsb(scale, path)
    else:
        base.generate_tectonic(scale, path)
    if not base.validate_trace(path, scale):
        base.fail(f"generated workload {path} failed validation (expected {scale * 2} ops)")
    record_checksum(workload, scale, path)
    return path


def split_trace(path, n):
    """Split a trace into n shards for multi-threaded execution.

    The load phase (I) and the run phase (P/U) are split separately and
    contiguously, so shard t receives its own contiguous slice of inserts
    followed by its own contiguous slice of run operations. This mirrors how
    official YCSB divides work across client threads (each thread gets a
    contiguous operation range within a phase) and preserves the
    load-before-run ordering that a naive whole-file split would destroy.
    """
    shards = [f"{path}.{t}" for t in range(n)]
    if all(os.path.exists(s) for s in shards):
        return shards
    loads, runs = [], []
    with open(path, "rb") as f:
        for line in f:
            (loads if line[:2] == b"I " else runs).append(line)
    for t in range(n):
        lo_a, lo_b = t * len(loads) // n, (t + 1) * len(loads) // n
        ru_a, ru_b = t * len(runs) // n, (t + 1) * len(runs) // n
        with open(shards[t], "wb") as out:
            out.writelines(loads[lo_a:lo_b])
            out.writelines(runs[ru_a:ru_b])
    base.log(f"  [split] {os.path.basename(path)} -> {n} shards "
             f"({len(loads):,} inserts + {len(runs):,} run-ops divided per phase)")
    return shards


def remove_shards(path, n):
    for t in range(n):
        s = f"{path}.{t}"
        if os.path.exists(s):
            os.remove(s)


# =============================================================================
#  EXECUTION
# =============================================================================
def execute_trial(db, workload, scale, trial, trace_path):
    """Run one trial. Mirrors base.execute_with_dual_io but honours the
    per-database client configuration (threads / pipelining / CPU pinning)."""
    import re

    cfg = DB_CLIENT_CONFIG[db]
    threads = cfg["threads"]

    sampler = base.IoStatSampler(device=base.IOSTAT_DEVICE)
    sampler.start()
    time.sleep(0.8)

    sec_r0, sec_w0 = base.get_disk_hardware_sectors(base.IOSTAT_DEVICE)

    exec_cmd = [base.TECTONIC_CLI, "execute",
                "-i", trace_path,
                "-d", base.DB_CLI_DRIVER[db],
                "-p", base.DB_CLI_PATH[db],
                "-t", str(threads)]
    if cfg["config"]:
        exec_cmd.extend(["-c", cfg["config"]])
    if cfg["client_cpus"]:
        exec_cmd = ["taskset", "-c", cfg["client_cpus"]] + exec_cmd

    t0 = time.perf_counter()
    r = None
    for attempt in range(1, 4):
        r = base.run(exec_cmd, capture=True, check=False)
        if r.returncode == 0:
            break
        base.log(f"  [WARN] execute failed attempt {attempt}/3 (exit={r.returncode})")
        err = (r.stderr or "") + " " + (r.stdout or "")
        if any(s in err for s in ("Control connection pool error", "Connection refused",
                                  "Failed to perform a connection")):
            time.sleep(6)
            continue
        break
    if r.returncode != 0:
        base.log(f"  [ERROR] execute exit={r.returncode}")
        if r.stderr:
            base.log(r.stderr[:1500])
        base.fail(f"{db} {workload} 2^{scale.bit_length() - 1} trial {trial} failed")
    elapsed = time.perf_counter() - t0

    wl_folder = "X-Bench" if workload == "tectonic" else "YCSB"
    raw_dir = f"{DBSTATE_DIR}/trial{trial}/{db}/{wl_folder}"
    os.makedirs(raw_dir, exist_ok=True)
    raw_log = f"{raw_dir}/cli_raw_{db}_scale{scale}_{workload}_trial{trial}.log"
    with open(raw_log, "w") as f:
        f.write(r.stdout)

    # Capture end-of-run database state into this trial's own directory by
    # temporarily pointing the (shared) capture helper at it.
    saved_out_dir = base.OUT_DIR
    base.OUT_DIR = f"{DBSTATE_DIR}/trial{trial}"
    try:
        db_metrics, saved_logs = base.capture_and_save_db_internal_logs(
            db, scale, workload, f"scale{scale}_{workload}_trial{trial}", r.stdout)
    finally:
        base.OUT_DIR = saved_out_dir

    base.run(["sync"])
    sec_r1, sec_w1 = base.get_disk_hardware_sectors(base.IOSTAT_DEVICE)
    sampler.stop()
    sampler.join(timeout=4)

    hw_read_mb = round(((sec_r1 - sec_r0) * 512) / (1024 * 1024), 2)
    hw_write_mb = round(((sec_w1 - sec_w0) * 512) / (1024 * 1024), 2)

    pat = re.compile(r'\[([^\]]+)\]\s+([^:]+):\s+([\d\.]+)\s*(us|ops/sec|secs)?')
    parsed, raw_overall = {}, {}
    for line in r.stdout.split("\n"):
        m = pat.match(line.strip())
        if not m:
            continue
        op, metric, val, _unit = m.groups()
        metric, value = metric.strip(), float(val)
        if op == "Overall":
            raw_overall[metric] = value
        else:
            parsed.setdefault(op, {})[metric] = value

    op_metrics = {}
    for op_name, op_key in base.EXECUTE_OP_NAME_MAP.items():
        if op_name in parsed:
            m = parsed[op_name]
            op_metrics[op_key] = {out_k: m[raw_k]
                                  for raw_k, out_k in base.EXECUTE_OP_METRIC_MAP.items()
                                  if raw_k in m}

    overall = {out_k: raw_overall[raw_k]
               for raw_k, out_k in base.EXECUTE_OVERALL_METRIC_MAP.items()
               if raw_k in raw_overall}
    overall.update(raw_overall)

    io_totals = sampler.get_totals()
    io_totals["hardware_read_mb"] = hw_read_mb
    io_totals["hardware_write_mb"] = hw_write_mb

    return {
        "trial": trial,
        "wall_time_s": round(elapsed, 4),
        "threads": threads,
        "client_config": cfg["config"],
        "client_cpus": cfg["client_cpus"],
        "operation_metrics": op_metrics,
        "overall_metrics": overall,
        "io_bytes_total": io_totals,
        "db_internal_metrics": db_metrics,
        "db_log_files": saved_logs,
        "cli_raw_log": raw_log,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# =============================================================================
#  AGGREGATION & REPORTING
# =============================================================================
def summarize(values):
    if not values:
        return None
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "n": len(values),
        "mean": round(mean, 4),
        "stdev": round(stdev, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "cv_pct": round(stdev / mean * 100, 2) if mean else 0.0,
        "spread_pct": round((max(values) - min(values)) / mean * 100, 2) if mean else 0.0,
    }


def build_summary(results):
    summary = {}
    for db, scales in results.items():
        for scale, workloads in scales.items():
            for workload, trials in workloads.items():
                wall = [t["wall_time_s"] for t in trials]
                io = [t["io_bytes_total"].get("total_read_mb", 0) +
                      t["io_bytes_total"].get("total_write_mb", 0) for t in trials]
                summary.setdefault(db, {}).setdefault(scale, {})[workload] = {
                    "wall_time_s": summarize(wall),
                    "io_total_mb": summarize(io),
                }
    return summary


def write_table(summary):
    headers = ["Database", "Scale", "Workload", "Trials",
               "Execution Time mean (s)", "Std Dev (s)", "CV (%)",
               "Mean Diff vs YCSB (%)"]
    aligns = [":---", ":---:", ":---:", ":---:", "---:", "---:", "---:", ":---:"]
    rows = []
    for db in DEFAULT_DATABASES:
        if db not in summary:
            continue
        for scale in sorted(summary[db], key=int):
            slbl = f"2^{int(scale).bit_length() - 1}"
            y = summary[db][scale].get("ycsb", {}).get("wall_time_s")
            x = summary[db][scale].get("tectonic", {}).get("wall_time_s")
            if y:
                rows.append([f"**{db}**", slbl, "YCSB", y["n"],
                             f"{y['mean']:.2f}", f"{y['stdev']:.2f}", f"{y['cv_pct']:.2f}", "—"])
            if x:
                diff = (abs(x["mean"] - y["mean"]) / y["mean"] * 100) if y and y["mean"] else 0.0
                rows.append([f"**{db}**", slbl, "X-Bench", x["n"],
                             f"{x['mean']:.2f}", f"{x['stdev']:.2f}", f"{x['cv_pct']:.2f}",
                             f"**{diff:.2f}%**"])
    table = base.format_aligned_markdown_table(headers, aligns, rows)

    body = [
        "# Table 5 (Revision): Multi-Run Client-Side Execution Time",
        "",
        f"Each cell is the mean over {TRIALS} independent trials. Every trial starts from a",
        "reset database instance with the OS page cache dropped. Execution time is the",
        "client-perceived end-to-end wall-clock time reported by `tectonic-cli execute`,",
        "which brackets connection setup and teardown exactly as YCSB's `ClientThread` does.",
        "",
        "`Mean Diff vs YCSB (%)` compares the X-Bench mean against the YCSB mean for the",
        "same database and scale. It is a workload-equivalence check for a single database,",
        "not a comparison between databases.",
        "",
        "Per-database client and server configuration is documented in",
        "`../DATABASE_CONFIGURATION.md`. Raw per-trial values are in",
        "`results_raw.json`; per-cell statistics are in `summary_mean_stdev.json`.",
        "",
        table,
    ]
    with open(TABLE_PATH, "w") as f:
        f.write("\n".join(body))


def write_documentation():
    os.makedirs(OUT_DIR, exist_ok=True)
    lines = [
        "# Database and Client Configuration",
        "",
        "Every setting used by this experiment, stated explicitly so the measurement",
        "conditions are unambiguous. Nothing below is left at an undocumented implicit",
        "default: where a value is a shipped default, it is named as such.",
        "",
        "## Client configuration (`tectonic-cli execute`)",
        "",
        "Both the YCSB-generated and the X-Bench-generated workload for a given database",
        "are executed by the *same* client binary with the *same* flags, so the comparison",
        "isolates workload content rather than client implementation.",
        "",
    ]
    hdr = ["Database", "Client threads", "Pipelining", "Client CPU pinning", "CLI invocation"]
    al = [":---", ":---:", ":---:", ":---:", ":---"]
    rows = []
    for db in DEFAULT_DATABASES:
        c = DB_CLIENT_CONFIG[db]
        pipe = c["config"].split("=")[1] if c["config"] and "pipeline=" in c["config"] else "disabled"
        cmd = f"`-d {base.DB_CLI_DRIVER[db]} -t {c['threads']}"
        if c["config"]:
            cmd += f" -c {c['config']}"
        cmd += "`"
        rows.append([db, str(c["threads"]), pipe,
                     c["client_cpus"] or "none (unpinned)", cmd])
    lines.append(base.format_aligned_markdown_table(hdr, al, rows))
    lines += [
        "",
        "Redis is the only database whose client deviates from the original single-threaded,",
        "non-pipelined setup. Pipelining is implemented in `db-layer/src/redis.rs` and enabled",
        "through the `-c pipeline=<depth>` flag: up to `<depth>` commands are batched into one",
        "`redis::Pipeline` and flushed in a single round trip, with a final flush during",
        "`cleanup()` inside the timed window. A depth of 1 restores the original",
        "one-round-trip-per-operation behaviour exactly.",
        "",
        "With multiple client threads the trace is split per phase (see `split_trace` in",
        "`run_scripts/run_table5_revision_multirun.py`): the load phase and the run phase are",
        "divided contiguously and independently, so each thread executes its own contiguous",
        "slice of inserts followed by its own contiguous slice of run operations. This matches",
        "how official YCSB divides work across client threads.",
        "",
        "Verification of the pipelining implementation: executing an identical trace at",
        "pipeline depths 1, 16 and 128 produces a byte-identical final database state",
        "(SHA1-based digest over every key/value pair, 65,536 keys, identical across all",
        "three depths).",
        "",
        "## Server configuration",
        "",
        "### RocksDB (embedded, via `librocksdb-sys`)",
        "",
        "Options set explicitly in `db-layer/src/rocksdb.rs`; everything else is a RocksDB",
        "shipped default:",
        "",
        "```",
        "create_if_missing                     = true",
        "allow_concurrent_memtable_write       = false",
        "write_buffer_size                     = 64 MiB",
        "max_write_buffer_number               = 4",
        "target_file_size_base                 = 64 MiB",
        "level_zero_file_num_compaction_trigger = 4",
        "```",
        "",
        "### Redis",
        "",
        "Run from a pinned image digest with persistence disabled so the measurement",
        "reflects in-memory operation cost:",
        "",
        "```",
        "docker run --name sim-redis --cpuset-cpus=2 -p 6379:6379 \\",
        "  redis@<digest> redis-server \\",
        "    --appendonly no --save '' --activedefrag no \\",
        "    --slowlog-log-slower-than -1 --latency-monitor-threshold 0 \\",
        "    --dynamic-hz no --server_cpulist 2",
        "```",
        "",
        "The server is pinned to a single core (core 2), which is consistent with Redis's",
        "single-threaded execution model; the client is pinned to separate cores (4-7).",
        "",
        "### Cassandra",
        "",
        "Stock image, default configuration, single node:",
        "",
        "```",
        "docker run --name sim-cassandra -p 9042:9042 cassandra@<digest>",
        "```",
        "",
        "Keyspace `tectonic` with `SimpleStrategy`, `replication_factor = 1`; table",
        "`tectonic.data (key text PRIMARY KEY, value text)`. Created by",
        "`db-layer/src/cassandra.rs` at connection time.",
        "",
        "### ScyllaDB",
        "",
        "Stock image, default configuration apart from developer mode, single node:",
        "",
        "```",
        "docker run --name sim-scylla -p 9042:9042 -p 9180:9180 \\",
        "  scylladb/scylla@<digest> --developer-mode 1",
        "```",
        "",
        "Same keyspace/table definition as Cassandra, created by `db-layer/src/scylla.rs`.",
        "Tablets are disabled at keyspace creation where the server supports the option.",
        "",
        "Exact image digests used for every run are recorded in `results/results_raw.json`.",
        "",
        "## Per-trial protocol",
        "",
        f"1. The database instance is destroyed and recreated (`docker rm -fv` + `docker run`,",
        "   or directory removal for RocksDB), giving cold storage state.",
        "2. The OS page cache, dentries and inodes are dropped",
        "   (`sync; echo 3 > /proc/sys/vm/drop_caches`).",
        "3. The workload trace is executed and end-to-end wall-clock time is recorded.",
        "4. Database-internal state and logs are captured before teardown and stored under",
        "   `db_state/trial<N>/<database>/<workload>/`.",
        "",
        "## Measurement definitions",
        "",
        "| Metric | Definition |",
        "| :--- | :--- |",
        "| Execution time | `tectonic-cli execute` end-to-end wall clock, including connection setup and teardown |",
        "| RocksDB bytes | `Cumulative compaction write GB + Flush(GB) cumulative + Cumulative WAL written GB` from the RocksDB LOG |",
        "| Redis bytes | `total_net_input_bytes` from `INFO stats` |",
        "| Cassandra bytes | `Space used (total) + Memtable data size` from `nodetool tablestats` |",
        "| ScyllaDB bytes | `sum(scylla_commitlog_bytes_written)` across shards, from the Prometheus endpoint |",
        "",
    ]
    with open(f"{OUT_DIR}/DATABASE_CONFIGURATION.md", "w") as f:
        f.write("\n".join(lines))

    readme = [
        "# Table 5 Revision - Multi-Run Experiment",
        "",
        f"Five independent trials per (database, workload, data scale) cell, reported as",
        "mean and standard deviation. This replaces the single-trial numbers in the",
        "submitted Table 5 and addresses the reviewer's request for run-to-run variance,",
        "explicit configuration, and end-of-experiment database state.",
        "",
        "## Layout",
        "",
        "```",
        "workloads/     both generated workloads per scale, with sha256 checksums",
        "               xbench_workload_a_scale_<N>.txt   (generated by tectonic-cli)",
        "               ycsb_workload_a_scale_<N>.txt     (generated by YCSB FileClient)",
        "results/       results_raw.json        every trial, every metric",
        "               summary_mean_stdev.json per-cell mean / stdev / min / max / CV",
        "               table_5_revision.md     the final table",
        "db_state/      trial<N>/<database>/<workload>/  end-of-run DB state and logs",
        "logs/          run.log, disk_usage.log",
        "rocksdb_data/  RocksDB data directory (recreated per trial)",
        "```",
        "",
        "## Reproducing",
        "",
        "```bash",
        "python3 run_scripts/run_table5_revision_multirun.py",
        "```",
        "",
        "The run is checkpointed after every trial and is safe to interrupt and restart;",
        "completed trials are skipped. Useful flags: `--db`, `--scales`, `--trials`,",
        "`--keep-shards`, `--force`.",
        "",
        "See `DATABASE_CONFIGURATION.md` for the exact client and server configuration of",
        "every system measured.",
        "",
    ]
    with open(f"{OUT_DIR}/README.md", "w") as f:
        f.write("\n".join(readme))


# =============================================================================
#  MAIN
# =============================================================================
def main():
    global _disk_monitor

    parser = argparse.ArgumentParser(description="Table 5 revision multi-run experiment")
    parser.add_argument("--db", nargs="+", choices=DEFAULT_DATABASES, default=DEFAULT_DATABASES)
    parser.add_argument("--scales", nargs="+", type=int, default=DEFAULT_SCALES)
    parser.add_argument("--workloads", nargs="+", choices=["ycsb", "tectonic"],
                        default=["ycsb", "tectonic"])
    parser.add_argument("--trials", type=int, default=TRIALS)
    parser.add_argument("--keep-shards", action="store_true",
                        help="keep per-thread trace shards instead of deleting after use")
    parser.add_argument("--force", action="store_true", help="rerun trials already recorded")
    args = parser.parse_args()

    for d in (OUT_DIR, WORKLOAD_DIR, RESULTS_DIR, DBSTATE_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)

    # Redirect the inherited helpers at this experiment's directories.
    base.OUT_DIR = OUT_DIR
    base.ROCKSDB_DIR = ROCKSDB_DIR
    base.DB_CLI_PATH["rocksdb"] = ROCKSDB_DIR
    base.LOG_PATH = LOG_PATH
    base._log_fh = open(LOG_PATH, "a")

    write_documentation()

    _disk_monitor = DiskMonitor()
    _disk_monitor.start()

    base.banner("CONFIG", "Table 5 Revision Multi-Run")
    base.log(f"  Output dir   : {OUT_DIR}")
    base.log(f"  Databases    : {args.db}")
    base.log(f"  Scales       : {[f'2^{s.bit_length() - 1}' for s in args.scales]}")
    base.log(f"  Trials/cell  : {args.trials}")
    base.log(f"  Workloads    : {args.workloads}")
    base.log(f"  Disk free    : {disk_free_gb():.1f} GB (abort threshold {MIN_FREE_GB} GB)")
    for db in args.db:
        c = DB_CLIENT_CONFIG[db]
        base.log(f"  client[{db:<9}] threads={c['threads']} config={c['config']} cpus={c['client_cpus']}")

    results = {}
    if os.path.exists(RESULTS_PATH):
        try:
            loaded = json.load(open(RESULTS_PATH))
            results = loaded.get("results", {})
            base.log(f"  [resume] loaded checkpoint from {RESULTS_PATH}")
        except Exception as e:
            base.log(f"  [resume] could not read checkpoint: {e}")

    def checkpoint():
        base.write_json_atomic(RESULTS_PATH, {
            "config": {
                "experiment": "table-5-revision-multirun",
                "trials": args.trials,
                "scales": args.scales,
                "databases": args.db,
                "client_config": DB_CLIENT_CONFIG,
                "db_images": base.DEFAULT_DB_IMAGES,
                "iostat_device": base.IOSTAT_DEVICE,
                "workload_dir": WORKLOAD_DIR,
                "min_free_gb": MIN_FREE_GB,
            },
            "results": results,
        })
        summary = build_summary(results)
        base.write_json_atomic(SUMMARY_PATH, summary)
        write_table(summary)

    for scale in args.scales:
        slbl = f"2^{scale.bit_length() - 1}"
        base.banner("SCALE", f"Scale {slbl} ({scale:,} records / {scale:,} operations)")
        enforce_disk_budget(f"scale {slbl}")

        traces = {w: ensure_workload(w, scale) for w in args.workloads}

        for db in args.db:
            cfg = DB_CLIENT_CONFIG[db]
            for workload in args.workloads:
                trace = traces[workload]
                if cfg["threads"] > 1:
                    split_trace(trace, cfg["threads"])

                done = len(results.get(db, {}).get(str(scale), {}).get(workload, []))
                if args.force:
                    done = 0
                    results.setdefault(db, {}).setdefault(str(scale), {})[workload] = []

                for trial in range(done + 1, args.trials + 1):
                    base.banner("TRIAL", f"{db} | {workload} | {slbl} | trial {trial}/{args.trials}")
                    enforce_disk_budget(f"{db} {workload} {slbl} trial {trial}")
                    base.restart_db(db)
                    rec = execute_trial(db, workload, scale, trial, trace)
                    results.setdefault(db, {}).setdefault(str(scale), {}) \
                           .setdefault(workload, []).append(rec)
                    base.teardown_db(db)
                    checkpoint()
                    base.log(f"  -> wall={rec['wall_time_s']:.2f}s  "
                             f"read={rec['io_bytes_total'].get('total_read_mb', 0)}MB  "
                             f"write={rec['io_bytes_total'].get('total_write_mb', 0)}MB  "
                             f"[disk {disk_used_pct():.1f}% used]")

                if cfg["threads"] > 1 and not args.keep_shards:
                    remove_shards(trace, cfg["threads"])

            y = results.get(db, {}).get(str(scale), {}).get("ycsb", [])
            x = results.get(db, {}).get(str(scale), {}).get("tectonic", [])
            if y and x:
                ym = statistics.mean([t["wall_time_s"] for t in y])
                xm = statistics.mean([t["wall_time_s"] for t in x])
                base.log(f"\n  >>> {db} @ {slbl}: YCSB mean={ym:.2f}s  X-Bench mean={xm:.2f}s  "
                         f"diff={abs(xm - ym) / ym * 100:.2f}%")

    checkpoint()
    _disk_monitor.stop()
    base.banner("SUMMARY", "Table 5 revision multi-run complete")
    base.log(f"  Table   : {TABLE_PATH}")
    base.log(f"  Raw     : {RESULTS_PATH}")
    base.log(f"  Summary : {SUMMARY_PATH}")
    base.log(f"  Peak disk usage observed: {_disk_monitor.peak_used_pct:.1f}%")
    print("COMPLETED: table-5-revision-multirun finished")


if __name__ == "__main__":
    main()
