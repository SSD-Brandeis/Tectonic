#!/usr/bin/env python3


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
ROOT_DIR     = "/home/cc/Tectonic"
HARNESS_DIR  = f"{ROOT_DIR}/rocksdb-benchmark-harness"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"
TECTONIC_SPEC = f"{ROOT_DIR}/example-specs/ycsb/a.spec.json"

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

OUT_DIR      = f"{ROOT_DIR}/data/table5_rerun"
RESULTS_PATH = f"{OUT_DIR}/results_io_bytes.json"
TABLE_OUT    = f"{OUT_DIR}/table_5_plotting_data"
SUMMARY_OUT  = f"{OUT_DIR}/plotting_data_summary.json"
IMAGE_LOCK_PATH = f"{OUT_DIR}/db_images.json"
ORIGINAL_IMAGE_LOCK = f"{ROOT_DIR}/data/ycsb_tectonic_similarity_io_bytes/db_images.json"
LOG_PATH     = f"{OUT_DIR}/run_table5_rerun.log"

IOSTAT_DEVICE = "sda"
ROCKSDB_DIR   = "/tmp/table5_rerun_rocksdb"

# Default scales: 2^18 to 2^21 first
DEFAULT_SCALES    = [2**18, 2**19, 2**20, 2**21]
DEFAULT_DATABASES = ["rocksdb", "redis", "cassandra", "scylla"]
EXEC_THREADS = 1

DEFAULT_DB_IMAGES = {
    "redis": "redis@sha256:2838d5524559494f6f1cd66e97e76b200d64a633a8614200620755ed395daf32",
    "cassandra": "cassandra@sha256:b89056c366c4b807380cd9a4ac865ad558c0b4d5aac342f69087d0169ca1ddcd",
    "scylla": "scylladb/scylla@sha256:c69e29868793b1c3fdf1402c23df97df616702938fc60048ea20790e4a183145",
}

SEP = "=" * 70

# =============================================================================
#  LOGGING & UTILS
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

def write_json_atomic(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def get_disk_hardware_sectors(device=IOSTAT_DEVICE):
    """Reads exact cumulative hardware sectors read & written from /sys/block/<device>/stat"""
    stat_file = f"/sys/block/{device}/stat"
    try:
        with open(stat_file) as f:
            parts = f.read().split()
            sec_read = int(parts[2])
            sec_write = int(parts[6])
            return sec_read, sec_write
    except Exception as e:
        log(f"  [warn] could not read {stat_file}: {e}")
        return 0, 0

# =============================================================================
#  IOSTAT TIME-SERIES SAMPLER
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

    def stop(self):
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
#  TRACE GENERATION (ISOLATED IN /dev/shm)
# =============================================================================
def generate_ycsb(scale, out_path):
    banner("STAGE 1", f"Generate YCSB Workload A  (scale={scale:,}  2^{scale.bit_length()-1})")
    log(f"  Tool   : YCSB FileClient")
    log(f"  Target : {out_path} (RAM tmpfs)")

    run_p = out_path + ".run"
    if os.path.exists(out_path): os.remove(out_path)
    if os.path.exists(run_p): os.remove(run_p)

    log(f"  [1a] load phase — {scale:,} inserts:")
    run(["java", "-cp", YCSB_CP, "site.ycsb.Client",
         "-db", "site.ycsb.db.FileClient",
         "-P", "workloads/workloada",
         "-p", f"file.output={out_path}",
         "-p", f"recordcount={scale}",
         "-p", f"operationcount={scale}",
         "-load"], cwd=YCSB_DIR)

    log(f"  [1b] run phase — {scale:,} ops:")
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
    sz_mb = os.path.getsize(out_path) / (1024 * 1024)
    log(f"  -> {n:,} total lines written ({sz_mb:.2f} MB in RAM)")
    return out_path

def generate_tectonic(scale, out_path):
    sf = scale / 1_000_000.0
    banner("STAGE 2", f"Generate X-Bench Workload A  (scale={scale:,}  2^{scale.bit_length()-1})")
    log(f"  Tool   : tectonic-cli generate")
    log(f"  Target : {out_path} (RAM tmpfs)")

    if os.path.exists(out_path): os.remove(out_path)

    run([TECTONIC_CLI, "generate",
         "-w", TECTONIC_SPEC,
         "-o", out_path,
         "-s", str(sf)])

    # Clean FS/FE metadata lines if any
    with open(out_path) as f:
        lines = f.readlines()
    cleaned = [l for l in lines if not l.startswith(("FS ", "FE "))]
    if len(cleaned) < len(lines):
        with open(out_path, "w") as f:
            f.writelines(cleaned)

    n = sum(1 for _ in open(out_path))
    sz_mb = os.path.getsize(out_path) / (1024 * 1024)
    log(f"  -> {n:,} total lines written ({sz_mb:.2f} MB in RAM)")
    return out_path

def validate_trace(path, scale):
    if not os.path.exists(path):
        return False
    expected = scale * 2
    c = 0
    with open(path) as f:
        for line in f:
            if line[:2] in ("I ", "P ", "U "):
                c += 1
    return c == expected

# =============================================================================
#  DATABASE LIFECYCLE & CACHE MANAGEMENT
# =============================================================================
def drop_caches():
    run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"],
        capture=True, check=False)
    log("  [cache] dropped kernel page cache / dentries / inodes on disk")

