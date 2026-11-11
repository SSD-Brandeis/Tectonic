#!/usr/bin/env python3
"""
run_fig7_load_execution.py
==========================
Fig. 7 (generation time and peak memory of YCSB, KV-Bench and X-Bench), run again with a loading /
execution split for every generator (authors, 2026-11-10). Output: data/revision/fig7-load-execution/.

Why: the original Fig. 7 split X-Bench's time at "[X-Bench Sequential Gen] Section 0 finished", which is the end
of the whole workload (load and execution groups are in one section), so its execution bars (0.02-0.05 s) were
only process exit; and its KV-Bench split came from a locally modified KV-Bench whose source is lost.

Loading / execution split, per generator:
  YCSB      two processes, as YCSB runs them: loading = the -load process, execution = the -t process (wall clock
            of each); memory = the larger peak of the two.
  X-Bench   one process. Its spec's first group is the load group when it has only inserts and more groups follow
            (YCSB A-F, KV-Bench I-IV); loading = when that group's "[X-Bench Group] ... finished" line reaches the
            runner (binary with the group-timing patch, bin/); execution = wall clock - loading. A spec without
            such a group (KV-Bench V) has loading 0.
  KV-Bench  one process. KV-Bench writes inserts only until each operation type's threshold (a fraction of the
            inserts, compile-time macros, KV-Bench paper Table 4); the thresholds are set per workload to X-Bench's
            spec (KVBENCH_VARIANT), and a one-line patch prints KVbench_LoadPhase_End when it chooses its first
            non-insert operation. loading = when that line reaches the runner; execution = wall clock - loading.
  Loading is always on the runner's clock, from launch, like wall_time_s (generator startup counts as loading,
  as in YCSB's load process); each generator's own reported time is kept (groups[].end_s, load_end_reported_s).

Generators (all on one machine, revision-2):
  YCSB      0.18.0-SNAPSHOT 0bedec5 (vendor/YCSB, jars recorded), FileClient, workloads a-f, 1M records + 1M ops,
            the commands of run_figure_6_validate_full.py.
  KV-Bench  SSD-Brandeis/KV-WorkloadGenerator refactor 699bc10 + the two bug fixes of data/revision/fig7-corrected/
            kvbench-minimal-fix/ + load_gen_thresholds_guard_and_load_end.patch, built as its README says with
            CMAKE_CXX_FLAGS = the variant's threshold macros (/home/cc/kvbench-builds/bin/load_gen.<variant>).
            YCSB-shaped A-E: Fig. 7's arguments; I-V: make_kvbench_paper_specs.py at full size, IV at 0.1x
            (letter Table 3).
  X-Bench   bin/xbench-cli-c481d18d (dffea48 + group-timing patch), 1 thread, adaptive KeySet:
            example-specs/ycsb-untracked/, example-specs/kvbench/ (IV: example-specs/kvbench-0.1x/).
            --with-fixed adds the fixed-KeySet jobs of letter Tables 3 and 4 (vec-hashset for I-V, empty for A-F).

Every process: `sync; echo 3 > /proc/sys/vm/drop_caches` first; wall clock from just before Popen to exit; VmRSS
and VmHWM sampled every 10 ms (peak_rss_mib = max VmRSS, as Fig. 7; peak_hwm_mib = last VmHWM, the kernel's own
peak); every stdout line with its arrival time; the trace's per-operation line counts, counted after exit, then
the trace is deleted.

Usage (on revision-2, inside tmux): python3 run_scripts/run_fig7_load_execution.py [--trials 5] [--with-fixed]
                                    [--only JOB ...] [--dry-run]
"""
from pathlib import Path as _Path
REPO_ROOT = str(_Path(__file__).resolve().parents[1])
import os
import re
import sys
import json
import time
import socket
import hashlib
import argparse
import threading
import subprocess
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_figure_6_validate_full as f7  # noqa: E402  (constants and commands only: YCSB_CP, YCSB_DIR, KVBENCH_YCSB_ARGS)
import make_kvbench_paper_specs as kvspecs  # noqa: E402

ROOT = Path(REPO_ROOT)
OUT_DIR = ROOT / "data/revision/fig7-load-execution"
XBENCH_CLI = OUT_DIR / "bin/xbench-cli-c481d18d"
# KV-Bench branch tectonic-plus (SSD-Brandeis/KV-WorkloadGenerator), commit 4a333fd: upstream refactor 699bc10 + the
# two bug fixes + build-time thresholds / load-end marker + sort once for range deletes (authors, 2026-11-11)
KVBENCH_COMMIT = "tectonic-plus 4a333fd"
KVBENCH_BIN = Path("/home/cc/kvbench-builds/tectonic-plus-4a333fd")
TMP = Path("/tmp")
TRIALS = 5
POLL_S = 0.01

