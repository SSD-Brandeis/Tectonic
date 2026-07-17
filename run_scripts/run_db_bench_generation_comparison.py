#!/usr/bin/env python3
"""Compare db_bench-compatible text generation against Tectonic generation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from run_db_bench_exp_test import (
    DEFAULT_SCALE,
    DEFAULT_TECTONIC_CLI,
    ROOT_DIR,
    TMP_DIR,
    WORKLOADS,
    add_efficiency_metrics,
    banner,
    build_tectonic,
    ensure_file,
    fail,
    generated_ops_for_workload,
    parse_workloads,
    run_monitored,
    scaled_ops,
)


OUT_DIR = ROOT_DIR / "data" / "db_bench_exp_test" / "generation"
GENERATOR_SCRIPT = ROOT_DIR / "run_scripts" / "generate_db_bench_workload_file.py"
PLOT_SCRIPT = ROOT_DIR / "plot_scripts" / "plot_db_bench_generation_comparison.py"
RESULTS_PATH = OUT_DIR / "generation_results.json"


def line_count(path: Path) -> int:
    count = 0
    with path.open("rb") as f:
        for _ in f:
            count += 1
    return count


def add_file_metrics(result: dict, output_path: Path, generated_ops: int) -> dict:
    add_efficiency_metrics(result, generated_ops)
    result["output_path"] = str(output_path)
    result["output_bytes"] = output_path.stat().st_size if output_path.exists() else 0
    result["output_lines"] = line_count(output_path) if output_path.exists() else 0
    return result


def run_workload_generation(workload: str, scale: float, op_count: int, tectonic_cli: Path) -> dict:
    cfg = WORKLOADS[workload]
    banner(f"db_bench workload-generation comparison {workload}: {cfg['description']}")
    generated_ops = generated_ops_for_workload(workload, op_count)

    db_bench_output = TMP_DIR / f"db_bench_wrapper_w{workload}.txt"
    tectonic_output = TMP_DIR / f"tectonic_w{workload}.txt"
    metadata_output = OUT_DIR / f"db_bench_wrapper_w{workload}.metadata.json"

    for path in (db_bench_output, tectonic_output):
        if path.exists():
            path.unlink()

    db_bench_result = run_monitored(
        f"db_bench-compatible file generation workload {workload}",
        [
            "python3",
            str(GENERATOR_SCRIPT),
            "--workload",
            workload,
            "--output",
            str(db_bench_output),
            "--scale",
            str(scale),
            "--metadata",
            str(metadata_output),
        ],
        OUT_DIR / f"db_bench_wrapper_generate_w{workload}.log",
        cwd=ROOT_DIR,
    )
    tectonic_result = run_monitored(
        f"Tectonic file generation workload {workload}",
        [
            str(tectonic_cli),
            "generate",
            "-w",
            str(cfg["spec"]),
            "-o",
            str(tectonic_output),
            "-s",
            str(scale),
        ],
        OUT_DIR / f"tectonic_generate_w{workload}.log",
        cwd=ROOT_DIR,
    )

    return {
        "workload": workload,
        "description": cfg["description"],
        "note": cfg.get("note"),
        "tectonic_spec": str(cfg["spec"]),
        "scale": scale,
        "scaled_ops_per_phase": op_count,
        "generated_operations_per_tool": generated_ops,
        "db_bench_wrapper": add_file_metrics(db_bench_result, db_bench_output, generated_ops),
        "tectonic": add_file_metrics(tectonic_result, tectonic_output, generated_ops),
    }


def write_results(payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = RESULTS_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(RESULTS_PATH)
    return RESULTS_PATH


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run db_bench-compatible vs Tectonic workload-generation comparison.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--workloads", default="1,2,4,5", help="comma-separated workload ids or all")
    parser.add_argument("--scale", type=float, default=DEFAULT_SCALE)
    parser.add_argument("--tectonic-cli", default=str(DEFAULT_TECTONIC_CLI))
    parser.add_argument("--no-build", action="store_true", help="do not build missing tectonic-cli")
    parser.add_argument("--no-plot", action="store_true", help="skip PDF plot generation")
    parser.add_argument("--keep-files", action="store_true", help="keep generated workload files in /tmp")
    args = parser.parse_args()

    workloads = parse_workloads(args.workloads)
    op_count = scaled_ops(args.scale)
    tectonic_cli = Path(args.tectonic_cli).expanduser().resolve()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    ensure_file(GENERATOR_SCRIPT, "db_bench-compatible generator")
    build_tectonic(tectonic_cli, args.no_build)
    for workload in workloads:
        ensure_file(WORKLOADS[workload]["spec"], f"Tectonic db_bench workload {workload} spec")

    runs = [run_workload_generation(workload, args.scale, op_count, tectonic_cli) for workload in workloads]
    payload = {
        "experiment": "db_bench_exp_test",
        "mode": "db_bench_compatible_vs_tectonic_workload_generation",
        "root_dir": str(ROOT_DIR),
        "output_dir": str(OUT_DIR),
        "scale": args.scale,
        "scaled_ops_per_phase": op_count,
        "workloads": workloads,
        "generator_script": str(GENERATOR_SCRIPT),
        "tectonic_cli": str(tectonic_cli),
        "comparison_caveat": (
            "db_bench_wrapper is a compatible text generator for db_bench workload families. "
            "Native db_bench does not expose generated operations in Tectonic's text format."
        ),
        "metric_definitions": {
            "duration_s": "wall-clock time around workload file generation",
            "cpu_seconds": "wait4 user+system CPU seconds for the generator process",
            "cpu_seconds_per_million_ops": "cpu_seconds divided by generated operations in millions",
            "wall_seconds_per_million_ops": "duration_s divided by generated operations in millions",
            "peak_vmhwm_kb": "max of wait4 ru_maxrss and sampled VmHWM",
        },
        "runs": runs,
    }
    results_path = write_results(payload)
    if not args.keep_files:
        shutil.rmtree(TMP_DIR, ignore_errors=True)
    print(f"\n  generation results saved: {results_path}", flush=True)
    if not args.no_plot:
        subprocess.run(["python3", str(PLOT_SCRIPT), "--results", str(results_path)], check=True)


if __name__ == "__main__":
    main()
