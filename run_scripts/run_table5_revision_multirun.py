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
import re
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


_SIZE_TO_MB = {"KB": 1 / 1024, "MB": 1.0, "GB": 1024.0, "TB": 1024.0 ** 2}
_SIZE_TO_GB = {k: v / 1024 for k, v in _SIZE_TO_MB.items()}

_TABLESTATS_METRICS = [
    ("Local write count", "count", r"^\s*Local write count:\s*(\d+)\s*$"),
    ("Local read count", "count", r"^\s*Local read count:\s*(\d+)\s*$"),
    ("Memtable cell count", "count", r"^\s*Memtable cell count:\s*(\d+)\s*$"),
    ("Memtable data size", "bytes", r"^\s*Memtable data size:\s*(\d+)\s*$"),
    ("Space used (total)", "bytes", r"^\s*Space used \(total\):\s*(\d+)\s*$"),
    ("SSTable Compression Ratio", "ratio", r"^\s*SSTable Compression Ratio:\s*(-?[\d.]+)\s*$"),
]

# Database-reported end-state metrics. Each is one counter exactly as the database
# prints it in the log file saved at the end of the trial (no sums, no derived values).
# db -> (saved file suffix, source command, [(exact metric name, unit, regex)]).
# A regex with a second group captures a size unit, converted to the stated unit.
DB_REPORTED_METRICS = {
    "rocksdb": ("_rocksdb_stats.txt", "`rocksdb.stats` property and, with `-c statistics=1`, RocksDB statistics (`Options::statistics`), both returned by RocksDB and printed verbatim by tectonic-cli at the end of the run", [
        ("Cumulative writes: ingest", "GB",
         r"^Cumulative writes:.*?ingest:\s*([\d.]+)\s*(KB|MB|GB|TB)"),
        ("Cumulative WAL: written", "GB",
         r"^Cumulative WAL:.*?written:\s*([\d.]+)\s*(KB|MB|GB|TB)"),
        ("Flush(GB): cumulative", "GB", r"^Flush\(GB\):\s*cumulative\s+([\d.]+)"),
        ("Cumulative compaction: GB write", "GB", r"^Cumulative compaction:\s*([\d.]+)\s*GB write"),
        ("Compaction Stats, Sum row: Size", "MB", r"^\s*Sum\s+\S+\s+([\d.]+)\s+(KB|MB|GB|TB)"),
        # RocksDB statistics tickers (printed only with tectonic-cli -c statistics=1), exact bytes
        ("rocksdb.bytes.written", "bytes", r"^rocksdb\.bytes\.written COUNT : (\d+)$"),
        ("rocksdb.wal.bytes", "bytes", r"^rocksdb\.wal\.bytes COUNT : (\d+)$"),
        ("rocksdb.flush.write.bytes", "bytes", r"^rocksdb\.flush\.write\.bytes COUNT : (\d+)$"),
        ("rocksdb.compact.write.bytes", "bytes", r"^rocksdb\.compact\.write\.bytes COUNT : (\d+)$"),
    ]),
    "redis": ("_redis_info.txt", "`redis-cli INFO all`", [
        ("total_commands_processed", "count", r"^total_commands_processed:(\d+)"),
        ("cmdstat_set: calls", "count", r"^cmdstat_set:calls=(\d+)"),
        ("cmdstat_get: calls", "count", r"^cmdstat_get:calls=(\d+)"),
        ("total_net_input_bytes", "bytes", r"^total_net_input_bytes:(\d+)"),
        ("db0: keys", "count", r"^db0:keys=(\d+)"),
        ("used_memory_dataset", "bytes", r"^used_memory_dataset:(\d+)"),
    ]),
    "cassandra": ("_cassandra_tablestats.txt", "`nodetool tablestats tectonic.data`",
                  _TABLESTATS_METRICS),
    "scylla": ("_scylla_tablestats.txt", "`nodetool tablestats tectonic.data`",
               _TABLESTATS_METRICS),
}


