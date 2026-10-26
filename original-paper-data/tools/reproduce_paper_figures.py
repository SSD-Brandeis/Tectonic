#!/usr/bin/env python3
"""
Reproduces the data figures and Table 5 of the paper (original-paper-data/X_Bench (5).pdf) from the
original experiment data, with the repository's own plot scripts, unmodified.

1. Stages each experiment's data where its plot script reads it (/home/cc/Tectonic/data/<experiment>),
   copied from the extracted archives (original-paper-data/x_bench_data1, x_bench_data2/data). The
   archives are never written. A destination this script staged before (it holds STAGED_MARK) is
   replaced by a fresh copy; any other existing destination is used only if it is identical to the
   archive copy, otherwise the script stops.
2. Runs the plot script of each figure.
3. Moves the plots into original-paper-data/reproduced/<figure>/ (and the per-run log there).

Usage: python3 original-paper-data/tools/reproduce_paper_figures.py
"""

import filecmp
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/cc/Tectonic")
PAPER = ROOT / "original-paper-data"
DATA1 = PAPER / "x_bench_data1"
DATA2 = PAPER / "x_bench_data2" / "data"
OUT = PAPER / "reproduced"
STAGED_MARK = ".staged_from_paper_archive"

# (archive source, staged destination) for every experiment a paper figure or table uses
STAGE = [
    (DATA1 / "figure_6_validate", ROOT / "data/figure_6_validate"),
    (DATA1 / "concurrent_blind_generation_resource_monitoring", ROOT / "data/concurrent_blind_generation_resource_monitoring"),
    (DATA1 / "CPU-utilization", ROOT / "data/CPU-utilization"),
    (DATA1 / "concurrent_experiment", ROOT / "data/concurrent_experiment"),
    (DATA1 / "ycsb_tectonic_correctness_thread_modes", ROOT / "data/ycsb_tectonic_correctness_thread_modes"),
    (DATA2 / "rocksdb_similarity_ycsba_10x", ROOT / "data/rocksdb_similarity_ycsba_10x"),
    (DATA1 / "ycsb_tectonic_similarity_io_bytes", ROOT / "data/ycsb_tectonic_similarity_io_bytes"),
    (DATA2 / "ycsb_tectonic_similarity_io_bytes", ROOT / "data/machine2_ycsb_tectonic_similarity_io_bytes"),
]

PY = sys.executable
# figure -> (command, extra environment, [(directory the script writes to, file names to collect)])
FIGURES = {
    "fig7": (
        [PY, "plot_scripts/plot_figure_6_validate.py"], {},
        [(ROOT / "figure_6_validate_plot", None)],
    ),
    "fig8": (
        [PY, "plot_scripts/plot_concurrent_blind_generation_resource_monitoring.py"], {},
        [(ROOT / "data/concurrent_blind_generation_resource_monitoring/plots", None)],
    ),
    "fig9": (
        [PY, "plot_scripts/plot_cpu_pidstat_multithread_generation.py"],
        {"CPU_UTILIZATION_OUT_DIR": str(ROOT / "data/CPU-utilization/YCSB_E_scale_10x_cpu"),
         "CPU_UTILIZATION_EXTRA_TIME_SERIES_THREADS": "16"},
        [(ROOT / "data/CPU-utilization/YCSB_E_scale_10x_cpu", ".pdf")],
    ),
    "fig10": (
        [PY, "plot_scripts/plot_concurrent_comparison.py"], {},
        [(ROOT / "concurrent_experiment_plots", None)],
    ),
    "fig11": (
        [PY, "plot_scripts/plot_ycsb_xbench_db_bench_correctness_thread_modes.py"], {},
        [(ROOT / "ycsb_tectonic_correctness_plots", None)],
    ),
    "fig12": (
        [PY, "plot_scripts/plot_rocksdb_similarity_ycsba.py", str(ROOT / "data/rocksdb_similarity_ycsba_10x"),
         "rocksdb_similarity_ycsba_10x"], {},
        [(ROOT / "experiment_plots", None)],
    ),
    "table5": (
        [PY, "run_scripts/consolidate_table_5_data.py"], {},
        [(ROOT / "data/table_5_data", None)],
    ),
}


def same_tree(a: Path, b: Path) -> bool:
    cmp = filecmp.dircmp(a, b)
    if cmp.left_only or cmp.right_only or cmp.funny_files:
        return False
    _, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    if mismatch or errors:
        return False
    return all(same_tree(a / d, b / d) for d in cmp.common_dirs)


def stage():
    for src, dst in STAGE:
        if (dst / STAGED_MARK).exists():
            shutil.rmtree(dst)
        if dst.exists():
            if not same_tree(src, dst):
                sys.exit(f"{dst} exists and differs from {src}; not overwriting it")
            print(f"staged (already identical): {dst}")
        else:
            shutil.copytree(src, dst, symlinks=True)
            (dst / STAGED_MARK).write_text(f"copied from {src} by {Path(__file__).name}\n")
            print(f"staged: {src} -> {dst}")


def collect(fig: str, outputs):
    dest = OUT / fig
    dest.mkdir(parents=True, exist_ok=True)
    for directory, suffix in outputs:
        if not directory.exists():
            print(f"  {fig}: {directory} was not created")
            continue
        for f in sorted(directory.rglob("*")):
            if f.is_file() and (suffix is None or f.suffix == suffix):
                target = dest / f.relative_to(directory)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)


def main():
    if OUT.exists():
        sys.exit(f"{OUT} exists; move it away first")
    stage()
    # directories the scripts create at the repository root (moved, not copied, afterwards)
    created = [d for d in (ROOT / "figure_6_validate_plot", ROOT / "concurrent_experiment_plots",
                           ROOT / "ycsb_tectonic_correctness_plots", ROOT / "experiment_plots") if not d.exists()]
    OUT.mkdir()
    failed = []
    for fig, (cmd, env, outputs) in FIGURES.items():
        log = OUT / f"{fig}.log"
        with open(log, "w") as f:
            f.write("$ " + " ".join(cmd) + "\n" + "".join(f"  {k}={v}\n" for k, v in env.items()))
            f.flush()
            res = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "MPLBACKEND": "Agg", **env},
                                 stdout=f, stderr=subprocess.STDOUT)
        print(f"{fig}: exit {res.returncode} (log: {log})")
        if res.returncode:
            failed.append(fig)
        collect(fig, outputs)
    for d in created:
        if d.exists():
            shutil.rmtree(d)
    print("failed:", failed or "none")


if __name__ == "__main__":
    main()
