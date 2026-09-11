#!/usr/bin/env python3
"""
run_table5_rerun.py
===================
Clean, isolated rerun of Table 5 experiments under:
    /home/cc/Tectonic/data/table5_rerun/

Key Methodological Guarantees:
  1. Workload traces are stored in RAM-backed /dev/shm (tmpfs):
       /dev/shm/table5_rerun_sim_ycsb_{scale}.txt
       /dev/shm/table5_rerun_sim_tectonic_{scale}.txt
     When drop_caches is invoked, storage block device caches on /dev/sda are flushed
     without evicting /dev/shm. The client streams the trace from memory at zero physical
     disk read cost to sda.
  2. Dual I/O Accounting:
     - Exact hardware sector delta from /sys/block/sda/stat (zero sampling error).
     - 1-second timeseries rate tracking via iostat for continuous rate CSVs.
  3. Strict cold state consistency:
     - Each database is fully reset/restarted and drop_caches is called prior to execution.
  4. Automatic similarity diff reporting between YCSB and X-Bench after each run.
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
        run(["docker", "run", "--name", "sim-scylla", "-p", "9042:9042", "-d",
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

def execute_with_dual_io(trace_path, db):
    # 1. Start iostat sampler
    sampler = IoStatSampler(device=IOSTAT_DEVICE)
    sampler.start()
    time.sleep(0.8) # skip boot block

    # 2. Hardware sectors before
    sec_r0, sec_w0 = get_disk_hardware_sectors(IOSTAT_DEVICE)

    # 3. Execute
    t0 = time.perf_counter()
    r = run([TECTONIC_CLI, "execute",
             "-i", trace_path,
             "-d", DB_CLI_DRIVER[db],
             "-p", DB_CLI_PATH[db],
             "-t", str(EXEC_THREADS)], capture=True)
    elapsed = time.perf_counter() - t0

    # 4. Hardware sectors after
    sec_r1, sec_w1 = get_disk_hardware_sectors(IOSTAT_DEVICE)

    # 5. Stop iostat sampler
    sampler.stop()
    sampler.join(timeout=4)

    # Compute hardware MB
    hw_read_mb  = round(((sec_r1 - sec_r0) * 512) / (1024 * 1024), 2)
    hw_write_mb = round(((sec_w1 - sec_w0) * 512) / (1024 * 1024), 2)

    # Parse stdout latencies
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
                overall[metric] = value
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

    ops = sum(1 for l in open(trace_path) if l[:2] in ("I ", "P ", "U "))
    tput = ops / elapsed if elapsed > 0 else 0

    io_totals = sampler.get_totals()
    io_totals["hardware_read_mb"]  = hw_read_mb
    io_totals["hardware_write_mb"] = hw_write_mb

    return lat, elapsed, tput, op_metrics, overall, sampler.get_timeseries(), io_totals

# =============================================================================
#  SIMILARITY & REPORTING
# =============================================================================
def update_table_and_summary(results_dict):
    table_rows = []
    table_rows.append("| Database | Scale | Workload | execution time (s) | Disk Read (MB) | Disk Write (MB) | bytes transferred (MB) | execution time diff (%) | bytes transferred diff (%) |")
    table_rows.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

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

            y_wt = y_res.get("wall_time_s", 0.0)
            t_wt = t_res.get("wall_time_s", 0.0)
            wt_diff_pct = abs(t_wt - y_wt) / y_wt * 100.0 if y_wt > 0 else 0.0

            y_io = y_res.get("io_bytes_total", {})
            t_io = t_res.get("io_bytes_total", {})

            y_r = y_io.get("total_read_mb", 0.0)
            y_w = y_io.get("total_write_mb", 0.0)
            y_tot = y_r + y_w

            t_r = t_io.get("total_read_mb", 0.0)
            t_w = t_io.get("total_write_mb", 0.0)
            t_tot = t_r + t_w

            io_diff_pct = abs(t_tot - y_tot) / y_tot * 100.0 if y_tot > 0 else 0.0

            summary[db][lbl]["ycsb"] = {"wall_time_s": y_wt, "total_read_mb": y_r, "total_write_mb": y_w, "total_io_mb": y_tot}
            summary[db][lbl]["tectonic"] = {"wall_time_s": t_wt, "total_read_mb": t_r, "total_write_mb": t_w, "total_io_mb": t_tot}
            summary[db][lbl]["diff_percent"] = {"wall_time": round(wt_diff_pct, 2), "total_io": round(io_diff_pct, 2)}

            table_rows.append(
                f"| **{db}** | {lbl} | YCSB | {y_wt:.2f} | {y_r:.2f} | {y_w:.2f} | {y_tot:.2f} | — | — |"
            )
            table_rows.append(
                f"| **{db}** | {lbl} | X-Bench | {t_wt:.2f} | {t_r:.2f} | {t_w:.2f} | {t_tot:.2f} | **{wt_diff_pct:.2f}%** | **{io_diff_pct:.2f}%** |"
            )

    table_content = "\n".join(table_rows) + "\n"
    with open(TABLE_OUT, "w") as f:
        f.write(table_content)
    with open(SUMMARY_OUT, "w") as f:
        json.dump(summary, f, indent=2)

# =============================================================================
#  MAIN RUNNER
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Table 5 Clean Rerun Runner")
    parser.add_argument("--db", nargs="+", choices=["rocksdb", "redis", "cassandra", "scylla"], default=DEFAULT_DATABASES)
    parser.add_argument("--scales", nargs="+", type=int, default=DEFAULT_SCALES)
    parser.add_argument("--workloads", nargs="+", choices=["ycsb", "tectonic"], default=["ycsb", "tectonic"])
    parser.add_argument("--device", default=IOSTAT_DEVICE, help="Block device to monitor (default: sda)")
    args = parser.parse_args()

    global IOSTAT_DEVICE
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

            prefix = f"{OUT_DIR}/{db}_scale{scale}"

            # YCSB run
            if "ycsb" in args.workloads and "ycsb" not in db_res:
                log(f"\n  [run] YCSB workload on {db}...")
                restart_db(db)
                lat, wt, tp, op_m, overall, ts, io_tot = execute_with_dual_io(ycsb_trace, db)
                db_res["ycsb"] = {
                    "latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp,
                    "io_bytes_total": io_tot, "operation_metrics": op_m, "overall_metrics": overall
                }
                save_iostat_csv(ts, f"{prefix}_ycsb_io_bytes.csv")
                log(f"  YCSB result: wall={wt:.2f}s  tput={tp:,.0f} ops/s  read={io_tot['total_read_mb']}MB  write={io_tot['total_write_mb']}MB")

            # X-Bench run
            if "tectonic" in args.workloads and "tectonic" not in db_res:
                log(f"\n  [run] X-Bench workload on {db}...")
                restart_db(db)
                lat, wt, tp, op_m, overall, ts, io_tot = execute_with_dual_io(tec_trace, db)
                db_res["tectonic"] = {
                    "latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp,
                    "io_bytes_total": io_tot, "operation_metrics": op_m, "overall_metrics": overall
                }
                save_iostat_csv(ts, f"{prefix}_tectonic_io_bytes.csv")
                log(f"  X-Bench result: wall={wt:.2f}s  tput={tp:,.0f} ops/s  read={io_tot['total_read_mb']}MB  write={io_tot['total_write_mb']}MB")

            # Compute and print similarity
            if "ycsb" in db_res and "tectonic" in db_res:
                y_wt = db_res["ycsb"]["wall_time_s"]
                t_wt = db_res["tectonic"]["wall_time_s"]
                wt_diff = abs(t_wt - y_wt) / y_wt * 100.0 if y_wt > 0 else 0

                y_tot_io = y_res["io_bytes_total"]["total_read_mb"] + y_res["io_bytes_total"]["total_write_mb"]
                t_tot_io = t_res["io_bytes_total"]["total_read_mb"] + t_res["io_bytes_total"]["total_write_mb"]
                io_diff = abs(t_tot_io - y_tot_io) / y_tot_io * 100.0 if y_tot_io > 0 else 0

                log(f"\n  >>> SIMILARITY CHECK ({db} @ {slbl}):")
                log(f"      Wall Time Diff: {wt_diff:.2f}%  (YCSB: {y_res['wall_time_s']:.2f}s vs X-Bench: {t_res['wall_time_s']:.2f}s)")
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
