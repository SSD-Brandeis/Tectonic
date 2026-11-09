#!/usr/bin/env python3
"""
run_ycsb_tectonic_similarity_io_bytes.py
=========================================
YCSB Workload A Similarity Experiment — All Four Databases with Disk I/O bytes tracking (iostat)

Experiment : run_ycsb_tectonic_similarity_io_bytes
Data dir   : /home/cc/Tectonic/data/ycsb_tectonic_similarity_io_bytes/

This script runs the workload similarity experiment across all four databases (rocksdb, redis, cassandra, scylla)
and records disk read/write MB/s time-series data using `iostat` for all runs.
"""

import os
import sys
import re
import time
import json
import shutil
import subprocess
import collections
import csv
import threading
import argparse
from datetime import datetime

# =============================================================================
#  CONFIG
# =============================================================================
HARNESS_DIR  = "/home/cc/Tectonic/rocksdb-benchmark-harness"
TECTONIC_CLI = "/home/cc/Tectonic/target/release/tectonic-cli"

YCSB_DIR = f"{HARNESS_DIR}/vendor/YCSB"
M2       = "/home/cc/.m2/repository"
YCSB_CP  = ":".join([
    f"{YCSB_DIR}/file/conf",
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar",
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar",
])

HARNESS_BIN  = f"{HARNESS_DIR}/cmake-build-release-with-stats/rocksdb-benchmark-harness"
ROCKSDB_OPTS = f"{HARNESS_DIR}/experiments/workload-similarity/rocksdb-options.ini"
ROCKSDB_DIR  = "/tmp/ycsb_tectonic_sim_rocksdb"

TECTONIC_SPEC = "/home/cc/Tectonic/example-specs/ycsb/a.spec.json"

OUT_DIR      = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_io_bytes"
RESULTS_PATH = f"{OUT_DIR}/results_io_bytes.json"
PLOT_SCRIPT  = "/home/cc/Tectonic/plot_scripts/plot_ycsb_tectonic_similarity_io_bytes.py"
IMAGE_LOCK_PATH = f"{OUT_DIR}/db_images.json"
ORIGINAL_IMAGE_LOCK = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_all_dbs/db_images.json"
LOG_PATH     = f"{OUT_DIR}/run_io_bytes.log"

IOSTAT_DEVICE = "sda"

SCALES    = [2**18, 2**19, 2**20, 2**21, 2**22]
DATABASES = ["rocksdb", "redis", "cassandra", "scylla"]
EXEC_THREADS = 1
DEFAULT_DB_IMAGES = {
    "redis": "redis:latest",
    "cassandra": "cassandra:latest",
    "scylla": "scylladb/scylla:latest",
}
TRACE_GEN_MIN_GB = 25
DB_RUN_MIN_GB = 20

SEP = "=" * 70

# =============================================================================
#  LOGGING & HELPERS
# =============================================================================
_log_fh = None

def log(msg=""):
    print(msg, flush=True)
    if _log_fh:
        _log_fh.write(msg + "\n")
        _log_fh.flush()

def banner(stage, title):
    sep = "=" * 70
    log(f"\n{sep}\n  {stage}: {title}\n{sep}")

