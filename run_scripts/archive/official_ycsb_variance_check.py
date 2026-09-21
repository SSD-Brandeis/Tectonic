#!/usr/bin/env python3
"""
official_ycsb_variance_check.py
================================
Answers a specific question: does the OFFICIAL Apache YCSB Java client
itself show run-to-run execution-time variance in the same ballpark as the
5-10% gaps seen between "YCSB" and "X-Bench" rows in
data/discrepancy-investigation/table_5_client_driver.md?

Unlike every other script in this repo, this one runs YCSB with its REAL
database bindings (site.ycsb.db.rocksdb.RocksDBClient,
site.ycsb.db.CassandraCQLClient) directly against a live database — not
site.ycsb.db.FileClient (which never touches a database at all; see
debug_difference.md, Finding 3).

For each (db, scale), runs TRIALS independent load+run passes against a
freshly reset database (same restart_db()/drop_caches() pattern as
run_table5_rerun.py) and records YCSB's own reported
"[OVERALL], RunTime(ms)" for both phases. Reports mean/stddev/min/max/
coefficient-of-variation so it can be compared directly against the
X-Bench-vs-YCSB diffs already measured.

Writes to data/official_ycsb_variance_check/ (does not touch
data/table5_rerun/ or data/discrepancy-investigation/).
"""
import os
import re
import sys
import json
import time
import shutil
import subprocess
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_table5_rerun as t5  # reuse restart_db/teardown_db/run/log/banner/drop_caches

ROOT_DIR = "/home/cc/Tectonic"
# Use the pristine, unmodified upstream YCSB checkout (github.com/brianfrankcooper/YCSB)
# rather than the project's vendored fork under rocksdb-benchmark-harness/vendor/YCSB
# (which is an older commit with a custom FileClient binding bolted on) — this is a
# clean-room test of official YCSB's own variance, not the project's fork of it.
YCSB_DIR = f"{ROOT_DIR}/YCSB"
M2 = "/home/cc/.m2/repository"

OUT_DIR = f"{ROOT_DIR}/data/official_ycsb_variance_check"
RESULTS_PATH = f"{OUT_DIR}/results.json"
ROCKSDB_DIR = "/tmp/official_ycsb_variance_rocksdb"

TRIALS = 5
SCALES = [2**18, 2**19]  # the two scales that showed >5% X-Bench-vs-YCSB diffs

os.makedirs(OUT_DIR, exist_ok=True)

CORE_CP = [
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar",
]

ROCKSDB_CP = CORE_CP + [
    f"{YCSB_DIR}/rocksdb/target/rocksdb-binding-0.18.0-SNAPSHOT.jar",
] + [str(p) for p in __import__("pathlib").Path(f"{YCSB_DIR}/rocksdb/target/dependency").glob("*.jar")]

CASSANDRA_CP = CORE_CP + [
    f"{YCSB_DIR}/cassandra/target/cassandra-binding-0.18.0-SNAPSHOT.jar",
] + [str(p) for p in __import__("pathlib").Path(f"{YCSB_DIR}/cassandra/target/dependency").glob("*.jar")]

REDIS_CP = CORE_CP + [
    f"{YCSB_DIR}/redis/target/redis-binding-0.18.0-SNAPSHOT.jar",
] + [str(p) for p in __import__("pathlib").Path(f"{YCSB_DIR}/redis/target/dependency").glob("*.jar")]

SCYLLA_CP = CORE_CP + [
    f"{YCSB_DIR}/scylla/target/scylla-binding-0.18.0-SNAPSHOT.jar",
] + [str(p) for p in __import__("pathlib").Path(f"{YCSB_DIR}/scylla/target/dependency").glob("*.jar")]


def parse_runtime_ms(stdout):
    m = re.search(r'\[OVERALL\],\s*RunTime\(ms\),\s*([\d.]+)', stdout)
    return float(m.group(1)) if m else None


def run_ycsb_phase(cp, extra_args, phase_flag):
    cmd = ["java", "-cp", ":".join(cp), "site.ycsb.Client",
           "-P", f"{YCSB_DIR}/workloads/workloada"] + extra_args + [phase_flag]
    t5.log(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=YCSB_DIR, capture_output=True, text=True)
    if r.returncode != 0:
        t5.log(f"  [ERROR] exit={r.returncode}\n{r.stderr[-2000:]}")
        return None
    return parse_runtime_ms(r.stdout)


def setup_cql_schema(container):
    t5.run(["docker", "exec", container, "cqlsh", "-e",
            "CREATE KEYSPACE IF NOT EXISTS ycsb WITH REPLICATION = "
            "{'class':'SimpleStrategy','replication_factor':1};"], check=False)
    t5.run(["docker", "exec", container, "cqlsh", "-e",
            "CREATE TABLE IF NOT EXISTS ycsb.usertable (y_id varchar primary key, "
            "field0 varchar, field1 varchar, field2 varchar, field3 varchar, field4 varchar, "
            "field5 varchar, field6 varchar, field7 varchar, field8 varchar, field9 varchar);"],
           check=False)


