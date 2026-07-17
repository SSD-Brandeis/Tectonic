#!/usr/bin/env python3
"""Move legacy root-level experiment artifacts into data-local folders.

The migration is intentionally narrow: it only touches known plot/artifact files
whose producing scripts and data ownership are clear. Legacy paths are preserved
as symlinks so old hardcoded plotting scripts keep working.
"""

from __future__ import annotations

import argparse
import filecmp
import json
from pathlib import Path
import shutil
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT_DIR / "data" / "experiment_artifact_index.json"


def p(value: str) -> Path:
    return ROOT_DIR / value


MAPPINGS: list[dict[str, Any]] = [
    {
        "experiment": "concurrent_experiment",
        "legacy": "concurrent_experiment_plots/fig1.pdf",
        "canonical": "data/concurrent_experiment/plots/fig1.pdf",
        "plot_script": "plot_scripts/plot_concurrent_comparison.py",
        "run_script": "run_scripts/run_concurrent_experiment.py",
        "data_sources": ["data/concurrent_experiment/results.json"],
    },
    {
        "experiment": "concurrent_experiment",
        "legacy": "concurrent_experiment_plots/fig1_legend.pdf",
        "canonical": "data/concurrent_experiment/plots/fig1_legend.pdf",
        "plot_script": "plot_scripts/plot_concurrent_comparison.py",
        "run_script": "run_scripts/run_concurrent_experiment.py",
        "data_sources": ["data/concurrent_experiment/results.json"],
    },
    {
        "experiment": "concurrent_experiment",
        "legacy": "concurrent_experiment_plots/fig2_sequential.pdf",
        "canonical": "data/concurrent_experiment/plots/fig2_sequential.pdf",
        "plot_script": "plot_scripts/plot_concurrent_comparison.py",
        "run_script": "run_scripts/run_concurrent_experiment.py",
        "data_sources": ["data/concurrent_experiment/results.json"],
    },
    {
        "experiment": "concurrent_experiment",
        "legacy": "concurrent_experiment_plots/fig2_parallel.pdf",
        "canonical": "data/concurrent_experiment/plots/fig2_parallel.pdf",
        "plot_script": "plot_scripts/plot_concurrent_comparison.py",
        "run_script": "run_scripts/run_concurrent_experiment.py",
        "data_sources": ["data/concurrent_experiment/results.json"],
    },
    {
        "experiment": "concurrent_blind_experiment",
        "legacy": "concurrent_blind_experiment/generation_speedup_comparison.pdf",
        "canonical": "data/concurrent_blind_experiment/plots/generation_speedup_comparison.pdf",
        "plot_script": "plot_scripts/plot_concurrent_blind_generation_experiment.py",
        "run_script": "run_scripts/run_concurrent_blind_generation_experiment.py",
        "data_sources": ["data/concurrent_blind_experiment/results.json"],
    },
    {
        "experiment": "concurrent_blind_experiment",
        "legacy": "concurrent_blind_experiment/generation_speedup_comparison_legend.pdf",
        "canonical": "data/concurrent_blind_experiment/plots/generation_speedup_comparison_legend.pdf",
        "plot_script": "plot_scripts/plot_concurrent_blind_generation_experiment.py",
        "run_script": "run_scripts/run_concurrent_blind_generation_experiment.py",
        "data_sources": ["data/concurrent_blind_experiment/results.json"],
    },
    {
        "experiment": "concurrent_blind_experiment",
        "legacy": "blind_unique_compare/generation_speedup_comparison.pdf",
        "canonical": "data/concurrent_blind_experiment/blind_unique_compare/generation_speedup_comparison.pdf",
        "plot_script": "plot_scripts/plot_concurrent_blind_generation_experiment.py",
        "run_script": "run_scripts/run_concurrent_blind_generation_experiment.py",
        "data_sources": ["data/concurrent_blind_experiment/results.json"],
    },
    {
        "experiment": "concurrent_blind_experiment",
        "legacy": "blind_unique_compare/generation_speedup_comparison_legend.pdf",
        "canonical": "data/concurrent_blind_experiment/blind_unique_compare/generation_speedup_comparison_legend.pdf",
        "plot_script": "plot_scripts/plot_concurrent_blind_generation_experiment.py",
        "run_script": "run_scripts/run_concurrent_blind_generation_experiment.py",
        "data_sources": ["data/concurrent_blind_experiment/results.json"],
    },
    {
        "experiment": "concurrent_blind_experiment",
        "legacy": "blind_unique_compare/plot_blind_unique_compare.py",
        "canonical": "data/concurrent_blind_experiment/blind_unique_compare/plot_blind_unique_compare.py",
        "plot_script": "plot_scripts/plot_concurrent_blind_generation_experiment.py",
        "run_script": "run_scripts/run_concurrent_blind_generation_experiment.py",
        "data_sources": ["data/concurrent_blind_experiment/results.json"],
    },
    {
        "experiment": "generator_comparison",
        "legacy": "generator_experiment_plots/fig1.pdf",
        "canonical": "data/generator_comparison/plots/fig1.pdf",
        "plot_script": "plot_scripts/plot_generator_comparison.py",
        "run_script": "run_scripts/run_generator_benchmarks.py",
        "data_sources": ["data/generator_comparison"],
    },
    {
        "experiment": "generator_comparison",
        "legacy": "generator_experiment_plots/fig1_legend.pdf",
        "canonical": "data/generator_comparison/plots/fig1_legend.pdf",
        "plot_script": "plot_scripts/plot_generator_comparison.py",
        "run_script": "run_scripts/run_generator_benchmarks.py",
        "data_sources": ["data/generator_comparison"],
    },
    {
        "experiment": "generator_comparison",
        "legacy": "generator_experiment_plots/fig2.pdf",
        "canonical": "data/generator_comparison/plots/fig2.pdf",
        "plot_script": "plot_scripts/plot_generator_comparison.py",
        "run_script": "run_scripts/run_generator_benchmarks.py",
        "data_sources": ["data/generator_comparison"],
    },
    {
        "experiment": "generator_comparison",
        "legacy": "generator_experiment_plots/fig2_legend.pdf",
        "canonical": "data/generator_comparison/plots/fig2_legend.pdf",
        "plot_script": "plot_scripts/plot_generator_comparison.py",
        "run_script": "run_scripts/run_generator_benchmarks.py",
        "data_sources": ["data/generator_comparison"],
    },
    {
        "experiment": "generator_comparison",
        "legacy": "generator_experiment_plots/fig3.pdf",
        "canonical": "data/generator_comparison/plots/fig3.pdf",
        "plot_script": "plot_scripts/plot_generator_comparison.py",
        "run_script": "run_scripts/run_generator_benchmarks.py",
        "data_sources": ["data/generator_comparison"],
    },
    {
        "experiment": "generator_comparison",
        "legacy": "generator_experiment_plots/fig3_legend.pdf",
        "canonical": "data/generator_comparison/plots/fig3_legend.pdf",
        "plot_script": "plot_scripts/plot_generator_comparison.py",
        "run_script": "run_scripts/run_generator_benchmarks.py",
        "data_sources": ["data/generator_comparison"],
    },
    {
        "experiment": "generator_comparison",
        "legacy": "generator_experiment_plots/fig4.pdf",
        "canonical": "data/generator_comparison/plots/fig4.pdf",
        "plot_script": "plot_scripts/plot_generator_comparison.py",
        "run_script": "run_scripts/run_generator_benchmarks.py",
        "data_sources": ["data/generator_comparison"],
    },
    {
        "experiment": "generator_comparison",
        "legacy": "generator_experiment_plots/fig4_legend.pdf",
        "canonical": "data/generator_comparison/plots/fig4_legend.pdf",
        "plot_script": "plot_scripts/plot_generator_comparison.py",
        "run_script": "run_scripts/run_generator_benchmarks.py",
        "data_sources": ["data/generator_comparison"],
    },
]


