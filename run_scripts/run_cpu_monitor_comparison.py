#!/usr/bin/env python3
"""Compare CPU monitoring methods during workload generation only.

By default this pins each generator to one logical CPU. That keeps this aligned
with the single-thread workload-generation plot and avoids mixing the earlier
multi-core Tectonic blind-key pregeneration spike into the monitor comparison.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time


ROOT_DIR = "/home/cc/Tectonic"
HARNESS_DIR = f"{ROOT_DIR}/rocksdb-benchmark-harness"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"
YCSB_DIR = f"{HARNESS_DIR}/vendor/YCSB"
M2 = "/home/cc/.m2/repository"
OUT_DIR = f"{ROOT_DIR}/data/CPU-utilization"
PLOT_SCRIPT = f"{ROOT_DIR}/plot_scripts/plot_cpu_monitor_comparison.py"
DEFAULT_SCALE = 1_000
DEFAULT_WORKLOAD = "a"
POLL_INTERVAL = 1.0
CLK_TCK = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
CPU_COUNT = os.cpu_count() or 1
SEP = "=" * 72

YCSB_CP = ":".join([
    f"{YCSB_DIR}/file/conf",
    f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar",
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar",
])


def banner(title):
    print(f"\n{SEP}\n  {title}\n{SEP}", flush=True)


def fail(message):
    print(f"[FATAL] {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def ensure_file(path, label):
    if not os.path.exists(path):
        fail(f"{label} not found: {path}")


def remove_if_exists(path):
    if os.path.exists(path):
        os.remove(path)


def parse_stat_cpu_seconds(stat_text):
    end_comm = stat_text.rfind(")")
    if end_comm == -1:
        return None
    fields = stat_text[end_comm + 2:].split()
    if len(fields) <= 12:
        return None
    return (int(fields[11]) + int(fields[12])) / CLK_TCK


def read_process_cpu_seconds(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return parse_stat_cpu_seconds(f.read())
    except (FileNotFoundError, ProcessLookupError):
        return None


def process_exists(pid):
    return os.path.exists(f"/proc/{pid}")


def parse_float(text):
    try:
        return float(text.strip())
    except (TypeError, ValueError):
        return None


def parse_pidstat_cpu(output, pid):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    pid_s = str(pid)
    for line in reversed(lines):
        parts = line.split()
        if len(parts) < 8 or parts[0] == "Linux" or parts[0].startswith("#"):
            continue
        if "Average:" in parts[0]:
            continue
        try:
            pid_idx = parts.index(pid_s)
        except ValueError:
            continue
        # pidstat columns: time UID PID %usr %system %guest %wait %CPU CPU Command
        cpu_idx = pid_idx + 5
        if cpu_idx < len(parts):
            value = parse_float(parts[cpu_idx])
            if value is not None:
                return value
    return None


def parse_top_cpu(output, pid):
    pid_s = str(pid)
    for line in reversed(output.splitlines()):
        parts = line.split()
        if not parts or parts[0] != pid_s:
            continue
        # top columns: PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ COMMAND
        if len(parts) >= 9:
            return parse_float(parts[8])
    return None


class MonitorCollector:
    def __init__(self, pid, start_time, stop_event):
        self.pid = pid
        self.start_time = start_time
        self.stop_event = stop_event
        self.logs = {
            "procfs": [],
            "pidstat": [],
            "top": [],
        }
        self._lock = threading.Lock()
        self._threads = []

    def add_sample(self, tool, cpu_percent):
        if cpu_percent is None:
            return
        with self._lock:
            self.logs[tool].append({
                "elapsed_s": time.monotonic() - self.start_time,
                "cpu_percent": max(0.0, float(cpu_percent)),
            })

    def start(self):
        for target in (self._procfs_loop, self._pidstat_loop, self._top_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)

    def join(self):
        for thread in self._threads:
            thread.join(timeout=3.0)

    def _procfs_loop(self):
        last_wall = time.monotonic()
        last_cpu = read_process_cpu_seconds(self.pid)
        while not self.stop_event.is_set() and process_exists(self.pid):
            time.sleep(0.2)
            now = time.monotonic()
            current_cpu = read_process_cpu_seconds(self.pid)
            if current_cpu is not None and last_cpu is not None:
                delta_wall = now - last_wall
                delta_cpu = current_cpu - last_cpu
                if delta_wall > 0:
                    self.add_sample("procfs", (delta_cpu / delta_wall) * 100.0)
            last_wall = now
            last_cpu = current_cpu

    def _pidstat_loop(self):
        while not self.stop_event.is_set() and process_exists(self.pid):
            result = subprocess.run(
                ["pidstat", "-h", "-u", "-p", str(self.pid), "1", "1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self.add_sample("pidstat", parse_pidstat_cpu(result.stdout, self.pid))

    def _top_loop(self):
        while not self.stop_event.is_set() and process_exists(self.pid):
            result = subprocess.run(
                ["top", "-b", "-n", "2", "-d", "0.2", "-p", str(self.pid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self.add_sample("top", parse_top_cpu(result.stdout, self.pid))
            time.sleep(max(POLL_INTERVAL - 0.4, 0.1))


def tail_file(path, max_lines=40):
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return ""
    return "".join(lines[-max_lines:])


def available_cpu():
    affinity = sorted(os.sched_getaffinity(0))
    if not affinity:
        fail("no CPUs available in current affinity mask")
    return affinity[0]


def run_monitored(label, cmd, log_path, cpu_id=None, cwd=None):
    cpu_label = f"cpu={cpu_id} " if cpu_id is not None else ""
    print(f"\n  [{label}] {cpu_label}{' '.join(cmd)}", flush=True)
    start_time = time.monotonic()
    stop_event = threading.Event()

    preexec_fn = None
    if cpu_id is not None:
        def pin_child():
            os.sched_setaffinity(0, {cpu_id})
        preexec_fn = pin_child

    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            preexec_fn=preexec_fn,
        )
        collector = MonitorCollector(proc.pid, start_time, stop_event)
        collector.start()
        initial_cpu = read_process_cpu_seconds(proc.pid)
        last_cpu = initial_cpu
        usage_cpu = None
        while True:
            waited_pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
            if waited_pid == proc.pid:
                return_code = os.waitstatus_to_exitcode(status)
                proc.returncode = return_code
                usage_cpu = usage.ru_utime + usage.ru_stime
                break
            current_cpu = read_process_cpu_seconds(proc.pid)
            if current_cpu is not None:
                last_cpu = current_cpu
            time.sleep(0.02)
        end_time = time.monotonic()
        final_cpu = last_cpu
        stop_event.set()
        collector.join()

    duration_s = end_time - start_time
    cpu_seconds = 0.0
    if usage_cpu is not None:
        cpu_seconds = max(0.0, usage_cpu)
    elif initial_cpu is not None and final_cpu is not None:
        cpu_seconds = max(0.0, final_cpu - initial_cpu)
    avg_cpu = cpu_seconds / duration_s * 100.0 if duration_s > 0 else 0.0
    print(f"  [{label}] duration={duration_s:.2f}s cpu_seconds={cpu_seconds:.2f}s avg_cpu={avg_cpu:.1f}%", flush=True)

    if return_code != 0:
        print(tail_file(log_path), file=sys.stderr)
        fail(f"{label} failed with exit code {return_code}; see {log_path}")

    return {
        "cmd": cmd,
        "cwd": cwd,
        "cpu_id": cpu_id,
        "log_path": log_path,
        "duration_s": duration_s,
        "cpu_seconds": cpu_seconds,
        "average_cpu_percent": avg_cpu,
        "monitors": collector.logs,
    }


def offset_monitor_logs(phases, tool):
    offset = 0.0
    out = []
    boundaries = []
    for phase in phases:
        name = phase["phase"]
        result = phase["result"]
        for sample in result["monitors"].get(tool, []):
            shifted = dict(sample)
            shifted["elapsed_s"] = sample["elapsed_s"] + offset
            shifted["phase"] = name
            out.append(shifted)
        offset += result["duration_s"]
        boundaries.append(offset)
    return out, boundaries[:-1]


def generate_ycsb(workload, scale, cpu_id, tag):
    banner(f"generate YCSB workload {workload.upper()}")
    ycsb_load = f"{OUT_DIR}/monitor_{tag}_ycsb_load.part"
    ycsb_run = f"{OUT_DIR}/monitor_{tag}_ycsb_run.part"
    for path in (ycsb_load, ycsb_run):
        remove_if_exists(path)
    common = [
        "java", "-cp", YCSB_CP, "site.ycsb.Client",
        "-db", "site.ycsb.db.FileClient",
        "-P", f"workloads/workload{workload}",
        "-p", f"recordcount={scale}",
        "-p", f"operationcount={scale}",
    ]
    load_result = run_monitored(
        "YCSB load generation",
        common + ["-p", f"file.output={ycsb_load}", "-load"],
        f"{OUT_DIR}/monitor_{tag}_ycsb_load.log",
        cpu_id=cpu_id,
        cwd=YCSB_DIR,
    )
    remove_if_exists(ycsb_load)
    run_result = run_monitored(
        "YCSB run generation",
        common + ["-p", f"file.output={ycsb_run}", "-t"],
        f"{OUT_DIR}/monitor_{tag}_ycsb_run.log",
        cpu_id=cpu_id,
        cwd=YCSB_DIR,
    )
    remove_if_exists(ycsb_run)

    monitors = {}
    boundaries = []
    phases = [{"phase": "load", "result": load_result}, {"phase": "run", "result": run_result}]
    for tool in ("procfs", "pidstat", "top"):
        monitors[tool], boundaries = offset_monitor_logs(phases, tool)
    return {
        "phases": {"load": load_result, "run": run_result},
        "monitors": monitors,
        "phase_boundaries_s": boundaries,
        "duration_s": load_result["duration_s"] + run_result["duration_s"],
        "cpu_seconds": load_result["cpu_seconds"] + run_result["cpu_seconds"],
    }


def generate_tectonic(workload, scale, cpu_id, tag):
    banner(f"generate Tectonic workload {workload.upper()} blind")
    spec_path = f"{ROOT_DIR}/example-specs/ycsb_blind/{workload}.spec.json"
    output_path = f"{OUT_DIR}/monitor_{tag}_tectonic.txt"
    remove_if_exists(output_path)
    ensure_file(spec_path, "Tectonic blind spec")
    scale_factor = scale / 1_000_000.0
    result = run_monitored(
        "Tectonic generation",
        [TECTONIC_CLI, "generate", "-w", spec_path, "-o", output_path, "-s", str(scale_factor)],
        f"{OUT_DIR}/monitor_{tag}_tectonic_generate.log",
        cpu_id=cpu_id,
        cwd=ROOT_DIR,
    )
    remove_if_exists(output_path)
    return {
        "spec_path": spec_path,
        "scale_factor": scale_factor,
        "monitors": result["monitors"],
        "duration_s": result["duration_s"],
        "cpu_seconds": result["cpu_seconds"],
        "result": result,
    }


def output_tag(workload, scale, cpu_id):
    cpu_tag = "allcpus" if cpu_id is None else "singlecpu"
    return f"workload_generation_workload{workload}_scale{scale}_{cpu_tag}"


def result_path(tag):
    return f"{OUT_DIR}/monitor_comparison_{tag}.json"


def write_results(payload, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser(description="Run workload-generation CPU monitor comparison.")
    parser.add_argument("--workload", choices=["a", "b", "c", "d"], default=DEFAULT_WORKLOAD)
    parser.add_argument("--scale", type=int, default=DEFAULT_SCALE)
    parser.add_argument("--all-cpus", action="store_true", help="do not pin generation processes to one logical CPU")
    parser.add_argument("--no-plot", action="store_true")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    ensure_file(TECTONIC_CLI, "tectonic-cli")
    cpu_id = None if args.all_cpus else available_cpu()
    print(SEP)
    print("  experiment : CPU-utilization monitor comparison, workload generation only")
    print(f"  workload   : YCSB {args.workload.upper()}")
    print(f"  scale      : {args.scale:,} records + {args.scale:,} operations")
    print("  monitors   : procfs, pidstat, top")
    print(f"  cpu mode   : {'all available cpus' if cpu_id is None else f'one logical cpu ({cpu_id})'}")
    print(SEP, flush=True)

    tag = output_tag(args.workload, args.scale, cpu_id)
    results_path = result_path(tag)
    ycsb = generate_ycsb(args.workload, args.scale, cpu_id, tag)
    tectonic = generate_tectonic(args.workload, args.scale, cpu_id, tag)
    payload = {
        "experiment": "CPU-utilization",
        "mode": "workload_generation_monitor_comparison",
        "workload": args.workload,
        "scale": args.scale,
        "cpu_count": CPU_COUNT,
        "cpu_mode": "all_available_cpus" if cpu_id is None else "one_logical_cpu",
        "cpu_id": cpu_id,
        "output_tag": tag,
        "cpu_metric": "process cpu percent; 100% equals one fully used logical cpu",
        "monitors": ["procfs", "pidstat", "top"],
        "generation": {"ycsb": ycsb, "tectonic": tectonic},
    }
    write_results(payload, results_path)
    print(f"\n  results saved: {results_path}", flush=True)
    if not args.no_plot:
        subprocess.run(["python3", PLOT_SCRIPT, "--results", results_path], check=True)


if __name__ == "__main__":
    main()