def run(cmd, cwd=None, capture=False, check=True):
    log(f"  $ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True)
    if check and r.returncode != 0:
        log(f"  [ERROR] exit={r.returncode}")
        if capture and r.stderr:
            log(r.stderr[:1500])
        sys.exit(1)
    return r

def fail(msg):
    log(f"  [FATAL] {msg}")
    sys.exit(1)

def disk_free_gb():
    _, _, free = shutil.disk_usage("/")
    return free / 1024**3

def check_disk(min_gb=None, context=None):
    free = disk_free_gb()
    msg = f"  [disk] {free:.1f} GB free"
    if context:
        msg += f"  ({context})"
    log(msg)
    if min_gb is not None and free < min_gb:
        fail(f"Insufficient free space for {context or 'current stage'}: "
             f"{free:.1f} GB available, require at least {min_gb:.1f} GB.")

def write_json_atomic(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def trace_expected_ops(scale):
    return scale * 2

def count_ops(path):
    c = collections.Counter()
    with open(path) as f:
        for line in f:
            op = line.split(" ", 1)[0]
            if op in ("I", "P", "U", "BP"):
                c[op] += 1
    return c

def trace_actual_ops(path):
    return sum(count_ops(path).values())

def remove_if_exists(path):
    if os.path.exists(path):
        os.remove(path)
        log(f"  [cleanup] deleted {path}")

def cleanup_trace_artifacts(path, remove_output=False):
    for suffix in (".load", ".run"):
        remove_if_exists(path + suffix)
    if remove_output:
        remove_if_exists(path)

def validate_trace(path, scale):
    if not os.path.exists(path):
        return False
    expected = trace_expected_ops(scale)
    actual = trace_actual_ops(path)
    if actual != expected:
        log(f"  [trace] {path} is incomplete: expected {expected:,} ops, found {actual:,}")
        return False
    log(f"  [trace] validated {path} ({actual:,} ops)")
    return True

def clean_metadata_lines(path):
    with open(path) as f:
        lines = f.readlines()
    cleaned = [l for l in lines if not l.startswith(("FS ", "FE "))]
    if len(cleaned) < len(lines):
        with open(path, "w") as f:
            f.writelines(cleaned)
        log(f"  [clean] removed {len(lines)-len(cleaned)} FS/FE lines")

def load_image_lock():
    # Try local first, then original
    for path in [IMAGE_LOCK_PATH, ORIGINAL_IMAGE_LOCK]:
        if os.path.exists(path):
            try:
                return json.load(open(path))
            except Exception as e:
                log(f"  [image] could not load {path}: {e}")
    return {}

def save_image_lock(image_lock):
    write_json_atomic(IMAGE_LOCK_PATH, image_lock)

def inspect_repo_digests(image_ref):
    r = run(["docker", "image", "inspect", image_ref, "--format", "{{json .RepoDigests}}"],
            capture=True, check=False)
    if r.returncode != 0:
        return None
    text = (r.stdout or "").strip()
    return json.loads(text) if text else []

def ensure_image_locked(db, image_lock):
    ref = image_lock.get(db, DEFAULT_DB_IMAGES[db])
    repo = DEFAULT_DB_IMAGES[db].split(":", 1)[0]
    repo_digests = inspect_repo_digests(ref)
    if repo_digests is None:
        check_disk(TRACE_GEN_MIN_GB, f"before pulling Docker image for {db}")
        run(["docker", "pull", ref])
        repo_digests = inspect_repo_digests(ref)
        if repo_digests is None:
            fail(f"could not inspect Docker image {ref} for {db}")
    locked_ref = next((d for d in repo_digests if d.startswith(repo + "@")), ref)
    if image_lock.get(db) != locked_ref:
        image_lock[db] = locked_ref
        save_image_lock(image_lock)
        log(f"  [image] locked {db} -> {locked_ref}")
    else:
        log(f"  [image] using locked {db} -> {locked_ref}")
    return locked_ref

# =============================================================================
#  IoStat Sampler
# =============================================================================
class IoStatSampler(threading.Thread):
    def __init__(self, device=IOSTAT_DEVICE):
        super().__init__(daemon=True)
        self.device   = device
        self._stop_event = threading.Event()
        self._samples = []
        self._t0      = None
        self._proc    = None

    def run(self):
        self._t0 = time.perf_counter()
        self._proc = subprocess.Popen(
            ["iostat", "-x", "-d", self.device, "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        rkb_col   = None
        wkb_col   = None
        block_num = 0

        for raw in self._proc.stdout:
            if self._stop_event.is_set():
                break
            line = raw.rstrip()

            if line.startswith("Device"):
                block_num += 1
                if rkb_col is None:
                    cols = line.split()
                    for i, c in enumerate(cols):
                        if c == "rkB/s":
                            rkb_col = i
                        if c == "wkB/s":
                            wkb_col = i
                continue

            if line.startswith(self.device) and rkb_col is not None:
                if block_num <= 1:
                    continue  # skip since-boot average
                parts = line.split()
                try:
                    rkb_s = float(parts[rkb_col])
                    wkb_s = float(parts[wkb_col])
                    elapsed = time.perf_counter() - self._t0
                    self._samples.append({
                        "elapsed_s":   round(elapsed, 2),
                        "read_kb_s":   round(rkb_s,  3),
                        "write_kb_s":  round(wkb_s,  3),
                        "read_mb_s":   round(rkb_s  / 1024.0, 4),
                        "write_mb_s":  round(wkb_s  / 1024.0, 4),
                    })
                except (IndexError, ValueError):
                    pass

    def stop(self):  # noqa: A003
        self._stop_event.set()
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def get_timeseries(self):
        return list(self._samples)

    def get_totals(self):
        if not self._samples:
            return {"total_read_mb": 0.0, "total_write_mb": 0.0, "sample_count": 0}
        return {
            "total_read_mb":  round(sum(s["read_kb_s"]  for s in self._samples) / 1024.0, 2),
            "total_write_mb": round(sum(s["write_kb_s"] for s in self._samples) / 1024.0, 2),
            "sample_count":   len(self._samples),
        }

def save_iostat_csv(timeseries, path):
    fields = ["elapsed_s", "read_kb_s", "write_kb_s", "read_mb_s", "write_mb_s"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(timeseries)
    log(f"  [saved iostat csv] {path}  ({len(timeseries)} rows)")

# =============================================================================
#  STAGE 1 — Generate YCSB trace
# =============================================================================
def generate_ycsb(scale, out_path):
    banner("STAGE 1", f"Generate YCSB Workload A  (scale={scale:,}  2^{scale.bit_length()-1})")
    log(f"  Tool      : YCSB FileClient")
    log(f"  Spec      : workloads/workloada")
    log(f"  recordcount = operationcount = {scale:,}")
    log(f"  output     : {out_path}")

    run_p = out_path + ".run"
    cleanup_trace_artifacts(out_path, remove_output=True)
    check_disk(TRACE_GEN_MIN_GB, f"before YCSB trace generation for 2^{scale.bit_length()-1}")

    log(f"\n  [1a] load phase — {scale:,} inserts:")
    run(["java", "-cp", YCSB_CP, "site.ycsb.Client",
         "-db", "site.ycsb.db.FileClient",
         "-P", "workloads/workloada",
         "-p", f"file.output={out_path}",
         "-p", f"recordcount={scale}",
         "-p", f"operationcount={scale}",
         "-load"], cwd=YCSB_DIR)

    check_disk(DB_RUN_MIN_GB, f"before YCSB run phase append for 2^{scale.bit_length()-1}")
    log(f"\n  [1b] run phase — {scale:,} ops:")
    run(["java", "-cp", YCSB_CP, "site.ycsb.Client",
         "-db", "site.ycsb.db.FileClient",
         "-P", "workloads/workloada",
         "-p", f"file.output={run_p}",
         "-p", f"recordcount={scale}",
         "-p", f"operationcount={scale}",
         "-t"], cwd=YCSB_DIR)

    with open(out_path, "a") as fout, open(run_p) as fin:
        shutil.copyfileobj(fin, fout)
    os.remove(run_p)

    n = sum(1 for _ in open(out_path))
    log(f"\n  -> {n:,} total lines written")
    return out_path

# =============================================================================
#  STAGE 2 — Generate Tectonic trace
# =============================================================================
def generate_tectonic(scale, out_path):
    sf = scale / 1_000_000.0
    banner("STAGE 2", f"Generate Tectonic Workload A  (scale={scale:,}  2^{scale.bit_length()-1})")
    log(f"  Tool         : tectonic-cli generate")
    log(f"  Spec         : {TECTONIC_SPEC}")
    log(f"  scale factor : {sf}")
    log(f"  output       : {out_path}")

    cleanup_trace_artifacts(out_path, remove_output=True)
    check_disk(TRACE_GEN_MIN_GB, f"before Tectonic trace generation for 2^{scale.bit_length()-1}")
    run([TECTONIC_CLI, "generate",
         "-w", TECTONIC_SPEC,
         "-o", out_path,
         "-s", str(sf)])

    clean_metadata_lines(out_path)
    n = sum(1 for _ in open(out_path))
    log(f"\n  -> {n:,} total lines written")
    return out_path

# =============================================================================
#  STAGE 3 — Compare traces
# =============================================================================
def compare_traces(ycsb_path, tec_path):
    banner("STAGE 3", "Compare YCSB vs Tectonic traces")
    yc = count_ops(ycsb_path)
    tc = count_ops(tec_path)

    def first_key(path):
        with open(path) as f:
            for line in f:
                if line.startswith("I "):
                    return line.split(" ", 2)[1].strip()
        return "?"

    def first_val_len(path):
        with open(path) as f:
            for line in f:
                if line.startswith("I "):
                    parts = line.split(" ", 2)
                    return len(parts[2].rstrip()) if len(parts) == 3 else 0
        return 0

    log(f"\n  {'Op':<12} {'YCSB':>12} {'Tectonic':>12}  description")
    log(f"  {'-'*52}")
    for op, desc in [("I", "insert (db->Put, load phase)"),
                     ("P", "point query (db->Get, valid key, Zipfian)"),
                     ("U", "update (db->Put, existing key)")]:
        yv, tv = yc.get(op, 0), tc.get(op, 0)
        if yv > 0 or tv > 0:
            log(f"  {op:<12} {yv:>12,} {tv:>12,}  {desc}")
    log(f"  {'Total':<12} {sum(yc.values()):>12,} {sum(tc.values()):>12,}")

    yk, tk = first_key(ycsb_path), first_key(tec_path)
    yvl, tvl = first_val_len(ycsb_path), first_val_len(tec_path)
    log(f"\n  First insert key  — YCSB: {yk}")
    log(f"                       Tectonic: {tk}")
    log(f"  First insert val  — YCSB: {yvl} bytes  Tectonic: {tvl} bytes")

    return {"ycsb_ops": dict(yc), "tectonic_ops": dict(tc),
            "ycsb_key_example": yk, "tectonic_key_example": tk,
            "ycsb_val_bytes": yvl, "tectonic_val_bytes": tvl}

# =============================================================================
#  DATABASE SETUP / RESET / TEARDOWN
# =============================================================================
def setup_db(db, image_ref=None):
    if db == "rocksdb":
        if os.path.exists(ROCKSDB_DIR):
            shutil.rmtree(ROCKSDB_DIR)
        os.makedirs(ROCKSDB_DIR, exist_ok=True)
    elif db == "redis":
        if not image_ref:
            fail("missing locked Redis image reference")
        run(["docker", "rm", "-f", "sim-redis"], check=False, capture=True)
        run(["docker", "run", "--name", "sim-redis", "-p", "6379:6379", "-d",
             image_ref, "redis-server", "--appendonly", "no", "--save", ""])
        for _ in range(30):
            r = run(["docker", "exec", "sim-redis", "redis-cli", "ping"],
                    capture=True, check=False)
            if "PONG" in (r.stdout or ""):
                break
            time.sleep(1)
    elif db == "cassandra":
        if not image_ref:
            fail("missing locked Cassandra image reference")
        run(["docker", "rm", "-f", "sim-cassandra"], check=False, capture=True)
        time.sleep(2)
        run(["docker", "run", "--name", "sim-cassandra", "-p", "9042:9042",
             "-d", image_ref])
        for _ in range(60):
            r = run(["docker", "exec", "sim-cassandra", "cqlsh",
                     "-e", "DESCRIBE KEYSPACES"], capture=True, check=False)
            if "system" in (r.stdout or ""):
                break
            time.sleep(2)
    elif db == "scylla":
        if not image_ref:
            fail("missing locked ScyllaDB image reference")
        run(["docker", "stop", "sim-cassandra"], check=False, capture=True)
        run(["docker", "rm", "-f", "sim-scylla"], check=False, capture=True)
        run(["docker", "run", "--name", "sim-scylla", "-p", "9042:9042", "-d",
             image_ref, "--developer-mode", "1"])
        for _ in range(60):
            r = run(["docker", "exec", "sim-scylla", "cqlsh",
                     "-e", "DESCRIBE KEYSPACES"], capture=True, check=False)
            if "system" in (r.stdout or ""):
                break
            time.sleep(2)

def drop_caches():
    run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"],
        capture=True, check=False)
    log("  [cache] dropped page cache / dentries / inodes")

def restart_db(db, image_ref=None):
    """Fully restart the db (fresh container / fresh data dir) so YCSB and
    Tectonic phases both see identical cold state — avoids one-time startup
    costs (e.g. Scylla commitlog preallocation) leaking into whichever
    phase happens to run first."""
    log(f"  [restart] restarting {db} for a cold, comparable state")
    setup_db(db, image_ref)
    drop_caches()

def teardown_db(db):
    if db == "rocksdb":
        if os.path.exists(ROCKSDB_DIR):
            shutil.rmtree(ROCKSDB_DIR)
    elif db == "redis":
        run(["docker", "rm", "-f", "sim-redis"], check=False, capture=True)
    elif db == "scylla":
        run(["docker", "rm", "-f", "sim-scylla"], check=False, capture=True)
        run(["docker", "start", "sim-cassandra"], check=False, capture=True)
        for _ in range(60):
            r = run(["docker", "exec", "sim-cassandra", "cqlsh",
                     "-e", "DESCRIBE KEYSPACES"], capture=True, check=False)
            if "system" in (r.stdout or ""):
                break
            time.sleep(2)

# =============================================================================
#  EXECUTION — RocksDB C++ harness
# =============================================================================
def run_rocksdb_harness_with_iostat(trace_path, stats_out, latency_out):
    sampler = IoStatSampler(device=IOSTAT_DEVICE)
    sampler.start()
    time.sleep(0.8) # skip the first block

    t0 = time.perf_counter()
    run([HARNESS_BIN, ROCKSDB_OPTS, trace_path, stats_out, latency_out],
        cwd=ROCKSDB_DIR)
    elapsed = time.perf_counter() - t0

    sampler.stop()
    sampler.join(timeout=4)

    # Parse percentile CSV (ns -> us)
    lat = {}
    cur = None
    with open(latency_out) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if "," not in line:
                cur = line.replace(" (ns)", "").replace(" ", "_")
                lat[cur] = {}
            else:
                pct, val = line.split(",", 1)
                lat[cur][pct.strip()] = float(val.strip()) / 1000.0

    ops = sum(1 for l in open(trace_path) if l[0] in ("I", "P", "U"))
    tput = ops / elapsed if elapsed > 0 else 0

    return lat, elapsed, tput, sampler.get_timeseries(), sampler.get_totals()

# =============================================================================
#  EXECUTION — tectonic-cli execute (Redis / Cassandra / ScyllaDB)
# =============================================================================
DB_CLI_DRIVER = {"redis": "redis", "cassandra": "scylla", "scylla": "scylla", "rocksdb": "rocksdb"}
DB_CLI_PATH   = {
    "redis":     "redis://127.0.0.1:6379",
    "cassandra": "127.0.0.1:9042",
    "scylla":    "127.0.0.1:9042",
    "rocksdb":   ROCKSDB_DIR,
}
EXECUTE_OP_NAME_MAP = {"Insert": "insert", "Point Query": "point_query", "Update": "update"}
EXECUTE_OP_METRIC_MAP = {
    "Count": "count",
    "Successful Operations Count": "successful_operations_count",
    "Total Latency": "total_latency_us",
    "Average Latency": "average_latency_us",
    "Minimum Latency": "p0",
    "Maximum Latency": "max_latency_us",
    "25th Percentile Latency": "p25",
    "50th Percentile Latency": "p50",
    "75th Percentile Latency": "p75",
    "95th Percentile Latency": "p95",
    "99th Percentile Latency": "p99",
}
EXECUTE_OVERALL_METRIC_MAP = {
    "Total Operations": "total_operations",
    "Average Latency": "average_latency_us",
    "Throughput (using start and end time)": "throughput_ops_s_start_end",
    "Throughput (using aggregate operation times)": "throughput_ops_s_aggregate_operation_times",
}

def run_tectonic_execute_with_iostat(trace_path, db):
    sampler = IoStatSampler(device=IOSTAT_DEVICE)
    sampler.start()
    time.sleep(0.8) # skip first boot block

    t0 = time.perf_counter()
    r = run([TECTONIC_CLI, "execute",
             "-i", trace_path,
             "-d", DB_CLI_DRIVER[db],
             "-p", DB_CLI_PATH[db],
             "-t", str(EXEC_THREADS)], capture=True)
    elapsed = time.perf_counter() - t0

    sampler.stop()
    sampler.join(timeout=4)

    pat = re.compile(r'\[([^\]]+)\]\s+([^:]+):\s+([\d\.]+)\s*(us|ops/sec|secs)?')
    parsed = {}
    overall = {}
    for line in r.stdout.split("\n"):
        m = pat.match(line.strip())
        if m:
            op, metric, val, _ = m.groups()
            metric = metric.strip()
            value = float(val)
            if op == "Overall":
                normalized = EXECUTE_OVERALL_METRIC_MAP.get(metric)
                if normalized:
                    overall[normalized] = value
                continue
            if op not in parsed:
                parsed[op] = {}
            parsed[op][metric] = value

    lat = {}
    op_metrics = {}
    for op_name, op_key in EXECUTE_OP_NAME_MAP.items():
        if op_name not in parsed:
            continue
        m = parsed[op_name]
        normalized = {}
        for raw_key, out_key in EXECUTE_OP_METRIC_MAP.items():
            if raw_key in m:
                normalized[out_key] = m[raw_key]
        if normalized:
            op_metrics[op_key] = normalized
            lat[op_key] = {
                key: normalized[key]
                for key in ("p0", "p25", "p50", "p75", "p95", "p99")
                if key in normalized
            }

    ops = sum(1 for l in open(trace_path) if l[0] in ("I", "P", "U"))
    tput = ops / elapsed if elapsed > 0 else 0
    return lat, elapsed, tput, op_metrics, overall, sampler.get_timeseries(), sampler.get_totals()

# =============================================================================
#  STAGES 4+5 — Run one (db, scale) pair
# =============================================================================
def run_one_db(db, scale, ycsb_trace, tec_trace, image_ref=None, workloads=("ycsb", "tectonic")):
    scale_lbl = f"2^{scale.bit_length()-1}"
    prefix    = f"{OUT_DIR}/{db}_scale{scale}"
    result    = {}

    # --- YCSB workload run ---
    if "ycsb" in workloads:
        banner("STAGE 4", f"Execute YCSB trace on {db}  [{scale_lbl}]")
        log(f"  trace  : {ycsb_trace}")
        restart_db(db, image_ref)

        lat, wt, tp, op_metrics, overall, timeseries, io_totals = run_tectonic_execute_with_iostat(ycsb_trace, db)
        result["ycsb"] = {
            "latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp,
            "io_bytes_total": io_totals
        }
        if op_metrics:
            result["ycsb"]["operation_metrics"] = op_metrics
        if overall:
            result["ycsb"]["overall_metrics"] = overall

        save_iostat_csv(timeseries, f"{prefix}_ycsb_io_bytes.csv")
        log(f"\n  YCSB:  wall={wt:.2f}s  tput={tp:,.0f} ops/s  read={io_totals['total_read_mb']:.2f}MB  write={io_totals['total_write_mb']:.2f}MB")
        _print_lat(lat)
    else:
        log("\n  [skip] YCSB execution phase not requested (--workloads)")

    # --- Tectonic workload run ---
    if "tectonic" in workloads:
        banner("STAGE 5", f"Execute Tectonic trace on {db}  [{scale_lbl}]  (same driver)")
        log(f"  trace  : {tec_trace}")
        restart_db(db, image_ref)

        lat, wt, tp, op_metrics, overall, timeseries, io_totals = run_tectonic_execute_with_iostat(tec_trace, db)
        result["tectonic"] = {
            "latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp,
            "io_bytes_total": io_totals
        }
        if op_metrics:
            result["tectonic"]["operation_metrics"] = op_metrics
        if overall:
            result["tectonic"]["overall_metrics"] = overall

        save_iostat_csv(timeseries, f"{prefix}_tectonic_io_bytes.csv")
        log(f"\n  Tectonic:  wall={wt:.2f}s  tput={tp:,.0f} ops/s  read={io_totals['total_read_mb']:.2f}MB  write={io_totals['total_write_mb']:.2f}MB")
        _print_lat(lat)
    else:
        log("\n  [skip] Tectonic execution phase not requested (--workloads)")

    return result

def _print_lat(lat):
    log(f"\n  {'op':<14} {'p0':>7} {'p25':>7} {'p50':>7} {'p75':>7} {'p95':>7} {'p99':>7} (us)")
    log(f"  {'-'*54}")
    for op in ["insert", "point_query", "update"]:
        d = lat.get(op, {})
        if not d:
            continue
        log(f"  {op:<14}"
              f" {d.get('p0',0):>7.2f} {d.get('p25',0):>7.2f}"
              f" {d.get('p50',0):>7.2f} {d.get('p75',0):>7.2f}"
              f" {d.get('p95',0):>7.2f} {d.get('p99',0):>7.2f}")

# =============================================================================
#  INTERMEDIATE PLOT
# =============================================================================
def trigger_plot():
    if os.path.exists(PLOT_SCRIPT):
        log("\n  [plot] Regenerating plots...")
        subprocess.run(["python3", PLOT_SCRIPT], capture_output=True)
        log("  [plot] Done.")
    else:
        log(f"  [plot] Script not found: {PLOT_SCRIPT}")

# =============================================================================
#  MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="YCSB vs Tectonic similarity with disk bytes tracking")
    parser.add_argument("--db", nargs="+", choices=DATABASES, default=DATABASES)
    parser.add_argument("--scales", nargs="+", type=int, default=SCALES)
    parser.add_argument("--workloads", nargs="+", choices=["ycsb", "tectonic"],
                         default=["ycsb", "tectonic"],
                         help="Which execution phase(s) to run against the target db. "
                              "Trace generation/comparison always runs regardless.")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    global _log_fh
    _log_fh = open(LOG_PATH, "w")

    scales, dbs, workloads = args.scales, args.db, args.workloads

    log(SEP)
    log("  YCSB vs Tectonic Similarity with Disk I/O bytes tracking (iostat)")
    log(f"  output dir   : {OUT_DIR}")
    log(f"  databases    : {dbs}")
    log(f"  scales       : {[f'2^{s.bit_length()-1}={s:,}' for s in scales]}")
    log(f"  workloads    : {workloads}")
    log(f"  iostat device: {IOSTAT_DEVICE}")
    log(SEP)

    results = {"config": {
        "scales": scales,
        "databases": dbs,
        "ycsb_workload": "workloads/workloada",
        "tectonic_spec": TECTONIC_SPEC,
        "exec_threads": EXEC_THREADS,
        "iostat_device": IOSTAT_DEVICE,
    }, "results": {}}

    if os.path.exists(RESULTS_PATH):
        try:
            loaded = json.load(open(RESULTS_PATH))
            if "results" in loaded:
                results["results"] = loaded["results"]
                log(f"  [resume] loaded checkpoint from {RESULTS_PATH}")
            if "config" in loaded and "db_images" in loaded["config"]:
                results["config"]["db_images"] = loaded["config"]["db_images"]
        except Exception as e:
            log(f"  [resume] could not load: {e}")

    image_lock = load_image_lock()
    if "db_images" in results["config"]:
        for db, ref in results["config"]["db_images"].items():
            image_lock.setdefault(db, ref)
    for db in dbs:
        if db != "rocksdb":
            ensure_image_locked(db, image_lock)
    if image_lock:
        results["config"]["db_images"] = {db: image_lock[db] for db in dbs if db in image_lock}

    for scale in scales:
        skey = str(scale)
        slbl = f"2^{scale.bit_length()-1}"
        todo = [db for db in dbs if skey not in results["results"].get(db, {})]
        if not todo:
            log(f"\n  [skip] scale {slbl} complete for all dbs")
            continue

        log(f"\n{'#'*70}\n#  SCALE = {scale:,}  ({slbl})\n{'#'*70}")
        check_disk(TRACE_GEN_MIN_GB, f"starting scale {slbl}")

        ycsb_trace = f"/tmp/sim_ycsb_{scale}.txt"
        tec_trace  = f"/tmp/sim_tectonic_{scale}.txt"

        if not validate_trace(ycsb_trace, scale):
            generate_ycsb(scale, ycsb_trace)
        else:
            cleanup_trace_artifacts(ycsb_trace, remove_output=False)
            log("  [skip] ycsb trace exists and is complete")

        if not validate_trace(tec_trace, scale):
            generate_tectonic(scale, tec_trace)
        else:
            cleanup_trace_artifacts(tec_trace, remove_output=False)
            log("  [skip] tectonic trace exists and is complete")

        comparison = compare_traces(ycsb_trace, tec_trace)
        check_disk(DB_RUN_MIN_GB, f"after trace generation for scale {slbl}")

        for db in todo:
            log(f"\n{'='*70}\n  DB={db}  SCALE={slbl}\n{'='*70}")
            check_disk(DB_RUN_MIN_GB, f"before database run {db} {slbl}")
            db_res = run_one_db(db, scale, ycsb_trace, tec_trace, image_lock.get(db), workloads)
            db_res["trace_comparison"] = comparison
            teardown_db(db)

            existing = results["results"].setdefault(db, {}).get(skey, {})
            existing.update(db_res)
            results["results"][db][skey] = existing
            write_json_atomic(RESULTS_PATH, results)
            log(f"\n  [checkpoint] saved -> {RESULTS_PATH}")
            trigger_plot()
            check_disk(DB_RUN_MIN_GB, f"after checkpoint for {db} {slbl}")

        if all(skey in results["results"].get(db, {}) for db in dbs):
            for p in [ycsb_trace, tec_trace]:
                if os.path.exists(p):
                    os.remove(p)
                    log(f"  [cleanup] deleted {p}")

    log(f"\n{SEP}\n  Done.  Results: {RESULTS_PATH}\n{SEP}")
    if _log_fh:
        _log_fh.close()

if __name__ == "__main__":
    main()
