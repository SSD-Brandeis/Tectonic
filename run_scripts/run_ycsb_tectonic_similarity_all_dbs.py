#!/usr/bin/env python3
"""
YCSB Workload A Similarity Experiment — All Four Databases
===========================================================
Experiment : run_ycsb_tectonic_similarity_all_dbs
Data dir   : /home/cc/Tectonic/data/ycsb_tectonic_similarity_all_dbs/

Stages (executed per scale, then per database):
  1. Generate YCSB Workload A trace        -> print first 20 lines to log
  2. Generate Tectonic Workload A trace    -> print first 20 lines to log
  3. Compare the two traces                -> op counts, key format, value size
  4. For each database:
       a. Setup / start container
       b. Reset DB to clean empty state
       c. Run YCSB trace   -> wall time + per-op latency percentiles
       d. Reset DB again (fresh state)
       e. Run Tectonic trace -> same driver, same config, same DB
       f. Checkpoint results.json
       g. Trigger intermediate plot
  5. Delete trace files to reclaim disk

Databases:
  rocksdb   : C++ rocksdb-benchmark-harness (stats build)
  redis     : Docker image locked in db_images.json via tectonic-cli execute -d redis
  cassandra : Docker image locked in db_images.json via tectonic-cli execute -d scylla
  scylla    : Docker image locked in db_images.json via tectonic-cli execute -d scylla

Reproducibility:
  All settings in CONFIG block. Re-running resumes from checkpoint.
  Docker images are resolved once and locked by repo digest in db_images.json.
  Run: python3 run_scripts/run_ycsb_tectonic_similarity_all_dbs.py
"""

import os, sys, re, time, json, shutil, subprocess, collections, argparse

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

# Tectonic spec: inserts=1M*sf, point_queries=500k*sf Beta(0.15,0.55), updates=500k*sf
TECTONIC_SPEC = f"{HARNESS_DIR}/experiments/workload-similarity/workload-a.spec.json"

OUT_DIR      = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_all_dbs"
RESULTS_PATH = f"{OUT_DIR}/results.json"
PLOT_SCRIPT  = "/home/cc/Tectonic/plot_scripts/plot_ycsb_tectonic_similarity_all_dbs.py"
IMAGE_LOCK_PATH = f"{OUT_DIR}/db_images.json"

os.makedirs(OUT_DIR, exist_ok=True)

SCALES    = [2**18, 2**19, 2**20, 2**21, 2**22]
DATABASES = ["rocksdb", "redis", "cassandra", "scylla"]
EXEC_THREADS = 1   # sequential: fairest single-op latency comparison
DEFAULT_DB_IMAGES = {
    "redis": "redis:latest",
    "cassandra": "cassandra:latest",
    "scylla": "scylladb/scylla:latest",
}
TRACE_GEN_MIN_GB = 25
DB_RUN_MIN_GB = 20

SEP = "=" * 70

# =============================================================================
#  HELPERS
# =============================================================================
def banner(stage, title):
    print(f"\n{SEP}\n  {stage}: {title}\n{SEP}")
    sys.stdout.flush()

def run(cmd, cwd=None, capture=False, check=True):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    sys.stdout.flush()
    r = subprocess.run(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True)
    if check and r.returncode != 0:
        print(f"  [ERROR] exit={r.returncode}", file=sys.stderr)
        if capture and r.stderr:
            print(r.stderr[:1500], file=sys.stderr)
        sys.exit(1)
    return r

def fail(msg):
    print(f"  [FATAL] {msg}", file=sys.stderr)
    sys.stderr.flush()
    sys.exit(1)

def disk_free_gb():
    _, _, free = shutil.disk_usage("/")
    return free / 1024**3

def check_disk(min_gb=None, context=None):
    free = disk_free_gb()
    msg = f"  [disk] {free:.1f} GB free"
    if context:
        msg += f"  ({context})"
    print(msg)
    if min_gb is not None and free < min_gb:
        fail(f"Insufficient free space for {context or 'current stage'}: "
             f"{free:.1f} GB available, require at least {min_gb:.1f} GB. "
             "Free disk space and resume from the checkpoint.")
    sys.stdout.flush()

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
        print(f"  [cleanup] deleted {path}")

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
        print(f"  [trace] {path} is incomplete: expected {expected:,} ops, found {actual:,}")
        return False
    print(f"  [trace] validated {path} ({actual:,} ops)")
    return True

def clean_metadata_lines(path):
    with open(path) as f:
        lines = f.readlines()
    cleaned = [l for l in lines if not l.startswith(("FS ", "FE "))]
    if len(cleaned) < len(lines):
        with open(path, "w") as f:
            f.writelines(cleaned)
        print(f"  [clean] removed {len(lines)-len(cleaned)} FS/FE lines")

