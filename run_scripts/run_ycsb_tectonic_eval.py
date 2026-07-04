#!/usr/bin/env python3
"""
YCSB vs Tectonic Workload Profile Experiment
=============================================
Stages (explicitly separated):
  STAGE 1 – Generate YCSB Workload A trace file
  STAGE 2 – Generate Tectonic Workload A trace file
  STAGE 3 – Compare the two trace files (op counts, key format, sample lines)
  STAGE 4 – Run YCSB trace on target database (tectonic-cli execute)
  STAGE 5 – Run Tectonic trace on SAME target database (tectonic-cli execute)
  STAGE 6 – Collect and compare metrics, plot, save results

Both workloads are executed via the SAME tectonic-cli execute driver with
IDENTICAL database configuration, so any difference in metrics reflects
the workload content itself, not execution mechanism differences.
"""

import os, sys, time, json, re, subprocess, shutil, argparse, collections

# ── Paths ──────────────────────────────────────────────────────────────────
TECTONIC_CLI = "/home/cc/Tectonic/target/release/tectonic-cli"
HARNESS_DIR  = "/home/cc/Tectonic/rocksdb-benchmark-harness"
YCSB_DIR     = f"{HARNESS_DIR}/vendor/YCSB"
M2           = "/home/cc/.m2/repository"

# Tectonic spec: workload-a.spec.json encodes YCSB Workload A with valid-key
# point queries (Beta(0.15,0.55) ≈ Zipfian) and matching key format.
TECTONIC_SPEC = f"{HARNESS_DIR}/experiments/workload-similarity/workload-a.spec.json"

YCSB_CP = ":".join([
    f"{YCSB_DIR}/file/conf",
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar",
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar",
])

STATS_DIR = "/home/cc/Tectonic/data/ycsb_tectonic_profile"
os.makedirs(STATS_DIR, exist_ok=True)

DATABASES   = ["rocksdb", "redis", "cassandra", "scylla"]
TEST_SCALES = [5_000, 10_000]
PROD_SCALES = [262144, 524288, 1048576, 2097152, 4194304]

# ── Helpers ────────────────────────────────────────────────────────────────
def banner(title):
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")
    sys.stdout.flush()

def run_cmd(cmd, cwd=None, capture=True):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    sys.stdout.flush()
    res = subprocess.run(cmd, cwd=cwd,
                         stdout=subprocess.PIPE if capture else None,
                         stderr=subprocess.PIPE if capture else None,
                         text=True)
    if res.returncode != 0:
        print(f"  [WARN] exit={res.returncode}")
        if capture and res.stderr:
            print(res.stderr[:800])
    return res

def check_disk():
    _, used, free = shutil.disk_usage("/")
    free_gb = free / 1024**3
    if free_gb < 20:
        print(f"  [DiskMonitor] Low space: {free_gb:.1f} GB free – pruning docker...")
        subprocess.run(["docker", "system", "prune", "-a", "-f"], capture_output=True)

# ── STAGE 1: Generate YCSB trace ──────────────────────────────────────────
def generate_ycsb(scale, out_path):
    banner(f"STAGE 1 – Generate YCSB Workload A trace  (scale={scale:,})")
    print(f"   Tool      : YCSB FileClient (Java)")
    print(f"   Spec      : workloads/workloada  (50% reads, 50% updates, Zipfian)")
    print(f"   Load ops  : {scale:,} sequential inserts")
    print(f"   Run ops   : {scale:,} (50% point queries + 50% updates)")
    print(f"   Output    : {out_path}")

    load_part = out_path + ".load"
    run_part  = out_path + ".run"

    run_cmd(["java", "-cp", YCSB_CP, "site.ycsb.Client",
             "-db", "site.ycsb.db.FileClient",
             "-P", "workloads/workloada",
             "-p", f"file.output={load_part}",
             "-p", f"recordcount={scale}",
             "-p", f"operationcount={scale}",
             "-load"], cwd=YCSB_DIR)

    run_cmd(["java", "-cp", YCSB_CP, "site.ycsb.Client",
             "-db", "site.ycsb.db.FileClient",
             "-P", "workloads/workloada",
             "-p", f"file.output={run_part}",
             "-p", f"recordcount={scale}",
             "-p", f"operationcount={scale}",
             "-t"], cwd=YCSB_DIR)

    with open(out_path, "w") as out_f:
        for part in [load_part, run_part]:
            with open(part) as inf:
                shutil.copyfileobj(inf, out_f)
    os.remove(load_part)
    os.remove(run_part)

    n = sum(1 for _ in open(out_path))
    print(f"  → Written: {n:,} lines")
    return out_path

