#!/usr/bin/env python3
"""Full empirical runner for figure_6_validate.

Runs fresh workload-generator measurements for:
  - YCSB workloads A-F
  - X-Bench/Tectonic YCSB-shaped workloads A-F
  - KVBench YCSB-shaped workloads A-E (KVBench has no F)
  - KVBench workloads I-V
  - X-Bench/Tectonic KVBench-shaped workloads I-V

All traces are written under data/figure_6_validate/traces. This script never reads
or writes data/generator_comparison.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/cc/Tectonic')
OUT_DIR = ROOT / 'data' / 'figure_6_validate'
TRACE_DIR = OUT_DIR / 'traces'
LOG_PATH = OUT_DIR / 'figure_6_validate_full.log'
STATUS_PATH = OUT_DIR / 'figure_6_validate_status.json'

TECTONIC_CLI = ROOT / 'target' / 'release' / 'tectonic-cli'
KVBENCH_CLI = Path('/home/cc/KV-WorkloadGenerator/bin/load_gen')
YCSB_DIR = ROOT / 'rocksdb-benchmark-harness' / 'vendor' / 'YCSB'
M2 = Path('/home/cc/.m2/repository')

YCSB_CP = ':'.join([
    str(YCSB_DIR / 'file' / 'conf'),
    str(YCSB_DIR / 'file' / 'target' / 'file-binding-0.18.0-SNAPSHOT.jar'),
    str(M2 / 'org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar'),
    str(M2 / 'org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar'),
    str(M2 / 'org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar'),
    str(M2 / 'org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar'),
    str(YCSB_DIR / 'core' / 'target' / 'core-0.18.0-SNAPSHOT.jar'),
])

ALL_OPS = ['Insert', 'Update', 'Point Query', 'Point Delete', 'Range Query', 'Range Delete']
YCSB_OP_MAP = {
    'INSERT': 'Insert',
    'UPDATE': 'Update',
    'READ': 'Point Query',
    'DELETE': 'Point Delete',
    'SCAN': 'Range Query',
}
YCSB_WORKLOADS = list('ABCDEF')
KV_WORKLOADS = ['I', 'II', 'III', 'IV', 'V']

KVBENCH_YCSB_ARGS = {
    'A': '-I 1000000 -Q 500000 -U 500000 --UD 3 --ED 3 --entry_size 1050 -L 0.025',
    'B': '-I 1000000 -Q 950000 -U 50000 --UD 3 --ED 3 --entry_size 1050 -L 0.025',
    'C': '-I 1000000 -Q 1000000 --UD 3 --ED 3 --entry_size 1050 -L 0.025',
    'D': '-I 1050000 -Q 950000 --UD 3 --ED 3 --entry_size 1050 -L 0.025',
    'E': '-I 1050000 -S 950000 -Y 0.0001 --YCSB=1 --ED 3 --entry_size 1050 -L 0.025',
}
KVBENCH_ARGS = {
    'I': '-I 1000000 -Q 1000000 -Z 0.8 --ED 0 --ZD 2 --entry_size 1024',
    'II': '-I 500000 -D 100000 -U 250000 -Q 150000 -Z 1 --ID 0 --UD 0 --ED 0 --ZD 0 --entry_size 1024',
    'III': '-I 1000000 -U 500000 -Q 500000 -Z 0.5 --UD 3 --ED 0 --ZD 0 --entry_size 1024',
    'IV': '-I 1000000 -U 500000 -R 500000 -y 0.000001 --UD 3 --entry_size 1024',
    'V': '-I 950000 -Q 50000 -Z 0 --ID 3 --ED 0 --ZD 0 --entry_size 1024',
}
TECTONIC_YCSB_SPECS = {w: f'example-specs/ycsb_blind/{w.lower()}.spec.json' for w in YCSB_WORKLOADS}
TECTONIC_KV_SPECS = {w: f'example-specs/kvbench/{w.lower()}.spec.json' for w in KV_WORKLOADS}

class HumanLog:
    def __init__(self, path: Path, append: bool):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a' if append else 'w', encoding='utf-8')
    def write(self, line: str = '') -> None:
        print(line, flush=True)
        self.handle.write(line + '\n')
        self.handle.flush()
    def close(self) -> None:
        self.handle.close()

def get_process_rss_mb(pid: int) -> float:
    try:
        with open(f'/proc/{pid}/status', 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) / 1024.0
    except (FileNotFoundError, ProcessLookupError):
        return 0.0
    return 0.0

def peak_mem(trace: dict) -> float:
    return max((float(row[1]) for row in trace.get('mem_log', [])), default=0.0)

def write_status(status: dict) -> None:
    tmp = STATUS_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(status, indent=2), encoding='utf-8')
    tmp.replace(STATUS_PATH)

def should_skip(path: Path, resume: bool) -> bool:
    return resume and path.exists() and path.stat().st_size > 0

def run_command(cmd, cwd: Path | None, output_file: Path, log: HumanLog, poll_interval: float = 0.01) -> dict:
    if output_file.exists():
        output_file.unlink()
    log.write('')
    log.write('command:')
    log.write('  ' + ' '.join(str(part) for part in cmd))
    log.write(f'working directory: {cwd if cwd else ROOT}')
    log.write(f'temporary workload output: {output_file}')

    start = time.time()
    proc = subprocess.Popen([str(part) for part in cmd], cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    stdout_lines: list[str] = []

    def reader():
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_lines.append(line)
            log.write('  stdout | ' + line.rstrip())

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    mem_log = []
    last_poll = 0.0
    while proc.poll() is None:
        elapsed = time.time() - start
        if elapsed - last_poll >= poll_interval:
            rss = get_process_rss_mb(proc.pid)
            if rss > 0.0:
                mem_log.append([elapsed, rss])
            last_poll = elapsed
        time.sleep(0.005)
    thread.join(timeout=2.0)
    duration = time.time() - start
    if output_file.exists():
        output_file.unlink()
    log.write(f'finished: exit_code={proc.returncode}, duration_s={duration:.6f}, peak_rss_mb={max((m[1] for m in mem_log), default=0.0):.3f}')
    if proc.returncode != 0:
        raise RuntimeError(f'command failed with exit code {proc.returncode}: {' '.join(str(part) for part in cmd)}')
    return {'command': [str(part) for part in cmd], 'cwd': str(cwd) if cwd else None, 'stdout': stdout_lines, 'total_duration': duration, 'mem_log': mem_log}

def parse_ycsb_stdout(stdout_lines: list[str]) -> dict:
    by_raw_op: dict[str, dict[str, float]] = {}
    pattern = re.compile(r'^\[(?P<op>[A-Z]+)\],\s*(?P<metric>[^,]+),\s*(?P<value>[-0-9.]+)')
    for line in stdout_lines:
        match = pattern.match(line.strip())
        if not match:
            continue
        raw_op = match.group('op')
        metric = match.group('metric')
        value = float(match.group('value'))
        by_raw_op.setdefault(raw_op, {})[metric] = value
    op_durations = {op: 0.0 for op in ALL_OPS}
    op_counts = {op: 0 for op in ALL_OPS}
    for raw_op, metrics in by_raw_op.items():
        mapped = YCSB_OP_MAP.get(raw_op)
        if mapped is None:
            continue
        operations = int(metrics.get('Operations', 0))
        avg_us = metrics.get('AverageLatency(us)', 0.0)
        op_counts[mapped] += operations
        op_durations[mapped] += operations * avg_us / 1_000_000.0
    return {'op_counts': op_counts, 'op_durations': op_durations, 'raw_metrics': by_raw_op}

def parse_generator_stdout(stdout_lines: list[str], prefix: str) -> dict:
    op_durations = {op: 0.0 for op in ALL_OPS}
    load_phase_end = None
    marker = f'{prefix}_Op_Timings:'
    for line in stdout_lines:
        stripped = line.strip()
        if marker in stripped:
            payload = stripped.split(marker, 1)[1].strip()
            parsed = json.loads(payload)
            op_durations.update({op: float(parsed.get(op, 0.0) or 0.0) for op in ALL_OPS})
        elif stripped.startswith('KVbench_LoadPhase_End:'):
            load_phase_end = float(stripped.split(':', 1)[1].strip())
        elif prefix == 'Tectonic' and '[Tectonic Sequential Gen] Section 0' in stripped and 'finished at' in stripped:
            try:
                load_phase_end = float(stripped.split('finished at', 1)[1].strip().split('s')[0].strip())
            except Exception:
                pass
    return {'op_durations': op_durations, 'loading_phase_end_time': load_phase_end}

def run_ycsb(workload: str, log: HumanLog) -> dict:
    w = workload.lower()
    common = ['java', '-cp', YCSB_CP, 'site.ycsb.Client', '-db', 'site.ycsb.db.FileClient', '-P', f'workloads/workload{w}', '-p', '/tmp/unused=1']
    common = ['java', '-cp', YCSB_CP, 'site.ycsb.Client', '-db', 'site.ycsb.db.FileClient', '-P', f'workloads/workload{w}', '-p', f'file.output=/tmp/figure_6_validate_ycsb_{w}.txt', '-p', 'recordcount=1000000', '-p', 'operationcount=1000000']
    log.write(f'\n=== YCSB workload {workload}: load phase ===')
    load_raw = run_command(common + ['-load'], YCSB_DIR, Path(f'/tmp/figure_6_validate_ycsb_{w}.txt'), log)
    load_parsed = parse_ycsb_stdout(load_raw['stdout'])
    log.write(f'\n=== YCSB workload {workload}: execution phase ===')
    run_raw = run_command(common + ['-t'], YCSB_DIR, Path(f'/tmp/figure_6_validate_ycsb_{w}.txt'), log)
    run_parsed = parse_ycsb_stdout(run_raw['stdout'])
    return {
        'tool': 'YCSB', 'workload': workload,
        'load': {'total_duration': load_raw['total_duration'], 'op_counts': load_parsed['op_counts'], 'op_durations': load_parsed['op_durations'], 'raw_metrics': load_parsed['raw_metrics'], 'mem_log': load_raw['mem_log'], 'command': load_raw['command']},
        'run': {'total_duration': run_raw['total_duration'], 'op_counts': run_parsed['op_counts'], 'op_durations': run_parsed['op_durations'], 'raw_metrics': run_parsed['raw_metrics'], 'mem_log': run_raw['mem_log'], 'command': run_raw['command']},
    }

def run_kvbench(workload: str, args: str, log: HumanLog, label: str) -> dict:
    out = Path(f'/tmp/figure_6_validate_kvbench_{label}_{workload.lower()}.txt')
    cmd = [KVBENCH_CLI] + args.split() + ['--OP', out]
    log.write(f'\n=== KVBench {label} workload {workload} ===')
    raw = run_command(cmd, ROOT, out, log)
    parsed = parse_generator_stdout(raw['stdout'], 'KVbench')
    load_end = parsed['loading_phase_end_time']
    return {'tool': 'KVBench', 'workload': workload, 'workload_set': label, 'total_duration': raw['total_duration'], 'op_durations': parsed['op_durations'], 'empirical_op_durations': parsed['op_durations'], 'loading_phase_end_time': load_end, 'execution_phase_duration': raw['total_duration'] - load_end if load_end is not None else None, 'mem_log': raw['mem_log'], 'command': raw['command']}

def run_tectonic(workload: str, spec: str, log: HumanLog, label: str) -> dict:
    out = Path(f'/tmp/figure_6_validate_tectonic_{label}_{workload.lower()}.txt')
    cmd = [TECTONIC_CLI, 'generate', '-w', spec, '-o', out]
    log.write(f'\n=== X-Bench/Tectonic {label} workload {workload} ===')
    raw = run_command(cmd, ROOT, out, log)
    parsed = parse_generator_stdout(raw['stdout'], 'Tectonic')
    op = parsed['op_durations']
    load_end = parsed['loading_phase_end_time']
    if load_end is None:
        load_end = op.get('Insert', 0.0)
    return {'tool': 'X-Bench', 'workload': workload, 'workload_set': label, 'total_duration': raw['total_duration'], 'op_durations': op, 'empirical_op_durations': op, 'loading_phase_end_time': load_end, 'execution_phase_duration': raw['total_duration'] - load_end if load_end is not None else None, 'mem_log': raw['mem_log'], 'command': raw['command']}

def save_trace(path: Path, trace: dict, log: HumanLog) -> None:
    path.write_text(json.dumps(trace, indent=2), encoding='utf-8')
    log.write(f'saved: {path}')

def summarize_trace(trace: dict, log: HumanLog) -> None:
    if trace.get('tool') == 'YCSB':
        for phase in ['load', 'run']:
            part = trace[phase]
            log.write(f'  {phase} wall_time_s={part["total_duration"]:.6f}, peak_rss_mb={max((m[1] for m in part["mem_log"]), default=0.0):.3f}')
            for op in ALL_OPS:
                count = part['op_counts'].get(op, 0)
                duration = part['op_durations'].get(op, 0.0)
                if count or duration:
                    log.write(f'  {phase} {op}: operations={count}, total_latency_s={duration:.6f}')
    else:
        log.write(f'  wall_time_s={trace["total_duration"]:.6f}, peak_rss_mb={peak_mem(trace):.3f}')
        log.write(f'  load_phase_end_s={trace.get("loading_phase_end_time")}')
        for op in ALL_OPS:
            duration = trace['op_durations'].get(op, 0.0)
            if duration:
                log.write(f'  {op}: total_latency_s={duration:.6f}')

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume', action='store_true', help='skip traces that already exist')
    parser.add_argument('--no-resume', action='store_true', help='rerun even if traces exist')
    args = parser.parse_args()
    resume = args.resume and not args.no_resume

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    log = HumanLog(LOG_PATH, append=resume)
    status = {'started_utc': datetime.now(timezone.utc).isoformat(), 'trace_dir': str(TRACE_DIR), 'log': str(LOG_PATH), 'state': 'running', 'completed': [], 'current': None}
    write_status(status)
    try:
        log.write('figure_6_validate full empirical run')
        log.write(f'started_utc: {status["started_utc"]}')
        log.write(f'trace_dir: {TRACE_DIR}')
        log.write(f'resume: {resume}')
        log.write('data policy: this run writes fresh empirical traces and does not read data/generator_comparison')

        jobs = []
        for w in YCSB_WORKLOADS:
            jobs.append((f'ycsb_{w.lower()}', TRACE_DIR / f'ycsb_{w.lower()}_trace.json', lambda w=w: run_ycsb(w, log)))
        for w, spec in TECTONIC_YCSB_SPECS.items():
            jobs.append((f'tectonic_{w.lower()}', TRACE_DIR / f'tectonic_{w.lower()}_trace.json', lambda w=w, spec=spec: run_tectonic(w, spec, log, 'ycsb')))
        for w, kv_args in KVBENCH_YCSB_ARGS.items():
            jobs.append((f'kvbench_{w.lower()}', TRACE_DIR / f'kvbench_{w.lower()}_trace.json', lambda w=w, kv_args=kv_args: run_kvbench(w, kv_args, log, 'ycsb')))
        for w, kv_args in KVBENCH_ARGS.items():
            jobs.append((f'kvbench_{w.lower()}', TRACE_DIR / f'kvbench_{w.lower()}_trace.json', lambda w=w, kv_args=kv_args: run_kvbench(w, kv_args, log, 'kvbench')))
        for w, spec in TECTONIC_KV_SPECS.items():
            jobs.append((f'tectonic_{w.lower()}', TRACE_DIR / f'tectonic_{w.lower()}_trace.json', lambda w=w, spec=spec: run_tectonic(w, spec, log, 'kvbench')))

        for name, path, fn in jobs:
            status['current'] = name
            write_status(status)
            if should_skip(path, resume):
                log.write(f'\n=== skipping existing trace: {name} -> {path} ===')
                status['completed'].append({'name': name, 'path': str(path), 'skipped': True})
                write_status(status)
                continue
            trace = fn()
            summarize_trace(trace, log)
            save_trace(path, trace, log)
            status['completed'].append({'name': name, 'path': str(path), 'skipped': False})
            write_status(status)

        status['state'] = 'complete'
        status['current'] = None
        status['finished_utc'] = datetime.now(timezone.utc).isoformat()
        write_status(status)
        log.write(f'finished_utc: {status["finished_utc"]}')
        return 0
    except Exception as exc:
        status['state'] = 'failed'
        status['error'] = repr(exc)
        status['failed_utc'] = datetime.now(timezone.utc).isoformat()
        write_status(status)
        log.write(f'FAILED: {exc!r}')
        raise
    finally:
        log.close()

if __name__ == '__main__':
    raise SystemExit(main())