def load_image_lock():
    if os.path.exists(IMAGE_LOCK_PATH):
        try:
            return json.load(open(IMAGE_LOCK_PATH))
        except Exception as e:
            fail(f"could not load {IMAGE_LOCK_PATH}: {e}")
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
        print(f"  [image] locked {db} -> {locked_ref}")
    else:
        print(f"  [image] using locked {db} -> {locked_ref}")
    return locked_ref

# =============================================================================
#  STAGE 1 — Generate YCSB trace
# =============================================================================
def generate_ycsb(scale, out_path):
    banner("STAGE 1", f"Generate YCSB Workload A  (scale={scale:,}  2^{scale.bit_length()-1})")
    print(f"  Tool      : YCSB FileClient (vendor/YCSB in rocksdb-benchmark-harness)")
    print(f"  Spec      : workloads/workloada  (50% reads, 50% updates, Zipfian)")
    print(f"  recordcount = operationcount = {scale:,}")
    print(f"  key format : usertable:user<19-digit-numeric>")
    print(f"  output     : {out_path}")

    run_p = out_path + ".run"
    cleanup_trace_artifacts(out_path, remove_output=True)
    check_disk(TRACE_GEN_MIN_GB, f"before YCSB trace generation for 2^{scale.bit_length()-1}")

    print(f"\n  [1a] load phase — {scale:,} inserts:")
    run(["java", "-cp", YCSB_CP, "site.ycsb.Client",
         "-db", "site.ycsb.db.FileClient",
         "-P", "workloads/workloada",
         "-p", f"file.output={out_path}",
         "-p", f"recordcount={scale}",
         "-p", f"operationcount={scale}",
         "-load"], cwd=YCSB_DIR)

    check_disk(DB_RUN_MIN_GB, f"before YCSB run phase append for 2^{scale.bit_length()-1}")
    print(f"\n  [1b] run phase — {scale:,} ops (50% P + 50% U, Zipfian):")
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
    print(f"\n  -> {n:,} total lines written")
    print(f"\n  First 20 lines of YCSB trace:")
    with open(out_path) as f:
        for i, line in enumerate(f):
            if i >= 20:
                break
            print(f"    {line}", end="")
    print()
    return out_path

# =============================================================================
#  STAGE 2 — Generate Tectonic trace
# =============================================================================
def generate_tectonic(scale, out_path):
    sf = scale / 1_000_000.0
    banner("STAGE 2", f"Generate Tectonic Workload A  (scale={scale:,}  2^{scale.bit_length()-1})")
    print(f"  Tool         : tectonic-cli generate")
    print(f"  Spec         : {TECTONIC_SPEC}")
    print(f"  scale factor : {sf}  (= {scale:,} / 1,000,000)")
    print(f"  inserts      : {scale:,}  key=usertable:user<19-digit-numeric>  val=1090B")
    print(f"  point_queries: {scale//2:,}  Beta(alpha=0.15, beta=0.55) approx Zipfian(0.99)")
    print(f"  updates      : {scale//2:,}  same key selection  val=109B")
    print(f"  output       : {out_path}")

    cleanup_trace_artifacts(out_path, remove_output=True)
    check_disk(TRACE_GEN_MIN_GB, f"before Tectonic trace generation for 2^{scale.bit_length()-1}")
    run([TECTONIC_CLI, "generate",
         "-w", TECTONIC_SPEC,
         "-o", out_path,
         "-s", str(sf)])

    clean_metadata_lines(out_path)
    n = sum(1 for _ in open(out_path))
    print(f"\n  -> {n:,} total lines written")
    print(f"\n  First 20 lines of Tectonic trace:")
    with open(out_path) as f:
        for i, line in enumerate(f):
            if i >= 20:
                break
            print(f"    {line}", end="")
    print()
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

    print(f"\n  {'Op':<12} {'YCSB':>12} {'Tectonic':>12}  description")
    print(f"  {'-'*52}")
    for op, desc in [("I", "insert (db->Put, load phase)"),
                     ("P", "point query (db->Get, valid key, Zipfian)"),
                     ("U", "update (db->Put, existing key)"),
                     ("BP", "blind query (key not in DB)")]:
        yv, tv = yc.get(op, 0), tc.get(op, 0)
        if yv > 0 or tv > 0:
            print(f"  {op:<12} {yv:>12,} {tv:>12,}  {desc}")
    print(f"  {'Total':<12} {sum(yc.values()):>12,} {sum(tc.values()):>12,}")

    yk, tk = first_key(ycsb_path), first_key(tec_path)
    yvl, tvl = first_val_len(ycsb_path), first_val_len(tec_path)
    print(f"\n  First insert key  — YCSB: {yk}")
    print(f"                       Tectonic: {tk}")
    print(f"  First insert val  — YCSB: {yvl} bytes  Tectonic: {tvl} bytes")
    sys.stdout.flush()

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