# ── STAGE 2: Generate Tectonic trace ─────────────────────────────────────
def generate_tectonic(scale, out_path):
    banner(f"STAGE 2 – Generate Tectonic Workload A trace  (scale={scale:,})")
    scale_factor = scale / 1_000_000.0
    print(f"   Tool         : tectonic-cli generate")
    print(f"   Spec         : {TECTONIC_SPEC}")
    print(f"   Scale factor : {scale_factor}  (= {scale:,} / 1,000,000)")
    print(f"   Key format   : usertable:user<19-digit-numeric>  (same as YCSB)")
    print(f"   Read dist    : Beta(alpha=0.15, beta=0.55) ≈ YCSB Zipfian(0.99)")
    print(f"   Output       : {out_path}")

    run_cmd([TECTONIC_CLI, "generate",
             "-w", TECTONIC_SPEC,
             "-o", out_path,
             "-s", str(scale_factor)])

    n = sum(1 for _ in open(out_path))
    print(f"  → Written: {n:,} lines")
    return out_path

# ── STAGE 3: Compare traces ───────────────────────────────────────────────
def compare_traces(ycsb_path, tec_path):
    banner("STAGE 3 – Compare YCSB vs Tectonic trace files")

    def inspect(path):
        ops   = collections.Counter()
        keys  = set()
        first = []
        with open(path) as f:
            for line in f:
                line = line.rstrip()
                parts = line.split(" ", 2)
                op = parts[0]
                ops[op] += 1
                if len(parts) >= 2:
                    keys.add(parts[1])
                if len(first) < 3:
                    first.append(line[:80])
        return ops, len(keys), first

    y_ops, y_keys, y_sample = inspect(ycsb_path)
    t_ops, t_keys, t_sample = inspect(tec_path)

    print(f"\n  {'Op':<8} {'YCSB':>10} {'Tectonic':>10}")
    print(f"  {'-'*30}")
    all_ops = sorted(set(y_ops) | set(t_ops))
    for op in all_ops:
        print(f"  {op:<8} {y_ops.get(op,0):>10,} {t_ops.get(op,0):>10,}")
    print(f"  {'unique keys':<8} {y_keys:>10,} {t_keys:>10,}")

    print(f"\n  YCSB sample lines:")
    for l in y_sample: print(f"    {l}")
    print(f"\n  Tectonic sample lines:")
    for l in t_sample: print(f"    {l}")

    return {"ycsb_ops": dict(y_ops), "tectonic_ops": dict(t_ops),
            "ycsb_unique_keys": y_keys, "tectonic_unique_keys": t_keys}

