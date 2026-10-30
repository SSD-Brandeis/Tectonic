#!/usr/bin/env python3
"""Table 5 rerun with database-attributed byte counts (started 2026-10-29).

Same protocol as run_table5_revision_multirun.py (same databases, images, client
settings, workload traces, reset + page-cache drop before every trial, iostat on sda),
plus, for every trial, snapshots taken just before and just after execution:

  * kernel block I/O of the database container's cgroup (io.stat, device 8:0 = sda);
  * kernel block I/O of the client (tectonic-cli), run in its own cgroup
    /sys/fs/cgroup/table5-client; for RocksDB this is the database's own I/O;
  * each database's own counters:
      - Redis:     redis-cli INFO all
      - Cassandra: JMX metrics via `nodetool sjk mxdump` (Table tectonic.data,
                   CommitLog, Compaction)
      - ScyllaDB:  Prometheus metrics (http://127.0.0.1:9180/metrics)
      - RocksDB:   RocksDB statistics (-c statistics=1); counters start at 0 when the
                   database opens inside tectonic-cli, so no "before" snapshot is needed.

Per-trial order:
  reset database -> "before" snapshots -> drop page cache -> start iostat ->
  execute -> sync -> "after" snapshots -> end-of-run captures (as before) -> stop iostat

Output layout (under --out-dir): results/results_raw.json, db_state/trial<N>/<db>/<suite>/
(raw snapshot files and logs), logs/run.log, README.md.
"""
import os
import re
import sys
import json
import time
import shlex
import hashlib
import argparse
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_table5_revision_multirun as mr

base = mr.base

CLIENT_CGROUP = "/sys/fs/cgroup/table5-client"
DEVICE = "8:0"  # sda
CONTAINER = {"redis": "sim-redis", "cassandra": "sim-cassandra", "scylla": "sim-scylla"}
CASSANDRA_MX_QUERIES = [
    "org.apache.cassandra.metrics:type=Table,keyspace=tectonic,scope=data,*",
    "org.apache.cassandra.metrics:type=CommitLog,*",
    "org.apache.cassandra.metrics:type=Compaction,*",
]
ROCKSDB_CONFIG = "statistics=1"


# =============================================================================
#  SNAPSHOTS
# =============================================================================
def read_io_stat(cgroup_dir):
    """rbytes/wbytes/rios/wios/dbytes/dios for DEVICE from a cgroup's io.stat (0 if absent)."""
    out = {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0, "dbytes": 0, "dios": 0}
    try:
        for line in open(f"{cgroup_dir}/io.stat"):
            fields = line.split()
            if fields and fields[0] == DEVICE:
                for kv in fields[1:]:
                    k, v = kv.split("=")
                    out[k] = int(v)
    except FileNotFoundError:
        return None
    return out


def container_cgroup(db):
    r = base.run(["docker", "inspect", "-f", "{{.Id}}", CONTAINER[db]], capture=True, check=False)
    cid = (r.stdout or "").strip()
    return f"/sys/fs/cgroup/system.slice/docker-{cid}.scope" if cid else None


def capture_counters(db):
    """Raw text of the database's own counters, or None for RocksDB."""
    if db == "redis":
        r = base.run(["docker", "exec", CONTAINER[db], "redis-cli", "INFO", "all"], capture=True, check=False)
        return r.stdout or ""
    if db == "cassandra":
        parts = []
        for q in CASSANDRA_MX_QUERIES:
            r = base.run(["docker", "exec", CONTAINER[db], "nodetool", "sjk", "mxdump", "-q", q],
                         capture=True, check=False)
            parts.append(f"### query: {q}\n{r.stdout or ''}")
        return "\n".join(parts)
    if db == "scylla":
        r = base.run(["curl", "-s", "http://127.0.0.1:9180/metrics"], capture=True, check=False)
        return r.stdout or ""
    return None


def parse_counters(db, text):
    """The counters this rerun reports, parsed from capture_counters() text."""
    if text is None:
        return {}
    if db == "redis":
        keys = ("total_net_input_bytes", "total_net_output_bytes", "total_commands_processed",
                "used_memory_dataset")
        return {k: int(m.group(1)) for k in keys
                for m in [re.search(rf"^{k}:(\d+)", text, re.M)] if m}
    if db == "cassandra":
        out = {}
        for m in re.finditer(r'"name" : "org\.apache\.cassandra\.metrics:([^"]+)",\s*\n(.*?)\n  \}',
                             text, re.S):
            attrs = dict(re.findall(r'"(Count|Value)" : (-?\d+)', m.group(2)))
            if attrs:
                out[m.group(1)] = int(attrs.get("Count", attrs.get("Value")))
        return out
    if db == "scylla":
        out = {}
        for line in text.splitlines():
            m = re.match(r'(scylla_commitlog_bytes_written|scylla_io_queue_total_(?:write|read)_bytes)'
                         r'\{([^}]*)\} ([0-9.e+]+)$', line)
            if not m:
                continue
            labels = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
            key = m.group(1) + (f"[class={labels['class']}]" if "class" in labels else "")
            out[key] = out.get(key, 0) + int(float(m.group(3)))
        return out
    return {}