# KV-Bench threshold variant per workload: each threshold = the share of the inserts X-Bench's spec writes before
# that operation type can appear (its load group, or 0 without one)
KVBENCH_VARIANT_FLAGS = {
    "std-rd1": "-DRD_THRESHOLD=1",                                          # all inserts first (shipped U/PQ/RQ = 1)
    "ii": "-DPD_THRESHOLD=0.2 -DU_THRESHOLD=0.2 -DPQ_THRESHOLD=0.2",        # II: 100K of 500K inserts preloaded
    # V as one interleaved group. Superseded 2026-11-11: the authors split X-Bench's V spec into all inserts first,
    # then the point queries, and KV-Bench V runs with std-rd1 (PQ = 1, as shipped).
    "v": "-DPQ_THRESHOLD=0",
    # YCSB D, E with 1M of 1.05M inserts loaded. Superseded 2026-11-11: once KV-Bench has sorted its insert pool (first
    # skewed point query / first range query), each later insert is an O(n) vector insert, ~280 s for the 50K
    # run-phase inserts. The authors chose all inserts first for D and E (std-rd1, as shipped for PQ/RQ).
    "de": "-DPQ_THRESHOLD=0.95238095 -DRQ_THRESHOLD=0.95238095",
}
KVBENCH_VARIANT = {("ycsb", "A"): "std-rd1", ("ycsb", "B"): "std-rd1", ("ycsb", "C"): "std-rd1",
                   ("ycsb", "D"): "std-rd1", ("ycsb", "E"): "std-rd1",  # all 1.05M inserts first (authors, 2026-11-11)
                   ("kvbench", "I"): "std-rd1", ("kvbench", "II"): "ii", ("kvbench", "III"): "std-rd1",
                   ("kvbench", "IV"): "std-rd1", ("kvbench", "V"): "std-rd1"}  # V: all inserts first (authors, 2026-11-11)
KV_SCALE = {"I": 1, "II": 1, "III": 1, "IV": 1, "V": 1}  # all full size; IV was 0.1x until 2026-11-11 (sort once)
KVBENCH_ARGS = {w: kvspecs.kvbench_args(KV_SCALE[w])[w] for w in f7.KV_WORKLOADS}
XBENCH_YCSB_SPEC = "example-specs/ycsb-untracked/{}.spec.json"
XBENCH_KV_SPEC = {w: (f"example-specs/kvbench-0.1x/{w.lower()}.spec.json" if KV_SCALE[w] == 10
                      else f"example-specs/kvbench/{w.lower()}.spec.json") for w in f7.KV_WORKLOADS}
FIXED_KEYSET = {"ycsb": "empty", "kvbench": "vec-hashset"}  # letter Tables 4 and 3


def jobs(with_fixed):
    out = []  # (job, tool, workload set, workload, keyset); one trial in Fig. 7's order
    for w in f7.YCSB_WORKLOADS:
        out.append((f"YCSB-{w}/YCSB", "YCSB", "ycsb", w, None))
        if w in f7.KVBENCH_YCSB_ARGS:
            out.append((f"YCSB-{w}/KV-Bench", "KV-Bench", "ycsb", w, None))
        out.append((f"YCSB-{w}/X-Bench", "X-Bench", "ycsb", w, "adaptive"))
        if with_fixed:
            out.append((f"YCSB-{w}/X-Bench-fixed", "X-Bench", "ycsb", w, FIXED_KEYSET["ycsb"]))
    for w in f7.KV_WORKLOADS:
        out.append((f"KVBench-{w}/KV-Bench", "KV-Bench", "kvbench", w, None))
        out.append((f"KVBench-{w}/X-Bench", "X-Bench", "kvbench", w, "adaptive"))
        if with_fixed:
            out.append((f"KVBench-{w}/X-Bench-fixed", "X-Bench", "kvbench", w, FIXED_KEYSET["kvbench"]))
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def proc_status_kib(pid):
    rss = hwm = None
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1])
                elif line.startswith("VmHWM:"):
                    hwm = int(line.split()[1])
    except (FileNotFoundError, ProcessLookupError):
        pass
    return rss, hwm