# One database-reported bytes figure per database: the sum of these exact metrics,
# each converted to MB (1 MB = 2^20 bytes; 1 GB = 1024 MB).
DB_REPORTED_BYTES = {
    "rocksdb": ["rocksdb.bytes.written", "rocksdb.wal.bytes",
                "rocksdb.flush.write.bytes", "rocksdb.compact.write.bytes"],
    "redis": ["used_memory_dataset", "total_net_input_bytes"],
    "cassandra": ["Space used (total)", "Memtable data size"],
    "scylla": ["Space used (total)", "Memtable data size"],
}
_UNIT_TO_MB = {"GB": 1024.0, "MB": 1.0, "bytes": 1 / (1024 * 1024)}


def reported_bytes_mb(db, values):
    units = {n: u for n, u, _p in DB_REPORTED_METRICS[db][2]}
    names = DB_REPORTED_BYTES[db]
    if any(n not in values for n in names):
        return None
    return sum(values[n] * _UNIT_TO_MB[units[n]] for n in names)


def read_reported_metrics(db, trial_rec):
    """Extract DB_REPORTED_METRICS for one trial from the log file it recorded."""
    suffix, _cmd, specs = DB_REPORTED_METRICS[db]
    paths = [p for p in trial_rec.get("db_log_files", []) if p.endswith(suffix)]
    if len(paths) != 1 or not os.path.exists(paths[0]):
        return {}
    text = open(paths[0]).read()
    values = {}
    for name, unit, pattern in specs:
        m = re.search(pattern, text, re.MULTILINE)
        if not m:
            continue
        v = float(m.group(1))
        if m.lastindex and m.lastindex > 1:
            v *= (_SIZE_TO_GB if unit == "GB" else _SIZE_TO_MB)[m.group(2)]
        values[name] = v
    return values


def reported_line(db, name, trial_rec):
    """The exact line the database printed for metric `name` in this trial, leading and
    trailing whitespace removed, with the extracted value (and its unit) in bold."""
    suffix, _cmd, specs = DB_REPORTED_METRICS[db]
    pattern = {n: p for n, _u, p in specs}[name]
    paths = [p for p in trial_rec.get("db_log_files", []) if p.endswith(suffix)]
    if len(paths) != 1 or not os.path.exists(paths[0]):
        return None
    text = open(paths[0]).read()
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return None
    ls = text.rfind("\n", 0, m.start()) + 1
    le = text.find("\n", m.end())
    le = len(text) if le == -1 else le
    vs, ve = m.start(1) - ls, m.end(m.lastindex) - ls
    line = text[ls:le]
    marked = line[:vs] + "**" + line[vs:ve] + "**" + line[ve:]
    return marked.strip()


def example_lines(results):
    """Exact printed line per (db, metric), from the first YCSB trial at the largest scale."""
    lines = {}
    for db, (_suffix, _cmd, specs) in DB_REPORTED_METRICS.items():
        scales = sorted(results.get(db, {}), key=int, reverse=True)
        for name, _u, _p in specs:
            for sc in scales:
                trials = results[db][sc].get("ycsb", [])
                line = reported_line(db, name, trials[0]) if trials else None
                if line:
                    lines[(db, name)] = (line, sc, trials[0]["trial"])
                    break
    return lines


def always_zero(summary, db, name):
    """True if the database printed 0 for this metric in every trial of every cell."""
    stats = [summary[db][s][w].get("db_reported", {}).get(name)
             for s in summary.get(db, {}) for w in summary[db][s]]
    return all(v and v["min"] == 0 and v["max"] == 0 for v in stats)


def worked_example(db, names, trial_rec, scale):
    """The DB-reported bytes sum for one trial, written out term by term."""
    units = {n: u for n, u, _p in DB_REPORTED_METRICS[db][2]}
    values = read_reported_metrics(db, trial_rec)
    if any(n not in values for n in names) or any(units[n] != "bytes" for n in names):
        return None
    total = sum(int(values[n]) for n in names)
    terms = " + ".join(f"{int(values[n]):,}" for n in names)
    added = f"{terms} = {total:,} bytes" if len(names) > 1 else f"{total:,} bytes"
    return (f"- **{db}** (trial {trial_rec['trial']}, YCSB, 2^{int(scale).bit_length() - 1}): "
            f"{added}; {total:,} / 1,048,576 = {total / 1048576:.2f} MB")