def setup_db(db, image_ref=None):
    if db == "rocksdb":
        if os.path.exists(ROCKSDB_DIR):
            shutil.rmtree(ROCKSDB_DIR)
        os.makedirs(ROCKSDB_DIR, exist_ok=True)
    elif db == "redis":
        ref = image_ref or DEFAULT_DB_IMAGES["redis"]
        run(["docker", "rm", "-f", "sim-redis"], check=False, capture=True)
        run(["docker", "run", "--name", "sim-redis", "-p", "6379:6379", "-d",
             ref, "redis-server", "--appendonly", "no", "--save", ""])
        for _ in range(30):
            r = run(["docker", "exec", "sim-redis", "redis-cli", "ping"],
                    capture=True, check=False)
            if "PONG" in (r.stdout or ""):
                break
            time.sleep(1)
    elif db == "cassandra":
        ref = image_ref or DEFAULT_DB_IMAGES["cassandra"]
        run(["docker", "rm", "-f", "sim-cassandra"], check=False, capture=True)
        time.sleep(2)
        run(["docker", "run", "--name", "sim-cassandra", "-p", "9042:9042",
             "-d", ref])
        for _ in range(60):
            r = run(["docker", "exec", "sim-cassandra", "cqlsh",
                     "-e", "DESCRIBE KEYSPACES"], capture=True, check=False)
            if "system" in (r.stdout or ""):
                break
            time.sleep(2)
    elif db == "scylla":
        ref = image_ref or DEFAULT_DB_IMAGES["scylla"]
        run(["docker", "stop", "sim-cassandra"], check=False, capture=True)
        run(["docker", "rm", "-f", "sim-scylla"], check=False, capture=True)
        run(["docker", "run", "--name", "sim-scylla", "-p", "9042:9042", "-p", "9180:9180", "-d",
             ref, "--developer-mode", "1"])
        for _ in range(60):
            r = run(["docker", "exec", "sim-scylla", "cqlsh",
                     "-e", "DESCRIBE KEYSPACES"], capture=True, check=False)
            if "system" in (r.stdout or ""):
                break
            time.sleep(2)

def restart_db(db, image_ref=None):
    log(f"  [restart] resetting {db} for fresh cold storage state")
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

# =============================================================================
#  BENCHMARK EXECUTION WITH DUAL I/O ACCOUNTING
# =============================================================================
DB_CLI_DRIVER = {"redis": "redis", "cassandra": "scylla", "scylla": "scylla", "rocksdb": "rocksdb"}
DB_CLI_PATH   = {
    "redis":     "redis://127.0.0.1:6379",
    "cassandra": "127.0.0.1:9042",
    "scylla":    "127.0.0.1:9042",
    "rocksdb":   ROCKSDB_DIR,
}
DB_CLI_CONFIG = {}

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
    "Throughput (using start and end time)": "throughput_start_end_ops_s",
    "Throughput (using aggregate operation times)": "throughput_aggregate_ops_s",
    "End to End Time": "end_to_end_time_s",
    "Aggregate Operation Time": "aggregate_operation_time_s",
}