for name in [
    "correctness.pdf",
    "correctness_legend.pdf",
    "correctness_small.pdf",
    "correctness_small_legend.pdf",
    "correctness_large.pdf",
    "correctness_large_legend.pdf",
    "correctness_small_broken.pdf",
    "correctness_small_broken_legend.pdf",
    "correctness_large_broken.pdf",
    "correctness_large_broken_legend.pdf",
]:
    MAPPINGS.append(
        {
            "experiment": "ycsb_tectonic_correctness",
            "legacy": f"ycsb_tectonic_correctness_plots/{name}",
            "canonical": f"data/ycsb_tectonic_correctness/plots/{name}",
            "plot_script": "plot_scripts/plot_ycsb_tectonic_correctness.py",
            "run_script": "run_scripts/run_ycsb_tectonic_correctness_experiment.py",
            "data_sources": ["data/ycsb_tectonic_correctness/results.json"],
        }
    )


for name in [
    "workload_accuracy_sequential_3runs.pdf",
    "workload_accuracy_sequential_3runs_legend.pdf",
    "workload_accuracy_parallel_3runs.pdf",
    "workload_accuracy_parallel_3runs_legend.pdf",
    "workload_accuracy_by_thread_setting.pdf",
    "workload_accuracy_by_thread_setting_legend.pdf",
    "workload_accuracy_by_thread_setting_3runs.pdf",
    "workload_accuracy_by_thread_setting_3runs_legend.pdf",
]:
    MAPPINGS.append(
        {
            "experiment": "ycsb_tectonic_correctness_thread_modes",
            "legacy": f"ycsb_tectonic_correctness_plots/thread_modes/{name}",
            "canonical": f"data/ycsb_tectonic_correctness_thread_modes/plots/{name}",
            "plot_script": "plot_scripts/plot_ycsb_tectonic_db_bench_correctness_thread_modes.py",
            "run_script": "run_scripts/run_ycsb_tectonic_db_bench_correctness_thread_modes.py",
            "data_sources": ["data/ycsb_tectonic_correctness_thread_modes/results.json"],
        }
    )