def build_summary(results):
    summary = {}
    for db, scales in results.items():
        for scale, workloads in scales.items():
            for workload, trials in workloads.items():
                wall = [t["wall_time_s"] for t in trials]
                rd = [t["io_bytes_total"].get("total_read_mb", 0) for t in trials]
                wr = [t["io_bytes_total"].get("total_write_mb", 0) for t in trials]
                io = [r + w for r, w in zip(rd, wr)]
                reported = [read_reported_metrics(db, t) for t in trials] if db in DB_REPORTED_METRICS else []
                names = [n for n, _u, _p in DB_REPORTED_METRICS.get(db, ("", "", []))[2]]
                db_reported = {n: summarize([r[n] for r in reported if n in r]) for n in names}
                combined = [reported_bytes_mb(db, r) for r in reported] if db in DB_REPORTED_BYTES else []
                summary.setdefault(db, {}).setdefault(scale, {})[workload] = {
                    "wall_time_s": summarize(wall),
                    "io_total_mb": summarize(io),
                    "io_read_mb": summarize(rd),
                    "io_write_mb": summarize(wr),
                    "db_reported": db_reported,
                    "db_reported_bytes_mb": summarize([c for c in combined if c is not None]),
                }
    return summary


def mean_diff_pct(x, y):
    return (abs(x["mean"] - y["mean"]) / y["mean"] * 100) if y and y["mean"] else 0.0


def fmt_reported(stat, unit):
    if not stat:
        return "—"
    v = stat["mean"]
    if unit == "GB":
        return f"{v:.3f}"
    if unit == "MB":
        return f"{v:.2f}"
    if unit == "ratio":
        return f"{v:.4f}"
    return f"{v:,.0f}" if v == int(v) else f"{v:,.1f}"


