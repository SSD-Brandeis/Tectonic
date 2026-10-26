#!/usr/bin/env python3
"""
R2.O2(iii) Empirical Experiment Runner: YCSB Memory Under Bounded JVM Heap
Disentangling Heap Headroom from Working Set

Evaluates:
  1. Base JVM runtime footprint (glibc, libjvm.so, metaspace, code cache).
  2. Survivability threshold (4m fatal error, 6m/8m GC thrashing, 16m knee).
  3. Full sweep of YCSB workloads A-F across geometric heap limits:
     [16m, 32m, 64m, 128m, 256m, 512m, 1024m, default]
     Measuring both Load phase and Run phase, 5 independent trials each.
  4. Native X-Bench baseline on identical workloads A-F (5 trials each).
  5. (--stage breakdown) Heap headroom vs. working set, measured: G1 log (live heap after
     each GC) and the smaps split of resident memory at peak RSS, for 16m, 32m, 1024m, default.
"""

import os
import re
import sys
import time
import json
import shutil
import argparse
import subprocess
from pathlib import Path

# Paths
ROOT = Path("/home/cc/Tectonic")
EXP_DIR = ROOT / "data" / "revision" / "R2.O2_ycsb-bounded-memory"
RESULTS_DIR = EXP_DIR / "results"
LOGS_DIR = EXP_DIR / "logs"
YCSB_DIR = ROOT / "rocksdb-benchmark-harness" / "vendor" / "YCSB"
M2 = Path("/home/cc/.m2/repository")
TECTONIC_CLI = ROOT / "target" / "release" / "tectonic-cli"

YCSB_CP = (
    f"{YCSB_DIR}/file/conf:"
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar:"
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar:"
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar:"
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar:"
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar:"
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar"
)

WORKLOADS = ["a", "b", "c", "d", "e", "f"]
HEAP_CONFIGS = ["16m", "32m", "64m", "128m", "256m", "512m", "1024m", "default"]
NUM_TRIALS = 5
RECORD_COUNT = 1000000
OPERATION_COUNT = 1000000

TEMP_OUTPUT = Path("/tmp/ycsb_bounded_tmp.txt")
TEMP_XBENCH_OUTPUT = Path("/tmp/xbench_bounded_tmp.txt")


SMAPS_HEADER = re.compile(r"^([0-9a-f]+)-([0-9a-f]+) \S+ \S+ \S+ \S+\s*(.*)$")


def read_smaps(pid):
    """Resident memory per mapping of a live process: [[start, end, path, rss_kib], ...]."""
    maps = []
    with open(f"/proc/{pid}/smaps", "r") as f:
        for line in f:
            m = SMAPS_HEADER.match(line)
            if m:
                maps.append([int(m[1], 16), int(m[2], 16), m[3], 0])
            elif line.startswith("Rss:"):
                maps[-1][3] = int(line.split()[1])
    return maps