# ── Database setup/teardown ────────────────────────────────────────────────
def setup_db(db):
    if db == "rocksdb":
        run_cmd(["rm", "-rf", "/tmp/eval-rocksdb"])
        os.makedirs("/tmp/eval-rocksdb", exist_ok=True)
    elif db == "redis":
        run_cmd(["docker", "rm", "-f", "redis-bench"])
        run_cmd(["docker", "run", "--name", "redis-bench", "-p", "6379:6379",
                 "-d", "redis:latest", "redis-server", "--appendonly", "no", "--save", ""])
        for _ in range(30):
            res = run_cmd(["docker", "exec", "redis-bench", "redis-cli", "ping"])
            if "PONG" in (res.stdout or ""):
                break
            time.sleep(1)
    elif db == "cassandra":
        res = run_cmd(["docker", "ps", "-a", "--filter", "name=cassandra-node-1", "--format", "{{.Names}}"])
        if "cassandra-node-1" not in (res.stdout or ""):
            run_cmd(["docker", "run", "--name", "cassandra-node-1", "-p", "9042:9042",
                     "-d", "cassandra:latest"])
        else:
            run_cmd(["docker", "start", "cassandra-node-1"])
        for _ in range(60):
            res = run_cmd(["docker", "exec", "cassandra-node-1", "cqlsh", "-e", "DESCRIBE KEYSPACES"])
            if "system" in (res.stdout or ""):
                break
            time.sleep(2)
    elif db == "scylla":
        # Stop cassandra first (shares port 9042)
        run_cmd(["docker", "stop", "cassandra-node-1"])
        run_cmd(["docker", "rm", "-f", "scylla-bench"])
        run_cmd(["docker", "run", "--name", "scylla-bench", "-p", "9042:9042",
                 "-d", "scylladb/scylla:latest", "--developer-mode", "1"])
        for _ in range(60):
            res = run_cmd(["docker", "exec", "scylla-bench", "cqlsh", "-e", "DESCRIBE KEYSPACES"])
            if "system" in (res.stdout or ""):
                break
            time.sleep(2)

def reset_db_state(db):
    """Wipe all data so both workloads start from an identical empty state."""
    if db == "rocksdb":
        run_cmd(["rm", "-rf", "/tmp/eval-rocksdb"])
        os.makedirs("/tmp/eval-rocksdb", exist_ok=True)
    elif db == "redis":
        run_cmd(["docker", "exec", "redis-bench", "redis-cli", "flushall"])
    elif db == "cassandra":
        run_cmd(["docker", "exec", "cassandra-node-1", "cqlsh", "-e",
                 "DROP KEYSPACE IF EXISTS tectonic;"])
    elif db == "scylla":
        run_cmd(["docker", "exec", "scylla-bench", "cqlsh", "-e",
                 "DROP KEYSPACE IF EXISTS tectonic;"])

def db_path_for(db):
    return {
        "rocksdb":  "/tmp/eval-rocksdb",
        "redis":    "redis://127.0.0.1:6379",
        "cassandra": "127.0.0.1:9042",
        "scylla":   "127.0.0.1:9042",
    }[db]

def cli_db_name(db):
    # tectonic-cli uses "scylla" driver for both cassandra and scylla
    return "scylla" if db in ("cassandra", "scylla") else db

def teardown_db(db):
    if db == "redis":
        run_cmd(["docker", "rm", "-f", "redis-bench"])
    elif db == "rocksdb":
        run_cmd(["rm", "-rf", "/tmp/eval-rocksdb"])
    elif db == "scylla":
        run_cmd(["docker", "rm", "-f", "scylla-bench"])
        # Restart cassandra for next iteration
        run_cmd(["docker", "start", "cassandra-node-1"])
        for _ in range(60):
            res = run_cmd(["docker", "exec", "cassandra-node-1", "cqlsh",
                           "-e", "DESCRIBE KEYSPACES"])
            if "system" in (res.stdout or ""):
                break
            time.sleep(2)

# ── STAGE 4/5: Execute a trace ────────────────────────────────────────────
def clean_metadata(path):
    """Remove FS/FE stat-flush lines that tectonic-cli generate emits."""
    with open(path) as f:
        lines = f.readlines()
    clean = [l for l in lines if not l.startswith(("FS ", "FE "))]
    with open(path, "w") as f:
        f.writelines(clean)

def execute_trace(label, workload_path, db, threads=1):
    banner(f"STAGE {'4' if label=='YCSB' else '5'} – Execute {label} trace on {db}")
    print(f"   Driver    : tectonic-cli execute  (SAME for both workloads)")
    print(f"   DB        : {db}  @ {db_path_for(db)}")
    print(f"   Threads   : {threads}")
    print(f"   Workload  : {workload_path}")

    clean_metadata(workload_path)

    cmd = [TECTONIC_CLI, "execute",
           "-i", workload_path,
           "-d", cli_db_name(db),
           "-p", db_path_for(db),
           "-t", str(threads)]

    res = run_cmd(cmd, capture=True)
    return res.stdout, res.stderr