def snapshot(db, client_only=False):
    snap = {"time": time.time(), "client_io": read_io_stat(CLIENT_CGROUP)}
    if db in CONTAINER and not client_only:
        cg = container_cgroup(db)
        snap["container_cgroup"] = cg
        snap["container_io"] = read_io_stat(cg) if cg else None
        snap["counters_raw"] = capture_counters(db)
    return snap


def delta(after, before):
    if after is None:
        return None
    before = before or {}
    return {k: after[k] - before.get(k, 0) for k in after}


def save_raw(raw_dir, name, text):
    if text is None:
        return None
    path = f"{raw_dir}/{name}"
    with open(path, "w") as f:
        f.write(text)
    return path


# =============================================================================
#  EXECUTION
# =============================================================================
def client_command(db, trace_path):
    """tectonic-cli command line, run inside CLIENT_CGROUP as the invoking user."""
    cfg = mr.DB_CLIENT_CONFIG[db]
    cmd = [base.TECTONIC_CLI, "execute", "-i", trace_path, "-d", base.DB_CLI_DRIVER[db],
           "-p", base.DB_CLI_PATH[db], "-t", str(cfg["threads"])]
    if cfg["config"]:
        cmd += ["-c", cfg["config"]]
    if cfg["client_cpus"]:
        cmd = ["taskset", "-c", cfg["client_cpus"]] + cmd
    user = os.environ.get("USER") or subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
    inner = shlex.join(cmd)
    return ["sudo", "sh", "-c",
            f"echo $$ > {CLIENT_CGROUP}/cgroup.procs && exec runuser -u {user} -- {inner}"]