def capture_and_save_db_internal_logs(db, scale, workload, trace_fname, cli_stdout):
    log(f"  [db-internal] capturing internal database logs and telemetry for {db} ({workload} @ 2^{scale.bit_length()-1})...")
    metrics = {}
    saved_files = []
    wl_folder = "X-Bench" if workload == "tectonic" else "YCSB"
    db_out_dir = os.path.join(OUT_DIR, db, wl_folder)
    os.makedirs(db_out_dir, exist_ok=True)
    prefix = f"{db_out_dir}/{db}_scale{scale}_{workload}"

    if db == "rocksdb":
        # 1. Copy RocksDB LOG file(s)
        if os.path.exists(ROCKSDB_DIR):
            for lf in os.listdir(ROCKSDB_DIR):
                if lf.startswith("LOG"):
                    src = os.path.join(ROCKSDB_DIR, lf)
                    dst = f"{prefix}_{lf}.txt"
                    shutil.copy2(src, dst)
                    saved_files.append(dst)

        # 2. Extract stats from stdout
        stats_match = re.search(r'=== RocksDB Internal Stats ===\s*(.*)', cli_stdout, re.DOTALL)
        if stats_match:
            stats_text = stats_match.group(1)
            dst_stats = f"{prefix}_rocksdb_stats.txt"
            with open(dst_stats, "w") as f:
                f.write(stats_text)
            saved_files.append(dst_stats)

            flush_m = re.search(r'Flush\(GB\):\s*cumulative\s+([\d\.]+)', stats_text)
            if flush_m:
                metrics["flush_cumulative_gb"] = float(flush_m.group(1))
                metrics["flush_cumulative_mb"] = round(float(flush_m.group(1)) * 1024, 2)

            comp_m = re.search(r'Cumulative compaction:\s*([\d\.]+)\s*GB write.*?\s*([\d\.]+)\s*GB read.*?\s*([\d\.]+)\s*seconds', stats_text)
            if comp_m:
                metrics["compaction_write_gb"] = float(comp_m.group(1))
                metrics["compaction_write_mb"] = round(float(comp_m.group(1)) * 1024, 2)
                metrics["compaction_read_gb"] = float(comp_m.group(2))
                metrics["compaction_read_mb"] = round(float(comp_m.group(2)) * 1024, 2)
                metrics["compaction_time_s"] = float(comp_m.group(3))

            ingest_m = re.search(r'Cumulative writes:.*?ingest:\s*([\d\.]+)\s*(GB|MB|KB|B)', stats_text)
            if ingest_m:
                val, unit = float(ingest_m.group(1)), ingest_m.group(2)
                mul = {"GB": 1024.0, "MB": 1.0, "KB": 1/1024.0, "B": 1/(1024*1024)}.get(unit, 1.0)
                metrics["ingest_mb"] = round(val * mul, 2)

            wal_m = re.search(r'Cumulative WAL:.*?written:\s*([\d\.]+)\s*(GB|MB|KB|B)', stats_text)
            if wal_m:
                val, unit = float(wal_m.group(1)), wal_m.group(2)
                mul = {"GB": 1024.0, "MB": 1.0, "KB": 1/1024.0, "B": 1/(1024*1024)}.get(unit, 1.0)
                metrics["wal_written_mb"] = round(val * mul, 2)

        if os.path.exists(ROCKSDB_DIR):
            sst_bytes = sum(os.path.getsize(os.path.join(ROCKSDB_DIR, f)) for f in os.listdir(ROCKSDB_DIR) if f.endswith(".sst"))
            metrics["sstable_total_size_mb"] = round(sst_bytes / (1024 * 1024), 2)
            metrics["sstable_file_count"] = sum(1 for f in os.listdir(ROCKSDB_DIR) if f.endswith(".sst"))

    elif db == "redis":
        r_info = run(["docker", "exec", "sim-redis", "redis-cli", "info", "all"], capture=True, check=False)
        info_dst = f"{prefix}_redis_info.txt"
        with open(info_dst, "w") as f:
            f.write(r_info.stdout or "")
        saved_files.append(info_dst)

        r_logs = run(["docker", "logs", "sim-redis"], capture=True, check=False)
        log_dst = f"{prefix}_redis_docker.log"
        with open(log_dst, "w") as f:
            f.write(r_logs.stdout or "")
            if r_logs.stderr:
                f.write("\n" + r_logs.stderr)
        saved_files.append(log_dst)

        info_txt = r_info.stdout or ""
        for line in info_txt.splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if k in ("used_memory", "used_memory_rss", "used_memory_peak", "used_memory_dataset",
                         "total_net_input_bytes", "total_net_output_bytes", "total_commands_processed"):
                    try:
                        val = int(v)
                        metrics[k] = val
                        if "bytes" in k or "memory" in k:
                            metrics[k + "_mb"] = round(val / (1024 * 1024), 2)
                    except ValueError: pass
                elif k in ("used_cpu_sys", "used_cpu_user"):
                    try: metrics[k] = float(v)
                    except ValueError: pass
                elif k in ("cmdstat_get", "cmdstat_set"):
                    cmd_m = re.findall(r'(\w+)=([\d\.]+)', v)
                    metrics[k] = {ck: float(cv) for ck, cv in cmd_m}

        cpu_sys = metrics.get("used_cpu_sys", 0.0)
        cpu_user = metrics.get("used_cpu_user", 0.0)
        metrics["engine_cpu_total_s"] = round(cpu_sys + cpu_user, 4)

        get_usec = metrics.get("cmdstat_get", {}).get("usec", 0.0)
        set_usec = metrics.get("cmdstat_set", {}).get("usec", 0.0)
        metrics["commands_cpu_time_s"] = round((get_usec + set_usec) / 1e6, 4)

    elif db == "cassandra":
        r_stats = run(["docker", "exec", "sim-cassandra", "nodetool", "tablestats", "tectonic.data"], capture=True, check=False)
        stats_dst = f"{prefix}_cassandra_tablestats.txt"
        with open(stats_dst, "w") as f:
            f.write(r_stats.stdout or "")
        saved_files.append(stats_dst)

        r_comp = run(["docker", "exec", "sim-cassandra", "nodetool", "compactionstats"], capture=True, check=False)
        comp_dst = f"{prefix}_cassandra_compactionstats.txt"
        with open(comp_dst, "w") as f:
            f.write(r_comp.stdout or "")
        saved_files.append(comp_dst)

        sys_dst = f"{prefix}_cassandra_system.log"
        run(["docker", "cp", "sim-cassandra:/var/log/cassandra/system.log", sys_dst], capture=True, check=False)
        if os.path.exists(sys_dst):
            saved_files.append(sys_dst)

        for line in (r_stats.stdout or "").splitlines():
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if "Space used (total)" in k:
                    try:
                        metrics["space_used_total_bytes"] = int(v)
                        metrics["space_used_total_mb"] = round(int(v) / (1024 * 1024), 2)
                    except ValueError: pass
                elif "Space used (live)" in k:
                    try:
                        metrics["space_used_live_bytes"] = int(v)
                        metrics["space_used_live_mb"] = round(int(v) / (1024 * 1024), 2)
                    except ValueError: pass
                elif "SSTable count" in k:
                    try: metrics["sstable_count"] = int(v)
                    except ValueError: pass
                elif "Memtable data size" in k:
                    try:
                        metrics["memtable_data_size_bytes"] = int(v)
                        metrics["memtable_data_size_mb"] = round(int(v) / (1024 * 1024), 2)
                    except ValueError: pass
                elif "Memtable cell count" in k:
                    try: metrics["memtable_cell_count"] = int(v)
                    except ValueError: pass
                elif "Local read latency" in k:
                    m = re.search(r'([\d\.]+)\s*ms', v)
                    if m: metrics["local_read_latency_ms"] = float(m.group(1))
                elif "Local write latency" in k:
                    m = re.search(r'([\d\.]+)\s*ms', v)
                    if m: metrics["local_write_latency_ms"] = float(m.group(1))
                elif "Local read count" in k:
                    try: metrics["local_read_count"] = int(v)
                    except ValueError: pass
                elif "Local write count" in k:
                    try: metrics["local_write_count"] = int(v)
                    except ValueError: pass

    elif db == "scylla":
        r_stats = run(["docker", "exec", "sim-scylla", "nodetool", "tablestats", "tectonic.data"], capture=True, check=False)
        stats_dst = f"{prefix}_scylla_tablestats.txt"
        with open(stats_dst, "w") as f:
            f.write(r_stats.stdout or "")
        saved_files.append(stats_dst)

        prom_dst = f"{prefix}_scylla_metrics.prom"
        r_prom = run(["curl", "-s", "http://127.0.0.1:9180/metrics"], capture=True, check=False)
        with open(prom_dst, "w") as f:
            f.write(r_prom.stdout or "")
        saved_files.append(prom_dst)

        r_logs = run(["docker", "logs", "sim-scylla"], capture=True, check=False)
        log_dst = f"{prefix}_scylla_docker.log"
        with open(log_dst, "w") as f:
            f.write(r_logs.stdout or "")
        saved_files.append(log_dst)

        for line in (r_stats.stdout or "").splitlines():
            line = line.strip()
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if "Space used (total)" in k:
                    try:
                        metrics["space_used_total_bytes"] = int(v)
                        metrics["space_used_total_mb"] = round(int(v) / (1024 * 1024), 2)
                    except ValueError: pass
                elif "Space used (live)" in k:
                    try:
                        metrics["space_used_live_bytes"] = int(v)
                        metrics["space_used_live_mb"] = round(int(v) / (1024 * 1024), 2)
                    except ValueError: pass
                elif "SSTable count" in k:
                    try: metrics["sstable_count"] = int(v)
                    except ValueError: pass
                elif "Memtable cell count" in k:
                    try: metrics["memtable_cell_count"] = int(v)
                    except ValueError: pass

        cl_bytes = 0
        for line in (r_prom.stdout or "").splitlines():
            if line.startswith("scylla_commitlog_bytes_written{"):
                parts = line.split()
                if len(parts) >= 2:
                    try: cl_bytes += float(parts[-1])
                    except ValueError: pass
            elif line.startswith("scylla_disk_io_queue_total_read_bytes"):
                parts = line.split()
                if len(parts) >= 2:
                    try: metrics["scylla_disk_read_mb"] = round(float(parts[-1]) / (1024 * 1024), 2)
                    except ValueError: pass
            elif line.startswith("scylla_disk_io_queue_total_write_bytes"):
                parts = line.split()
                if len(parts) >= 2:
                    try: metrics["scylla_disk_write_mb"] = round(float(parts[-1]) / (1024 * 1024), 2)
                    except ValueError: pass
        if cl_bytes > 0:
            metrics["scylla_commitlog_bytes_mb"] = round(cl_bytes / (1024 * 1024), 2)

    log(f"  [db-internal] captured {len(saved_files)} log files, extracted {len(metrics)} internal metrics")
    return metrics, saved_files