def write_table(summary, results):
    headers = ["Database", "Scale", "Workload", "Trials",
               "Execution Time mean (s)", "Std Dev (s)", "CV (%)", "Mean Diff vs YCSB (%)",
               "Bytes Transferred mean (MB)", "Std Dev (MB)", "CV (%)", "Mean Diff vs YCSB (%)",
               "DB-Reported Bytes mean (MB)", "Std Dev (MB)", "CV (%)"]
    aligns = [":---", ":---:", ":---:", ":---:",
              "---:", "---:", "---:", ":---:",
              "---:", "---:", "---:", ":---:",
              "---:", "---:", "---:"]
    bd_headers = ["Database", "Scale", "Workload",
                  "Read mean (MB)", "Read Std Dev (MB)", "Write mean (MB)", "Write Std Dev (MB)",
                  "Write Mean Diff vs YCSB (%)"]
    bd_aligns = [":---", ":---:", ":---:", "---:", "---:", "---:", "---:", ":---:"]
    rows, bd_rows = [], []
    for db in DEFAULT_DATABASES:
        if db not in summary:
            continue
        for scale in sorted(summary[db], key=int):
            slbl = f"2^{int(scale).bit_length() - 1}"
            ys = summary[db][scale].get("ycsb", {})
            xs = summary[db][scale].get("tectonic", {})
            for label, s in (("YCSB", ys), ("X-Bench", xs)):
                t, b = s.get("wall_time_s"), s.get("io_total_mb")
                r, w = s.get("io_read_mb"), s.get("io_write_mb")
                if not t:
                    continue
                is_x = label == "X-Bench"
                t_diff = f"**{mean_diff_pct(t, ys.get('wall_time_s')):.2f}%**" if is_x else "—"
                b_diff = f"**{mean_diff_pct(b, ys.get('io_total_mb')):.2f}%**" if is_x else "—"
                d = s.get("db_reported_bytes_mb")
                d_cols = ([f"{d['mean']:.2f}", f"{d['stdev']:.2f}", f"{d['cv_pct']:.2f}"]
                          if d else ["—", "—", "—"])
                rows.append([f"**{db}**", slbl, label, t["n"],
                             f"{t['mean']:.2f}", f"{t['stdev']:.2f}", f"{t['cv_pct']:.2f}", t_diff,
                             f"{b['mean']:.2f}", f"{b['stdev']:.2f}", f"{b['cv_pct']:.2f}", b_diff]
                            + d_cols)
                if r and w:
                    yw = ys.get("io_write_mb")
                    if not is_x:
                        w_diff = "—"
                    elif yw and yw["mean"]:
                        w_diff = f"{mean_diff_pct(w, yw):.2f}%"
                    else:
                        w_diff = "n/a"
                    bd_rows.append([f"**{db}**", slbl, label,
                                    f"{r['mean']:.2f}", f"{r['stdev']:.2f}",
                                    f"{w['mean']:.2f}", f"{w['stdev']:.2f}", w_diff])
    table = base.format_aligned_markdown_table(headers, aligns, rows)
    bd_table = base.format_aligned_markdown_table(bd_headers, bd_aligns, bd_rows)

    scales = sorted({int(s) for db in summary for s in summary[db]})
    slbls = [f"2^{s.bit_length() - 1}" for s in scales]
    examples = example_lines(results)

    def exact(db, name):
        return examples[(db, name)][0] if (db, name) in examples else name

    ex_scale = {db: examples[(db, n)][1] for (db, n) in examples}
    rb_headers = ["Database", "Metric (MB): sum of the bold values in these exact lines"] + [f"{l} YCSB / X-Bench" for l in slbls] + ["Max CV (%)"]
    rb_aligns = [":---", ":---"] + ["---:"] * len(scales) + ["---:"]
    val_headers = ["Metric: bold value in this exact line", "Unit"] + [f"{l} YCSB / X-Bench" for l in slbls]
    val_aligns = [":---", ":---"] + ["---:"] * len(scales)
    rb_rows, st_sources, val_sections, worked = [], [], [], []
    for db in DEFAULT_DATABASES:
        if db not in summary or db not in DB_REPORTED_METRICS:
            continue
        suffix, cmd, specs = DB_REPORTED_METRICS[db]
        st_sources.append(f"- **{db}**: {cmd}, saved as `<db>_scale<N>_<workload>{suffix}`")
        if db in DB_REPORTED_BYTES:
            vals, cvs = [], []
            for s in scales:
                cell = summary[db].get(str(s), {})
                y = cell.get("ycsb", {}).get("db_reported_bytes_mb")
                x = cell.get("tectonic", {}).get("db_reported_bytes_mb")
                vals.append(f"{fmt_reported(y, 'MB')} / {fmt_reported(x, 'MB')}")
                cvs += [v["cv_pct"] for v in (x, y) if v and v["mean"] > 0]
            terms = [n for n in DB_REPORTED_BYTES[db] if not always_zero(summary, db, n)]
            metric = " + ".join(f"[{exact(db, n)}]" for n in terms)
            big = max(scales)
            ex = worked_example(db, terms, results[db][str(big)]["ycsb"][0], big)
            if ex:
                worked.append(ex)
            rb_rows.append([f"**{db}**", metric] + vals + [f"{max(cvs):.2f}" if cvs else "—"])
        val_rows = []
        for name, unit, _pat in specs:
            if always_zero(summary, db, name):
                continue
            vals = []
            for s in scales:
                cell = summary[db].get(str(s), {})
                y = cell.get("ycsb", {}).get("db_reported", {}).get(name)
                x = cell.get("tectonic", {}).get("db_reported", {}).get(name)
                vals.append(f"{fmt_reported(y, unit)} / {fmt_reported(x, unit)}")
            val_rows.append([exact(db, name), unit] + vals)
        val_sections += [f"### {db}", "",
                         base.format_aligned_markdown_table(val_headers, val_aligns, val_rows), ""]
    rb_table = base.format_aligned_markdown_table(rb_headers, rb_aligns, rb_rows)

    body = [
        "# Table 5 (Revision): Multi-Run Execution Time and Bytes Transferred",
        "",
        f"Each cell is the mean over {TRIALS} independent trials. Every trial starts from a",
        "reset database instance with the OS page cache dropped. Execution time is the",
        "client-perceived end-to-end wall-clock time reported by `tectonic-cli execute`,",
        "which brackets connection setup and teardown exactly as YCSB's `ClientThread` does.",
        "",
        "Bytes transferred uses the original Table 5 definition: block-device read + write",
        f"(MB) on `{base.IOSTAT_DEVICE}`, summed from `iostat -x -d {base.IOSTAT_DEVICE} 1` "
        "samples over the execution window.",
        "",
        "DB-reported bytes is the sum of byte counters printed by the database itself at the",
        "end of each trial (RocksDB statistics, Redis `INFO`, `nodetool tablestats`), converted",
        "to MB; the exact lines summed for each database are listed in the section",
        "\"Database-reported bytes\" below. It excludes the trace read and other host I/O.",
        "",
        "`Mean Diff vs YCSB (%)` compares the X-Bench mean against the YCSB mean for the",
        "same database and scale. It is a workload-equivalence check for a single database,",
        "not a comparison between databases.",
        "",
        "Per-database client and server configuration is documented in",
        "`DATABASE_CONFIGURATION.md`. Raw per-trial values are in",
        "`results_raw.json`; per-cell statistics are in `summary_mean_stdev.json`.",
        "",
        table,
        "",
        "## Bytes transferred: read / write breakdown",
        "",
        "Because the page cache is dropped before every trial, the client reads its",
        "workload trace from disk during execution, so read bytes include the trace file",
        "(about 305 MB at 2^18, doubling with each scale; YCSB and X-Bench traces of the same",
        "scale differ in size by less than 0.1%). Write bytes come from the database alone.",
        "Redis runs with persistence disabled, so its bytes are almost entirely the trace read;",
        "its writes are below 1 MB, so the Redis write percentages compare background noise",
        "(`n/a` where the YCSB mean is zero).",
        "",
        bd_table,
        "",
        "## Database-reported bytes",
        "",
        "One row per database. Each metric is identified by the exact lines the database",
        "printed in the output saved at the end of every trial, before teardown (files under",
        f"`trial <N>/<database>/<workload>/`). The lines are shown as printed in trial 1, YCSB,",
        f"2^{max(scales).bit_length() - 1}, with only leading and trailing whitespace removed; the value taken from each",
        "line is in bold, and the other numbers in the line are that trial's. Per trial, the",
        "bold values are added and the total is converted to MB (1 MB = 2^20 bytes).",
        "Metrics that are 0 in every trial of a database are not shown.",
        "Cells are 5-trial means, YCSB / X-Bench; `Max CV (%)` is the largest",
        "trial-to-trial CV over all scales and both workloads. Unlike device-level bytes,",
        "these counters exclude the trace read and other host I/O. Means and standard",
        "deviations are under `db_reported_bytes_mb` in `summary_mean_stdev.json`.",
        "",
        "Sources:",
        "",
        *st_sources,
        "",
        rb_table,
        "",
        f"Calculation for the trial the lines come from (trial 1, YCSB, 2^{max(scales).bit_length() - 1}). This is one",
        "trial; the table cells above are means over 5 trials.",
        "",
        *worked,
        "",
        "Notes:",
        "",
        "- The RocksDB row uses RocksDB statistics counters, which print exact byte counts.",
        "  The `rocksdb.stats` lines in the per-metric table below round instead: the GB values",
        "  in the lines starting `Cumulative writes:`, `Cumulative WAL:` and",
        "  `Cumulative compaction:` to 0.01 GB (10.24 MB), and the value in the line starting",
        "  `Flush(GB): cumulative` to 0.001 GB.",
        "- Cassandra flushes SSTables only at 2^21 and 2^22. At smaller scales it prints",
        "  `Space used (total): 0` and all data is in the line starting `Memtable data size:`.",
        "",
        "### Reported values per metric (5-trial mean, YCSB / X-Bench)",
        "",
        "Every value extracted from the saved output, including those summed above, shown",
        "with its exact line as in the table above; metrics that are 0 in every trial of a",
        "database are not shown. With no SSTables, Cassandra prints",
        "`SSTable Compression Ratio: -1.00000`. The RocksDB line starting `Sum` is the `Sum` row",
        "of the first `** Compaction Stats [default] **` table, whose header line is",
        "`Level    Files   Size     Score Read(GB)  Rn(GB) Rnp1(GB) Write(GB) WPreComp(GB) Wnew(GB) Moved(GB) W-Amp Rd(MB/s) Wr(MB/s) Comp(sec) CompMergeCPU(sec) Comp(cnt) Avg(sec) KeyIn KeyDrop Rblob(GB) Wblob(GB)`;",
        "the bold value is in its `Size` column.",
        "",
        *val_sections,
    ]
    with open(TABLE_PATH, "w") as f:
        f.write("\n".join(body))