# ── Parse tectonic-cli execute output ────────────────────────────────────
def parse_exec_output(stdout):
    metrics = {}
    for line in stdout.split("\n"):
        m = re.match(r'\[([^\]]+)\]\s+([^:]+):\s+([\d\.]+)(us|secs|ops/sec)?', line)
        if m:
            op, metric, val, _ = m.groups()
            if op not in metrics:
                metrics[op] = {}
            metrics[op][metric.strip()] = float(val)
    return metrics

# ── STAGE 6: Plot ─────────────────────────────────────────────────────────
def trigger_plot():
    subprocess.run(["python3", "plot_scripts/plot_ycsb_tectonic_eval.py"],
                   capture_output=True, cwd="/home/cc/Tectonic")

# ── Core per-database run ─────────────────────────────────────────────────
def run_one(db, scale, ycsb_trace, tec_trace, threads=1):
    print(f"\n{'#'*65}")
    print(f"# DATABASE={db}  SCALE={scale:,}")
    print(f"{'#'*65}")

    setup_db(db)
    check_disk()

    result = {}

    # ── YCSB run ──
    reset_db_state(db)
    stdout, stderr = execute_trace("YCSB", ycsb_trace, db, threads)
    result["ycsb"] = {"metrics": parse_exec_output(stdout)}

    # ── Tectonic run (same DB, same driver, same config) ──
    reset_db_state(db)
    stdout, stderr = execute_trace("Tectonic", tec_trace, db, threads)
    result["tectonic"] = {"metrics": parse_exec_output(stdout)}

    teardown_db(db)
    check_disk()
    return result

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                        help="Use small test scales (5k, 10k)")
    parser.add_argument("--db", nargs="+", default=DATABASES,
                        help="Databases to run (default: all)")
    args = parser.parse_args()

    scales  = TEST_SCALES if args.test else PROD_SCALES
    dbs     = args.db

    print("="*65)
    print("  YCSB vs Tectonic Profile Experiment")
    print(f"  Scales  : {scales}")
    print(f"  Databases: {dbs}")
    print("="*65)

    results_file = f"{STATS_DIR}/results_partial.json"
    results = {}
    if os.path.exists(results_file):
        try:
            with open(results_file) as f:
                loaded = json.load(f)
            # Only resume if scales match
            first_db = list(loaded.keys())[0] if loaded else None
            if first_db and str(scales[0]) in loaded.get(first_db, {}):
                results = loaded
                print(f"  Resuming from partial results ({results_file})")
        except Exception as e:
            print(f"  Could not load partial results: {e}")

    for scale in scales:
        # ── STAGE 1+2: Generate BOTH traces once per scale ─────────────
        ycsb_trace = f"/tmp/ycsb-workload-{scale}.txt"
        tec_trace  = f"/tmp/tectonic-workload-{scale}.txt"

        generate_ycsb(scale, ycsb_trace)
        generate_tectonic(scale, tec_trace)

        # ── STAGE 3: Compare ────────────────────────────────────────────
        comparison = compare_traces(ycsb_trace, tec_trace)

        # ── STAGE 4+5+6: Run on each DB ─────────────────────────────────
        for db in dbs:
            key = str(scale)
            if db not in results:
                results[db] = {}
            if key in results[db]:
                print(f"  Skipping {db} scale={scale} (already done)")
                continue

            db_result = run_one(db, scale, ycsb_trace, tec_trace)
            db_result["trace_comparison"] = comparison
            results[db][key] = db_result

            with open(results_file, "w") as f:
                json.dump(results, f, indent=2)
            print(f"  [Saved] {db} scale={scale}")

            trigger_plot()

        # Clean up trace files after all DBs are done for this scale
        for p in [ycsb_trace, tec_trace]:
            if os.path.exists(p):
                os.remove(p)

    final = f"{STATS_DIR}/results.json"
    with open(final, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  ✓ Done. Results saved to {final}")

if __name__ == "__main__":
    main()