def execute_with_dual_io(trace_path, db, scale=None, workload=None):
    # 1. Start iostat sampler
    sampler = IoStatSampler(device=IOSTAT_DEVICE)
    sampler.start()
    time.sleep(0.8) # skip boot block

    # 2. Hardware sectors before
    sec_r0, sec_w0 = get_disk_hardware_sectors(IOSTAT_DEVICE)

    # 3. Execute
    t0 = time.perf_counter()
    exec_cmd = [TECTONIC_CLI, "execute",
                "-i", trace_path,
                "-d", DB_CLI_DRIVER[db],
                "-p", DB_CLI_PATH[db]]
    if DB_CLI_CONFIG.get(db):
        exec_cmd.extend(["-c", DB_CLI_CONFIG[db]])
    exec_cmd.extend(["-t", str(EXEC_THREADS)])
    r = run(exec_cmd, capture=True)
    elapsed = time.perf_counter() - t0

    # Save raw stdout to text file for complete provenance
    trace_fname = os.path.basename(trace_path).replace(".txt", "")
    wl_folder = "X-Bench" if workload == "tectonic" else ("YCSB" if workload == "ycsb" else "")
    db_out_dir = os.path.join(OUT_DIR, db, wl_folder) if wl_folder else os.path.join(OUT_DIR, db)
    os.makedirs(db_out_dir, exist_ok=True)
    raw_log_path = f"{db_out_dir}/cli_raw_{db}_{trace_fname}.log"
    with open(raw_log_path, "w") as f_raw:
        f_raw.write(r.stdout)

    # Capture database internal state and logs before stopping container / cleaning up
    db_metrics, saved_logs = {}, []
    if scale is not None and workload is not None:
        db_metrics, saved_logs = capture_and_save_db_internal_logs(db, scale, workload, trace_fname, r.stdout)

    # 4. Hardware sectors after (flush kernel page cache dirty writeback first)
    run(["sync"])
    sec_r1, sec_w1 = get_disk_hardware_sectors(IOSTAT_DEVICE)

    # 5. Stop iostat sampler
    sampler.stop()
    sampler.join(timeout=4)

    # Compute hardware MB
    hw_read_mb  = round(((sec_r1 - sec_r0) * 512) / (1024 * 1024), 2)
    hw_write_mb = round(((sec_w1 - sec_w0) * 512) / (1024 * 1024), 2)

    # Parse stdout latencies and overall metrics with units
    pat = re.compile(r'\[([^\]]+)\]\s+([^:]+):\s+([\d\.]+)\s*(us|ops/sec|secs)?')
    parsed = {}
    raw_overall = {}
    for line in r.stdout.split("\n"):
        m = pat.match(line.strip())
        if m:
            op, metric, val, unit = m.groups()
            metric = metric.strip()
            value = float(val)
            if op == "Overall":
                raw_overall[metric] = value
                continue
            if op not in parsed:
                parsed[op] = {}
            parsed[op][metric] = value

    lat = {}
    op_metrics = {}
    for op_name, op_key in EXECUTE_OP_NAME_MAP.items():
        if op_name in parsed:
            m = parsed[op_name]
            normalized = {out_k: m[raw_k] for raw_k, out_k in EXECUTE_OP_METRIC_MAP.items() if raw_k in m}
            op_metrics[op_key] = normalized
            lat[op_key] = {k: normalized[k] for k in ("p0", "p25", "p50", "p75", "p95", "p99") if k in normalized}

    # Normalize overall metrics with explicit units
    overall = {}
    for raw_k, out_k in EXECUTE_OVERALL_METRIC_MAP.items():
        if raw_k in raw_overall:
            overall[out_k] = raw_overall[raw_k]
    # Keep raw keys for backward compatibility
    overall.update(raw_overall)

    ops = sum(1 for l in open(trace_path) if l[:2] in ("I ", "P ", "U "))
    tput = ops / elapsed if elapsed > 0 else 0

    io_totals = sampler.get_totals()
    io_totals["hardware_read_mb"]  = hw_read_mb
    io_totals["hardware_write_mb"] = hw_write_mb

    return lat, elapsed, tput, op_metrics, overall, sampler.get_timeseries(), io_totals, db_metrics, saved_logs