LATEX_PATH_NAME = "table_5_revision.tex"
LATEX_DB_ORDER = [("rocksdb", "RocksDB"), ("redis", "Redis"),
                  ("cassandra", "Cassandra"), ("scylla", "ScyllaDB")]


def _latex_stack(top, bottom, align="r"):
    """Two-line table header cell, bottom-aligned with the one-line headers."""
    return rf"\begin{{tabular}}[b]{{@{{}}{align}@{{}}}}{top}\\{bottom}\end{{tabular}}"


def write_latex_table(summary):
    """Two-column (table*) LaTeX version of the main table: execution time and
    DB-reported bytes per DB, scale and benchmarking suite (5-trial statistics)."""
    scales = sorted({int(s) for db in summary for s in summary[db]})
    lines = [
        r"\begin{table*}[t]",
        r"\centering",
        r"\small",
        r"\setlength{\tabcolsep}{3pt}",
        r"\renewcommand{\arraystretch}{1}",
        r"\caption{Comparison of YCSB and \bench{} generated workloads in terms of execution time and bytes transferred."
        r" Each value is computed over " + str(TRIALS) + r" trials. Diff.\ is the difference between"
        r" the \bench{} and YCSB means relative to the YCSB mean. DB-reported bytes is the sum of byte counters"
        r" reported by each database.}\label{tab:db_execution_times_compare}",
        r"\vspace{-1em}",
        r"\begin{tabular}{@{}l c l rrrr rrrr@{}}",
        r"\toprule",
        r" & & & \multicolumn{4}{c}{\textbf{Execution time}} & \multicolumn{4}{c}{\textbf{DB-reported bytes}} \\",
        r"\cmidrule(lr){4-7} \cmidrule(l){8-11}",
        r"\textbf{DB} & \textbf{Scale} & " + _latex_stack(r"\textbf{Benchmarking}", r"\textbf{suite}", "l")
        + r" & mean (s) & std dev (s) & CV (\%) & " + _latex_stack(r"diff.", r"(\%)")
        + r" & mean (MB) & std dev (MB) & CV (\%) & " + _latex_stack(r"diff.", r"(\%)") + r" \\",
    ]
    for db, name in LATEX_DB_ORDER:
        if db not in summary:
            continue
        lines.append(r"\midrule")
        db_scales = [s for s in scales if all(w in summary[db].get(str(s), {}) for w in ("ycsb", "tectonic"))]
        if not db_scales:
            continue
        for j, scale in enumerate(db_scales):
            cell = summary[db][str(scale)]
            for k, (workload, suite) in enumerate((("ycsb", "YCSB"), ("tectonic", r"\bench{}"))):
                t = cell[workload]["wall_time_s"]
                b = cell[workload]["db_reported_bytes_mb"]
                diff = f"{mean_diff_pct(t, cell['ycsb']['wall_time_s']):.2f}" if workload == "tectonic" else "--"
                b_diff = (f"{mean_diff_pct(b, cell['ycsb']['db_reported_bytes_mb']):.2f}"
                          if workload == "tectonic" else "--")
                db_cell = rf"\multirow{{{2 * len(db_scales)}}}{{*}}{{\textbf{{{name}}}}}" if j == 0 and k == 0 else ""
                scale_cell = rf"\multirow{{2}}{{*}}{{$2^{{{scale.bit_length() - 1}}}$}}" if k == 0 else ""
                lines.append(
                    f"{db_cell} & {scale_cell} & {suite}"
                    f" & {t['mean']:.2f} & {t['stdev']:.2f} & {t['cv_pct']:.2f} & {diff}"
                    f" & {b['mean']:.2f} & {b['stdev']:.2f} & {b['cv_pct']:.2f} & {b_diff} \\\\"
                    + (r" \cmidrule(l){2-11}" if k == 1 and j < len(db_scales) - 1 else ""))
    lines += [r"\bottomrule", r"\end{tabular}", r"\vspace{-1em}", r"\end{table*}", ""]
    with open(f"{RESULTS_DIR}/{LATEX_PATH_NAME}", "w") as f:
        f.write("\n".join(lines))


