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
  redis     : Docker redis:latest via tectonic-cli execute -d redis
  cassandra : Docker cassandra:latest via tectonic-cli execute -d scylla
  scylla    : Docker scylladb/scylla via tectonic-cli execute -d scylla

Reproducibility:
  All settings in CONFIG block. Re-running resumes from checkpoint.
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

os.makedirs(OUT_DIR, exist_ok=True)

SCALES    = [2**18, 2**19, 2**20, 2**21, 2**22]
DATABASES = ["rocksdb", "redis", "cassandra", "scylla"]
EXEC_THREADS = 1   # sequential: fairest single-op latency comparison

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

def disk_free_gb():
    _, _, free = shutil.disk_usage("/")
    return free / 1024**3

def check_disk(min_gb=15):
    free = disk_free_gb()
    print(f"  [disk] {free:.1f} GB free")
    if free < min_gb:
        print("  [disk] WARNING: low — pruning docker...")
        subprocess.run(["docker", "system", "prune", "-a", "-f"], capture_output=True)
        print(f"  [disk] after prune: {disk_free_gb():.1f} GB free")
    sys.stdout.flush()

def count_ops(path):
    c = collections.Counter()
    with open(path) as f:
        for line in f:
            op = line.split(" ", 1)[0]
            if op in ("I", "P", "U", "BP"):
                c[op] += 1
    return c

def clean_metadata_lines(path):
    with open(path) as f:
        lines = f.readlines()
    cleaned = [l for l in lines if not l.startswith(("FS ", "FE "))]
    if len(cleaned) < len(lines):
        with open(path, "w") as f:
            f.writelines(cleaned)
        print(f"  [clean] removed {len(lines)-len(cleaned)} FS/FE lines")

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

    load = out_path + ".load"
    run_p = out_path + ".run"

    print(f"\n  [1a] load phase — {scale:,} inserts:")
    run(["java", "-cp", YCSB_CP, "site.ycsb.Client",
         "-db", "site.ycsb.db.FileClient",
         "-P", "workloads/workloada",
         "-p", f"file.output={load}",
         "-p", f"recordcount={scale}",
         "-p", f"operationcount={scale}",
         "-load"], cwd=YCSB_DIR)

    print(f"\n  [1b] run phase — {scale:,} ops (50% P + 50% U, Zipfian):")
    run(["java", "-cp", YCSB_CP, "site.ycsb.Client",
         "-db", "site.ycsb.db.FileClient",
         "-P", "workloads/workloada",
         "-p", f"file.output={run_p}",
         "-p", f"recordcount={scale}",
         "-p", f"operationcount={scale}",
         "-t"], cwd=YCSB_DIR)

    with open(out_path, "w") as fout:
        for part in [load, run_p]:
            with open(part) as fin:
                shutil.copyfileobj(fin, fout)
    os.remove(load); os.remove(run_p)

    n = sum(1 for _ in open(out_path))
    print(f"\n  -> {n:,} total lines written")
    print(f"\n  First 20 lines of YCSB trace:")
    with open(out_path) as f:
        for i, line in enumerate(f):
            if i >= 20: break
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
            if i >= 20: break
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
    for op, desc in [("I","insert (db->Put, load phase)"),
                     ("P","point query (db->Get, valid key, Zipfian)"),
                     ("U","update (db->Put, existing key)"),
                     ("BP","blind query (key not in DB)")]:
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
def setup_db(db):
    if db == "rocksdb":
        if os.path.exists(ROCKSDB_DIR): shutil.rmtree(ROCKSDB_DIR)
        os.makedirs(ROCKSDB_DIR, exist_ok=True)
    elif db == "redis":
        run(["docker", "rm", "-f", "sim-redis"], check=False, capture=True)
        run(["docker", "run", "--name", "sim-redis", "-p", "6379:6379", "-d",
             "redis:latest", "redis-server", "--appendonly", "no", "--save", ""])
        for _ in range(30):
            r = run(["docker", "exec", "sim-redis", "redis-cli", "ping"],
                    capture=True, check=False)
            if "PONG" in (r.stdout or ""): break
            time.sleep(1)
    elif db == "cassandra":
        # Always remove first to avoid port-already-allocated on resume
        run(["docker", "rm", "-f", "sim-cassandra"], check=False, capture=True)
        time.sleep(2)
        run(["docker", "run", "--name", "sim-cassandra", "-p", "9042:9042",
             "-d", "cassandra:latest"])
        for _ in range(60):
            r = run(["docker", "exec", "sim-cassandra", "cqlsh",
                     "-e", "DESCRIBE KEYSPACES"], capture=True, check=False)
            if "system" in (r.stdout or ""): break
            time.sleep(2)
    elif db == "scylla":
        run(["docker", "stop", "sim-cassandra"], check=False, capture=True)
        run(["docker", "rm", "-f", "sim-scylla"], check=False, capture=True)
        run(["docker", "run", "--name", "sim-scylla", "-p", "9042:9042", "-d",
             "scylladb/scylla:latest", "--developer-mode", "1"])
        for _ in range(60):
            r = run(["docker", "exec", "sim-scylla", "cqlsh",
                     "-e", "DESCRIBE KEYSPACES"], capture=True, check=False)
            if "system" in (r.stdout or ""): break
            time.sleep(2)