# =============================================================================
#  SIMILARITY & REPORTING
# =============================================================================
def format_aligned_markdown_table(headers, alignments, rows):
    all_rows = [headers] + rows
    col_widths = [max(len(str(r[i])) for r in all_rows) for i in range(len(headers))]
    col_widths = [max(w, len(alignments[i])) for i, w in enumerate(col_widths)]

    lines = []
    # Header row
    h_cells = [headers[i].ljust(col_widths[i]) for i in range(len(headers))]
    lines.append("| " + " | ".join(h_cells) + " |")

    # Separator row
    sep_cells = []
    for i, a in enumerate(alignments):
        w = col_widths[i]
        if a == ":---:":
            sep_cells.append(":" + "-" * (w - 2) + ":")
        elif a == "---:":
            sep_cells.append("-" * (w - 1) + ":")
        else:
            sep_cells.append(":" + "-" * (w - 1))
    lines.append("| " + " | ".join(sep_cells) + " |")

    # Data rows
    for r in rows:
        r_cells = []
        for i, val in enumerate(r):
            w = col_widths[i]
            a = alignments[i]
            sval = str(val)
            if a == ":---:":
                r_cells.append(sval.center(w))
            elif a == "---:":
                r_cells.append(sval.rjust(w))
            else:
                r_cells.append(sval.ljust(w))
        lines.append("| " + " | ".join(r_cells) + " |")

    return "\n".join(lines) + "\n"

