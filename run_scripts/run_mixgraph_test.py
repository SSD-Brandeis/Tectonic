#!/usr/bin/env python3
"""Run a mixgraph empirical performance comparison, extract access heatmaps, and plot them.

1. Generates Tectonic spec JSON.
2. Generates flat trace files for both db_bench and Tectonic.
3. Executes both workloads empirically against RocksDB.
4. Extracts access frequency vs key sequence heatmap data from traces.
5. Generates Figure 11 style access heatmap PDF plots.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
RUN_SCRIPTS_DIR = ROOT_DIR / "run_scripts"
PLOT_SCRIPTS_DIR = ROOT_DIR / "plot_scripts"
DATA_DIR = ROOT_DIR / "data" / "mixgraph_test"

DEFAULT_DB_BENCH = ROOT_DIR / "rocksdb-benchmark-harness" / "cmake-build-release" / "vendor/rocksdb/db_bench"
DEFAULT_TECTONIC_CLI = ROOT_DIR / "target" / "release" / "tectonic-cli"

def fail(msg: str) -> None:
    print(f"[FATAL] {msg}", file=sys.stderr)
    sys.exit(1)

def ensure_file(path: Path, label: str) -> None:
    if not path.exists():
        fail(f"{label} not found: {path}")

def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)

def find_latest_rocksdb_log(db_dir: Path) -> Path | None:
    log_file = db_dir / "LOG"
    if log_file.exists():
        return log_file
    log_files = list(db_dir.glob("LOG*"))
    if log_files:
        log_files.sort(key=lambda p: p.stat().st_mtime)
        return log_files[-1]
    return None

def main() -> None:
    parser = argparse.ArgumentParser(description="Run mixgraph Tectonic spec and db_bench heatmap experiment.")
    parser.add_argument("--num", type=int, default=1000000, help="Total number of entries (FLAGS_num)")
    parser.add_argument("--reads", type=int, default=420000, help="Total operations to execute")
    parser.add_argument("--key-size", type=int, default=48, help="Key size in bytes")
    parser.add_argument("--db-bench-bin", default=str(DEFAULT_DB_BENCH))
    parser.add_argument("--tectonic-cli", default=str(DEFAULT_TECTONIC_CLI))
    args = parser.parse_args()

    db_bench_bin = Path(args.db_bench_bin).resolve()
    tectonic_cli = Path(args.tectonic_cli).resolve()

    ensure_file(db_bench_bin, "db_bench binary")
    ensure_file(tectonic_cli, "tectonic-cli binary")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    db_bench_db = Path("/tmp/mixgraph_db_bench")
    tectonic_db = Path("/tmp/mixgraph_tectonic")
    
    clean_dir(db_bench_db)
    clean_dir(tectonic_db)

    # Ensure scripts are executable
    os.chmod(RUN_SCRIPTS_DIR / "create_mixgraph_spec.py", 0o755)
    os.chmod(RUN_SCRIPTS_DIR / "generate_mixgraph_workload.py", 0o755)
    os.chmod(RUN_SCRIPTS_DIR / "generate_heatmap_data.py", 0o755)
    os.chmod(PLOT_SCRIPTS_DIR / "plot_mixgraph_heatmap.py", 0o755)
    os.chmod(PLOT_SCRIPTS_DIR / "plot_mixgraph_distributions.py", 0o755)

    print("=" * 72)
    print("  Mixgraph Unified Heatmap Experiment Execution")
    print("=" * 72)

    # =========================================================================
    # STEP 1: GENERATE TECTONIC SPEC (.spec.json)
    # =========================================================================
    spec_file = DATA_DIR / "mixgraph.spec.json"
    gen_cmd = [
        sys.executable,
        str(RUN_SCRIPTS_DIR / "create_mixgraph_spec.py"),
        "--num", str(args.num),
        "--reads", str(args.reads),
        "--key-size", str(args.key_size),
        "--output", str(spec_file)
    ]
    print(f"\n[1/6] Generating Tectonic spec JSON: {' '.join(gen_cmd)}")
    subprocess.run(gen_cmd, check=True)


    # =========================================================================
    # STEP 2: GENERATE WORKLOAD TRACES FROM SPECS
    # =========================================================================
    tectonic_trace = Path("/tmp/tectonic_trace.txt")
    db_bench_trace = Path("/tmp/db_bench_trace.txt")

    # Generate Tectonic Spec Trace
    tec_gen_cmd = [
        str(tectonic_cli),
        "generate",
        "-w", str(spec_file),
        "-o", str(tectonic_trace)
    ]
    print(f"\n[2/6] Generating Tectonic Spec Trace: {' '.join(tec_gen_cmd)}")
    subprocess.run(tec_gen_cmd, check=True)

    # Generate Matching db_bench Trace
    db_gen_cmd = [
        sys.executable,
        str(RUN_SCRIPTS_DIR / "generate_mixgraph_workload.py"),
        "--num", str(args.num),
        "--reads", str(args.reads),
        "--key-size", str(args.key_size),
        "--output", str(db_bench_trace)
    ]
    print(f"Generating matching db_bench Trace: {' '.join(db_gen_cmd)}")
    subprocess.run(db_gen_cmd, check=True)


    # =========================================================================
    # STEP 3: EMPIRICAL EXECUTION ON ROCKSDB
    # =========================================================================
    # Run Native db_bench
    db_bench_cmd = [
        str(db_bench_bin),
        f"--db={db_bench_db}",
        "--benchmarks=fillrandom,mixgraph",
        "--use_direct_io_for_flush_and_compaction=true",
        "--use_direct_reads=true",
        "--cache_size=268435456",
        "--keyrange_dist_a=14.18",
        "--keyrange_dist_b=-2.917",
        "--keyrange_dist_c=0.0164",
        "--keyrange_dist_d=-0.08082",
        "--keyrange_num=30",
        "--value_k=0.2615",
        "--value_sigma=25.45",
        "--iter_k=2.517",
        "--iter_sigma=14.236",
        "--mix_get_ratio=0.85",
        "--mix_put_ratio=0.14",
        "--mix_seek_ratio=0.01",
        "--sine_mix_rate_interval_milliseconds=5000",
        "--sine_a=1000",
        "--sine_b=0.000073",
        "--sine_d=4500",
        "--perf_level=2",
        f"--reads={args.reads}",
        f"--num={args.num}",
        f"--key_size={args.key_size}"
    ]
    print(f"\n[3/6] Executing native db_bench empirically: {' '.join(db_bench_cmd)}")
    db_bench_log_path = DATA_DIR / "db_bench_run.log"
    with db_bench_log_path.open("w") as log_file:
        proc = subprocess.run(db_bench_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        log_file.write(proc.stdout)

    # Copy RocksDB LOG from native run
    db_bench_rocksdb_log = find_latest_rocksdb_log(db_bench_db)
    if db_bench_rocksdb_log:
        shutil.copy(db_bench_rocksdb_log, DATA_DIR / "db_bench_rocksdb.log")
        print(f"Copied db_bench RocksDB LOG to {DATA_DIR / 'db_bench_rocksdb.log'}")

    # Execute Tectonic Trace
    tectonic_cmd = [
        str(tectonic_cli),
        "execute",
        "-i", str(tectonic_trace),
        "-d", "rocksdb",
        "-p", str(tectonic_db),
        "-c", str(db_bench_db),
        "-t", "1"
    ]
    print(f"Executing Tectonic trace empirically: {' '.join(tectonic_cmd)}")
    tectonic_log_path = DATA_DIR / "tectonic_run.log"
    with tectonic_log_path.open("w") as log_file:
        proc = subprocess.run(tectonic_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        log_file.write(proc.stdout)

    # Copy RocksDB LOG from Tectonic execution
    tectonic_rocksdb_log = find_latest_rocksdb_log(tectonic_db)
    if tectonic_rocksdb_log:
        shutil.copy(tectonic_rocksdb_log, DATA_DIR / "tectonic_rocksdb.log")
        print(f"Copied Tectonic RocksDB LOG to {DATA_DIR / 'tectonic_rocksdb.log'}")


    # =========================================================================
    # STEP 4: PARSE TRACES TO EXTRACT HEATMAP DATA
    # =========================================================================
    heatmap_json = DATA_DIR / "heatmap_data.json"
    parse_cmd = [
        sys.executable,
        str(RUN_SCRIPTS_DIR / "generate_heatmap_data.py"),
        "--db-bench-trace", str(db_bench_trace),
        "--tectonic-trace", str(tectonic_trace),
        "--output", str(heatmap_json)
    ]
    print(f"\n[4/6] Extracting heatmap data from traces: {' '.join(parse_cmd)}")
    subprocess.run(parse_cmd, check=True)


    # =========================================================================
    # STEP 5: GENERATE FIGURES 11 ACCESS HEATMAPS
    # =========================================================================
    plot_cmd = [
        sys.executable,
        str(PLOT_SCRIPTS_DIR / "plot_mixgraph_heatmap.py"),
        "--results", str(heatmap_json),
        "--out-dir", str(DATA_DIR)
    ]
    print(f"\n[5/6] Generating access heatmap plots: {' '.join(plot_cmd)}")
    subprocess.run(plot_cmd, check=True)


    # =========================================================================
    # STEP 6: GENERATE VALUE SIZE & SCAN LENGTH DISTRIBUTION CDFS
    # =========================================================================
    dist_plot_cmd = [
        sys.executable,
        str(PLOT_SCRIPTS_DIR / "plot_mixgraph_distributions.py"),
        "--db-bench-trace", str(db_bench_trace),
        "--tectonic-trace", str(tectonic_trace),
        "--out-dir", str(DATA_DIR)
    ]
    print(f"\n[6/6] Generating distribution CDF plots: {' '.join(dist_plot_cmd)}")
    subprocess.run(dist_plot_cmd, check=True)

    # Save trace files to preserve them
    print(f"\nSaving execution trace files to {DATA_DIR}...")
    shutil.copy(db_bench_trace, DATA_DIR / "db_bench_trace.txt")
    shutil.copy(tectonic_trace, DATA_DIR / "tectonic_trace.txt")

    print("\n" + "=" * 72)
    print("  Mixgraph Heatmap & Distribution Experiment Finished Successfully!")
    print(f"  Spec JSON stored in : {spec_file}")
    print(f"  Plots stored in     : {DATA_DIR}")
    print("=" * 72)

if __name__ == "__main__":
    main()