def reset_db(db):
    if db == "rocksdb":
        if os.path.exists(ROCKSDB_DIR): shutil.rmtree(ROCKSDB_DIR)
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
        if os.path.exists(ROCKSDB_DIR): shutil.rmtree(ROCKSDB_DIR)
    elif db == "redis":
        run(["docker", "rm", "-f", "sim-redis"], check=False, capture=True)
    elif db == "scylla":
        run(["docker", "rm", "-f", "sim-scylla"], check=False, capture=True)
        run(["docker", "start", "sim-cassandra"], check=False, capture=True)
        for _ in range(60):
            r = run(["docker", "exec", "sim-cassandra", "cqlsh",
                     "-e", "DESCRIBE KEYSPACES"], capture=True, check=False)
            if "system" in (r.stdout or ""): break
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
            if not line: continue
            if "," not in line:
                # Header line e.g. "insert (ns)" or "point query (ns)"
                cur = line.replace(" (ns)", "").replace(" ", "_")
                lat[cur] = {}
            else:
                pct, val = line.split(",", 1)
                lat[cur][pct.strip()] = float(val.strip()) / 1000.0  # ns->us

    ops  = sum(1 for l in open(trace_path) if l[0] in ("I","P","U"))
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

def run_tectonic_execute(trace_path, db):
    t0 = time.perf_counter()
    r = run([TECTONIC_CLI, "execute",
             "-i", trace_path,
             "-d", DB_CLI_DRIVER[db],
             "-p", DB_CLI_PATH[db],
             "-t", str(EXEC_THREADS)], capture=True)
    elapsed = time.perf_counter() - t0

    # Parse stdout for per-op latency percentiles
    pat = re.compile(r'\[([^\]]+)\]\s+([^:]+):\s+([\d\.]+)\s*(us|ops/sec|secs)?')
    parsed = {}
    for line in r.stdout.split("\n"):
        m = pat.match(line.strip())
        if m:
            op, metric, val, _ = m.groups()
            if op not in parsed: parsed[op] = {}
            parsed[op][metric.strip()] = float(val)

    # Convert to unified lat dict
    name_map = {"Insert": "insert", "Point Query": "point_query", "Update": "update"}
    lat = {}
    for op_name, op_key in name_map.items():
        if op_name not in parsed: continue
        m = parsed[op_name]
        lat[op_key] = {
            "p0":  m.get("Minimum Latency", 0.0),
            "p25": m.get("25th Percentile Latency", 0.0),
            "p50": m.get("50th Percentile Latency", 0.0),
            "p75": m.get("75th Percentile Latency", 0.0),
            "p99": m.get("99th Percentile Latency", 0.0),
        }

    ops  = sum(1 for l in open(trace_path) if l[0] in ("I","P","U"))
    tput = ops / elapsed if elapsed > 0 else 0
    return lat, elapsed, tput

