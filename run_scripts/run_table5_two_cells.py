#!/usr/bin/env python3
"""
run_table5_two_cells.py
=======================
Table 5 (authors' decision, 2026-11-03): the two cells whose bytes transferred (iostat) differ most between YCSB
and X-Bench, run again: Cassandra 2^19 (9.35%; X-Bench trial 4 moved ~485 MB more than the other trials while
Cassandra's own byte counters were identical in all five) and Redis 2^18 (7.06%; Redis writes ~0 MB, so the cell is
the trace read of a ~2 s run). Both workloads, 5 trials each, with the original harness unchanged
(run_scripts/run_table5_revision_multirun.py: same trace files, same reset and page-cache drop per trial, same
iostat window, same client configuration and database images). The rerun's cells replace those two cells of
Table 5 (execution time, bytes transferred and DB-reported bytes), with a note.

This wrapper only
  - points the harness at --cli (the harness hard-codes target/release/xbench-cli; the rerun uses fc63df5f, the
    binary of the October Table 5 run, so that the local build is left as it is), and records its sha256;
  - verifies the sha256 of the trace files of the selected scales against workloads/checksums.json first;
  - lets the harness's markdown report (write_table) fail without stopping the run: it assumes every database ran
    the largest scale, and here Redis ran only 2^18 and Cassandra only 2^19 (KeyError '524288' for Redis stopped the
    first attempt on 2026-11-03 03:34, after Cassandra YCSB trial 1, which had been saved). Results and summary JSON
    are written before it; the two cells are merged into Table 5 from those.
Every other argument goes to run_table5_revision_multirun.py.

Usage: python3 run_scripts/run_table5_two_cells.py --cli <xbench-cli> --db redis --scales 262144 --out-dir <dir>
"""
import os
import sys
import json
import hashlib
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_table5_revision_multirun as mr  # noqa: E402
import run_table5_post_fix_validation as base  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    argv = sys.argv[1:]
    i = argv.index("--cli")
    cli = os.path.abspath(argv[i + 1])
    del argv[i:i + 2]
    scales = [int(x) for x in argv[argv.index("--scales") + 1:] if x.isdigit()]
    out_dir = os.path.abspath(argv[argv.index("--out-dir") + 1])

    checksums = json.load(open(mr.CHECKSUM_PATH))
    for scale in scales:
        for w in ("ycsb", "xbench"):
            path = mr.workload_path(w, scale)
            want = checksums[os.path.basename(path)]["sha256"]
            got = sha256(path)
            if got != want:
                sys.exit(f"trace {path}: sha256 {got} differs from checksums.json {want}")
            print(f"trace {os.path.basename(path)}: sha256 matches checksums.json ({got[:16]}...)", flush=True)

    os.makedirs(out_dir, exist_ok=True)
    prov_path = f"{out_dir}/provenance.json"
    prov = json.load(open(prov_path)) if os.path.exists(prov_path) else []
    prov.append({"started": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "argv": argv,
                 "xbench_cli": cli, "xbench_cli_sha256": sha256(cli)})
    with open(prov_path, "w") as f:
        json.dump(prov, f, indent=2)

    base.XBENCH_CLI = cli
    write_table = mr.write_table

    def write_table_tolerant(summary, results):
        try:
            write_table(summary, results)
        except KeyError as e:
            print(f"[two-cells] markdown report skipped (KeyError {e}); results and summary JSON are written", flush=True)
    mr.write_table = write_table_tolerant
    sys.argv = [sys.argv[0]] + argv
    mr.main()


if __name__ == "__main__":
    main()