LATEX_SINGLE_COLUMN_PATH_NAME = "table_5_revision_single_column.tex"


def write_latex_table_single_column(summary, font=r"\footnotesize", tabcolsep="3.4pt"):
    """Single-column (table) version of the LaTeX table: execution time and bytes transferred
    (iostat block-device read + write), DB names rotated, units on their own header row
    under each label (about 300 pt wide at the defaults)."""
    scales = sorted({int(s) for db in summary for s in summary[db]})
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        font,
        rf"\setlength{{\tabcolsep}}{{{tabcolsep}}}",
        r"\renewcommand{\arraystretch}{1}",
        r"\caption{Comparison of YCSB and \bench{} generated workloads in terms of execution time and bytes transferred."
        r" Each value is computed over " + str(TRIALS) + r" trials; SD is the standard deviation and CV the"
        r" coefficient of variation. Diff.\ is the difference between the \bench{} and YCSB means relative to"
        r" the YCSB mean. Bytes transferred is the block-device read and write volume measured by iostat.}"
        r"\label{tab:db_execution_times_compare}",
        r"\vspace{-1em}",
        r"\begin{tabular}{@{}c c l rrrr rrrr@{}}",
        r"\toprule",
        r" & & & \multicolumn{4}{c}{\textbf{Execution time}} & \multicolumn{4}{c}{\textbf{Bytes transferred}} \\",
        r"\cmidrule(lr){4-7} \cmidrule(l){8-11}",
        r" & & \textbf{Bench.} & mean & SD & CV & diff. & mean & SD & CV & diff. \\",
        r"\multirow{-2}{*}{\rotatebox{90}{\textbf{DB}}} & \textbf{Scale}"
        r" & \textbf{suite} & (s) & (s) & (\%) & (\%) & (MB) & (MB) & (\%) & (\%) \\",
    ]
    for db, name in LATEX_DB_ORDER:
        if db not in summary:
            continue
        lines.append(r"\midrule")
        db_scales = [s for s in scales if all(w in summary[db].get(str(s), {}) for w in ("ycsb", "tectonic"))]
        if not db_scales:
            continue
        for j, scale in enumerate(db_scales):
            cell = summary[db][str(scale)]
            for k, (workload, suite) in enumerate((("ycsb", "YCSB"), ("tectonic", r"\bench{}"))):
                t = cell[workload]["wall_time_s"]
                b = cell[workload]["io_total_mb"]
                diff = f"{mean_diff_pct(t, cell['ycsb']['wall_time_s']):.2f}" if workload == "tectonic" else "--"
                b_diff = (f"{mean_diff_pct(b, cell['ycsb']['io_total_mb']):.2f}"
                          if workload == "tectonic" else "--")
                db_cell = (rf"\multirow{{{2 * len(db_scales)}}}{{*}}{{\rotatebox{{90}}{{\textbf{{{name}}}}}}}"
                           if j == 0 and k == 0 else "")
                scale_cell = rf"\multirow{{2}}{{*}}{{$2^{{{scale.bit_length() - 1}}}$}}" if k == 0 else ""
                lines.append(
                    f"{db_cell} & {scale_cell} & {suite}"
                    f" & {t['mean']:.2f} & {t['stdev']:.2f} & {t['cv_pct']:.2f} & {diff}"
                    f" & {b['mean']:.2f} & {b['stdev']:.2f} & {b['cv_pct']:.2f} & {b_diff} \\\\"
                    + (r" \cmidrule(l){2-11}" if k == 1 and j < len(db_scales) - 1 else ""))
    lines += [r"\bottomrule", r"\end{tabular}", r"\vspace{-1em}", r"\end{table}", ""]
    with open(f"{RESULTS_DIR}/{LATEX_SINGLE_COLUMN_PATH_NAME}", "w") as f:
        f.write("\n".join(lines))


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
        f"| Bytes transferred (Table 5) | block-device read + write MB on `{base.IOSTAT_DEVICE}`, summed from "
        f"`iostat -x -d {base.IOSTAT_DEVICE} 1` over the execution window (`io_bytes_total` in `results_raw.json`) |",
        "",
        "Database-reported bytes (supplementary; not the Table 5 bytes metric) are read from",
        "each database's own output, saved at the end of every trial:",
        "",
        "| Database | Source |",
        "| :--- | :--- |",
        *[f"| {db} | {cmd} |" for db, (_suffix, cmd, _specs) in DB_REPORTED_METRICS.items()],
        "",
        "The exact printed lines and the value taken from each are listed in",
        "`table_5_revision.md`, section \"Database-reported bytes\".",
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
def set_out_dir(out_dir):
    """Write results, DB state, logs and the RocksDB data directory under out_dir.
    Workload traces stay in WORKLOAD_DIR, so a rerun executes the same trace files."""
    global OUT_DIR, RESULTS_DIR, DBSTATE_DIR, LOGS_DIR, RESULTS_PATH, SUMMARY_PATH
    global TABLE_PATH, LOG_PATH, DISK_LOG_PATH, ROCKSDB_DIR
    OUT_DIR = os.path.abspath(out_dir)
    RESULTS_DIR = f"{OUT_DIR}/results"
    DBSTATE_DIR = f"{OUT_DIR}/db_state"
    LOGS_DIR = f"{OUT_DIR}/logs"
    RESULTS_PATH = f"{RESULTS_DIR}/results_raw.json"
    SUMMARY_PATH = f"{RESULTS_DIR}/summary_mean_stdev.json"
    TABLE_PATH = f"{RESULTS_DIR}/table_5_revision.md"
    LOG_PATH = f"{LOGS_DIR}/run.log"
    DISK_LOG_PATH = f"{LOGS_DIR}/disk_usage.log"
    ROCKSDB_DIR = f"{OUT_DIR}/rocksdb_data"


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
    parser.add_argument("--report-only", action="store_true",
                        help="rebuild summary, table and documentation from results_raw.json without running")
    parser.add_argument("--out-dir", help=f"experiment directory (default {OUT_DIR}); "
                                          f"workload traces are always read from {WORKLOAD_DIR}")
    parser.add_argument("--rocksdb-config",
                        help='tectonic-cli -c config for RocksDB, e.g. "statistics=1"')
    args = parser.parse_args()

    if args.out_dir:
        set_out_dir(args.out_dir)
    if args.rocksdb_config:
        DB_CLIENT_CONFIG["rocksdb"]["config"] = args.rocksdb_config

    if args.report_only:
        write_documentation()
        results = json.load(open(RESULTS_PATH))["results"]
        summary = build_summary(results)
        base.write_json_atomic(SUMMARY_PATH, summary)
        write_table(summary, results)
        write_latex_table(summary)
        write_latex_table_single_column(summary)
        print(f"Rebuilt {SUMMARY_PATH}, {TABLE_PATH}, {OUT_DIR}/DATABASE_CONFIGURATION.md")
        return

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
        write_table(summary, results)
        write_latex_table(summary)
        write_latex_table_single_column(summary)

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