def op_counts(path):
    """Lines per first token of a trace (awk, after the process exited)."""
    if not path.exists():
        return None
    out = subprocess.run(["awk", "{c[$1]++} END {for (k in c) print k, c[k]}", str(path)],
                         capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"}).stdout
    return {k: int(v) for k, v in (line.split() for line in out.splitlines())}


def run_process(cmd, cwd, out_file, log):
    """One generator process: caches dropped, wall clock, VmRSS/VmHWM every 10 ms, stdout lines with arrival time."""
    if out_file.exists():
        out_file.unlink()
    subprocess.run(["sudo", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)
    log(f"  $ {' '.join(map(str, cmd))}   (cwd {cwd}; caches dropped)")
    lines = []
    start = time.perf_counter()
    proc = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)

    def reader():
        for line in proc.stdout:
            lines.append((time.perf_counter() - start, line.rstrip("\n")))

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    peak_rss = last_hwm = 0
    while proc.poll() is None:
        rss, hwm = proc_status_kib(proc.pid)
        if rss:
            peak_rss = max(peak_rss, rss)
        if hwm:
            last_hwm = hwm
        time.sleep(POLL_S)
    wall = time.perf_counter() - start
    th.join(timeout=5)
    counts = op_counts(out_file)
    trace_bytes = out_file.stat().st_size if out_file.exists() else None
    if out_file.exists():
        out_file.unlink()
    for t, line in lines:
        if not re.match(r"^\s*\[Generating\]", line):
            log(f"    {t:9.4f} | {line[:300]}")
    log(f"  exit {proc.returncode}, wall {wall:.4f} s, peak VmRSS {peak_rss / 1024:.1f} MiB, "
        f"VmHWM {last_hwm / 1024:.1f} MiB, trace {trace_bytes} B, ops {counts}")
    if proc.returncode != 0:
        raise RuntimeError(f"exit code {proc.returncode}")
    return {"wall_time_s": wall, "peak_rss_mib": peak_rss / 1024, "peak_hwm_mib": last_hwm / 1024,
            "stdout": lines, "op_counts": counts, "trace_bytes": trace_bytes, "command": [str(c) for c in cmd]}


def xbench_load_group(spec_path):
    """Index of the load group (0) when the spec's first group has only inserts and more groups follow, else None."""
    spec = json.load(open(spec_path))
    groups = spec["sections"][0]["groups"]
    ops = {k for k, v in groups[0].items() if isinstance(v, dict) and "op_count" in v}
    return 0 if len(spec["sections"]) == 1 and len(groups) > 1 and ops <= {"inserts", "unique_inserts"} else None


GROUP_RE = re.compile(r"\[X-Bench Group\] Section (\d+) Group (\d+) \((.*)\) started at ([\d.]+)s, finished at ([\d.]+)s")


def run_job(tool, wset, w, keyset, log):
    if tool == "YCSB":
        wl = w.lower()
        out = TMP / f"fig7le_ycsb_{wl}.txt"
        common = ["java", "-cp", f7.YCSB_CP, "site.ycsb.Client", "-db", "site.ycsb.db.FileClient", "-P",
                  f"workloads/workload{wl}", "-p", f"file.output={out}", "-p", "recordcount=1000000",
                  "-p", "operationcount=1000000"]
        load = run_process(common + ["-load"], f7.YCSB_DIR, out, log)
        run = run_process(common + ["-t"], f7.YCSB_DIR, out, log)
        return {"wall_time_s": load["wall_time_s"] + run["wall_time_s"],
                "loading_s": load["wall_time_s"], "execution_s": run["wall_time_s"],
                "peak_rss_mib": max(load["peak_rss_mib"], run["peak_rss_mib"]),
                "peak_hwm_mib": max(load["peak_hwm_mib"], run["peak_hwm_mib"]),
                "load": {k: v for k, v in load.items() if k != "stdout"},
                "run": {k: v for k, v in run.items() if k != "stdout"},
                "stdout": {"load": load["stdout"], "run": run["stdout"]}}
    if tool == "KV-Bench":
        variant = KVBENCH_VARIANT[(wset, w)]
        binary = KVBENCH_BIN / f"load_gen.{variant}"
        args = (f7.KVBENCH_YCSB_ARGS if wset == "ycsb" else KVBENCH_ARGS)[w]
        out = TMP / f"fig7le_kvbench_{wset}_{w.lower()}.txt"
        r = run_process([binary] + args.split() + ["--OP", out], ROOT, out, log)
        marks = [(t, float(line.split(":", 1)[1])) for t, line in r["stdout"] if line.startswith("KVbench_LoadPhase_End:")]
        if len(marks) != 1:
            raise RuntimeError(f"expected one KVbench_LoadPhase_End line, got {len(marks)}")
        arrival, reported = marks[0]
        return {**r, "loading_s": arrival, "execution_s": r["wall_time_s"] - arrival,
                "load_end_reported_s": reported, "kvbench_variant": variant,
                "kvbench_variant_flags": KVBENCH_VARIANT_FLAGS[variant], "kvbench_binary_sha256": sha256(binary),
                "kvbench_commit": KVBENCH_COMMIT,
                "kvbench_args": args, "kvbench_scale": (KV_SCALE[w] if wset == "kvbench" else 1)}
    spec = ROOT / (XBENCH_YCSB_SPEC.format(w.lower()) if wset == "ycsb" else XBENCH_KV_SPEC[w])
    out = TMP / f"fig7le_xbench_{wset}_{w.lower()}_{keyset}.txt"
    r = run_process([XBENCH_CLI, "generate", "-w", spec, "-o", out, "--keyset", keyset], ROOT, out, log)
    groups = [dict(zip(("section", "group", "name", "start_s", "end_s", "arrival_s"),
                       (int(m[1]), int(m[2]), m[3], float(m[4]), float(m[5]), t)))
              for t, m in ((t, GROUP_RE.search(line)) for t, line in r["stdout"]) if m]
    load_group = xbench_load_group(spec)
    loading = 0.0
    if load_group is not None:
        g = [x for x in groups if x["section"] == 0 and x["group"] == load_group]
        if len(g) != 1:
            raise RuntimeError(f"no [X-Bench Group] line for the load group: {groups}")
        loading = g[0]["arrival_s"]  # harness clock (from launch), as wall_time_s; g[0]["end_s"] is X-Bench's own
    keysets = [line for _, line in r["stdout"] if "[X-Bench KeySet]" in line]
    return {**r, "loading_s": loading, "execution_s": r["wall_time_s"] - loading, "groups": groups,
            "load_group": load_group, "keyset": keyset, "keysets_used": keysets,
            "spec": str(spec.relative_to(ROOT)), "spec_sha256": sha256(spec), "xbench_cli_sha256": sha256(XBENCH_CLI)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=TRIALS)
    ap.add_argument("--with-fixed", action="store_true")
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    all_jobs = jobs(a.with_fixed)
    if a.only:
        unknown = set(a.only) - {j[0] for j in all_jobs}
        assert not unknown, f"unknown jobs: {unknown}"
    sel = [j for j in all_jobs if not a.only or j[0] in a.only]
    if a.dry_run:
        for j in sel:
            print(j)
        return
    os.environ.pop("XBENCH_PARALLEL_GEN", None)  # X-Bench single-threaded
    host = socket.gethostname()
    out_dir = Path(a.out_dir)
    (out_dir / "results").mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)
    runs_path = out_dir / f"results/runs.{host}.json"
    runs = json.load(open(runs_path)) if runs_path.exists() else []
    logf = open(out_dir / f"logs/generators.{host}.log", "a")

    def log(msg):
        print(msg, flush=True)
        logf.write(msg + "\n")
        logf.flush()

    versions = {"host": host, "ycsb_core_jar_sha256": sha256(f7.YCSB_DIR / "core/target/core-0.18.0-SNAPSHOT.jar"),
                "ycsb_file_binding_jar_sha256": sha256(f7.YCSB_DIR / "file/target/file-binding-0.18.0-SNAPSHOT.jar"),
                "java": subprocess.run(["java", "-version"], capture_output=True, text=True).stderr.splitlines()[0]}
    log(f"\n=== run_fig7_load_execution.py {time.strftime('%Y-%m-%d %H:%M:%S')} UTC, {len(sel)} jobs x {a.trials} trials: {versions}")
    done = {(r["job"], r["trial"]) for r in runs if not r.get("failed")}
    for trial in range(1, a.trials + 1):
        for job, tool, wset, w, keyset in sel:
            if (job, trial) in done:
                continue
            log(f"\n##### {job} trial {trial}/{a.trials}")
            rec = {"job": job, "tool": tool, "workload_set": wset, "workload": w, "trial": trial, **versions}
            try:
                rec.update(run_job(tool, wset, w, keyset, log))
                rec["failed"] = False
            except Exception as e:
                rec.update({"failed": True, "error": repr(e)[:2000]})
            rec["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
            runs.append(rec)
            with open(str(runs_path) + ".tmp", "w") as f:
                json.dump(runs, f, indent=1)
            os.replace(str(runs_path) + ".tmp", runs_path)
            log(f"##### {job} trial {trial}: " + (f"FAILED {rec['error']}" if rec["failed"] else
                f"{rec['wall_time_s']:.2f} s (loading {rec['loading_s']:.2f} + execution {rec['execution_s']:.2f}), "
                f"{rec['peak_rss_mib']:.1f} MiB (VmHWM {rec['peak_hwm_mib']:.1f})"))
    log("DONE")
    logf.close()


if __name__ == "__main__":
    main()
