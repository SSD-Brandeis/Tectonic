#!/usr/bin/env python3
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import os
import subprocess
import time
import sys

# Configurations
STATS_DIR = REPO_ROOT + "/data/cross_db_profile"
PLOTS_DIR = REPO_ROOT + "/experiment_plots_cross_db_profile"
XBENCH_CLI = REPO_ROOT + "/target/release/xbench-cli"

os.makedirs(STATS_DIR, exist_ok=True)
os.makedirs(PLOTS_DIR, exist_ok=True)

# List of workloads (name, path, scale)
workloads = []

# YCSB Workloads (baseline 1M ops, scale 0.01 -> 10k ops)
for w in ["a", "b", "c", "d", "e", "f"]:
    workloads.append((f"ycsb_{w}", f"example-specs/ycsb/{w}.spec.json", 0.01))

# KVBench Workloads (baseline 1M ops, scale 0.01 -> 10k ops)
for w in ["i", "ii", "iii", "iv", "v"]:
    workloads.append((f"kvbench_{w}", f"example-specs/kvbench/{w}.spec.json", 0.01))

# db_bench Workloads (baseline 900M ops, scale 0.00001 -> 9k ops)
for w in ["1", "2", "3", "4", "4b", "5"]:
    workloads.append((f"db_bench_{w}", f"example-specs/db_bench/{w}.spec.json", 0.00001))

# X-Bench Workloads (baseline 1M ops, scale 0.01 -> 10k ops)
workloads.append(("xbench_1", "example-specs/xbench/1.spec.json", 0.01))

print("=== Step 1: Compiling X-Bench with specific features ===")
sys.stdout.flush()
# Build with release profiles and specific database features
try:
    subprocess.run(["cargo", "build", "--release", "--features", "db-layer/rocksdb db-layer/redis db-layer/scylla"], cwd=REPO_ROOT, check=True)
except subprocess.CalledProcessError as e:
    print(f"Compilation failed: {e}")
    sys.exit(1)

def run_xbench(db_log_name, db_driver_name, db_path, workload_name, workload_path, scale):
    print(f"  Running workload {workload_name} on {db_log_name} (using {db_driver_name} driver at scale {scale})...")
    sys.stdout.flush()
    log_file_path = os.path.join(STATS_DIR, f"{db_log_name}_{workload_name}.log")
    
    cmd = [
        XBENCH_CLI, "benchmark",
        "-w", workload_path,
        "-d", db_driver_name,
        "-s", str(scale),
        "-t", "1"
    ]
    if db_path:
        cmd.extend(["-p", db_path])
        
    with open(log_file_path, "w") as f:
        # Run and capture stdout/stderr in log file
        subprocess.run(cmd, stdout=f, stderr=f)

# === Redis ===
print("\n=== Running Redis benchmarks ===")
sys.stdout.flush()
# Start container
subprocess.run(["docker", "rm", "-f", "redis-bench"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(["docker", "run", "--name", "redis-bench", "-p", "6379:6379", "-d", "redis:latest"], check=True)

# Wait for Redis
print("Waiting for Redis container to be ready...")
sys.stdout.flush()
for _ in range(30):
    res = subprocess.run(["docker", "exec", "redis-bench", "redis-cli", "ping"], capture_output=True, text=True)
    if "PONG" in res.stdout:
        break
    time.sleep(1)

for name, path, scale in workloads:
    # Flush Redis
    subprocess.run(["docker", "exec", "redis-bench", "redis-cli", "flushall"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run_xbench("redis", "redis", "redis://127.0.0.1:6379", name, path, scale)

# Cleanup Redis
subprocess.run(["docker", "rm", "-f", "redis-bench"], check=True)


# === Cassandra ===
print("\n=== Running Cassandra benchmarks ===")
sys.stdout.flush()
# Make sure cassandra-node-1 is running
subprocess.run(["docker", "start", "cassandra-node-1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Wait for Cassandra cqlsh
print("Waiting for Cassandra container to be ready...")
sys.stdout.flush()
for _ in range(60):
    res = subprocess.run(["docker", "exec", "cassandra-node-1", "cqlsh", "-e", "DESCRIBE KEYSPACES"], capture_output=True, text=True)
    if "system" in res.stdout or "xbench" in res.stdout or "ycsb" in res.stdout:
        break
    time.sleep(2)

for name, path, scale in workloads:
    # Drop keyspace xbench
    subprocess.run(["docker", "exec", "cassandra-node-1", "cqlsh", "-e", "DROP KEYSPACE IF EXISTS xbench;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run_xbench("cassandra", "scylla", "127.0.0.1:9042", name, path, scale)

# Stop Cassandra node to free port 9042 for ScyllaDB
subprocess.run(["docker", "stop", "cassandra-node-1"], check=True)


# === ScyllaDB ===
print("\n=== Running ScyllaDB benchmarks ===")
sys.stdout.flush()
subprocess.run(["docker", "rm", "-f", "scylla-bench"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(["docker", "run", "--name", "scylla-bench", "-p", "9042:9042", "-d", "scylladb/scylla:latest"], check=True)

# Wait for ScyllaDB cqlsh
print("Waiting for ScyllaDB container to be ready...")
sys.stdout.flush()
for _ in range(60):
    res = subprocess.run(["docker", "exec", "scylla-bench", "cqlsh", "-e", "DESCRIBE KEYSPACES"], capture_output=True, text=True)
    if "system" in res.stdout:
        break
    time.sleep(2)

for name, path, scale in workloads:
    # Drop keyspace xbench
    subprocess.run(["docker", "exec", "scylla-bench", "cqlsh", "-e", "DROP KEYSPACE IF EXISTS xbench;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run_xbench("scylla", "scylla", "127.0.0.1:9042", name, path, scale)

# Cleanup ScyllaDB
subprocess.run(["docker", "rm", "-f", "scylla-bench"], check=True)

# Restart original Cassandra node to restore env
subprocess.run(["docker", "start", "cassandra-node-1"], check=True)


# === RocksDB ===
print("\n=== Running RocksDB benchmarks ===")
sys.stdout.flush()
for name, path, scale in workloads:
    # Clean up RocksDB directory
    subprocess.run(["rm", "-rf", "/tmp/xbench-rocksdb"])
    run_xbench("rocksdb", "rocksdb", "/tmp/xbench-rocksdb", name, path, scale)
subprocess.run(["rm", "-rf", "/tmp/xbench-rocksdb"])

print("\n=== All Benchmarks Completed! ===")
sys.stdout.flush()