for name in [
    "ycsb_workloads_comparison.pdf",
    "ycsb_workloads_comparison_legend.pdf",
    "kvbench_workloads_comparison_latency.pdf",
    "kvbench_workloads_comparison_latency_legend.pdf",
    "kvbench_workloads_comparison_memory.pdf",
    "kvbench_workloads_comparison_memory_legend.pdf",
    "ycsb_workloads_comparison_with_db_bench.pdf",
    "ycsb_workloads_comparison_with_db_bench_legend.pdf",
]:
    MAPPINGS.append(
        {
            "experiment": "overall_benchmarks",
            "legacy": f"experiment_plots/{name}",
            "canonical": f"data/overall_benchmarks/plots/{name}",
            "plot_script": "plot_scripts/plot_benchmarks.py",
            "run_script": "run_scripts/run_db_bench_ycsb_workloads_baseline.py",
            "data_sources": [
                "data/overall_benchmarks/logs_fig3",
                "data/overall_benchmarks/logs_fig4",
                "data/overall_benchmarks/db_bench_ycsb_workloads",
            ],
        }
    )


for entry in MAPPINGS:
    if entry["legacy"].startswith("experiment_plots/ycsb_workloads_comparison_with_db_bench"):
        entry["plot_script"] = "plot_scripts/plot_ycsb_workloads_comparison_with_db_bench.py"


for name in [
    "rocksdb_similarity_ycsba_accuracy.pdf",
    "rocksdb_similarity_ycsba_accuracy_legend.pdf",
]:
    MAPPINGS.append(
        {
            "experiment": "rocksdb_similarity_ycsba",
            "legacy": f"experiment_plots/{name}",
            "canonical": f"data/rocksdb_similarity_ycsba/plots/{name}",
            "plot_script": "plot_scripts/plot_rocksdb_similarity_ycsba.py",
            "run_script": "run_scripts/run_rocksdb_similarity_ycsba.sh",
            "data_sources": ["data/rocksdb_similarity_ycsba"],
        }
    )


for name in ["subplot_a.pdf", "subplot_a_legend.pdf"]:
    MAPPINGS.append(
        {
            "experiment": "workload_similarity_10x_iostat",
            "legacy": f"experiment_plots/{name}",
            "canonical": f"data/workload_similarity_10x_iostat/plots/{name}",
            "plot_script": "plot_scripts/plot_subplot_a.py",
            "run_script": None,
            "data_sources": ["rocksdb-benchmark-harness/experiments/workload-similarity/10x"],
        }
    )


PNG_COMPATIBLE_PLOT_SCRIPTS = {
    "plot_scripts/plot_benchmarks.py",
    "plot_scripts/plot_concurrent_blind_generation_experiment.py",
    "plot_scripts/plot_concurrent_comparison.py",
    "plot_scripts/plot_generator_comparison.py",
    "plot_scripts/plot_rocksdb_similarity_ycsba.py",
    "plot_scripts/plot_subplot_a.py",
    "plot_scripts/plot_ycsb_tectonic_correctness.py",
}

for entry in list(MAPPINGS):
    if not entry["legacy"].endswith(".pdf"):
        continue
    if entry.get("plot_script") not in PNG_COMPATIBLE_PLOT_SCRIPTS:
        continue
    png_entry = dict(entry)
    png_entry["legacy"] = f"{entry["legacy"][:-4]}.png"
    png_entry["canonical"] = f"{entry["canonical"][:-4]}.png"
    png_entry["optional_missing_target"] = True
    MAPPINGS.append(png_entry)


