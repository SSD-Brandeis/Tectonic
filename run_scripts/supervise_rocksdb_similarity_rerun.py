#!/usr/bin/env python3
"""
Detached supervisor for a RocksDB-only rerun of the YCSB/Tectonic similarity
experiment.

The supervisor is intentionally conservative:
  * backs up current result files before changing any checkpoints,
  * removes only RocksDB checkpoint entries for the requested scales,
  * runs the smallest scale first as a sanity pass,
  * monitors disk space while the child experiment is running,
  * aborts before the disk is exhausted instead of pruning Docker/system data,
  * regenerates the operation and end-to-end plots after a successful rerun.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Iterable


ROOT = Path("/home/cc/Tectonic")
OUT_DIR = ROOT / "data/ycsb_tectonic_similarity_all_dbs"
RESULTS_PATH = OUT_DIR / "results.json"
RUNNER = ROOT / "run_scripts/run_ycsb_tectonic_similarity_all_dbs.py"
OP_PLOT = ROOT / "plot_scripts/plot_ycsb_tectonic_similarity_all_dbs.py"
E2E_PLOT = ROOT / "plot_scripts/plot_ycsb_tectonic_similarity_end_to_end.py"

DEFAULT_SCALES = [2**18, 2**19, 2**20, 2**21, 2**22]


def utc_stamp() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def log(message: str) -> None:
    print(f"[{dt.datetime.utcnow().isoformat(timespec='seconds')}Z] {message}", flush=True)


def free_gb(path: Path | str = "/") -> float:
    usage = shutil.disk_usage(path)
    return usage.free / 1024**3


def check_free(min_free_gb: float, context: str) -> None:
    root_free = free_gb("/")
    tmp_free = free_gb("/tmp")
    log(f"disk check ({context}): root_free_gb={root_free:.1f}, tmp_free_gb={tmp_free:.1f}")
    if root_free < min_free_gb or tmp_free < min_free_gb:
        raise RuntimeError(
            f"insufficient free space for {context}: "
            f"root={root_free:.1f} GB, tmp={tmp_free:.1f} GB, "
            f"required={min_free_gb:.1f} GB"
        )


def atomic_write_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def load_results() -> dict:
    with RESULTS_PATH.open() as f:
        return json.load(f)


def backup_current_outputs(stamp: str) -> Path:
    backup_dir = OUT_DIR / "backups" / f"rocksdb_rerun_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    for path in OUT_DIR.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if (
            name == "results.json"
            or name == "db_images.json"
            or name.startswith("rocksdb_")
            or name.startswith("all_dbs_end_to_end_wall_time")
            or name.startswith("end_to_end_wall_time_legend")
        ):
            shutil.copy2(path, backup_dir / name)

    log(f"backup written: {backup_dir}")
    return backup_dir


def remove_rocksdb_scales(scales: Iterable[int]) -> list[str]:
    data = load_results()
    results = data.setdefault("results", {})
    rocksdb = results.setdefault("rocksdb", {})

    removed: list[str] = []
    for scale in scales:
        key = str(scale)
        if key in rocksdb:
            removed.append(key)
            del rocksdb[key]

    reruns = data.setdefault("reruns", [])
    reruns.append(
        {
            "timestamp_utc": utc_stamp(),
            "database": "rocksdb",
            "removed_scales": removed,
            "reason": "forced RocksDB-only rerun after end-to-end wall-time discrepancy",
        }
    )
    atomic_write_json(RESULTS_PATH, data)
    log(f"removed RocksDB checkpoint scales: {removed if removed else 'none'}")
    return removed


def append_disk_sample(csv_path: Path) -> tuple[float, float]:
    root_free = free_gb("/")
    tmp_free = free_gb("/tmp")
    new_file = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["timestamp_utc", "root_free_gb", "tmp_free_gb"])
        writer.writerow([dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", f"{root_free:.3f}", f"{tmp_free:.3f}"])
    return root_free, tmp_free


def run_monitored(
    cmd: list[str],
    *,
    disk_csv: Path,
    min_free_gb: float,
    abort_free_gb: float,
    monitor_interval_s: int,
    context: str,
) -> None:
    check_free(min_free_gb, f"before {context}")
    log(f"starting {context}: {' '.join(cmd)}")

    proc = subprocess.Popen(cmd, cwd=str(ROOT))
    try:
        while True:
            rc = proc.poll()
            root_free, tmp_free = append_disk_sample(disk_csv)
            if root_free < abort_free_gb or tmp_free < abort_free_gb:
                log(
                    f"aborting {context}: disk below threshold "
                    f"(root={root_free:.1f} GB, tmp={tmp_free:.1f} GB, threshold={abort_free_gb:.1f} GB)"
                )
                proc.terminate()
                try:
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=30)
                raise RuntimeError(f"aborted {context} due to low disk space")
            if rc is not None:
                if rc != 0:
                    raise RuntimeError(f"{context} failed with exit code {rc}")
                log(f"completed {context}")
                return
            time.sleep(monitor_interval_s)
    except KeyboardInterrupt:
        proc.terminate()
        raise


def require_scale_valid(scale: int) -> dict:
    data = load_results()
    try:
        pair = data["results"]["rocksdb"][str(scale)]
    except KeyError as exc:
        raise RuntimeError(f"missing RocksDB result for scale {scale}") from exc

    for workload in ("ycsb", "tectonic"):
        metrics = pair.get(workload, {})
        wall = metrics.get("wall_time_s")
        throughput = metrics.get("throughput_ops_s")
        lat = metrics.get("latency_us", {})
        if not isinstance(wall, (int, float)) or wall <= 0:
            raise RuntimeError(f"invalid {workload} wall_time_s for scale {scale}: {wall}")
        if not isinstance(throughput, (int, float)) or throughput <= 0:
            raise RuntimeError(f"invalid {workload} throughput_ops_s for scale {scale}: {throughput}")
        for op in ("insert", "point_query", "update"):
            if op not in lat:
                raise RuntimeError(f"missing {workload} latency for {op} at scale {scale}")
            if "p50" not in lat[op] or "p99" not in lat[op]:
                raise RuntimeError(f"missing {workload} p50/p99 for {op} at scale {scale}")

    comp = pair.get("trace_comparison", {})
    y_ops = comp.get("ycsb_ops", {})
    t_ops = comp.get("tectonic_ops", {})
    if y_ops.get("I") != scale or t_ops.get("I") != scale:
        raise RuntimeError(f"insert count mismatch for scale {scale}: ycsb={y_ops}, tectonic={t_ops}")
    expected_total = scale * 2
    if sum(y_ops.values()) != expected_total or sum(t_ops.values()) != expected_total:
        raise RuntimeError(f"total op count mismatch for scale {scale}: ycsb={y_ops}, tectonic={t_ops}")

    return pair


def read_rocksdb_counter(stats_path: Path, counter: str) -> float | None:
    if not stats_path.exists():
        return None
    prefix = f"{counter} "
    with stats_path.open() as f:
        for line in f:
            if line.startswith(prefix) and "COUNT :" in line:
                try:
                    return float(line.rsplit("COUNT :", 1)[1].strip().split()[0])
                except ValueError:
                    return None
    return None


def build_validation_summary(scales: Iterable[int], stamp: str, backup_dir: Path) -> Path:
    summary = {
        "timestamp_utc": utc_stamp(),
        "database": "rocksdb",
        "scales": [],
        "backup_dir": str(backup_dir),
        "results_path": str(RESULTS_PATH),
    }

    for scale in scales:
        pair = require_scale_valid(scale)
        row = {
            "scale": scale,
            "ycsb_wall_time_s": pair["ycsb"]["wall_time_s"],
            "tectonic_wall_time_s": pair["tectonic"]["wall_time_s"],
            "wall_time_delta_pct": (
                pair["tectonic"]["wall_time_s"] / pair["ycsb"]["wall_time_s"] - 1.0
            ) * 100.0,
            "trace_comparison": pair.get("trace_comparison", {}),
        }
        prefix = OUT_DIR / f"rocksdb_scale{scale}"
        for workload in ("ycsb", "tectonic"):
            stats_path = Path(f"{prefix}_{workload}_stats.txt")
            row[f"{workload}_stall_micros"] = read_rocksdb_counter(stats_path, "rocksdb.stall.micros")
            row[f"{workload}_compact_read_bytes"] = read_rocksdb_counter(stats_path, "rocksdb.compact.read.bytes")
            row[f"{workload}_compact_write_bytes"] = read_rocksdb_counter(stats_path, "rocksdb.compact.write.bytes")
        summary["scales"].append(row)

    out_path = OUT_DIR / f"rocksdb_rerun_validation_{stamp}.json"
    atomic_write_json(out_path, summary)
    log(f"validation summary written: {out_path}")
    return out_path


def run_plot_scripts(disk_csv: Path, min_free_gb: float, abort_free_gb: float, monitor_interval_s: int) -> None:
    for script in (OP_PLOT, E2E_PLOT):
        if script.exists():
            run_monitored(
                ["python3", str(script)],
                disk_csv=disk_csv,
                min_free_gb=min_free_gb,
                abort_free_gb=abort_free_gb,
                monitor_interval_s=monitor_interval_s,
                context=f"plot {script.name}",
            )
        else:
            log(f"plot script not found, skipping: {script}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Supervise a safe RocksDB-only similarity rerun.")
    parser.add_argument("--scales", nargs="+", type=int, default=DEFAULT_SCALES)
    parser.add_argument("--sanity-scale", type=int, default=2**18)
    parser.add_argument("--min-free-gb", type=float, default=35.0)
    parser.add_argument("--abort-free-gb", type=float, default=25.0)
    parser.add_argument("--monitor-interval-s", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = utc_stamp()
    pid_path = OUT_DIR / "rocksdb_rerun_supervisor.pid"
    disk_csv = OUT_DIR / f"rocksdb_rerun_disk_{stamp}.csv"
    pid_path.write_text(str(os.getpid()) + "\n")

    scales = list(dict.fromkeys(args.scales))
    if args.sanity_scale not in scales:
        raise RuntimeError(f"sanity scale {args.sanity_scale} is not in requested scales {scales}")
    remaining = [s for s in scales if s != args.sanity_scale]

    log(f"supervisor pid: {os.getpid()}")
    log(f"requested scales: {scales}")
    log(f"disk monitor csv: {disk_csv}")
    check_free(args.min_free_gb, "startup")
    backup_dir = backup_current_outputs(stamp)

    remove_rocksdb_scales([args.sanity_scale])
    run_monitored(
        ["python3", str(RUNNER), "--db", "rocksdb", "--scales", str(args.sanity_scale)],
        disk_csv=disk_csv,
        min_free_gb=args.min_free_gb,
        abort_free_gb=args.abort_free_gb,
        monitor_interval_s=args.monitor_interval_s,
        context=f"RocksDB sanity scale {args.sanity_scale}",
    )
    sanity = require_scale_valid(args.sanity_scale)
    y_wall = sanity["ycsb"]["wall_time_s"]
    t_wall = sanity["tectonic"]["wall_time_s"]
    log(
        f"sanity valid: scale={args.sanity_scale}, "
        f"ycsb_wall={y_wall:.3f}s, tectonic_wall={t_wall:.3f}s, "
        f"delta_pct={(t_wall / y_wall - 1.0) * 100.0:.2f}%"
    )

    if remaining:
        remove_rocksdb_scales(remaining)
        run_monitored(
            ["python3", str(RUNNER), "--db", "rocksdb", "--scales", *[str(s) for s in remaining]],
            disk_csv=disk_csv,
            min_free_gb=args.min_free_gb,
            abort_free_gb=args.abort_free_gb,
            monitor_interval_s=args.monitor_interval_s,
            context=f"RocksDB full remaining scales {remaining}",
        )

    validation_path = build_validation_summary(scales, stamp, backup_dir)
    run_plot_scripts(disk_csv, args.min_free_gb, args.abort_free_gb, args.monitor_interval_s)
    check_free(args.min_free_gb, "completion")
    log(f"done. validation={validation_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"FAILED: {exc}")
        raise