def execute_trial(db, workload, scale, trial, trace_path, before):
    """Like mr.execute_trial, with the client in its own cgroup and "after" snapshots
    taken right after execution (followed by sync), before the end-of-run captures."""
    sampler = base.IoStatSampler(device=base.IOSTAT_DEVICE)
    sampler.start()
    time.sleep(0.8)
    sec_r0, sec_w0 = base.get_disk_hardware_sectors(base.IOSTAT_DEVICE)

    exec_cmd = client_command(db, trace_path)
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

    base.run(["sync"])
    after = snapshot(db)

    wl_folder = "X-Bench" if workload == "tectonic" else "YCSB"
    raw_dir = f"{mr.DBSTATE_DIR}/trial{trial}/{db}/{wl_folder}"
    os.makedirs(raw_dir, exist_ok=True)
    raw_log = f"{raw_dir}/cli_raw_{db}_scale{scale}_{workload}_trial{trial}.log"
    with open(raw_log, "w") as f:
        f.write(r.stdout)

    saved_out_dir = base.OUT_DIR
    base.OUT_DIR = f"{mr.DBSTATE_DIR}/trial{trial}"
    try:
        db_metrics, saved_logs = base.capture_and_save_db_internal_logs(
            db, scale, workload, f"scale{scale}_{workload}_trial{trial}", r.stdout)
    finally:
        base.OUT_DIR = saved_out_dir

    base.run(["sync"])
    sec_r1, sec_w1 = base.get_disk_hardware_sectors(base.IOSTAT_DEVICE)
    sampler.stop()
    sampler.join(timeout=4)

    prefix = f"{db}_scale{scale}_{workload}"
    tool = {"redis": "redis_info.txt", "cassandra": "cassandra_mxdump.json.txt",
            "scylla": "scylla_metrics.prom"}.get(db)
    snapshot_files = {}
    if tool:
        snapshot_files["before"] = save_raw(raw_dir, f"{prefix}_before_run_{tool}", before.get("counters_raw"))
        snapshot_files["after"] = save_raw(raw_dir, f"{prefix}_after_run_{tool}", after.get("counters_raw"))

    rec = mr_parse_cli(r.stdout)
    io_totals = sampler.get_totals()
    io_totals["hardware_read_mb"] = round(((sec_r1 - sec_r0) * 512) / (1024 * 1024), 2)
    io_totals["hardware_write_mb"] = round(((sec_w1 - sec_w0) * 512) / (1024 * 1024), 2)
    counters_before = parse_counters(db, before.get("counters_raw"))
    counters_after = parse_counters(db, after.get("counters_raw"))
    rec.update({
        "trial": trial,
        "wall_time_s": round(elapsed, 4),
        "threads": mr.DB_CLIENT_CONFIG[db]["threads"],
        "client_config": mr.DB_CLIENT_CONFIG[db]["config"],
        "client_cpus": mr.DB_CLIENT_CONFIG[db]["client_cpus"],
        "io_bytes_total": io_totals,
        "db_internal_metrics": db_metrics,
        "db_log_files": saved_logs + [p for p in snapshot_files.values() if p],
        "cli_raw_log": raw_log,
        "cgroup_io": {
            "device": DEVICE,
            "client_cgroup": CLIENT_CGROUP,
            "client": delta(after["client_io"], before["client_io"]),
            "container_cgroup": after.get("container_cgroup"),
            "container": delta(after.get("container_io"), before.get("container_io")),
        },
        "db_counters": {
            "before": counters_before,
            "after": counters_after,
            "delta": {k: counters_after[k] - counters_before.get(k, 0) for k in counters_after},
            "snapshot_files": snapshot_files,
            "seconds_before_to_after": round(after["time"] - before["time"], 2),
        },
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return rec


def mr_parse_cli(stdout):
    """Per-operation and overall client metrics, parsed as in mr.execute_trial."""
    pat = re.compile(r'\[([^\]]+)\]\s+([^:]+):\s+([\d\.]+)\s*(us|ops/sec|secs)?')
    parsed, raw_overall = {}, {}
    for line in stdout.split("\n"):
        m = pat.match(line.strip())
        if not m:
            continue
        op, metric, val, _unit = m.groups()
        if op == "Overall":
            raw_overall[metric.strip()] = float(val)
        else:
            parsed.setdefault(op, {})[metric.strip()] = float(val)
    op_metrics = {}
    for op_name, op_key in base.EXECUTE_OP_NAME_MAP.items():
        if op_name in parsed:
            op_metrics[op_key] = {out_k: parsed[op_name][raw_k]
                                  for raw_k, out_k in base.EXECUTE_OP_METRIC_MAP.items()
                                  if raw_k in parsed[op_name]}
    overall = {out_k: raw_overall[raw_k] for raw_k, out_k in base.EXECUTE_OVERALL_METRIC_MAP.items()
               if raw_k in raw_overall}
    overall.update(raw_overall)
    return {"operation_metrics": op_metrics, "overall_metrics": overall}


# =============================================================================
#  PROVENANCE
# =============================================================================
def provenance():
    def out(cmd):
        return subprocess.run(cmd, capture_output=True, text=True, cwd=mr.ROOT_DIR).stdout.strip()
    h = hashlib.sha256()
    with open(base.TECTONIC_CLI, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return {
        "git_commit": out(["git", "rev-parse", "HEAD"]),
        "git_status_porcelain": out(["git", "status", "--porcelain"]).splitlines(),
        "tectonic_cli": base.TECTONIC_CLI,
        "tectonic_cli_sha256": h.hexdigest(),
        "tectonic_cli_mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.gmtime(os.path.getmtime(base.TECTONIC_CLI))),
        "kernel": out(["uname", "-r"]),
        "docker": out(["docker", "version", "--format", "{{.Server.Version}}"]),
    }


def write_readme(args, started):
    lines = [
        f"# Table 5 rerun with database-attributed bytes — started {started}",
        "",
        f"Status: {'PILOT (validation only, not for the paper)' if args.pilot else 'full rerun'}.",
        f"Databases: {', '.join(args.db)}. Scales: {', '.join(f'2^{s.bit_length() - 1}' for s in args.scales)}."
        f" Trials per cell: {args.trials}.",
        "",
        "Produced by `run_scripts/run_table5_rerun_db_counters.py` (see its docstring for the",
        "per-trial protocol). Same protocol as `data/table-5-revision-multirun/` plus, per trial,",
        "before/after snapshots of the database container's and client's cgroup block I/O",
        "(`cgroup_io` in `results/results_raw.json`) and of each database's own counters",
        "(`db_counters`, raw snapshot files under `db_state/`).",
        "",
        "Workload traces are read from `data/table-5-revision-multirun/workloads/` (unchanged,",
        "SHA-256 recorded there). RocksDB runs with `-c statistics=1`.",
        "",
    ]
    with open(f"{mr.OUT_DIR}/README.md", "w") as f:
        f.write("\n".join(lines))


# =============================================================================
#  MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Table 5 rerun with database-attributed byte counts")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--db", nargs="+", choices=mr.DEFAULT_DATABASES, default=mr.DEFAULT_DATABASES)
    parser.add_argument("--scales", nargs="+", type=int, required=True)
    parser.add_argument("--workloads", nargs="+", choices=["ycsb", "tectonic"], default=["ycsb", "tectonic"])
    parser.add_argument("--trials", type=int, default=mr.TRIALS)
    parser.add_argument("--pilot", action="store_true", help="label the output directory as a pilot")
    args = parser.parse_args()

    mr.set_out_dir(args.out_dir)
    mr.DB_CLIENT_CONFIG["rocksdb"]["config"] = ROCKSDB_CONFIG
    for d in (mr.OUT_DIR, mr.RESULTS_DIR, mr.DBSTATE_DIR, mr.LOGS_DIR):
        os.makedirs(d, exist_ok=True)
    base.OUT_DIR = mr.OUT_DIR
    base.ROCKSDB_DIR = mr.ROCKSDB_DIR
    base.DB_CLI_PATH["rocksdb"] = mr.ROCKSDB_DIR
    base.LOG_PATH = mr.LOG_PATH
    base._log_fh = open(mr.LOG_PATH, "a")
    subprocess.run(["sudo", "mkdir", "-p", CLIENT_CGROUP], check=True)

    started = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    if not os.path.exists(f"{mr.OUT_DIR}/README.md"):
        write_readme(args, started)
    config = {
        "experiment": "table-5-rerun-db-counters",
        "pilot": args.pilot,
        "started": started,
        "trials": args.trials,
        "scales": args.scales,
        "databases": args.db,
        "client_config": mr.DB_CLIENT_CONFIG,
        "db_images": base.DEFAULT_DB_IMAGES,
        "iostat_device": base.IOSTAT_DEVICE,
        "cgroup_device": DEVICE,
        "client_cgroup": CLIENT_CGROUP,
        "cassandra_mx_queries": CASSANDRA_MX_QUERIES,
        "workload_dir": mr.WORKLOAD_DIR,
        "provenance": provenance(),
    }
    results = {}
    if os.path.exists(mr.RESULTS_PATH):
        loaded = json.load(open(mr.RESULTS_PATH))
        results = loaded.get("results", {})
        config = {**loaded.get("config", {}), "resumed": started}
        base.log(f"  [resume] loaded {mr.RESULTS_PATH}")

    base.banner("CONFIG", "Table 5 rerun with database-attributed bytes")
    base.log(f"  Output dir : {mr.OUT_DIR}")
    base.log(f"  Pilot      : {args.pilot}")
    base.log(f"  Databases  : {args.db}  Scales: {args.scales}  Trials: {args.trials}")
    base.log(f"  CLI sha256 : {config['provenance']['tectonic_cli_sha256']}")

    def checkpoint():
        base.write_json_atomic(mr.RESULTS_PATH, {"config": config, "results": results})

    for scale in args.scales:
        slbl = f"2^{scale.bit_length() - 1}"
        base.banner("SCALE", f"Scale {slbl}")
        mr.enforce_disk_budget(f"scale {slbl}")
        traces = {w: mr.ensure_workload(w, scale) for w in args.workloads}
        for db in args.db:
            cfg = mr.DB_CLIENT_CONFIG[db]
            for workload in args.workloads:
                trace = traces[workload]
                if cfg["threads"] > 1:
                    mr.split_trace(trace, cfg["threads"])
                done = len(results.get(db, {}).get(str(scale), {}).get(workload, []))
                for trial in range(done + 1, args.trials + 1):
                    base.banner("TRIAL", f"{db} | {workload} | {slbl} | trial {trial}/{args.trials}")
                    mr.enforce_disk_budget(f"{db} {workload} {slbl} trial {trial}")
                    base.log(f"  [restart] resetting {db} for fresh cold storage state")
                    base.setup_db(db)
                    before = snapshot(db)
                    base.drop_caches()
                    rec = execute_trial(db, workload, scale, trial, trace, before)
                    results.setdefault(db, {}).setdefault(str(scale), {}).setdefault(workload, []).append(rec)
                    base.teardown_db(db)
                    checkpoint()
                    io = rec["io_bytes_total"]
                    cg = rec["cgroup_io"]
                    base.log(f"  -> wall={rec['wall_time_s']:.2f}s iostat r/w="
                             f"{io.get('total_read_mb', 0)}/{io.get('total_write_mb', 0)}MB "
                             f"client r/w={cg['client']['rbytes'] / 2**20:.1f}/{cg['client']['wbytes'] / 2**20:.1f}MB "
                             + (f"container r/w={cg['container']['rbytes'] / 2**20:.1f}/"
                                f"{cg['container']['wbytes'] / 2**20:.1f}MB" if cg["container"] else ""))
                if cfg["threads"] > 1:
                    mr.remove_shards(trace, cfg["threads"])
    checkpoint()
    base.banner("SUMMARY", "Table 5 rerun complete")
    print("COMPLETED: table-5-rerun-db-counters finished")


if __name__ == "__main__":
    main()