def relative_symlink_target(link_path: Path, target_path: Path) -> Path:
    return Path(*([".."] * len(link_path.parent.relative_to(ROOT_DIR).parts))) / target_path.relative_to(ROOT_DIR)


def link_points_to(link_path: Path, target_path: Path) -> bool:
    if not link_path.is_symlink():
        return False
    return (link_path.parent / Path(link_path.readlink())).resolve() == target_path.resolve()


def migrate_one(mapping: dict[str, Any], apply: bool) -> str:
    legacy = p(mapping["legacy"])
    canonical = p(mapping["canonical"])
    if legacy.is_symlink() and link_points_to(legacy, canonical):
        return "already-linked"
    if not legacy.exists() and canonical.exists():
        if apply:
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.symlink_to(relative_symlink_target(legacy, canonical))
        return "linked-missing-legacy" if apply else "would-link-missing-legacy"
    if not legacy.exists() and mapping.get("optional_missing_target"):
        if apply:
            canonical.parent.mkdir(parents=True, exist_ok=True)
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.symlink_to(relative_symlink_target(legacy, canonical))
        return "linked-optional-missing-target" if apply else "would-link-optional-missing-target"
    if not legacy.exists():
        return "missing"

    if canonical.exists():
        if not filecmp.cmp(legacy, canonical, shallow=False):
            raise RuntimeError(f"refusing to replace non-identical files: {legacy} and {canonical}")
        if apply:
            legacy.unlink()
            legacy.symlink_to(relative_symlink_target(legacy, canonical))
        return "deduplicated" if apply else "would-deduplicate"

    if apply:
        canonical.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(canonical))
        legacy.symlink_to(relative_symlink_target(legacy, canonical))
    return "moved" if apply else "would-move"


def manifest_for_experiment(experiment: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    data_dir = p(f"data/{experiment}")
    plot_dirs = sorted({str(p(entry["canonical"]).parent.relative_to(ROOT_DIR)) for entry in entries})
    scripts = sorted(
        {
            str(value)
            for entry in entries
            for value in (entry.get("run_script"), entry.get("plot_script"))
            if value
        }
    )
    sources = sorted({source for entry in entries for source in entry.get("data_sources", [])})
    return {
        "experiment": experiment,
        "data_dir": str(data_dir),
        "plot_dirs": plot_dirs,
        "scripts": scripts,
        "data_sources": sources,
        "legacy_compatibility": "legacy artifact paths are symlinks to canonical files",
        "artifacts": [
            {
                "legacy_path": entry["legacy"],
                "canonical_path": entry["canonical"],
                "plot_script": entry.get("plot_script"),
                "run_script": entry.get("run_script"),
                "data_sources": entry.get("data_sources", []),
            }
            for entry in sorted(entries, key=lambda item: item["canonical"])
        ],
    }


def write_manifests(results: list[dict[str, Any]], apply: bool) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in MAPPINGS:
        grouped.setdefault(item["experiment"], []).append(item)
    manifests = {experiment: manifest_for_experiment(experiment, entries) for experiment, entries in grouped.items()}
    index = {
        "description": "Canonical index for legacy experiment artifacts moved from root-level plot folders.",
        "organizer_script": str(Path(__file__).resolve()),
        "experiments": manifests,
        "migration_results": results,
    }
    if not apply:
        return
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("w") as f:
        json.dump(index, f, indent=2)
    for experiment, manifest in manifests.items():
        manifest_path = p(f"data/{experiment}/artifact_manifest.json")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with manifest_path.open("w") as f:
            json.dump(manifest, f, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Organize legacy experiment artifacts safely.")
    parser.add_argument("--apply", action="store_true", help="perform the migration; default is dry-run")
    args = parser.parse_args()

    results = []
    for mapping in MAPPINGS:
        status = migrate_one(mapping, args.apply)
        results.append(
            {
                "status": status,
                "experiment": mapping["experiment"],
                "legacy": mapping["legacy"],
                "canonical": mapping["canonical"],
            }
        )
    write_manifests(results, args.apply)
    for result in results:
        print(f"{result['status']}: {result['legacy']} -> {result['canonical']}")
    action = "applied" if args.apply else "dry-run"
    print(f"{action}: {len(results)} mapped artifacts")
    if args.apply:
        print(f"index: {INDEX_PATH}")


if __name__ == "__main__":
    main()