def run_and_measure(cmd, cwd=None, log_path=None, poll_interval=0.005, snapshot_smaps=False):
    """
    Executes a process, monitors resident set size via /proc/<pid>/status,
    records exact ru_maxrss via os.wait4, and writes complete output to log_path.
    With snapshot_smaps, also keeps the /proc/<pid>/smaps snapshot taken at the highest
    VmRSS seen (a new snapshot whenever VmRSS grows by more than 1 MB or 2%).
    """
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fd = os.open(str(log_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
    else:
        log_fd = os.open("/dev/null", os.O_WRONLY)

    start_time = time.time()
    pid = os.fork()
    if pid == 0:
        # Child process
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(log_fd)
        if cwd:
            os.chdir(str(cwd))
        try:
            os.execvp(cmd[0], cmd)
        except Exception as e:
            sys.stderr.write(f"execvp failed: {e}\n")
            sys.exit(127)

    # Parent process
    os.close(log_fd)
    mem_samples = []
    smaps_peak, smaps_peak_mb = None, 0.0

    while True:
        now = time.time()
        try:
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_mb = int(line.split()[1]) / 1024.0
                        mem_samples.append((now - start_time, rss_mb))
                        if snapshot_smaps and rss_mb > smaps_peak_mb + max(1.0, 0.02 * smaps_peak_mb):
                            snap = read_smaps(pid)
                            snap_mb = sum(m[3] for m in snap) / 1024.0
                            if snap_mb > smaps_peak_mb:
                                smaps_peak, smaps_peak_mb = snap, snap_mb
                        break
        except (FileNotFoundError, ProcessLookupError, IndexError):
            pass

        wpid, status, rusage = os.wait4(pid, os.WNOHANG)
        if wpid == pid:
            break
        time.sleep(poll_interval)

    duration = time.time() - start_time
    exit_code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (status >> 8)
    ru_maxrss_mb = rusage.ru_maxrss / 1024.0  # Linux rusage.ru_maxrss is in KiB

    sampled_max_rss = max([s[1] for s in mem_samples], default=0.0)
    # Peak RSS is the true kernel high-water mark
    peak_rss = max(ru_maxrss_mb, sampled_max_rss)

    # Read log
    log_content = ""
    if log_path and log_path.exists():
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            log_content = f.read()

    return {
        "cmd": cmd,
        "duration_s": duration,
        "peak_rss_mb": peak_rss,
        "ru_maxrss_mb": ru_maxrss_mb,
        "sampled_max_rss_mb": sampled_max_rss,
        "sample_count": len(mem_samples),
        "exit_code": exit_code,
        "ru_minflt": rusage.ru_minflt,
        "ru_majflt": rusage.ru_majflt,
        "smaps_peak": smaps_peak,
        "log_content": log_content,
    }


def parse_ycsb_log(log_content):
    """Parses runtime, throughput, and GC metrics from YCSB output."""
    metrics = {
        "runtime_ms": None,
        "throughput_ops_sec": None,
        "gc_count": 0,
        "gc_time_ms": 0,
        "gc_time_pct": 0.0,
        "op_counts": {},
    }
    for line in log_content.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            section, metric, val = parts[0], parts[1], parts[2]
            if section == "[OVERALL]" and metric == "RunTime(ms)":
                try:
                    metrics["runtime_ms"] = float(val)
                except ValueError:
                    pass
            elif section == "[OVERALL]" and metric == "Throughput(ops/sec)":
                try:
                    metrics["throughput_ops_sec"] = float(val)
                except ValueError:
                    pass
            elif section == "[TOTAL_GCs]" and metric == "Count":
                try:
                    metrics["gc_count"] = int(val)
                except ValueError:
                    pass
            elif section == "[TOTAL_GC_TIME]" and metric == "Time(ms)":
                try:
                    metrics["gc_time_ms"] = float(val)
                except ValueError:
                    pass
            elif section == "[TOTAL_GC_TIME_%]" and metric == "Time(%)":
                try:
                    metrics["gc_time_pct"] = float(val)
                except ValueError:
                    pass
            elif metric == "Operations":
                try:
                    metrics["op_counts"][section] = int(val)
                except ValueError:
                    pass

    return metrics


def cleanup_file(path):
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def run_base_jvm_benchmarks():
    """Measures minimal JVM execution footprint."""
    print("\n=======================================================")
    print("Part 1: Measuring Base JVM Runtime Footprint (5 trials)")
    print("=======================================================")

    benchmarks = {
        "java_version_default": ["java", "-version"],
        "java_version_16m": ["java", "-Xmx16m", "-version"],
        "ycsb_client_startup_16m": [
            "java", "-Xmx16m", "-cp", YCSB_CP, "site.ycsb.Client"
        ],
    }

    results = {}
    for name, cmd in benchmarks.items():
        print(f"\n--- Baseline: {name} ---")
        trials = []
        for t in range(1, NUM_TRIALS + 1):
            log_path = LOGS_DIR / "base_jvm" / f"{name}_trial{t}.log"
            res = run_and_measure(cmd, log_path=log_path)
            trials.append({
                "trial": t,
                "duration_s": res["duration_s"],
                "peak_rss_mb": res["peak_rss_mb"],
                "exit_code": res["exit_code"],
            })
            print(f"  Trial {t}: Peak RSS = {res['peak_rss_mb']:.2f} MB, Duration = {res['duration_s']:.3f} s")

        rss_vals = [t["peak_rss_mb"] for t in trials]
        results[name] = {
            "trials": trials,
            "mean_rss_mb": sum(rss_vals) / len(rss_vals),
            "min_rss_mb": min(rss_vals),
            "max_rss_mb": max(rss_vals),
        }

    with open(RESULTS_DIR / "jvm_base_footprint.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


def run_survivability_threshold():
    """Measures the JVM heap threshold below 16m (4m, 6m, 8m)."""
    print("\n=======================================================")
    print("Part 2: Measuring Survivability Threshold (Workload A)")
    print("=======================================================")

    thresholds = ["4m", "6m", "8m"]
    results = {}

    for heap in thresholds:
        print(f"\n--- Testing -Xmx{heap} on Workload A Load (1M ops) ---")
        cmd = [
            "java", f"-Xmx{heap}", "-cp", YCSB_CP, "site.ycsb.Client",
            "-db", "site.ycsb.db.FileClient",
            "-P", "workloads/workloada",
            "-p", f"file.output={TEMP_OUTPUT}",
            "-p", f"recordcount={RECORD_COUNT}",
            "-p", f"operationcount={OPERATION_COUNT}",
            "-load"
        ]
        log_path = LOGS_DIR / "threshold" / f"workloada_xmx{heap}.log"
        cleanup_file(TEMP_OUTPUT)
        res = run_and_measure(cmd, cwd=YCSB_DIR, log_path=log_path)
        cleanup_file(TEMP_OUTPUT)

        ycsb_metrics = parse_ycsb_log(res["log_content"])
        results[heap] = {
            "duration_s": res["duration_s"],
            "peak_rss_mb": res["peak_rss_mb"],
            "exit_code": res["exit_code"],
            "ycsb_metrics": ycsb_metrics,
        }
        print(f"  Result for -Xmx{heap}: Exit code = {res['exit_code']}, Duration = {res['duration_s']:.2f}s, Peak RSS = {res['peak_rss_mb']:.2f} MB")
        if ycsb_metrics["gc_count"] > 0:
            print(f"    GC: {ycsb_metrics['gc_count']} pauses, {ycsb_metrics['gc_time_ms']:.1f} ms ({ycsb_metrics['gc_time_pct']:.1f}%)")

    with open(RESULTS_DIR / "ycsb_threshold.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


def run_ycsb_bounded_sweep():
    """Runs the main empirical sweep across workloads A-F, 8 heap configs, 5 trials."""
    print("\n=======================================================")
    print("Part 3: Main YCSB Bounded Memory Sweep (A-F, 8 configs, 5 trials)")
    print("=======================================================")

    results_file = RESULTS_DIR / "ycsb_bounded_memory.json"
    data = {}
    if results_file.exists():
        try:
            with open(results_file, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}

    total_configs = len(WORKLOADS) * len(HEAP_CONFIGS)
    config_idx = 0

    for w in WORKLOADS:
        if w not in data:
            data[w] = {}

        for heap in HEAP_CONFIGS:
            config_idx += 1
            print(f"\n[{config_idx}/{total_configs}] Workload {w.upper()} | Heap: {heap}")

            if heap in data[w] and len(data[w][heap].get("load_trials", [])) == NUM_TRIALS:
                print("  Already completed. Skipping.")
                continue

            load_trials = []
            run_trials = []

            for t in range(1, NUM_TRIALS + 1):
                # 1. Load Phase
                load_cmd = ["java"]
                if heap != "default":
                    load_cmd.append(f"-Xmx{heap}")
                load_cmd.extend([
                    "-cp", YCSB_CP, "site.ycsb.Client",
                    "-db", "site.ycsb.db.FileClient",
                    "-P", f"workloads/workload{w}",
                    "-p", f"file.output={TEMP_OUTPUT}",
                    "-p", f"recordcount={RECORD_COUNT}",
                    "-p", f"operationcount={OPERATION_COUNT}",
                    "-load"
                ])
                load_log = LOGS_DIR / "ycsb" / f"{w}_{heap}_trial{t}_load.log"
                cleanup_file(TEMP_OUTPUT)
                load_res = run_and_measure(load_cmd, cwd=YCSB_DIR, log_path=load_log)
                cleanup_file(TEMP_OUTPUT)
                load_metrics = parse_ycsb_log(load_res["log_content"])

                # 2. Run Phase
                run_cmd = ["java"]
                if heap != "default":
                    run_cmd.append(f"-Xmx{heap}")
                run_cmd.extend([
                    "-cp", YCSB_CP, "site.ycsb.Client",
                    "-db", "site.ycsb.db.FileClient",
                    "-P", f"workloads/workload{w}",
                    "-p", f"file.output={TEMP_OUTPUT}",
                    "-p", f"recordcount={RECORD_COUNT}",
                    "-p", f"operationcount={OPERATION_COUNT}",
                    "-t"
                ])
                run_log = LOGS_DIR / "ycsb" / f"{w}_{heap}_trial{t}_run.log"
                cleanup_file(TEMP_OUTPUT)
                run_res = run_and_measure(run_cmd, cwd=YCSB_DIR, log_path=run_log)
                cleanup_file(TEMP_OUTPUT)
                run_metrics = parse_ycsb_log(run_res["log_content"])

                load_trials.append({
                    "trial": t,
                    "duration_s": load_res["duration_s"],
                    "peak_rss_mb": load_res["peak_rss_mb"],
                    "throughput_ops_sec": load_metrics["throughput_ops_sec"],
                    "gc_count": load_metrics["gc_count"],
                    "gc_time_ms": load_metrics["gc_time_ms"],
                    "gc_time_pct": load_metrics["gc_time_pct"],
                })

                run_trials.append({
                    "trial": t,
                    "duration_s": run_res["duration_s"],
                    "peak_rss_mb": run_res["peak_rss_mb"],
                    "throughput_ops_sec": run_metrics["throughput_ops_sec"],
                    "gc_count": run_metrics["gc_count"],
                    "gc_time_ms": run_metrics["gc_time_ms"],
                    "gc_time_pct": run_metrics["gc_time_pct"],
                })

                print(
                    f"  Trial {t}: Load RSS = {load_res['peak_rss_mb']:.1f} MB ({load_res['duration_s']:.2f}s) | "
                    f"Run RSS = {run_res['peak_rss_mb']:.1f} MB ({run_res['duration_s']:.2f}s)"
                )

            # Calculate summary stats
            def calc_stats(trials, key):
                vals = [tr[key] for tr in trials if tr[key] is not None]
                if not vals:
                    return {"mean": 0.0, "stdev": 0.0, "min": 0.0, "max": 0.0}
                mean = sum(vals) / len(vals)
                variance = sum((x - mean) ** 2 for x in vals) / len(vals) if len(vals) > 1 else 0.0
                stdev = variance ** 0.5
                return {
                    "mean": mean,
                    "stdev": stdev,
                    "min": min(vals),
                    "max": max(vals),
                    "cv_pct": (stdev / mean * 100.0) if mean > 0 else 0.0,
                }

            data[w][heap] = {
                "load_trials": load_trials,
                "run_trials": run_trials,
                "summary": {
                    "load_rss": calc_stats(load_trials, "peak_rss_mb"),
                    "load_duration": calc_stats(load_trials, "duration_s"),
                    "load_throughput": calc_stats(load_trials, "throughput_ops_sec"),
                    "load_gc_time": calc_stats(load_trials, "gc_time_ms"),
                    "run_rss": calc_stats(run_trials, "peak_rss_mb"),
                    "run_duration": calc_stats(run_trials, "duration_s"),
                    "run_throughput": calc_stats(run_trials, "throughput_ops_sec"),
                    "run_gc_time": calc_stats(run_trials, "gc_time_ms"),
                }
            }

            # Save incrementally after each heap config
            with open(results_file, "w") as f:
                json.dump(data, f, indent=2)

    return data


def run_xbench_baseline():
    """Runs native X-Bench baseline on workloads A-F (5 trials each)."""
    print("\n=======================================================")
    print("Part 4: Native X-Bench Baseline (A-F, 5 trials)")
    print("=======================================================")

    results = {}
    for w in WORKLOADS:
        spec_path = ROOT / "example-specs" / "ycsb" / f"{w}.spec.json"
        print(f"\n--- X-Bench Workload {w.upper()} ({spec_path.name}) ---")
        trials = []

        for t in range(1, NUM_TRIALS + 1):
            cmd = [
                str(TECTONIC_CLI), "generate",
                "-w", str(spec_path),
                "-o", str(TEMP_XBENCH_OUTPUT)
            ]
            log_path = LOGS_DIR / "xbench" / f"{w}_trial{t}.log"
            cleanup_file(TEMP_XBENCH_OUTPUT)
            res = run_and_measure(cmd, log_path=log_path)
            file_size_mb = TEMP_XBENCH_OUTPUT.stat().st_size / (1024.0 * 1024.0) if TEMP_XBENCH_OUTPUT.exists() else 0.0
            cleanup_file(TEMP_XBENCH_OUTPUT)

            trials.append({
                "trial": t,
                "duration_s": res["duration_s"],
                "peak_rss_mb": res["peak_rss_mb"],
                "output_size_mb": file_size_mb,
                "exit_code": res["exit_code"],
            })
            print(f"  Trial {t}: Peak RSS = {res['peak_rss_mb']:.2f} MB, Duration = {res['duration_s']:.3f} s")

        rss_vals = [tr["peak_rss_mb"] for tr in trials]
        dur_vals = [tr["duration_s"] for tr in trials]
        results[w] = {
            "trials": trials,
            "mean_rss_mb": sum(rss_vals) / len(rss_vals),
            "stdev_rss_mb": (sum((x - sum(rss_vals)/len(rss_vals))**2 for x in rss_vals)/len(rss_vals))**0.5,
            "min_rss_mb": min(rss_vals),
            "max_rss_mb": max(rss_vals),
            "mean_duration_s": sum(dur_vals) / len(dur_vals),
            "stdev_duration_s": (sum((x - sum(dur_vals)/len(dur_vals))**2 for x in dur_vals)/len(dur_vals))**0.5,
        }

    with open(RESULTS_DIR / "xbench_baseline.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


def export_csv_summaries(ycsb_data, xbench_data, base_data):
    """Exports structured CSV summary tables for reporting and plotting."""
    # 1. Peak RSS Summary (Load & Run)
    rss_csv_path = RESULTS_DIR / "ycsb_rss_summary.csv"
    with open(rss_csv_path, "w") as f:
        f.write("Workload,Phase," + ",".join(HEAP_CONFIGS) + ",X-Bench_Baseline\n")
        for w in WORKLOADS:
            # Load
            load_vals = [f"{ycsb_data[w][h]['summary']['load_rss']['mean']:.1f}" for h in HEAP_CONFIGS]
            xb_val = f"{xbench_data[w]['mean_rss_mb']:.1f}"
            f.write(f"{w.upper()},Load," + ",".join(load_vals) + f",{xb_val}\n")
            # Run
            run_vals = [f"{ycsb_data[w][h]['summary']['run_rss']['mean']:.1f}" for h in HEAP_CONFIGS]
            f.write(f"{w.upper()},Run," + ",".join(run_vals) + f",{xb_val}\n")

    # 2. Duration Summary
    dur_csv_path = RESULTS_DIR / "ycsb_duration_summary.csv"
    with open(dur_csv_path, "w") as f:
        f.write("Workload,Phase," + ",".join(HEAP_CONFIGS) + ",X-Bench_Baseline\n")
        for w in WORKLOADS:
            load_vals = [f"{ycsb_data[w][h]['summary']['load_duration']['mean']:.2f}" for h in HEAP_CONFIGS]
            xb_val = f"{xbench_data[w]['mean_duration_s']:.2f}"
            f.write(f"{w.upper()},Load," + ",".join(load_vals) + f",{xb_val}\n")
            run_vals = [f"{ycsb_data[w][h]['summary']['run_duration']['mean']:.2f}" for h in HEAP_CONFIGS]
            f.write(f"{w.upper()},Run," + ",".join(run_vals) + f",{xb_val}\n")

    print(f"\nExported summary CSVs to {rss_csv_path} and {dur_csv_path}")


BREAKDOWN_HEAPS = ["16m", "32m", "1024m", "default"]
GC_PAUSE = re.compile(r"GC\(\d+\) (Pause .*?) (\d+)M->(\d+)M\((\d+)M\) ([\d.]+)ms")
GC_EXIT_HEAP = re.compile(r"garbage-first heap\s+total (\d+)K, used (\d+)K \[(0x[0-9a-f]+), (0x[0-9a-f]+)\)")


def capture_environment():
    """Records the machine and JVM settings the runs used (logs/env/)."""
    env_dir = LOGS_DIR / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    commands = {
        "uname.txt": ["uname", "-a"],
        "lscpu.txt": ["lscpu"],
        "meminfo.txt": ["head", "-3", "/proc/meminfo"],
        "java_version.txt": ["java", "-version"],
        "java_flags_default.txt": ["java", "-XX:+PrintFlagsFinal", "-version"],
        "java_flags_xmx16m.txt": ["java", "-Xmx16m", "-XX:+PrintFlagsFinal", "-version"],
    }
    for name, cmd in commands.items():
        out = subprocess.run(cmd, capture_output=True, text=True)
        (env_dir / name).write_text(out.stdout + out.stderr)


def parse_gc_log(text):
    """Java heap from a -Xlog:gc,gc+heap+exit log: pauses, heap before/after each GC, heap range."""
    pauses = [(kind, int(b), int(a), int(c), float(ms)) for kind, b, a, c, ms in GC_PAUSE.findall(text)]
    exit_heap = GC_EXIT_HEAP.search(text)
    used_at_exit_mb = int(exit_heap[2]) / 1024.0
    return {
        "gc_pauses": len(pauses),
        "gc_pause_ms": sum(p[4] for p in pauses),
        # Live heap after a collection is the heap's working set; the most it ever was:
        "live_after_gc_max_mb": max((p[2] for p in pauses), default=None),
        "live_after_gc_mean_mb": (sum(p[2] for p in pauses) / len(pauses)) if pauses else None,
        # Most heap in use at any point (before a GC, or at exit):
        "heap_used_max_mb": max([p[1] for p in pauses] + [used_at_exit_mb]),
        "heap_committed_max_mb": max([p[3] for p in pauses] + [int(exit_heap[1]) / 1024.0]),
        "heap_used_at_exit_mb": used_at_exit_mb,
        "heap_range": [int(exit_heap[3], 16), int(exit_heap[4], 16)],
    }


def classify_smaps(maps, heap_range):
    """Splits resident memory (MB) into Java heap, file-backed mappings and other anonymous memory."""
    lo, hi = heap_range
    parts = {"java_heap_mb": 0.0, "file_backed_mb": 0.0, "other_anon_mb": 0.0}
    files = {}
    for start, end, path, rss_kib in maps:
        if start < hi and end > lo:
            parts["java_heap_mb"] += rss_kib / 1024.0
        elif path.startswith("/"):
            parts["file_backed_mb"] += rss_kib / 1024.0
            files[path] = files.get(path, 0.0) + rss_kib / 1024.0
        else:
            # metaspace, code cache, GC data structures, malloc arenas, thread stacks
            parts["other_anon_mb"] += rss_kib / 1024.0
    parts["total_mb"] = sum(parts.values())
    parts["largest_files_mb"] = dict(sorted(files.items(), key=lambda kv: -kv[1])[:5])
    return parts


def run_heap_breakdown():
    """
    Separates heap headroom from working set, measured: the YCSB runs again (A-F, load and run,
    BREAKDOWN_HEAPS, NUM_TRIALS) with the G1 log (live heap after every collection) and an smaps
    snapshot at peak RSS (resident Java heap vs. the rest of the process), plus page faults.
    """
    print("\n=======================================================")
    print("Part 5: Heap headroom vs. working set (GC log + smaps at peak RSS)")
    print("=======================================================")
    capture_environment()
    out_dir = LOGS_DIR / "breakdown"
    results = {}
    for w in WORKLOADS:
        results[w] = {}
        for heap in BREAKDOWN_HEAPS:
            results[w][heap] = {}
            for phase, flag in [("load", "-load"), ("run", "-t")]:
                trials = []
                for t in range(1, NUM_TRIALS + 1):
                    stem = f"{w}_{heap}_trial{t}_{phase}"
                    gc_log = out_dir / f"{stem}.gc.log"
                    cmd = ["java"] + ([f"-Xmx{heap}"] if heap != "default" else []) + [
                        f"-Xlog:gc,gc+heap+exit:file={gc_log}:uptime,level,tags",
                        "-cp", YCSB_CP, "site.ycsb.Client",
                        "-db", "site.ycsb.db.FileClient",
                        "-P", f"workloads/workload{w}",
                        "-p", f"file.output={TEMP_OUTPUT}",
                        "-p", f"recordcount={RECORD_COUNT}",
                        "-p", f"operationcount={OPERATION_COUNT}",
                        flag,
                    ]
                    cleanup_file(TEMP_OUTPUT)
                    res = run_and_measure(cmd, cwd=YCSB_DIR, log_path=out_dir / f"{stem}.log",
                                          snapshot_smaps=True)
                    cleanup_file(TEMP_OUTPUT)
                    gc = parse_gc_log(gc_log.read_text())
                    ycsb = parse_ycsb_log(res["log_content"])
                    trial = {
                        "trial": t,
                        "exit_code": res["exit_code"],
                        "duration_s": res["duration_s"],
                        "peak_rss_mb": res["peak_rss_mb"],
                        "ru_minflt": res["ru_minflt"],
                        "ru_majflt": res["ru_majflt"],
                        "throughput_ops_sec": ycsb["throughput_ops_sec"],
                        "ycsb_gc_count": ycsb["gc_count"],
                        "ycsb_gc_time_pct": ycsb["gc_time_pct"],
                        **{k: v for k, v in gc.items() if k != "heap_range"},
                        "resident_at_peak": classify_smaps(res["smaps_peak"], gc["heap_range"]),
                    }
                    trials.append(trial)
                    r = trial["resident_at_peak"]
                    print(f"  {stem}: RSS {res['peak_rss_mb']:.1f} MB = heap {r['java_heap_mb']:.1f} + "
                          f"file {r['file_backed_mb']:.1f} + anon {r['other_anon_mb']:.1f} "
                          f"(snapshot {r['total_mb']:.1f}); live after GC <= {gc['live_after_gc_max_mb']} MB, "
                          f"{gc['gc_pauses']} pauses, {res['ru_minflt']} minor faults")
                results[w][heap][phase] = trials
                with open(RESULTS_DIR / "ycsb_heap_breakdown.json", "w") as f:
                    json.dump(results, f, indent=2)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=["all", "breakdown"], default="all",
                        help="all: parts 1-4 (the original sweep); breakdown: part 5 only")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if args.stage == "breakdown":
        run_heap_breakdown()
        return

    print("===================================================================")
    print("Starting R2.O2(iii) Empirical Evaluation: YCSB Bounded Memory Sweep")
    print(f"System: OpenJDK 17 on Linux | RAM: 187 GB | Operations: {OPERATION_COUNT}")
    print("===================================================================")

    # 1. Base JVM
    base_data = run_base_jvm_benchmarks()

    # 2. Survivability threshold
    run_survivability_threshold()

    # 3. Native X-Bench baseline
    xbench_data = run_xbench_baseline()

    # 4. YCSB Bounded Memory Sweep
    ycsb_data = run_ycsb_bounded_sweep()

    # 5. Export summary tables
    export_csv_summaries(ycsb_data, xbench_data, base_data)

    print("\n===================================================================")
    print("All Empirical Evaluations Completed Successfully!")
    print(f"Results stored under: {RESULTS_DIR}")
    print("===================================================================")


if __name__ == "__main__":
    main()