# =============================================================================
#  STAGES 4+5 — Run one (db, scale) pair
# =============================================================================
def run_one_db(db, scale, ycsb_trace, tec_trace):
    scale_lbl = f"2^{scale.bit_length()-1}"
    prefix    = f"{OUT_DIR}/{db}_scale{scale}"
    result    = {}

    # ---- YCSB run -----------------------------------------------------------
    banner("STAGE 4", f"Execute YCSB trace on {db}  [{scale_lbl}]")
    print(f"  trace  : {ycsb_trace}")
    print(f"  driver : {'C++ rocksdb-benchmark-harness (stats build)' if db=='rocksdb' else 'tectonic-cli execute  (same for both workloads)'}")
    reset_db(db)
    if db == "rocksdb":
        lat, wt, tp = run_rocksdb_harness(
            ycsb_trace,
            f"{prefix}_ycsb_stats.txt",
            f"{prefix}_ycsb_latency.csv")
    else:
        lat, wt, tp = run_tectonic_execute(ycsb_trace, db)
    result["ycsb"] = {"latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp}
    print(f"\n  YCSB:  wall={wt:.2f}s  tput={tp:,.0f} ops/s")
    _print_lat(lat)

    # ---- Tectonic run -------------------------------------------------------
    banner("STAGE 5", f"Execute Tectonic trace on {db}  [{scale_lbl}]  (same driver)")
    print(f"  trace  : {tec_trace}")
    reset_db(db)
    if db == "rocksdb":
        lat, wt, tp = run_rocksdb_harness(
            tec_trace,
            f"{prefix}_tectonic_stats.txt",
            f"{prefix}_tectonic_latency.csv")
    else:
        lat, wt, tp = run_tectonic_execute(tec_trace, db)
    result["tectonic"] = {"latency_us": lat, "wall_time_s": wt, "throughput_ops_s": tp}
    print(f"\n  Tectonic:  wall={wt:.2f}s  tput={tp:,.0f} ops/s")
    _print_lat(lat)

    return result

def _print_lat(lat):
    print(f"\n  {'op':<14} {'p0':>7} {'p25':>7} {'p50':>7} {'p75':>7} {'p99':>7} (us)")
    print(f"  {'-'*54}")
    for op in ["insert", "point_query", "update"]:
        d = lat.get(op, {})
        if not d: continue
        print(f"  {op:<14}"
              f" {d.get('p0',0):>7.2f} {d.get('p25',0):>7.2f}"
              f" {d.get('p50',0):>7.2f} {d.get('p75',0):>7.2f}"
              f" {d.get('p99',0):>7.2f}")
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
    print(SEP); sys.stdout.flush()

    # Load checkpoint
    results = {"config": {
        "scales": scales, "databases": dbs,
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
        except Exception as e:
            print(f"  [resume] could not load: {e}")
    sys.stdout.flush()

    for scale in scales:
        skey  = str(scale)
        slbl  = f"2^{scale.bit_length()-1}"
        todo  = [db for db in dbs if skey not in results["results"].get(db, {})]
        if not todo:
            print(f"\n  [skip] scale {slbl} complete for all dbs"); continue

        print(f"\n{'#'*70}\n#  SCALE = {scale:,}  ({slbl})\n{'#'*70}")
        check_disk()

        ycsb_trace = f"/tmp/sim_ycsb_{scale}.txt"
        tec_trace  = f"/tmp/sim_tectonic_{scale}.txt"

        if not os.path.exists(ycsb_trace):
            generate_ycsb(scale, ycsb_trace)
        else:
            print(f"  [skip] ycsb trace exists")
        if not os.path.exists(tec_trace):
            generate_tectonic(scale, tec_trace)
        else:
            print(f"  [skip] tectonic trace exists")

        comparison = compare_traces(ycsb_trace, tec_trace)
        check_disk()

        for db in todo:
            print(f"\n{'='*70}\n  DB={db}  SCALE={slbl}\n{'='*70}")
            check_disk()
            setup_db(db)
            db_res = run_one_db(db, scale, ycsb_trace, tec_trace)
            db_res["trace_comparison"] = comparison
            teardown_db(db)

            results["results"].setdefault(db, {})[skey] = db_res
            with open(RESULTS_PATH, "w") as f:
                json.dump(results, f, indent=2)
            print(f"\n  [checkpoint] saved -> {RESULTS_PATH}")
            trigger_plot()
            check_disk()

        # Cleanup traces when all dbs done for this scale
        if all(skey in results["results"].get(db, {}) for db in dbs):
            for p in [ycsb_trace, tec_trace]:
                if os.path.exists(p):
                    os.remove(p)
                    print(f"  [cleanup] deleted {p}")

    print(f"\n{SEP}\n  Done.  Results: {RESULTS_PATH}\n{SEP}")

if __name__ == "__main__":
    main()