def update_table_and_summary(results_dict):
    headers = [
        "Database",
        "Scale",
        "Workload",
        "execution time (s)",
        "Disk Read (MB)",
        "Disk Write (MB)",
        "bytes transferred (MB)",
        "execution time diff (%)",
        "bytes transferred diff (%)"
    ]
    alignments = [
        ":---",
        ":---:",
        ":---:",
        "---:",
        "---:",
        "---:",
        "---:",
        ":---:",
        ":---:"
    ]
    raw_rows = []
    summary = {}

    dbs = sorted(list(results_dict.keys()))
    for db in dbs:
        summary[db] = {}
        scales = sorted([int(s) for s in results_dict[db].keys()])
        for scale in scales:
            skey = str(scale)
            lbl = f"2^{scale.bit_length() - 1}"
            sdata = results_dict[db][skey]
            summary[db][lbl] = {}

            y_res = sdata.get("ycsb", {})
            t_res = sdata.get("tectonic", {})

            y_et = y_res.get("overall_metrics", {}).get("end_to_end_time_s") or y_res.get("overall_metrics", {}).get("End to End Time") or y_res.get("wall_time_s", 0.0)
            t_et = t_res.get("overall_metrics", {}).get("end_to_end_time_s") or t_res.get("overall_metrics", {}).get("End to End Time") or t_res.get("wall_time_s", 0.0)
            et_diff_pct = abs(t_et - y_et) / y_et * 100.0 if y_et > 0 else 0.0

            y_io = y_res.get("io_bytes_total", {})
            t_io = t_res.get("io_bytes_total", {})

            # Prefer hardware counters (/sys/block/sda/stat) if present
            y_r = y_io.get("hardware_read_mb", y_io.get("total_read_mb", 0.0))
            y_w = y_io.get("hardware_write_mb", y_io.get("total_write_mb", 0.0))
            y_tot = round(y_r + y_w, 2)

            t_r = t_io.get("hardware_read_mb", t_io.get("total_read_mb", 0.0))
            t_w = t_io.get("hardware_write_mb", t_io.get("total_write_mb", 0.0))
            t_tot = round(t_r + t_w, 2)

            io_diff_pct = abs(t_tot - y_tot) / y_tot * 100.0 if y_tot > 0 else 0.0

            summary[db][lbl]["ycsb"] = {
                "execution_time_s": y_et,
                "wall_time_s": y_res.get("wall_time_s", 0.0),
                "total_read_mb": y_r,
                "total_write_mb": y_w,
                "bytes_transferred_mb": y_tot
            }
            summary[db][lbl]["tectonic"] = {
                "execution_time_s": t_et,
                "wall_time_s": t_res.get("wall_time_s", 0.0),
                "total_read_mb": t_r,
                "total_write_mb": t_w,
                "bytes_transferred_mb": t_tot
            }
            summary[db][lbl]["diff_percent"] = {
                "execution_time": round(et_diff_pct, 2),
                "bytes_transferred": round(io_diff_pct, 2)
            }

            if "db_internal_metrics" in y_res or "db_internal_metrics" in t_res:
                summary[db][lbl]["db_internal_metrics"] = {
                    "ycsb": y_res.get("db_internal_metrics", {}),
                    "tectonic": t_res.get("db_internal_metrics", {}),
                }

            raw_rows.append([
                f"**{db}**", lbl, "YCSB", f"{y_et:.2f}", f"{y_r:.2f}", f"{y_w:.2f}", f"{y_tot:.2f}", "—", "—"
            ])
            raw_rows.append([
                f"**{db}**", lbl, "X-Bench", f"{t_et:.2f}", f"{t_r:.2f}", f"{t_w:.2f}", f"{t_tot:.2f}", f"**{et_diff_pct:.2f}%**", f"**{io_diff_pct:.2f}%**"
            ])

    table_content = format_aligned_markdown_table(headers, alignments, raw_rows)
    with open(TABLE_OUT, "w") as f:
        f.write(table_content)
    with open(SUMMARY_OUT, "w") as f:
        json.dump(summary, f, indent=2)

    # Also generate a markdown comparison table for database internal metrics
    internal_rows = []
    internal_headers = ["Database", "Scale", "Internal Metric", "YCSB", "X-Bench", "Diff (%)"]
    internal_align = [":---", ":---:", ":---", "---:", "---:", ":---:"]
    for db in dbs:
        scales = sorted([int(s) for s in results_dict[db].keys()])
        for scale in scales:
            lbl = f"2^{scale.bit_length() - 1}"
            sdata = results_dict[db][str(scale)]
            y_im = sdata.get("ycsb", {}).get("db_internal_metrics", {})
            t_im = sdata.get("tectonic", {}).get("db_internal_metrics", {})
            all_keys = sorted(set(list(y_im.keys()) + list(t_im.keys())))
            for k in all_keys:
                y_v = y_im.get(k)
                t_v = t_im.get(k)
                if isinstance(y_v, (int, float)) and isinstance(t_v, (int, float)):
                    d_pct = f"{abs(t_v - y_v) / y_v * 100.0:.2f}%" if y_v > 0 else "0.00%"
                    internal_rows.append([f"**{db}**", lbl, k, f"{y_v}", f"{t_v}", f"**{d_pct}**"])
    if internal_rows:
        int_table = format_aligned_markdown_table(internal_headers, internal_align, internal_rows)
        with open(f"{OUT_DIR}/table_5_db_internal_comparison.md", "w") as f_int:
            f_int.write(int_table)
    try:
        import subprocess
        subprocess.run(["python3", f"{ROOT_DIR}/run_scripts/generate_table5_two_tables.py"], check=False)
    except Exception:
        pass