def trial_rocksdb(scale):
    if os.path.exists(ROCKSDB_DIR):
        shutil.rmtree(ROCKSDB_DIR)
    os.makedirs(ROCKSDB_DIR, exist_ok=True)
    t5.drop_caches()
    args = ["-db", "site.ycsb.db.rocksdb.RocksDBClient",
            "-p", f"rocksdb.dir={ROCKSDB_DIR}",
            "-p", f"recordcount={scale}", "-p", f"operationcount={scale}"]
    load_ms = run_ycsb_phase(ROCKSDB_CP, args, "-load")
    run_ms = run_ycsb_phase(ROCKSDB_CP, args, "-t")
    shutil.rmtree(ROCKSDB_DIR, ignore_errors=True)
    if load_ms is None or run_ms is None:
        return None
    return {"load_ms": load_ms, "run_ms": run_ms, "total_ms": load_ms + run_ms}


def trial_cassandra(scale):
    t5.restart_db("cassandra")  # docker rm -fv + fresh container + readiness poll + drop_caches
    setup_cql_schema("sim-cassandra")
    args = ["-db", "site.ycsb.db.CassandraCQLClient",
            "-p", "hosts=127.0.0.1", "-p", "cassandra.keyspace=ycsb",
            "-p", "cassandra.readconsistencylevel=ONE", "-p", "cassandra.writeconsistencylevel=ONE",
            "-p", f"recordcount={scale}", "-p", f"operationcount={scale}",
            "-threads", "1"]
    load_ms = run_ycsb_phase(CASSANDRA_CP, args, "-load")
    run_ms = run_ycsb_phase(CASSANDRA_CP, args, "-t")
    t5.teardown_db("cassandra")
    if load_ms is None or run_ms is None:
        return None
    return {"load_ms": load_ms, "run_ms": run_ms, "total_ms": load_ms + run_ms}


def trial_redis(scale):
    t5.restart_db("redis")  # docker rm -fv + fresh container + PING readiness poll + drop_caches
    args = ["-db", "site.ycsb.db.RedisClient",
            "-p", "redis.host=127.0.0.1", "-p", "redis.port=6379",
            "-p", f"recordcount={scale}", "-p", f"operationcount={scale}"]
    load_ms = run_ycsb_phase(REDIS_CP, args, "-load")
    run_ms = run_ycsb_phase(REDIS_CP, args, "-t")
    t5.teardown_db("redis")
    if load_ms is None or run_ms is None:
        return None
    return {"load_ms": load_ms, "run_ms": run_ms, "total_ms": load_ms + run_ms}


def trial_scylla(scale):
    t5.restart_db("scylla")  # docker rm -fv + fresh container + readiness poll + drop_caches
    setup_cql_schema("sim-scylla")
    args = ["-db", "site.ycsb.db.scylla.ScyllaCQLClient",
            "-p", "scylla.hosts=127.0.0.1", "-p", "scylla.keyspace=ycsb",
            "-p", "scylla.readconsistencylevel=ONE", "-p", "scylla.writeconsistencylevel=ONE",
            "-p", f"recordcount={scale}", "-p", f"operationcount={scale}"]
    load_ms = run_ycsb_phase(SCYLLA_CP, args, "-load")
    run_ms = run_ycsb_phase(SCYLLA_CP, args, "-t")
    t5.teardown_db("scylla")
    if load_ms is None or run_ms is None:
        return None
    return {"load_ms": load_ms, "run_ms": run_ms, "total_ms": load_ms + run_ms}


def summarize(vals):
    if len(vals) < 2:
        return {}
    mean = statistics.mean(vals)
    stdev = statistics.stdev(vals)
    return {
        "trials": len(vals), "mean_s": round(mean / 1000, 2), "stdev_s": round(stdev / 1000, 2),
        "min_s": round(min(vals) / 1000, 2), "max_s": round(max(vals) / 1000, 2),
        "cv_pct": round((stdev / mean) * 100, 2),
        "spread_pct": round((max(vals) - min(vals)) / mean * 100, 2),
    }


def main():
    results = {}
    if os.path.exists(RESULTS_PATH):
        results = json.load(open(RESULTS_PATH))

    for db, trial_fn in [("rocksdb", trial_rocksdb), ("cassandra", trial_cassandra)]:
        results.setdefault(db, {})
        for scale in SCALES:
            slbl = f"2^{scale.bit_length() - 1}"
            key = str(scale)
            results[db].setdefault(key, [])
            done = len(results[db][key])
            for trial in range(done + 1, TRIALS + 1):
                t5.banner("TRIAL", f"official YCSB {db} scale={slbl} trial={trial}/{TRIALS}")
                res = trial_fn(scale)
                if res is None:
                    t5.log(f"  [skip] trial failed, not recording")
                    continue
                t5.log(f"  load={res['load_ms']/1000:.2f}s run={res['run_ms']/1000:.2f}s "
                       f"total={res['total_ms']/1000:.2f}s")
                results[db][key].append(res)
                json.dump(results, open(RESULTS_PATH, "w"), indent=2)

    t5.banner("SUMMARY", "Official YCSB run-to-run variance (total = load + run RunTime)")
    for db in results:
        for scale, trials in results[db].items():
            slbl = f"2^{int(scale).bit_length() - 1}"
            totals = [t["total_ms"] for t in trials]
            summ = summarize(totals)
            if summ:
                t5.log(f"  {db} @ {slbl}: mean={summ['mean_s']}s stdev={summ['stdev_s']}s "
                       f"min={summ['min_s']}s max={summ['max_s']}s "
                       f"CV={summ['cv_pct']}% spread={summ['spread_pct']}%")


if __name__ == "__main__":
    main()