def reset_db(db):
    if db == "rocksdb":
        if os.path.exists(ROCKSDB_DIR):
            shutil.rmtree(ROCKSDB_DIR)
        os.makedirs(ROCKSDB_DIR, exist_ok=True)
    elif db == "redis":
        run(["docker", "exec", "sim-redis", "redis-cli", "flushall"], capture=True)
    elif db == "cassandra":
        run(["docker", "exec", "sim-cassandra", "cqlsh",
             "-e", "DROP KEYSPACE IF EXISTS tectonic;"], capture=True, check=False)
        time.sleep(1)
    elif db == "scylla":
        run(["docker", "exec", "sim-scylla", "cqlsh",
             "-e", "DROP KEYSPACE IF EXISTS tectonic;"], capture=True, check=False)
        time.sleep(1)

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
def run_rocksdb_harness(trace_path, stats_out, latency_out):
    t0 = time.perf_counter()
    run([HARNESS_BIN, ROCKSDB_OPTS, trace_path, stats_out, latency_out],
        cwd=ROCKSDB_DIR)
    elapsed = time.perf_counter() - t0

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
    return lat, elapsed, tput

# =============================================================================
#  EXECUTION — tectonic-cli execute (Redis / Cassandra / ScyllaDB)
# =============================================================================
DB_CLI_DRIVER = {"redis": "redis", "cassandra": "scylla", "scylla": "scylla"}
DB_CLI_PATH   = {
    "redis":     "redis://127.0.0.1:6379",
    "cassandra": "127.0.0.1:9042",
    "scylla":    "127.0.0.1:9042",
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

def run_tectonic_execute(trace_path, db):
    t0 = time.perf_counter()
    r = run([TECTONIC_CLI, "execute",
             "-i", trace_path,
             "-d", DB_CLI_DRIVER[db],
             "-p", DB_CLI_PATH[db],
             "-t", str(EXEC_THREADS)], capture=True)
    elapsed = time.perf_counter() - t0

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
    return lat, elapsed, tput, op_metrics, overall

# =============================================================================
#  STAGES 4+5 — Run one (db, scale) pair
# =============================================================================
def run_one_db(db, scale, ycsb_trace, tec_trace):
    scale_lbl = f"2^{scale.bit_length()-1}"
    prefix    = f"{OUT_DIR}/{db}_scale{scale}"
    result    = {}

    banner("STAGE 4", f"Execute YCSB trace on {db}  [{scale_lbl}]")
    print(f"  trace  : {ycsb_trace}")
    print(f"  driver : {'C++ rocksdb-benchmark-harness (stats build)' if db=='rocksdb' else 'tectonic-cli execute  (same for both workloads)'}")
    reset_db(db)
    if db == "rocksdb":
        lat, wt, tp = run_rocksdb_harness(
            ycsb_trace,
            f"{prefix}_ycsb_stats.txt",
            f"{prefix}_ycsb_latency.csv")
        result["ycsb"] = {"latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp}
    else:
        lat, wt, tp, op_metrics, overall = run_tectonic_execute(ycsb_trace, db)
        result["ycsb"] = {"latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp}
        if op_metrics:
            result["ycsb"]["operation_metrics"] = op_metrics
        if overall:
            result["ycsb"]["overall_metrics"] = overall
    print(f"\n  YCSB:  wall={wt:.2f}s  tput={tp:,.0f} ops/s")
    _print_lat(lat)

    banner("STAGE 5", f"Execute Tectonic trace on {db}  [{scale_lbl}]  (same driver)")
    print(f"  trace  : {tec_trace}")
    reset_db(db)
    if db == "rocksdb":
        lat, wt, tp = run_rocksdb_harness(
            tec_trace,
            f"{prefix}_tectonic_stats.txt",
            f"{prefix}_tectonic_latency.csv")
        result["tectonic"] = {"latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp}
    else:
        lat, wt, tp, op_metrics, overall = run_tectonic_execute(tec_trace, db)
        result["tectonic"] = {"latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp}
        if op_metrics:
            result["tectonic"]["operation_metrics"] = op_metrics
        if overall:
            result["tectonic"]["overall_metrics"] = overall
    print(f"\n  Tectonic:  wall={wt:.2f}s  tput={tp:,.0f} ops/s")
    _print_lat(lat)

    return result

def _print_lat(lat):
    print(f"\n  {'op':<14} {'p0':>7} {'p25':>7} {'p50':>7} {'p75':>7} {'p95':>7} {'p99':>7} (us)")
    print(f"  {'-'*54}")
    for op in ["insert", "point_query", "update"]:
        d = lat.get(op, {})
        if not d:
            continue
        print(f"  {op:<14}"
              f" {d.get('p0',0):>7.2f} {d.get('p25',0):>7.2f}"
              f" {d.get('p50',0):>7.2f} {d.get('p75',0):>7.2f}"
              f" {d.get('p95',0):>7.2f} {d.get('p99',0):>7.2f}")
    sys.stdout.flush()

# =============================================================================
#  INTERMEDIATE PLOT
# =============================================================================
def trigger_plot():
    if os.path.exists(PLOT_SCRIPT):
        print("\n  [plot] Regenerating plots...", flush=True)
        subprocess.run(["python3", PLOT_SCRIPT], capture_output=True)
        print("  [plot] Done.", flush=True)
    else:
        print(f"  [plot] Script not found: {PLOT_SCRIPT}", flush=True)

# =============================================================================
#  MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="YCSB vs Tectonic Workload A — all 4 databases, 2^18..2^22")
    parser.add_argument("--db", nargs="+", choices=DATABASES, default=DATABASES)
    parser.add_argument("--scales", nargs="+", type=int, default=SCALES)
    args = parser.parse_args()

    scales, dbs = args.scales, args.db

    print(SEP)
    print("  YCSB vs Tectonic Workload A Similarity — All Databases")
    print(f"  output dir : {OUT_DIR}")
    print(f"  databases  : {dbs}")
    print(f"  scales     : {[f'2^{s.bit_length()-1}={s:,}' for s in scales]}")
    print(f"  threads    : {EXEC_THREADS}")
    print(SEP)
    sys.stdout.flush()

    results = {"config": {
        "scales": scales,
        "databases": dbs,
        "ycsb_workload": "workloads/workloada",
        "tectonic_spec": TECTONIC_SPEC,
        "exec_threads": EXEC_THREADS,
    }, "results": {}}

    if os.path.exists(RESULTS_PATH):
        try:
            loaded = json.load(open(RESULTS_PATH))
            if "results" in loaded:
                results["results"] = loaded["results"]
                print(f"  [resume] loaded checkpoint from {RESULTS_PATH}")
            if "config" in loaded and "db_images" in loaded["config"]:
                results["config"]["db_images"] = loaded["config"]["db_images"]
        except Exception as e:
            print(f"  [resume] could not load: {e}")
    sys.stdout.flush()

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
            print(f"\n  [skip] scale {slbl} complete for all dbs")
            continue

        print(f"\n{'#'*70}\n#  SCALE = {scale:,}  ({slbl})\n{'#'*70}")
        check_disk(TRACE_GEN_MIN_GB, f"starting scale {slbl}")

        ycsb_trace = f"/tmp/sim_ycsb_{scale}.txt"
        tec_trace  = f"/tmp/sim_tectonic_{scale}.txt"

        if not validate_trace(ycsb_trace, scale):
            generate_ycsb(scale, ycsb_trace)
        else:
            cleanup_trace_artifacts(ycsb_trace, remove_output=False)
            print("  [skip] ycsb trace exists and is complete")

        if not validate_trace(tec_trace, scale):
            generate_tectonic(scale, tec_trace)
        else:
            cleanup_trace_artifacts(tec_trace, remove_output=False)
            print("  [skip] tectonic trace exists and is complete")

        comparison = compare_traces(ycsb_trace, tec_trace)
        check_disk(DB_RUN_MIN_GB, f"after trace generation for scale {slbl}")

        for db in todo:
            print(f"\n{'='*70}\n  DB={db}  SCALE={slbl}\n{'='*70}")
            check_disk(DB_RUN_MIN_GB, f"before database run {db} {slbl}")
            setup_db(db, image_lock.get(db))
            db_res = run_one_db(db, scale, ycsb_trace, tec_trace)
            db_res["trace_comparison"] = comparison
            teardown_db(db)

            results["results"].setdefault(db, {})[skey] = db_res
            write_json_atomic(RESULTS_PATH, results)
            print(f"\n  [checkpoint] saved -> {RESULTS_PATH}")
            trigger_plot()
            check_disk(DB_RUN_MIN_GB, f"after checkpoint for {db} {slbl}")

        if all(skey in results["results"].get(db, {}) for db in dbs):
            for p in [ycsb_trace, tec_trace]:
                if os.path.exists(p):
                    os.remove(p)
                    print(f"  [cleanup] deleted {p}")

    print(f"\n{SEP}\n  Done.  Results: {RESULTS_PATH}\n{SEP}")

if __name__ == "__main__":
    main()