# =============================================================================
#  MAIN RUNNER
# =============================================================================
def main():
    global IOSTAT_DEVICE, _log_fh
    parser = argparse.ArgumentParser(description="Table 5 Clean Rerun Runner")
    parser.add_argument("--db", nargs="+", choices=["rocksdb", "redis", "cassandra", "scylla"], default=DEFAULT_DATABASES)
    parser.add_argument("--scales", nargs="+", type=int, default=DEFAULT_SCALES)
    parser.add_argument("--workloads", nargs="+", choices=["ycsb", "tectonic"], default=["ycsb", "tectonic"])
    parser.add_argument("--device", default=IOSTAT_DEVICE, help="Block device to monitor (default: sda)")
    parser.add_argument("--force", action="store_true", help="Force rerun even if results exist in checkpoint")
    args = parser.parse_args()

    IOSTAT_DEVICE = args.device

    os.makedirs(OUT_DIR, exist_ok=True)
    global _log_fh
    _log_fh = open(LOG_PATH, "a")

    scales, dbs = args.scales, args.db

    banner("CONFIG", "Table 5 Clean Rerun Initializing")
    log(f"  Target dir   : {OUT_DIR}")
    log(f"  Databases    : {dbs}")
    log(f"  Scales       : {[f'2^{s.bit_length()-1}={s:,}' for s in scales]}")
    log(f"  Trace Path   : /dev/shm/table5_rerun_sim_*.txt (RAM tmpfs)")
    log(f"  Hardware I/O : /sys/block/{IOSTAT_DEVICE}/stat + iostat")

    # Load existing checkpoint if any
    results = {"config": {
        "experiment": "table5_rerun",
        "scales": scales,
        "databases": dbs,
        "exec_threads": EXEC_THREADS,
        "iostat_device": IOSTAT_DEVICE,
        "trace_storage": "/dev/shm (tmpfs)",
        "db_images": DEFAULT_DB_IMAGES,
    }, "results": {}}

    if os.path.exists(RESULTS_PATH):
        try:
            loaded = json.load(open(RESULTS_PATH))
            if "results" in loaded:
                results["results"] = loaded["results"]
                log(f"  [resume] loaded checkpoint from {RESULTS_PATH}")
        except Exception as e:
            log(f"  [resume] could not load {RESULTS_PATH}: {e}")

    for scale in scales:
        slbl = f"2^{scale.bit_length()-1}"
        if args.force:
            todo = list(dbs)
        else:
            todo = [db for db in dbs if str(scale) not in results["results"].get(db, {})]
        if not todo:
            log(f"\n  [skip] scale {slbl} already complete for all requested databases")
            continue

        banner("SCALE", f"Starting Scale {slbl} ({scale:,} operations)")

        ycsb_trace = f"/dev/shm/table5_rerun_sim_ycsb_{scale}.txt"
        tec_trace  = f"/dev/shm/table5_rerun_sim_tectonic_{scale}.txt"

        if "ycsb" in args.workloads and not validate_trace(ycsb_trace, scale):
            generate_ycsb(scale, ycsb_trace)
        if "tectonic" in args.workloads and not validate_trace(tec_trace, scale):
            generate_tectonic(scale, tec_trace)

        for db in todo:
            banner("DATABASE", f"Executing {db} at scale {slbl}")
            db_res = results["results"].setdefault(db, {}).setdefault(str(scale), {})

            db_out_dir = os.path.join(OUT_DIR, db)
            os.makedirs(db_out_dir, exist_ok=True)
            prefix = f"{db_out_dir}/{db}_scale{scale}"

            # YCSB run
            if "ycsb" in args.workloads and (args.force or "ycsb" not in db_res):
                log(f"\n  [run] YCSB workload on {db}...")
                restart_db(db)
                lat, wt, tp, op_m, overall, ts, io_tot, db_metrics, saved_logs = execute_with_dual_io(ycsb_trace, db, scale, "ycsb")
                db_res["ycsb"] = {
                    "latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp,
                    "io_bytes_total": io_tot, "operation_metrics": op_m, "overall_metrics": overall,
                    "db_internal_metrics": db_metrics, "db_log_files": saved_logs
                }
                log(f"  YCSB result: wall={wt:.2f}s  tput={tp:,.0f} ops/s  read={io_tot['total_read_mb']}MB  write={io_tot['total_write_mb']}MB")

            # X-Bench run
            if "tectonic" in args.workloads and (args.force or "tectonic" not in db_res):
                log(f"\n  [run] X-Bench workload on {db}...")
                restart_db(db)
                lat, wt, tp, op_m, overall, ts, io_tot, db_metrics, saved_logs = execute_with_dual_io(tec_trace, db, scale, "tectonic")
                db_res["tectonic"] = {
                    "latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp,
                    "io_bytes_total": io_tot, "operation_metrics": op_m, "overall_metrics": overall,
                    "db_internal_metrics": db_metrics, "db_log_files": saved_logs
                }
                log(f"  X-Bench result: wall={wt:.2f}s  tput={tp:,.0f} ops/s  read={io_tot['total_read_mb']}MB  write={io_tot['total_write_mb']}MB")

            # Compute and print similarity
            if "ycsb" in db_res and "tectonic" in db_res:
                y_res = db_res["ycsb"]
                t_res = db_res["tectonic"]
                y_wt = y_res.get("wall_time_s", 0.0)
                t_wt = t_res.get("wall_time_s", 0.0)
                wt_diff = abs(t_wt - y_wt) / y_wt * 100.0 if y_wt > 0 else 0

                y_tot_io = y_res["io_bytes_total"]["total_read_mb"] + y_res["io_bytes_total"]["total_write_mb"]
                t_tot_io = t_res["io_bytes_total"]["total_read_mb"] + t_res["io_bytes_total"]["total_write_mb"]
                io_diff = abs(t_tot_io - y_tot_io) / y_tot_io * 100.0 if y_tot_io > 0 else 0

                log(f"\n  >>> SIMILARITY CHECK ({db} @ {slbl}):")
                log(f"      Wall Time Diff: {wt_diff:.2f}%  (YCSB: {y_wt:.2f}s vs X-Bench: {t_wt:.2f}s)")
                log(f"      bytes transferred diff: {io_diff:.2f}%  (YCSB: {y_tot_io:.2f}MB vs X-Bench: {t_tot_io:.2f}MB)")

            teardown_db(db)
            write_json_atomic(RESULTS_PATH, results)
            update_table_and_summary(results["results"])
            log(f"  [checkpoint] updated results and table_5_plotting_data")

        # Cleanup traces in RAM for this scale
        for p in [ycsb_trace, tec_trace]:
            if os.path.exists(p):
                os.remove(p)
                log(f"  [cleanup] freed {p} from RAM")

    banner("COMPLETED", f"All requested runs finished. Results saved to {OUT_DIR}")
    if _log_fh:
        _log_fh.close()

if __name__ == "__main__":
    main()
