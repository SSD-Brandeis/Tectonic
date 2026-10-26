#!/usr/bin/env python3
"""
run_mixgraph_fidelity.py
========================
Revision items R1.O9 / R2.O1 / R2.Q1: "model publicly available production traces, as used by db_bench's
`mixgraph` workload ... and map them to multi-phase X-Bench specifications".

Stage `dbbench`: record the db_bench mixgraph workload empirically. db_bench (upstream RocksDB v10.4.2,
built in rocksdb-v10.4.2/) runs the commands of Appendix A.3 of Cao et al., FAST '20 (the paper that
introduced mixgraph; references/cao_fast20.pdf): `fillrandom` to create the database, then `mixgraph`
"Prefix_dist" (fitted to Facebook's ZippyDB trace) on it. Both runs record every operation with RocksDB's
own tracer (db_bench `--trace_file`, i.e. DB::StartTrace); `trace_analyzer` converts the traces to text,
one line per operation: `<key hex> <type> <cf> <value size> <time us>` (type 0 Get, 1 Put, 6 Seek).
The only changes to the paper's commands: the scale (num, reads), `--trace_file` and `--statistics`
(RocksDB statistics, for the number of iterator Next calls, which the tracer does not record).

Output: data/revision/R1.O9-R2.O1_mixgraph/ (see its README.md).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT_DIR = "/home/cc/Tectonic"
ROCKSDB_SRC = f"{ROOT_DIR}/rocksdb-v10.4.2"
DB_BENCH = f"{ROCKSDB_SRC}/db_bench"
TRACE_ANALYZER = f"{ROCKSDB_SRC}/trace_analyzer"
# The same source with references/db_bench_scan_length_log.patch: one log line per Seek (key id, scan length
# drawn, Next calls made), because RocksDB's tracer does not record Next. No other change.
DB_BENCH_SCANLOG = f"{ROOT_DIR}/rocksdb-v10.4.2-scanlog/db_bench"
TECTONIC_CLI = f"{ROOT_DIR}/target/release/tectonic-cli"

OUT_DIR = f"{ROOT_DIR}/data/revision/R1.O9-R2.O1_mixgraph"
DBBENCH_DIR = f"{OUT_DIR}/dbbench"
DBBENCH_SCANLOG_DIR = f"{OUT_DIR}/dbbench-scanlog"
DB_DIR = f"{OUT_DIR}/dbbench_db"
RESULTS_DIR = f"{OUT_DIR}/results"
LOG_PATH = f"{OUT_DIR}/logs/run.log"

# The paper runs 50M keys and 420M queries (traces of ~85 GB); 1/10 of both, nothing else scaled.
NUM = 5_000_000
READS = 42_000_000

# Appendix A.3 of Cao et al., FAST '20, verbatim except -num, -reads, -db and the tracing flags.
FILLRANDOM = ["--benchmarks=fillrandom", "--perf_level=3", "-use_direct_io_for_flush_and_compaction=true",
              "-use_direct_reads=true", "-cache_size=268435456", "-key_size=48", "-value_size=43"]
PREFIX_DIST = ["--benchmarks=mixgraph", "-use_direct_io_for_flush_and_compaction=true", "-use_direct_reads=true",
               "-cache_size=268435456", "-key_dist_a=0.002312", "-key_dist_b=0.3467",
               "-keyrange_dist_a=14.18", "-keyrange_dist_b=-2.917", "-keyrange_dist_c=0.0164",
               "-keyrange_dist_d=-0.08082", "-keyrange_num=30", "-value_k=0.2615", "-value_sigma=25.45",
               "-iter_k=2.517", "-iter_sigma=14.236", "-mix_get_ratio=0.83", "-mix_put_ratio=0.14",
               "-mix_seek_ratio=0.03", "-sine_mix_rate_interval_milliseconds=5000", "-sine_a=1000",
               "-sine_b=0.000073", "-sine_d=4500", "--perf_level=2", "-key_size=48", "-use_existing_db=true"]
TRACE_TYPES = {"0": "Get", "1": "Put", "6": "Seek"}

_log_fh = None


def log(msg=""):
    print(msg, flush=True)
    if _log_fh:
        _log_fh.write(msg + "\n")
        _log_fh.flush()


def fail(msg):
    log(f"FATAL: {msg}")
    sys.exit(1)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def drop_caches():
    subprocess.run(["sync"], check=True)
    subprocess.run(["sudo", "sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"], check=True)


def run(cmd, stdout_path):
    log(f"  $ {' '.join(cmd)}")
    t0 = time.perf_counter()
    with open(stdout_path, "w") as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    wall = time.perf_counter() - t0
    if r.returncode != 0:
        fail(f"exit {r.returncode}, see {stdout_path}")
    log(f"  -> {wall:.1f} s")
    return wall


def db_bench(binary, out_dir, name, args):
    """One db_bench run with RocksDB's tracer on; returns what it reported about itself."""
    trace = f"{out_dir}/{name}.trace"
    out = f"{out_dir}/{name}.db_bench.out"
    drop_caches()
    wall = run([binary, *args, f"-db={DB_DIR}", f"-trace_file={trace}", "--statistics"], out)
    shutil.copy2(f"{DB_DIR}/LOG", f"{out_dir}/{name}.rocksdb_LOG.txt")
    with open(out) as f:
        text = f.read()
    rec = {"wall_s": wall, "trace": trace, "stdout": out}
    m = re.search(r"^(\w+)\s+:\s+([\d.]+) micros/op (\d+) ops/sec ([\d.]+) seconds (\d+) operations;(.*)$", text, re.M)
    if not m:
        fail(f"no result line in {out}")
    rec.update({"benchmark": m.group(1), "micros_per_op": float(m.group(2)), "ops_per_sec": int(m.group(3)),
                "seconds": float(m.group(4)), "operations": int(m.group(5)), "result_line": m.group(0)})
    m = re.search(r"Gets:(\d+) Puts:(\d+) Seek:(\d+), reads (\d+) in (\d+) found, avg size: ([\d.]+) value, "
                  r"([\d.]+) scan", text)
    if m:
        rec["mixgraph"] = {"gets": int(m.group(1)), "puts": int(m.group(2)), "seeks": int(m.group(3)),
                           "found": int(m.group(4)), "gets_plus_seeks": int(m.group(5)),
                           "avg_value_size": float(m.group(6)), "avg_scan_length": float(m.group(7))}
    rec["statistics"] = {k: int(v) for k, v in re.findall(r"^(rocksdb\.[\w.]+) COUNT : (\d+)$", text, re.M)}
    return rec


def to_text(run_dir, name):
    """trace_analyzer: binary trace -> one text line per operation, plus its own summary."""
    out_dir = f"{run_dir}/{name}_analyzer"
    os.makedirs(out_dir, exist_ok=True)
    run([TRACE_ANALYZER, "-analyze_get", "-analyze_put", "-analyze_iterator",
         "-convert_to_human_readable_trace", f"-output_dir={out_dir}", f"-output_prefix={name}",
         f"-trace_path={run_dir}/{name}.trace"], f"{out_dir}/trace_analyzer.out")
    src = f"{out_dir}/{name}-human_readable_trace.txt"
    dst = f"{run_dir}/{name}.txt"
    os.replace(src, dst)
    counts = {}
    with open(dst, "rb") as f:
        for line in f:
            t = line.split(b" ", 2)[1].decode()
            counts[t] = counts.get(t, 0) + 1
    return dst, {TRACE_TYPES.get(t, t): n for t, n in counts.items()}


def record(binary, out_dir, result_name, extra_mixgraph_args=()):
    for d in (out_dir, RESULTS_DIR):
        os.makedirs(d, exist_ok=True)
    if os.path.exists(DB_DIR):
        shutil.rmtree(DB_DIR)
    version = subprocess.run([binary, "--version"], stdout=subprocess.PIPE, text=True).stdout.strip()
    src = os.path.dirname(binary)
    commit = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                            text=True).stdout.strip()
    diff = subprocess.run(["git", "-C", src, "diff", "--stat"], stdout=subprocess.PIPE, text=True).stdout.strip()
    rec = {"db_bench": binary, "db_bench_version": version, "rocksdb_commit": commit, "source_changes": diff,
           "num": NUM, "reads": READS, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "runs": {}}
    log(f"db_bench: {binary}, {version} (rocksdb {commit}){'; changed: ' + diff if diff else ''}")

    log("\n=== fillrandom (the database mixgraph runs on) ===")
    rec["runs"]["fillrandom"] = db_bench(binary, out_dir, "fillrandom", FILLRANDOM + [f"-num={NUM}"])
    log("\n=== mixgraph Prefix_dist ===")
    rec["runs"]["mixgraph"] = db_bench(binary, out_dir, "mixgraph",
                                       PREFIX_DIST + [f"-num={NUM}", f"-reads={READS}", *extra_mixgraph_args])
    shutil.rmtree(DB_DIR)

    for name, r in rec["runs"].items():
        log(f"\n=== trace_analyzer {name} ===")
        r["text_trace"], r["trace_op_counts"] = to_text(out_dir, name)
        r["trace_sha256"] = sha256_file(r["trace"])
        log(f"  trace operations: {r['trace_op_counts']}")
    # The trace holds exactly the operations db_bench reports it issued
    m = rec["runs"]["mixgraph"]["mixgraph"]
    want = {"Get": m["gets"], "Put": m["puts"], "Seek": m["seeks"]}
    if rec["runs"]["mixgraph"]["trace_op_counts"] != want:
        fail(f"mixgraph trace {rec['runs']['mixgraph']['trace_op_counts']} != db_bench {want}")
    if rec["runs"]["fillrandom"]["trace_op_counts"] != {"Put": NUM}:
        fail(f"fillrandom trace {rec['runs']['fillrandom']['trace_op_counts']} != {NUM} puts")
    log("\ntrace operation counts match db_bench's own counts")
    return rec


def stage_dbbench(args):
    rec = record(DB_BENCH, DBBENCH_DIR, "dbbench")
    with open(f"{RESULTS_DIR}/dbbench.json", "w") as f:
        json.dump(rec, f, indent=2)


def stage_dbbench_scanlog(args):
    scan_log = f"{DBBENCH_SCANLOG_DIR}/mixgraph.scan_lengths.txt"
    rec = record(DB_BENCH_SCANLOG, DBBENCH_SCANLOG_DIR, "dbbench-scanlog", [f"-mix_scan_length_log={scan_log}"])
    # The log has one line per Seek, for the same key as the trace's Seek at that position, and its Next
    # calls add up to RocksDB's own counter
    seek_keys = []
    with open(rec["runs"]["mixgraph"]["text_trace"], "rb") as f:
        for line in f:
            key, t = line.split(b" ", 2)[:2]
            if t == b"6":
                seek_keys.append(int(key[2:18], 16))
    drawn = nexts = n = 0
    with open(scan_log) as f:
        for i, line in enumerate(f):
            key_id, length, made = map(int, line.split())
            if i >= len(seek_keys) or key_id != seek_keys[i]:
                fail(f"scan log line {i + 1}: key {key_id} is not the trace's Seek key")
            drawn += length
            nexts += made
            n += 1
    if n != len(seek_keys):
        fail(f"scan log has {n} lines, the trace {len(seek_keys)} Seeks")
    counter = rec["runs"]["mixgraph"]["statistics"]["rocksdb.number.db.next"]
    if nexts != counter:
        fail(f"scan log Next calls {nexts} != rocksdb.number.db.next {counter}")
    rec["scan_length_log"] = {"path": scan_log, "seeks": n, "drawn_total": drawn, "next_total": nexts}
    log(f"scan log: {n} Seeks in trace order, {nexts} Next calls = rocksdb.number.db.next "
        f"(drawn {drawn}; the rest stopped at the end of the key space)")
    with open(f"{RESULTS_DIR}/dbbench-scanlog.json", "w") as f:
        json.dump(rec, f, indent=2)


def stage_scan_lengths(args):
    """Scan lengths two ways: (a) db_bench's model, (b) the logged scans of the dbbench-scanlog run.
    (a) is the formula of MixGraph, scan length = ParetoCdfInversion(u, iter_theta, iter_k, iter_sigma)
    % mix_max_scan_len with u = rand_v / num, enumerated over every rand_v in [1, num) that makes the query
    a Seek (QueryDecider: rand_v % 1000 >= 970 for 0.83 / 0.14 / 0.03). (b) has, per Seek, the length drawn
    and the Next calls made (fewer when the scan reaches the end of the key space)."""
    import numpy as np
    k, sigma, theta, scan_max = 2.517, 14.236, 0.0, 10000
    rand_v = np.arange(1, NUM, dtype=np.int64)
    u = rand_v[rand_v % 1000 >= 970] / NUM
    model = np.ceil(theta + sigma * (np.power(u, -k) - 1) / k)
    model = np.where(model < 2 ** 62, model, 0).astype(np.int64) % scan_max
    log_ = np.loadtxt(f"{DBBENCH_SCANLOG_DIR}/mixgraph.scan_lengths.txt", dtype=np.int64)
    key, drawn, made = log_[:, 0], log_[:, 1], log_[:, 2]

    def ks(x, y):
        x, y = np.sort(x), np.sort(y)
        grid = np.union1d(x, y)
        return float(np.max(np.abs(np.searchsorted(x, grid, "right") / len(x) -
                                   np.searchsorted(y, grid, "right") / len(y))))

    def summary(x):
        return {"n": int(len(x)), "mean": float(x.mean()),
                **{f"p{p}": int(np.percentile(x, p)) for p in (25, 50, 75, 90, 99)}, "max": int(x.max())}

    with open(f"{RESULTS_DIR}/dbbench.json") as f:
        first = json.load(f)["runs"]["mixgraph"]
    res = {"model_a": summary(model), "logged_drawn": summary(drawn), "logged_next_calls_b": summary(made),
           "ks_model_vs_drawn": ks(model, drawn), "ks_model_vs_next_calls": ks(model, made),
           "ks_drawn_vs_next_calls": ks(drawn, made),
           "seeks_cut_short": int((made < drawn).sum()), "seeks": int(len(made)),
           "spearman_key_id_vs_drawn": float(__import__("scipy.stats", fromlist=["spearmanr"]).spearmanr(key, drawn).correlation),
           "unmodified_run_mean_next_per_seek": first["statistics"]["rocksdb.number.db.next"] / first["mixgraph"]["seeks"]}
    for k_, v in res.items():
        log(f"  {k_}: {v}")
    with open(f"{RESULTS_DIR}/scan_lengths.json", "w") as f:
        json.dump(res, f, indent=2)


# =============================================================================
#  STEP 2: CHARACTERIZE
# =============================================================================
# Every trace (db_bench's, and later X-Bench's and YCSB's) is reduced to the same arrays, one entry per
# operation of the run phase: type (0 Get, 1 Put, 6 Seek), key id, value size, scan length (the number of
# keys the Seek asks for; db_bench's scan-length log, column "drawn"); plus the key ids and value sizes of
# the load phase. The properties of the revision plan are computed from these arrays.
ARRAYS_DIR = f"{RESULTS_DIR}/arrays"
KEYRANGE_NUM = 30
KEYRANGE_SIZE = NUM // KEYRANGE_NUM  # db_bench's GenerateTwoTermExpKeys: keyrange_size_ = num / keyrange_num
WINDOWS = 42  # 1M operations each


def dbbench_arrays(run_dir, side, scan_log=None):
    """db_bench text trace -> arrays (cached in results/arrays/<side>.npz)."""
    import numpy as np
    path = f"{ARRAYS_DIR}/{side}.npz"
    if os.path.exists(path):
        return dict(np.load(path))
    os.makedirs(ARRAYS_DIR, exist_ok=True)

    def parse(text):
        n = int(subprocess.run(["wc", "-l", text], stdout=subprocess.PIPE, text=True).stdout.split()[0])
        typ = np.empty(n, np.uint8)
        key = np.empty(n, np.uint32)
        vsz = np.empty(n, np.uint16)
        with open(text, "rb") as f:
            for i, line in enumerate(f):
                k, t, _, v, _ = line.split(b" ")
                typ[i] = int(t)
                key[i] = int(k[2:18], 16)
                vsz[i] = int(v)
        return typ, key, vsz

    _, load_key, load_vsz = parse(f"{run_dir}/fillrandom.txt")
    typ, key, vsz = parse(f"{run_dir}/mixgraph.txt")
    scan = np.zeros(len(typ), np.uint32)
    if scan_log:
        scan[typ == 6] = np.loadtxt(scan_log, dtype=np.int64)[:, 1]
    arrays = {"load_key": load_key, "load_vsz": load_vsz, "typ": typ, "key": key, "vsz": vsz, "scan": scan,
              "has_scan": np.array(scan_log is not None)}
    np.savez(path, **arrays)
    return arrays


def characterize(a):
    """The properties of the revision plan: per-window op mix, key/value size, temporal locality, plus the
    key-space locality mixgraph models (key-range hotness, per-key access counts) and the found ratio."""
    import numpy as np
    typ, key, vsz = a["typ"], a["key"], a["vsz"]
    n = len(typ)
    names = {0: "Get", 1: "Put", 6: "Seek"}
    c = {"operations": int(n), "load_puts": int(len(a["load_key"])),
         "load_distinct_keys": int(len(np.unique(a["load_key"]))),
         "op_counts": {names[t]: int((typ == t).sum()) for t in names}}
    # per-window op mix
    w = np.minimum(np.arange(n) * WINDOWS // n, WINDOWS - 1)
    c["op_mix_per_window"] = {names[t]: [float(x) for x in np.bincount(w, typ == t, WINDOWS) / np.bincount(w, None, WINDOWS)]
                              for t in names}
    # value sizes (Put)
    c["value_size_load"] = np.bincount(a["load_vsz"]).tolist()
    c["value_size_run"] = np.bincount(vsz[typ == 1]).tolist()
    # key-range hotness: share of the operations of each type in each of the 30 key ranges
    kr = np.minimum(key // KEYRANGE_SIZE, KEYRANGE_NUM - 1)
    c["keyrange_share"] = {names[t]: (np.bincount(kr[typ == t], None, KEYRANGE_NUM) / (typ == t).sum()).tolist()
                           for t in names}
    c["keyrange_share"]["all"] = (np.bincount(kr, None, KEYRANGE_NUM) / n).tolist()
    # access count per key (keys accessed at least once), all operations and per type
    def access_counts(sel):
        return np.bincount(np.bincount(key[sel]))[1:].tolist()  # index i -> number of keys accessed i+1 times
    c["access_count_hist"] = {"all": access_counts(np.ones(n, bool)),
                              **{names[t]: access_counts(typ == t) for t in names}}
    # temporal locality: reuse distance = operations since the previous access to the same key
    order = np.argsort(key, kind="stable")
    ks, pos = key[order], order
    same = ks[1:] == ks[:-1]
    reuse = (pos[1:] - pos[:-1])[same]
    c["reuse_distance_hist"] = np.bincount(np.floor(8 * np.log2(reuse)).astype(np.int64)).tolist()  # bins of 2^(1/8)
    c["reuse_distance_mean"] = float(reuse.mean())
    c["reuse_distance_median"] = float(np.median(reuse))
    c["first_access_fraction"] = float(1 - same.sum() / n)
    # which operation types each accessed key receives: share of the keys and of the operations per group
    per_type = np.stack([np.bincount(key[typ == t], minlength=NUM) for t in names])
    kinds = (per_type > 0).sum(0)
    groups = {"mixed": kinds >= 2, **{f"{names[t]}-only": (kinds == 1) & (per_type[i] > 0) for i, t in enumerate(names)}}
    accessed = (kinds > 0).sum()
    c["key_groups"] = {g: {"keys": float(m.sum() / accessed), "ops": float(per_type[:, m].sum() / n)} for g, m in groups.items()}
    # found ratio of Gets / Seeks: the key was written by the load or by an earlier Put
    first_put = np.full(NUM + 1, np.iinfo(np.int64).max, np.int64)
    puts = np.nonzero(typ == 1)[0]
    np.minimum.at(first_put, key[puts], puts)
    first_put[np.unique(a["load_key"])] = -1
    gets = typ == 0
    c["get_found_ratio"] = float((first_put[key[gets]] < np.nonzero(gets)[0]).mean())
    # db_bench draws a Put's value size and a Seek's scan length from the same random number as the key:
    # the share of distinct (key, value size) and (key, scan length) pairs shows how fixed they are per key
    puts = typ == 1
    c["put_distinct_key_size_pairs"] = float(len(np.unique(key[puts].astype(np.int64) * 65536 + vsz[puts])) / puts.sum())
    if bool(a["has_scan"]):
        c["scan_length_hist"] = np.bincount(a["scan"][typ == 6]).tolist()
        c["scan_length_mean"] = float(a["scan"][typ == 6].mean())
        seeks = typ == 6
        c["seek_distinct_key_length_pairs"] = float(
            len(np.unique(key[seeks].astype(np.int64) * 65536 + a["scan"][seeks])) / seeks.sum())
    return c


def stage_characterize(args):
    sides = {"dbbench": dbbench_arrays(DBBENCH_SCANLOG_DIR, "dbbench",
                                       f"{DBBENCH_SCANLOG_DIR}/mixgraph.scan_lengths.txt"),
             "dbbench_rerun": dbbench_arrays(DBBENCH_DIR, "dbbench_rerun")}
    out = {}
    for side, arr in sides.items():
        log(f"  characterize {side}")
        out[side] = characterize(arr)
    with open(f"{RESULTS_DIR}/characterization.json", "w") as f:
        json.dump(out, f)
    ref = out["dbbench"]
    log(f"  op counts {ref['op_counts']}, load distinct keys {ref['load_distinct_keys']}")
    log(f"  Get found {ref['get_found_ratio']:.4f} (rerun {out['dbbench_rerun']['get_found_ratio']:.4f})")
    log(f"  key-range share (all ops): " + " ".join(f"{x:.3f}" for x in ref["keyrange_share"]["all"]))
    log(f"  reuse distance mean {ref['reuse_distance_mean']:.0f} median {ref['reuse_distance_median']:.0f}; "
        f"first accesses {ref['first_access_fraction']:.4f}")


# =============================================================================
#  STEP 3: X-BENCH SPEC FROM THE EXTRACTED PROPERTIES, AND ITS STATISTICAL DISTANCE TO THE TRACE
# =============================================================================
SPEC_DIR = f"{ROOT_DIR}/example-specs/db_bench-mixgraph"
SPEC_PATH = f"{SPEC_DIR}/prefix_dist.spec.json"
XBENCH_DIR = f"{OUT_DIR}/xbench"
KEY_WIDTH = 16  # the key id in decimal, zero-padded: sorts like db_bench's 8-byte big-endian id
KEY_PAD = "0" * (48 - KEY_WIDTH)  # db_bench pads its 48-byte keys with ASCII '0'
# The spec models the trace the way db_bench generates it (Cao et al., FAST '20, and db_bench's MixGraph): every
# query draws one of a fixed set of rows; a row is a key range (with the range's hotness) and one of a fixed set
# of slots, each slot a key in the range drawn from a skewed (power-law) distribution. All parameters are fitted
# to the trace (fit_model; results/model.json).
OP_TYPES = ((0, "Get"), (1, "Put"), (6, "Seek"))
SLOT_SEED = 1  # the slots are the same for all operation types: their pools share this seed
# a Put's key and value size come from the same row, and a Seek's key and scan length from the same class
# and row: their weighted classes and pools pick alike (same pick_seed)
PICK_SEEDS = {"Put": 2, "Seek": {"class": 3, "hot": 4, "rest": 5}}
FIT_SEED = 20261024


def scatter_np(x, length):
    """X-Bench's `scatter` (spec.rs Scatter::apply) with start 0, for numpy arrays."""
    import numpy as np
    a = int(round(length * 0.6180339887498949))
    while a > 1 and np.gcd(a, length) != 1:
        a -= 1
    return (np.asarray(x, dtype=np.int64) % length) * a % length


def fit_slots(arrays):
    """The slots: KEYRANGE_SIZE draws of floor(Weibull(scale, shape)), scattered over a key range. Fitted on
    the trace's hottest key range, whose rows cover all slots: least squares on the log of the 3000 highest
    access counts (relative to all), plus the number of distinct keys. Returns (scale, shape)."""
    import numpy as np
    from scipy import optimize
    key = arrays["key"].astype(np.int64)
    kr = key // KEYRANGE_SIZE
    hot = int(np.bincount(kr).argmax())
    counts = np.bincount(key[kr == hot] - hot * KEYRANGE_SIZE)
    counts = np.sort(counts[counts > 0])[::-1]
    share = counts[:3000] / counts.sum()
    u = np.random.default_rng(FIT_SEED).random(KEYRANGE_SIZE)

    def loss(q):
        scale, shape = np.exp(q[0]), q[1]
        if not 0.05 < shape < 2:
            return np.inf
        slots = scatter_np(np.floor(scale * (-np.log1p(-u)) ** (1 / shape)), KEYRANGE_SIZE)
        c = np.bincount(slots)
        c = np.sort(c[c > 0])[::-1]
        return float(np.sum((np.log(c[:3000] / c.sum()) - np.log(share)) ** 2) + 50 * abs(len(c) / len(counts) - 1))
    fit = min((optimize.minimize(loss, [x0, k0], method="Nelder-Mead", options={"maxiter": 300})
               for x0 in (14, 17, 20) for k0 in (0.3, 0.4)), key=lambda r: r.fun)
    scale = float(f"{np.exp(fit.x[0]):.3g}")
    return (int(scale) if scale.is_integer() else scale), round(float(fit.x[1]), 3)


def gpd_sample(u, loc, scale, shape, top=1.0):
    """floor(location + GPD(scale, shape)) for uniforms u, as X-Bench's generalized_pareto (spec.rs); top is
    the probability below `max` when truncated."""
    import numpy as np
    y = np.floor(loc + scale * ((1 - top * u) ** (-shape) - 1) / shape)
    return np.where(y < 2.0 ** 62, y, 0).astype(np.int64)


def gpd_cdf(x, loc, scale, shape):
    import numpy as np
    return 1 - (1 + shape * np.maximum(x - loc, 0) / scale) ** (-1 / shape)


def fit_gpd(values, x0, cap=None, truncate=False):
    """floor(location + GPD(scale, shape)), modulo `cap` or truncated at a fitted `max`, closest to the values:
    least KS distance plus relative error of the mean (the mean sets the bytes the workload moves), Nelder-Mead
    on common random numbers. Returns {location, scale, shape, [max,] ks, mean, mean_trace}."""
    import numpy as np
    from scipy import optimize
    emp = np.cumsum(np.bincount(values)) / len(values)
    u = np.random.default_rng(FIT_SEED).random(2_000_000)

    def sample(q):
        top = gpd_cdf(q[3], *q[:3]) if truncate else 1.0
        y = gpd_sample(u, *q[:3], top)
        return y % cap if cap else y

    def loss(q):
        if q[1] <= 0 or q[2] <= 0 or (truncate and q[3] <= q[0] + 1):
            return np.inf
        y = sample(q)
        model = np.cumsum(np.bincount(np.minimum(y, len(emp) - 1), minlength=len(emp))) / len(y)
        ks = float(np.abs(model - emp).max())
        return ks + abs(y.mean() / values.mean() - 1)
    starts = [x0 + [m * values.max()] for m in (1.0, 1.3, 2.0)] if truncate else [x0]
    fit = min((optimize.minimize(loss, x, method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-6, "maxiter": 3000})
               for x in starts), key=lambda r: r.fun)
    y = sample(fit.x)
    model = np.cumsum(np.bincount(np.minimum(y, len(emp) - 1), minlength=len(emp))) / len(y)
    out = {"location": round(float(fit.x[0]), 3), "scale": round(float(fit.x[1]), 3), "shape": round(float(fit.x[2]), 4)}
    if truncate:
        out["max"] = int(round(fit.x[3]))
    return {**out, "ks": round(float(np.abs(model - emp).max()), 5), "mean": round(float(y.mean()), 2),
            "mean_trace": round(float(values.mean()), 2)}


def fit_attribute(name, values, cls=None):
    """Put value sizes: the share of the smallest size, the rest floor(location + GPD). Seek scan lengths:
    in the hottest key range floor(location + GPD) modulo db_bench's maximum (the largest length + 1,
    rounded up to a power of 10); in the other key ranges, which db_bench never gives its longest scans,
    a GPD truncated at a fitted max."""
    import numpy as np
    if name == "Put":
        low = int(values.min())
        return {"min": low, "p_min": round(float((values == low).mean()), 4),
                **fit_gpd(values[values > low], [low + 1, 25, 0.25])}
    if cls == "rest":
        return fit_gpd(values, [1, 14, 2.5], truncate=True)
    cap = int(10 ** np.ceil(np.log10(values.max() + 1)))
    return {"modulo": cap, **fit_gpd(values, [1, 14, 2.5], cap)}


def sample_attribute(m, u):
    import numpy as np
    top = gpd_cdf(m["max"], m["location"], m["scale"], m["shape"]) if "max" in m else 1.0
    y = gpd_sample(u[1], m["location"], m["scale"], m["shape"], top)
    if "p_min" in m:
        return np.where(u[0] < m["p_min"], m["min"], y)
    return y % m["modulo"] if "modulo" in m else y


def fit_rows(arrays, shares, slot_scale, slot_shape):
    """The rows of every operation type: how many, which key ranges (each type's share of the 30 key ranges),
    and how many of the slots they use (a prefix of the slots). db_bench draws a Put's value size and a
    Seek's scan length from the same random number as the key, so they are fixed per row; and it never
    gives its longest scans to rows outside the hottest key range, so Seeks get two classes of rows (the
    hottest key range, the others), each with its own row count and scan lengths. Fitted so that each type
    (and class) accesses as many distinct keys -- for Puts and Seeks distinct (key, size or length) pairs,
    i.e. rows -- as in the trace, and as many distinct keys of the hottest key range (for the slots). A row
    is accessed at least once with probability 1 - exp(-ops / rows)."""
    import numpy as np
    from scipy import optimize
    typ, key = arrays["typ"], arrays["key"].astype(np.int64)
    attr = {"Put": arrays["vsz"].astype(np.int64), "Seek": arrays["scan"].astype(np.int64)}
    kr = key // KEYRANGE_SIZE
    hot = int(np.argmax(shares["all"]))
    rng = np.random.default_rng(FIT_SEED)
    slots = scatter_np(np.floor(slot_scale * (-np.log1p(-rng.random(KEYRANGE_SIZE))) ** (1 / slot_shape)), KEYRANGE_SIZE)
    n_max = 8_000_000
    u_range, u_slot, u_seen = rng.random(n_max), rng.random(n_max), rng.random(n_max)
    u_attr = rng.random((2, n_max))

    def simulate(rows, prefix, ops, weights, attr_model=None):
        """Distinct keys (or (key, attribute) pairs) the rows yield, and distinct keys in the hottest range."""
        n = int(rows)
        p = np.asarray(weights, float)
        seen = u_seen[:n] < 1 - np.exp(-ops / n)
        r = np.searchsorted(np.cumsum(p) / p.sum(), u_range[:n][seen])
        k = r * KEYRANGE_SIZE + slots[(u_slot[:n][seen] * prefix).astype(np.int64)]
        ident = k if attr_model is None else k * 65536 + sample_attribute(attr_model, u_attr[:, :n][:, seen])
        return len(np.unique(ident)), len(np.unique(k[r == hot]))

    def solve(classes, sel, name):
        """Fits the attribute and rows of each class, and the slot prefix on the hottest key range."""
        fitted = {}
        for c, w in classes.items():
            m = sel & np.isin(kr, [i for i, x in enumerate(w) if x > 0])
            fitted[c] = {"weight": round(float(m.sum() / sel.sum()), 4)}
            if name != "Get":
                fitted[c].update(fit_attribute(name, attr[name][m], c))
            ident = key[m] if name == "Get" else key[m] * 65536 + attr[name][m]
            fitted[c]["_ops"], fitted[c]["_distinct"] = int(m.sum()), len(np.unique(ident))
        distinct_hot = len(np.unique(key[sel & (kr == hot)]))

        def model(c):
            return None if name == "Get" else {k: v for k, v in fitted[c].items() if not k.startswith("_")}
        rows, prefix = {c: fitted[c]["_distinct"] * 2.0 for c in classes}, KEYRANGE_SIZE
        with_hot = next(c for c, w in classes.items() if w[hot] > 0)
        for _ in range(3):
            if name != "Get":
                prefix = optimize.brentq(lambda x: simulate(rows[with_hot], x, fitted[with_hot]["_ops"], classes[with_hot])[1]
                                         - distinct_hot, 10, KEYRANGE_SIZE, xtol=10)
            for c in classes:
                rows[c] = optimize.brentq(lambda x: simulate(x, prefix, fitted[c]["_ops"], classes[c], model(c))[0]
                                          - fitted[c]["_distinct"], fitted[c]["_distinct"], n_max - 1, xtol=100)
        out = {"slots": int(round(prefix, -2)) if name != "Get" else KEYRANGE_SIZE}
        for c in classes:
            out[c] = {"rows": int(round(rows[c], -3)), **(model(c) or {}), "weight": fitted[c]["weight"]}
        return out

    out = {}
    for t, name in OP_TYPES:
        w = shares[name]
        classes = ({"hot": [x if i == hot else 0 for i, x in enumerate(w)], "rest": [0 if i == hot else x for i, x in enumerate(w)]}
                   if name == "Seek" else {"all": w})
        out[name] = solve(classes, typ == t, name)
    return out


def key_expr(value):
    return {"segmented": {"separator": "", "segments": [{"number": {"value": value, "width": KEY_WIDTH}}, KEY_PAD]}}


def fit_model(ref, arrays):
    """The parameters of the spec, all measured on the trace (stored in results/model.json)."""
    shares = {name: [round(x, 5) for x in ref["keyrange_share"][name]] for name in ("all", "Get", "Put", "Seek")}
    slot_scale, slot_shape = fit_slots(arrays)
    return {"keyrange_shares": {name: shares[name] for name in ("Get", "Put", "Seek")},
            "slots": {"weibull_scale": slot_scale, "weibull_shape": slot_shape},
            "types": fit_rows(arrays, shares, slot_scale, slot_shape)}


def build_spec(ref, model):
    """The X-Bench spec:
    - load: as many Puts as the trace's load, keys uniform over the key ids (fillrandom), its value size;
    - run: the trace's Get / Put / Seek counts, mixed at random. An operation draws one of its type's rows
      (a pool): a key range, picked by the type's share of the 30 key ranges (db_bench's, num/30 ids each),
      and one of the first `slots` slots. The slots (a pool shared by all types through its seed) are draws
      of floor(Weibull): a few hot ranks, many cold ones, `scatter`ed over the key range. A Put's value
      size and a Seek's scan length belong to the row, as in db_bench: generalized Pareto, drawn once per
      row (the pools of the key and of the size or length have the same size and pick_seed: they pick
      the same row). Seeks have two classes of rows, the hottest key range and the others, with their own
      scan lengths (a `weighted` of two pools for the key and the same for the length, with the same
      weights and pick_seed: the same class)."""
    sl, types, shares = model["slots"], model["types"], model["keyrange_shares"]
    hot = max(range(len(shares["Get"])), key=lambda i: shares["Get"][i])

    def offset(name, start=0):
        slot = {"pool": {"size": types[name]["slots"], "seed": SLOT_SEED,
                         "value": {"weibull": {"scale": sl["weibull_scale"], "shape": sl["weibull_shape"]}}}}
        return {"scatter": ({"start": start} if start else {}) | {"len": KEYRANGE_SIZE, "value": slot}}

    def in_ranges(name, exclude=None):
        weights = {"two_term_exponential": {"a": 14.18, "b": -2.917, "c": 0.0164, "d": -0.08082, "n": 30}}
        if exclude:
            weights["two_term_exponential"]["exclude"] = exclude
        return {"ranges": {"len": KEYRANGE_SIZE, "weights": weights, "value": offset(name)}}

    def gpd(m):
        return {"generalized_pareto": {k: m[k] for k in ("location", "scale", "shape", "max") if k in m}}

    def rows(name, cls, value, pick_seed=None):
        pool = {"size": types[name][cls]["rows"]}
        if pick_seed:
            pool["pick_seed"] = pick_seed
        return {"pool": {**pool, "value": value}}

    seeds = PICK_SEEDS
    seek = types["Seek"]

    def seek_classes(hot_value, rest_value):
        return {"weighted": [{"weight": seek["hot"]["weight"], "value": rows("Seek", "hot", hot_value, seeds["Seek"]["hot"])},
                             {"weight": seek["rest"]["weight"], "value": rows("Seek", "rest", rest_value, seeds["Seek"]["rest"])}],
                "pick_seed": seeds["Seek"]["class"]}
    put = types["Put"]["all"]
    ops = ref["op_counts"]
    load_sizes = [i for i, n in enumerate(ref["value_size_load"]) if n]
    if len(load_sizes) != 1:
        fail(f"load value sizes are not constant: {load_sizes[:10]}")
    return {"sections": [{"groups": [
        {"name": "load",
         "inserts": {"op_count": ref["load_puts"], "key": key_expr({"uniform": {"min": 0, "max": NUM}}),
                     "val": {"uniform": {"len": load_sizes[0]}}}},
        {"name": "run",
         "untracked_point_queries": {"op_count": ops["Get"], "key": key_expr(rows("Get", "all", in_ranges("Get")))},
         "inserts": {"op_count": ops["Put"], "key": key_expr(rows("Put", "all", in_ranges("Put"), seeds["Put"])),
                     "val": {"uniform": {"len": rows("Put", "all", {"weighted": [
                         {"weight": put["p_min"], "value": put["min"]},
                         {"weight": round(1 - put["p_min"], 4), "value": gpd(put)}]}, seeds["Put"])}}},
         "untracked_range_queries": {
             "op_count": ops["Seek"],
             "key": key_expr(seek_classes(offset("Seek", hot * KEYRANGE_SIZE), in_ranges("Seek", exclude=[hot]))),
             "scan_length": seek_classes({"modulo": {"len": seek["hot"]["modulo"], "value": gpd(seek["hot"])}}, gpd(seek["rest"]))}},
    ]}]}


def compact_json(obj, indent=0, width=100):
    """JSON indented by 2 spaces: objects and arrays up to `width` characters on one line, and the short entries
    of a `weighted` list one per line (so that they read like a table)."""
    flat = json.dumps(obj)
    rows = isinstance(obj, list) and all(isinstance(v, dict) and set(v) == {"weight", "value"}
                                         and len(json.dumps(v)) <= 1.5 * width for v in obj)
    if (len(flat) <= width and not (rows and len(obj) > 3)) or not isinstance(obj, (dict, list)) or not obj:
        return flat
    pad = " " * (indent + 2)
    if isinstance(obj, list) and all(isinstance(v, (int, float)) for v in obj):
        lines, line = [], []
        for v in obj:
            if line and len(", ".join(map(json.dumps, line + [v]))) > width:
                lines.append(", ".join(map(json.dumps, line)))
                line = []
            line.append(v)
        lines.append(", ".join(map(json.dumps, line)))
        return "[\n" + ",\n".join(pad + l for l in lines) + "\n" + " " * indent + "]"
    if isinstance(obj, dict):
        items = [f"{pad}{json.dumps(k)}: {compact_json(v, indent + 2, width)}" for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    items = [pad + (json.dumps(v) if rows else compact_json(v, indent + 2, width)) for v in obj]
    return "[\n" + ",\n".join(items) + "\n" + " " * indent + "]"


def stage_spec(args):
    with open(f"{RESULTS_DIR}/characterization.json") as f:
        ref = json.load(f)["dbbench"]
    model = fit_model(ref, dbbench_arrays(DBBENCH_SCANLOG_DIR, "dbbench",
                                          f"{DBBENCH_SCANLOG_DIR}/mixgraph.scan_lengths.txt"))
    with open(f"{RESULTS_DIR}/model.json", "w") as f:
        json.dump(model, f, indent=1)
    spec = build_spec(ref, model)
    os.makedirs(SPEC_DIR, exist_ok=True)
    with open(SPEC_PATH, "w") as f:
        f.write(compact_json({"$schema": "../../workload_schema.json", **spec}) + "\n")
    with open(SPEC_PATH) as f:
        lines = sum(1 for _ in f)
    log(f"  model {json.dumps(model)}")
    log(f"  spec: {SPEC_PATH} ({os.path.getsize(SPEC_PATH) / 1024:.0f} KiB, {lines} lines)")


def stage_xbench(args):
    os.makedirs(XBENCH_DIR, exist_ok=True)
    trace = f"{XBENCH_DIR}/mixgraph.txt"
    shutil.copy2(SPEC_PATH, f"{XBENCH_DIR}/prefix_dist.spec.json")
    wall = run([TECTONIC_CLI, "generate", "-w", SPEC_PATH, "-o", trace], f"{XBENCH_DIR}/generate.out")
    with open(f"{RESULTS_DIR}/xbench.json", "w") as f:
        json.dump({"spec": SPEC_PATH, "trace": trace, "generate_wall_s": wall, "trace_sha256": sha256_file(trace),
                   "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=2)
    # the arrays and the executor's copy of the previous trace are stale now
    for path in (f"{ARRAYS_DIR}/xbench.npz", f"{EXEC_DIR}/xbench.marked.txt"):
        if os.path.exists(path):
            os.remove(path)


def xbench_arrays(trace, side, load_ops):
    """X-Bench-format trace (I key value / BP key / BR key count) -> the same arrays as db_bench's."""
    import numpy as np
    path = f"{ARRAYS_DIR}/{side}.npz"
    if os.path.exists(path):
        return dict(np.load(path))
    n = int(subprocess.run(["wc", "-l", trace], stdout=subprocess.PIPE, text=True).stdout.split()[0]) - load_ops
    load_key = np.empty(load_ops, np.uint32)
    load_vsz = np.empty(load_ops, np.uint16)
    typ = np.empty(n, np.uint8)
    key = np.empty(n, np.uint32)
    vsz = np.zeros(n, np.uint16)
    scan = np.zeros(n, np.uint32)
    with open(trace, "rb") as f:
        for i, line in enumerate(f):
            parts = line.split()
            if i < load_ops:
                load_key[i] = int(parts[1][:KEY_WIDTH])
                load_vsz[i] = len(parts[2])
                continue
            j = i - load_ops
            key[j] = int(parts[1][:KEY_WIDTH])
            op = parts[0]
            if op == b"I":
                typ[j], vsz[j] = 1, len(parts[2])
            elif op == b"BP":
                typ[j] = 0
            elif op == b"BR":
                typ[j], scan[j] = 6, int(parts[2])
            else:
                fail(f"{trace}:{i + 1}: unexpected operation {op!r}")
    arrays = {"load_key": load_key, "load_vsz": load_vsz, "typ": typ, "key": key, "vsz": vsz, "scan": scan,
              "has_scan": np.array(True)}
    os.makedirs(ARRAYS_DIR, exist_ok=True)
    np.savez(path, **arrays)
    return arrays


def hist_ks(a, b):
    """Two-sample KS statistic of two histograms over the same bins."""
    import numpy as np
    n = max(len(a), len(b))
    ca = np.cumsum(np.pad(np.asarray(a, float), (0, n - len(a))))
    cb = np.cumsum(np.pad(np.asarray(b, float), (0, n - len(b))))
    return float(np.max(np.abs(ca / ca[-1] - cb / cb[-1])))


def hist_mean(h):
    import numpy as np
    h = np.asarray(h, float)
    return float((h * np.arange(len(h))).sum() / h.sum())


def distances(ref, other):
    """Distance of every property of `other` to the reference trace's."""
    d = {}
    d["op_mix_per_window_max_abs_diff"] = max(
        abs(x - y) for t in ref["op_mix_per_window"] for x, y in zip(ref["op_mix_per_window"][t], other["op_mix_per_window"][t]))
    d["op_share"] = {t: (other["op_counts"][t] / other["operations"]) - (ref["op_counts"][t] / ref["operations"])
                     for t in ref["op_counts"]}
    d["load_distinct_keys"] = other["load_distinct_keys"] - ref["load_distinct_keys"]
    for k in ("value_size_load", "value_size_run"):
        d[k] = {"ks": hist_ks(ref[k], other[k]), "mean_ref": hist_mean(ref[k]), "mean": hist_mean(other[k])}
    d["keyrange_share_tvd"] = {t: 0.5 * sum(abs(x - y) for x, y in zip(ref["keyrange_share"][t], other["keyrange_share"][t]))
                               for t in ref["keyrange_share"]}
    d["access_count_per_key_ks"] = {t: hist_ks(ref["access_count_hist"][t], other["access_count_hist"][t])
                                    for t in ref["access_count_hist"]}
    d["keys_accessed"] = {"ref": sum(ref["access_count_hist"]["all"]), "other": sum(other["access_count_hist"]["all"])}
    d["reuse_distance_ks"] = hist_ks(ref["reuse_distance_hist"], other["reuse_distance_hist"])
    d["reuse_distance_median"] = {"ref": ref["reuse_distance_median"], "other": other["reuse_distance_median"]}
    d["first_access_fraction"] = {"ref": ref["first_access_fraction"], "other": other["first_access_fraction"]}
    d["get_found_ratio"] = {"ref": ref["get_found_ratio"], "other": other["get_found_ratio"]}
    d["key_groups"] = {"ref": ref["key_groups"], "other": other["key_groups"]}
    d["put_distinct_key_size_pairs"] = {"ref": ref.get("put_distinct_key_size_pairs"),
                                         "other": other.get("put_distinct_key_size_pairs")}
    d["seek_distinct_key_length_pairs"] = {"ref": ref.get("seek_distinct_key_length_pairs"),
                                            "other": other.get("seek_distinct_key_length_pairs")}
    if "scan_length_hist" in other:
        d["scan_length"] = {"ks": hist_ks(ref["scan_length_hist"], other["scan_length_hist"]),
                            "mean_ref": ref["scan_length_mean"], "mean": other["scan_length_mean"]}
    return d


def stage_distance(args):
    with open(f"{RESULTS_DIR}/characterization.json") as f:
        chars = json.load(f)
    ref = chars["dbbench"]
    log("  characterize xbench")
    chars["xbench"] = characterize(xbench_arrays(f"{XBENCH_DIR}/mixgraph.txt", "xbench", ref["load_puts"]))
    with open(f"{RESULTS_DIR}/characterization.json", "w") as f:
        json.dump(chars, f)
    out = {side: distances(ref, chars[side]) for side in ("xbench", "dbbench_rerun")}
    with open(f"{RESULTS_DIR}/distances.json", "w") as f:
        json.dump(out, f, indent=2)
    for side, d in out.items():
        log(f"  {side}: {json.dumps(d)}")


# =============================================================================
#  STEP 4a: BEST-EFFORT YCSB RECONSTRUCTION
# =============================================================================
# YCSB (rocksdb-benchmark-harness/vendor/YCSB, 0.18 with the FileClient binding, as for the paper's YCSB
# traces) configured as close to the trace as its properties allow. What YCSB cannot express: misses (its
# reads always hit), key-range locality (its keys are hashed over the key space), separate load and run
# value sizes (one field length distribution), and the trace's per-key coupling of operation types.
YCSB_DIR = f"{ROOT_DIR}/rocksdb-benchmark-harness/vendor/YCSB"
M2 = "/home/cc/.m2/repository"
YCSB_CP = ":".join([
    f"{YCSB_DIR}/file/conf", f"{YCSB_DIR}/file/target/file-binding-0.18.0-SNAPSHOT.jar",
    f"{M2}/org/apache/htrace/htrace-core4/4.1.0-incubating/htrace-core4-4.1.0-incubating.jar",
    f"{M2}/org/hdrhistogram/HdrHistogram/2.1.12/HdrHistogram-2.1.12.jar",
    f"{M2}/org/codehaus/jackson/jackson-mapper-asl/1.9.4/jackson-mapper-asl-1.9.4.jar",
    f"{M2}/org/codehaus/jackson/jackson-core-asl/1.9.4/jackson-core-asl-1.9.4.jar",
    f"{YCSB_DIR}/core/target/core-0.18.0-SNAPSHOT.jar"])
YCSB_OUT = f"{OUT_DIR}/ycsb"
YCSB_FIELD_OVERHEAD = len("{field0=}")  # FileClient writes a value as {field0=<field>}
# YCSB's zipfian scan length over [1, maxscanlength]: 5000 gives the trace's mean scan length (554 vs. 566;
# KS 0.118; the KS-best 3000 has mean 351). Its uniform scan length with the same mean has KS 0.60.
YCSB_MAX_SCAN_LENGTH = 5000
YCSB_TABLE = "u"  # key "u:user" + 42 digits: 48 bytes, like db_bench's


def ycsb_properties(ref, arrays, request_distribution):
    import numpy as np
    typ, key = arrays["typ"], arrays["key"]
    loaded = np.zeros(NUM, bool)
    loaded[arrays["load_key"]] = True
    puts = np.nonzero(typ == 1)[0]
    first = np.full(NUM, np.iinfo(np.int64).max, np.int64)
    np.minimum.at(first, key[puts], puts)
    new_key_puts = float(((~loaded[key[puts]]) & (first[key[puts]] == puts)).mean())
    n = ref["operations"]
    put_share = ref["op_counts"]["Put"] / n
    props = {
        "workload": "site.ycsb.workloads.CoreWorkload",
        # db_bench's fillrandom leaves this many distinct keys; YCSB loads each record once
        "recordcount": ref["load_distinct_keys"], "operationcount": n,
        "readproportion": ref["op_counts"]["Get"] / n, "scanproportion": ref["op_counts"]["Seek"] / n,
        # Puts to keys that exist are updates, Puts to new keys inserts
        "updateproportion": put_share * (1 - new_key_puts), "insertproportion": put_share * new_key_puts,
        "readmodifywriteproportion": 0, "requestdistribution": request_distribution,
        "fieldcount": 1, "readallfields": "true", "writeallfields": "true",
        "fieldlengthdistribution": "histogram", "fieldlengthhistogram": f"{YCSB_OUT}/fieldlength_histogram.txt",
        "scanlengthdistribution": "zipfian", "minscanlength": 1, "maxscanlength": YCSB_MAX_SCAN_LENGTH,
        "insertorder": "hashed", "zeropadding": 48 - len(YCSB_TABLE) - 1 - len("user"), "table": YCSB_TABLE,
    }
    if request_distribution == "hotspot":
        # the trace's hottest key range: 1/30 of the key space receives this share of the operations
        props["hotspotdatafraction"] = 1 / KEYRANGE_NUM
        props["hotspotopnfraction"] = max(ref["keyrange_share"]["all"])
    return props, new_key_puts


def write_field_length_histogram(ref, path):
    """One field length distribution for load and run: the value sizes of all the trace's Puts (load and
    run), minus FileClient's "{field0=}". HistogramGenerator returns (bucket + 1) * BlockSize, so length L
    goes into bucket L - 1."""
    sizes = {}
    for hist in (ref["value_size_load"], ref["value_size_run"]):
        for L, n in enumerate(hist):
            if n:
                sizes[L - YCSB_FIELD_OVERHEAD] = sizes.get(L - YCSB_FIELD_OVERHEAD, 0) + n
    top = max(sizes)
    with open(path, "w") as f:
        f.write("BlockSize\t1\n")
        for bucket in range(top):
            f.write(f"{bucket}\t{sizes.get(bucket + 1, 0)}\n")


def ycsb_generate(props, trace, out_dir):
    if os.path.exists(trace):
        os.remove(trace)
    os.makedirs(out_dir, exist_ok=True)
    phases = []
    for phase, flag in (("load", "-load"), ("run", "-t")):
        cmd = ["java", "-cp", YCSB_CP, "site.ycsb.Client", "-db", "site.ycsb.db.FileClient",
               "-p", f"file.output={trace}"]
        for k, v in props.items():
            cmd += ["-p", f"{k}={v}"]
        cmd.append(flag)
        phases.append({"phase": phase, "wall_s": run(cmd, f"{out_dir}/ycsb_{phase}.out")})
    return phases


def ycsb_arrays(trace, side, load_ops):
    """YCSB FileClient trace (I / U / P / S lines) -> the same arrays as db_bench's. Key ids: the rank of
    the key among all of the trace's distinct keys, spread over [0, num), so that key order is kept and
    key ranges are num/30 ids wide, as for db_bench."""
    import numpy as np
    path = f"{ARRAYS_DIR}/{side}.npz"
    if os.path.exists(path):
        return dict(np.load(path))
    names, ops, vszs, scans = [], [], [], []
    code = {b"I": 1, b"U": 1, b"P": 0, b"S": 6}
    with open(trace, "rb") as f:
        for line in f:
            op, k, rest = line.rstrip(b"\n").split(b" ", 2) if line.count(b" ") >= 2 else (*line.split(), b"")
            names.append(int(k[len(YCSB_TABLE) + 1 + len("user"):]))
            ops.append(code[op])
            vszs.append(len(rest) if op in (b"I", b"U") else 0)
            scans.append(int(rest) if op == b"S" else 0)
    names = np.array(names, np.int64)
    uniq, ids = np.unique(names, return_inverse=True)
    ids = (ids * NUM // len(uniq)).astype(np.uint32)
    typ = np.array(ops, np.uint8)
    vsz = np.array(vszs, np.uint16)
    scan = np.array(scans, np.uint32)
    arrays = {"load_key": ids[:load_ops], "load_vsz": vsz[:load_ops], "typ": typ[load_ops:], "key": ids[load_ops:],
              "vsz": vsz[load_ops:], "scan": scan[load_ops:], "has_scan": np.array(True)}
    os.makedirs(ARRAYS_DIR, exist_ok=True)
    np.savez(path, **arrays)
    return arrays


YCSB_CANDIDATES = ("uniform", "zipfian", "hotspot", "exponential")


def stage_ycsb(args):
    """Generate YCSB with each request distribution Cao et al. compare against (uniform, zipfian, hotspot,
    exponential), keep the one whose trace is closest to db_bench's (sum of the key-range TVD, the per-key
    access-count KS and the reuse-distance KS)."""
    with open(f"{RESULTS_DIR}/characterization.json") as f:
        chars = json.load(f)
    ref = chars["dbbench"]
    arrays = dbbench_arrays(DBBENCH_SCANLOG_DIR, "dbbench", f"{DBBENCH_SCANLOG_DIR}/mixgraph.scan_lengths.txt")
    os.makedirs(YCSB_OUT, exist_ok=True)
    write_field_length_histogram(ref, f"{YCSB_OUT}/fieldlength_histogram.txt")
    result = {"candidates": {}}
    for dist in YCSB_CANDIDATES:
        log(f"\n=== YCSB {dist} ===")
        props, new_key_puts = ycsb_properties(ref, arrays, dist)
        trace = f"{YCSB_OUT}/{dist}/mixgraph.txt"
        phases = ycsb_generate(props, trace, f"{YCSB_OUT}/{dist}")
        side = f"ycsb_{dist}"
        a = ycsb_arrays(trace, side, props["recordcount"])
        chars[side] = characterize(a)
        d = distances(ref, chars[side])
        score = d["keyrange_share_tvd"]["all"] + d["access_count_per_key_ks"]["all"] + d["reuse_distance_ks"]
        result["candidates"][dist] = {"properties": props, "new_key_put_share": new_key_puts, "phases": phases,
                                      "trace": trace, "distances": d, "score": score}
        log(f"  score {score:.4f}: key-range TVD {d['keyrange_share_tvd']['all']:.4f}, access-count KS "
            f"{d['access_count_per_key_ks']['all']:.4f}, reuse KS {d['reuse_distance_ks']:.4f}")
    best = min(result["candidates"], key=lambda k: result["candidates"][k]["score"])
    result["chosen"] = best
    log(f"\nYCSB reconstruction: requestdistribution={best}")
    with open(f"{RESULTS_DIR}/characterization.json", "w") as f:
        json.dump(chars, f)
    with open(f"{RESULTS_DIR}/ycsb.json", "w") as f:
        json.dump(result, f, indent=2)


# =============================================================================
#  STEP 4b: EXECUTE ON ROCKSDB
# =============================================================================
# The same executor and harness as R1.O7 (run_ycsb_derivatives_fidelity.py): tectonic-cli execute on
# RocksDB, 1 thread, statistics on; per trial a fresh database, the trace in /dev/shm, dropped caches,
# iostat of the device; stat markers around the load and each of RUN_WINDOWS windows of the run.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_ycsb_derivatives_fidelity as r1o7  # noqa: E402

EXEC_SIDES = ("dbbench", "xbench", "ycsb")
RUN_WINDOWS = 10
TRIALS = 5
EXEC_DIR = f"{OUT_DIR}/execute"
DBSTATE_DIR = f"{OUT_DIR}/db_state"
ROCKSDB_DIR = f"{OUT_DIR}/rocksdb_data"
SHM_TRACE = "/dev/shm/mixgraph_trace.txt"
EXEC_PATH = f"{RESULTS_DIR}/execution.json"


def markers(load_ops, n):
    """{line: marker lines to write before it} for a trace with `load_ops` load lines and `n` lines in all:
    one window for the load, RUN_WINDOWS windows of equal size for the run."""
    bounds = [0] + [load_ops + (n - load_ops) * w // RUN_WINDOWS for w in range(RUN_WINDOWS + 1)]
    names = ["load"] + [f"run.w{w + 1}" for w in range(RUN_WINDOWS)]
    out = {}
    for i, name in enumerate(names):
        out[bounds[i]] = out.get(bounds[i], b"") + b"FS G\n"
        out[bounds[i + 1]] = f"FE G {name}\n".encode() + out.get(bounds[i + 1], b"")
    return out


def dbbench_trace_for_executor(arrays, dst):
    """The db_bench trace in the executor's format, with markers: load and Put -> I key value, Get -> BP
    key, Seek -> BR key count (the scan length db_bench asked for). Keys like X-Bench's (key id, 16 digits,
    + 32 '0'); values: random alphanumeric bytes of the recorded size, as X-Bench writes them (the tracer
    records sizes, not values)."""
    import numpy as np
    rng = np.random.default_rng(20261023)
    alnum = np.frombuffer(b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", np.uint8)
    load_ops = len(arrays["load_key"])
    typ = np.concatenate([np.ones(load_ops, np.uint8), arrays["typ"]])
    key = np.concatenate([arrays["load_key"], arrays["key"]])
    vsz = np.concatenate([arrays["load_vsz"], arrays["vsz"]]).astype(np.int64)
    scan = np.concatenate([np.zeros(load_ops, np.uint32), arrays["scan"]])
    n = len(typ)
    marks = markers(load_ops, n)
    values = alnum[rng.integers(0, len(alnum), int(vsz.sum()))].tobytes()
    vstart = np.concatenate([[0], np.cumsum(vsz)])
    with open(dst + ".tmp", "wb") as f:
        for i in range(n):
            if i in marks:
                f.write(marks[i])
            k = f"{int(key[i]):0{KEY_WIDTH}d}{KEY_PAD}".encode()
            t = typ[i]
            if t == 1:
                f.write(b"I " + k + b" " + values[vstart[i]:vstart[i + 1]] + b"\n")
            elif t == 0:
                f.write(b"BP " + k + b"\n")
            else:
                f.write(b"BR " + k + b" " + str(int(scan[i])).encode() + b"\n")
        f.write(marks[n])
    os.replace(dst + ".tmp", dst)


def marked_copy(src, dst, load_ops, scan_count=False):
    """The trace with markers. scan_count: YCSB's FileClient writes a scan as `S <start key> <count>`,
    which the executor reads as `S <start key> <end key>`; its count form is `SC <start key> <count>`."""
    n = int(subprocess.run(["wc", "-l", src], stdout=subprocess.PIPE, text=True).stdout.split()[0])
    marks = markers(load_ops, n)
    with open(src, "rb") as fin, open(dst + ".tmp", "wb") as fout:
        for ln, line in enumerate(fin):
            if ln in marks:
                fout.write(marks[ln])
            if scan_count and line[:2] == b"S ":
                line = b"SC " + line[2:]
            fout.write(line)
        fout.write(marks[n])
    os.replace(dst + ".tmp", dst)


def executor_traces():
    with open(f"{RESULTS_DIR}/characterization.json") as f:
        ref = json.load(f)["dbbench"]
    with open(f"{RESULTS_DIR}/ycsb.json") as f:
        ycsb = json.load(f)
    best = ycsb["candidates"][ycsb["chosen"]]
    os.makedirs(EXEC_DIR, exist_ok=True)
    traces = {side: f"{EXEC_DIR}/{side}.marked.txt" for side in EXEC_SIDES}
    if not os.path.exists(traces["dbbench"]):
        log("  writing the db_bench trace in the executor's format")
        dbbench_trace_for_executor(dbbench_arrays(DBBENCH_SCANLOG_DIR, "dbbench",
                                                  f"{DBBENCH_SCANLOG_DIR}/mixgraph.scan_lengths.txt"),
                                   traces["dbbench"])
    if not os.path.exists(traces["xbench"]):
        marked_copy(f"{XBENCH_DIR}/mixgraph.txt", traces["xbench"], ref["load_puts"])
    if not os.path.exists(traces["ycsb"]):
        marked_copy(best["trace"], traces["ycsb"], best["properties"]["recordcount"], scan_count=True)
    return traces


def execute_trial(side, trace, trial):
    if os.path.exists(ROCKSDB_DIR):
        shutil.rmtree(ROCKSDB_DIR)
    os.makedirs(ROCKSDB_DIR)
    out_dir = f"{DBSTATE_DIR}/{side}/trial{trial}"
    os.makedirs(out_dir, exist_ok=True)
    shutil.copyfile(trace, SHM_TRACE)
    r1o7.drop_caches()
    cmd = [TECTONIC_CLI, "execute", "-i", SHM_TRACE, "-d", "rocksdb", "-p", ROCKSDB_DIR, "-t", "1",
           "-c", f"scan_log={out_dir}/range_query_keys.txt;statistics=1"]
    log(f"  $ {' '.join(cmd)}   (trace: {trace})")
    iostat = r1o7.Iostat(r1o7.IOSTAT_DEVICE)
    t0 = time.perf_counter()
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    wall = time.perf_counter() - t0
    subprocess.run(["sync"], check=True)
    time.sleep(2)
    samples = iostat.stop()
    os.remove(SHM_TRACE)
    with open(f"{out_dir}/iostat.csv", "w") as f:
        f.write("second,read_MBps,write_MBps\n")
        for t, (rd, wr) in enumerate(samples):
            f.write(f"{t},{rd},{wr}\n")
    with open(f"{out_dir}/cli_raw.log", "w") as f:
        f.write(r.stdout)
    if r.returncode != 0 or "[ERROR]" in r.stderr:
        log(r.stderr[-3000:])
        fail(f"execute failed for {side} trial {trial}")
    ops, overall, read_stats, rocks, windows = r1o7.parse_execute_output(r.stdout)
    tickers = r1o7.parse_rocksdb_statistics(r.stdout)
    if not tickers:
        fail(f"no RocksDB statistics for {side} trial {trial}")
    shutil.copy2(f"{ROCKSDB_DIR}/LOG", f"{out_dir}/rocksdb_LOG.txt")
    sst = [f for f in os.listdir(ROCKSDB_DIR) if f.endswith(".sst")]
    rocks["SST total size (MB)"] = sum(os.path.getsize(f"{ROCKSDB_DIR}/{f}") for f in sst) / 2 ** 20
    if trial > 1:
        os.remove(f"{out_dir}/range_query_keys.txt")
    return {"trial": trial, "wall_time_s": wall, "end_to_end_time_s": overall.get("End to End Time"),
            "overall": overall, "operations": ops, "windows": windows, "read_stats": read_stats,
            "rocksdb": rocks, "rocksdb_statistics": tickers,
            "device_io": {"device": r1o7.IOSTAT_DEVICE, "read_MBps": [x for x, _ in samples],
                          "write_MBps": [y for _, y in samples]},
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}


def stage_execute(args):
    """TRIALS rounds; each round runs one trial of every trace, the order rotated every round."""
    traces = executor_traces()
    results = {}
    if os.path.exists(EXEC_PATH):
        with open(EXEC_PATH) as f:
            results = json.load(f)
    for rnd in range(1, TRIALS + 1):
        order = EXEC_SIDES[(rnd - 1) % len(EXEC_SIDES):] + EXEC_SIDES[:(rnd - 1) % len(EXEC_SIDES)]
        for pos, side in enumerate(order, 1):
            done = results.setdefault(side, [])
            if any(t["trial"] == rnd for t in done):
                continue
            log(f"\n=== EXECUTE round {rnd}/{TRIALS}: {pos}. {side} ===")
            rec = execute_trial(side, traces[side], rnd)
            rec["position"] = pos
            done.append(rec)
            with open(EXEC_PATH + ".tmp", "w") as f:
                json.dump(results, f)
            os.replace(EXEC_PATH + ".tmp", EXEC_PATH)
            log(f"  -> {rec['end_to_end_time_s']:.1f} s, {rec['overall'].get('Throughput (using start and end time)', 0):.0f} ops/s")
    if os.path.exists(ROCKSDB_DIR):
        shutil.rmtree(ROCKSDB_DIR)


# =============================================================================
#  REPORT
# =============================================================================
REPORT_PATH = f"{OUT_DIR}/report.md"
LAT = (("p50", "50th Percentile Latency"), ("p99", "99th Percentile Latency"), ("mean", "Average Latency"))


def mean_sd(values):
    import statistics
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)


def fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, tuple):
        return "-" if v[0] is None else f"{v[0]:,.{nd}f} ± {v[1]:,.{nd}f}"
    return f"{v:,.{nd}f}"


def pct(ref, x):
    return None if ref in (None, 0) or x is None else (x - ref) / ref * 100


def stage_report(args):
    with open(f"{RESULTS_DIR}/characterization.json") as f:
        chars = json.load(f)
    with open(f"{RESULTS_DIR}/ycsb.json") as f:
        ycsb = json.load(f)
    ref = chars["dbbench"]
    others = {"X-Bench": chars["xbench"], f"YCSB ({ycsb['chosen']})": chars[f"ycsb_{ycsb['chosen']}"],
              "db_bench rerun": chars["dbbench_rerun"]}
    dist = {name: distances(ref, c) for name, c in others.items()}
    L = ["# X-Bench vs. db_bench mixgraph Prefix_dist (R1.O9 / R2.O1 / R2.Q1)", "",
         f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')} by `run_scripts/run_mixgraph_fidelity.py --stage report`.",
         "", "Reference: the db_bench run with the scan-length log (`dbbench-scanlog/`). *db_bench rerun*: the "
         "unmodified db_bench run (`dbbench/`), an independent draw of the same benchmark (no scan lengths): "
         "the noise floor. YCSB: the best-effort reconstruction (`results/ycsb.json`).", "",
         "## 1. Workload properties: distance to the db_bench trace", "",
         "KS: two-sample Kolmogorov-Smirnov statistic; TVD: total variation distance.", "",
         "| Property | " + " | ".join(dist) + " |", "| --- |" + " --- |" * len(dist)]

    def row(label, f):
        L.append(f"| {label} | " + " | ".join(f(d) for d in dist.values()) + " |")

    row("Op mix per window (max abs. diff, 42 windows)", lambda d: f"{d['op_mix_per_window_max_abs_diff']:.4f}")
    row("Value size of load Puts (KS)", lambda d: f"{d['value_size_load']['ks']:.4f}")
    row("Value size of run Puts (KS; mean, ref " + f"{dist['X-Bench']['value_size_run']['mean_ref']:.1f} B)",
        lambda d: f"{d['value_size_run']['ks']:.4f} ({d['value_size_run']['mean']:.1f} B)")
    row("Key-range share of all operations (TVD)", lambda d: f"{d['keyrange_share_tvd']['all']:.4f}")
    for t in ("Get", "Put", "Seek"):
        row(f"Key-range share of {t}s (TVD)", lambda d, t=t: f"{d['keyrange_share_tvd'][t]:.4f}")
    row("Per-key access count, all operations (KS)", lambda d: f"{d['access_count_per_key_ks']['all']:.4f}")
    for t in ("Get", "Put", "Seek"):
        row(f"Per-key access count, {t}s (KS)", lambda d, t=t: f"{d['access_count_per_key_ks'][t]:.4f}")
    row(f"Distinct keys accessed (ref {dist['X-Bench']['keys_accessed']['ref']:,})",
        lambda d: f"{d['keys_accessed']['other']:,}")
    row("Reuse distance (KS)", lambda d: f"{d['reuse_distance_ks']:.4f}")
    row(f"Reuse distance median (ref {dist['X-Bench']['reuse_distance_median']['ref']:,.0f} ops)",
        lambda d: f"{d['reuse_distance_median']['other']:,.0f}")
    row(f"Gets that find their key (ref {100 * dist['X-Bench']['get_found_ratio']['ref']:.2f}%)",
        lambda d: f"{100 * d['get_found_ratio']['other']:.2f}%")
    row(f"Scan length (KS; mean, ref {dist['X-Bench']['scan_length']['mean_ref']:.1f})",
        lambda d: f"{d['scan_length']['ks']:.4f} ({d['scan_length']['mean']:.1f})" if "scan_length" in d else "-")
    groups = ref["key_groups"]
    L += ["", "Keys by the operation types they receive (share of the accessed keys / of the operations):", "",
          "| Group | db_bench | " + " | ".join(others) + " |", "| --- | --- |" + " --- |" * len(others)]
    for g in groups:
        cells = [f"{100 * c['key_groups'][g]['keys']:.1f}% / {100 * c['key_groups'][g]['ops']:.1f}%"
                 for c in [ref, *others.values()]]
        L.append(f"| {g} | " + " | ".join(cells) + " |")
    L += ["", "YCSB request distributions tried (score: key-range TVD + access-count KS + reuse KS):", "",
          "| requestdistribution | key-range TVD | access-count KS | reuse KS | score |", "| --- | --- | --- | --- | --- |"]
    for dist_name, c in ycsb["candidates"].items():
        d = c["distances"]
        L.append(f"| {dist_name}{' (chosen)' if dist_name == ycsb['chosen'] else ''} | {d['keyrange_share_tvd']['all']:.4f} | "
                 f"{d['access_count_per_key_ks']['all']:.4f} | {d['reuse_distance_ks']:.4f} | {c['score']:.4f} |")

    if os.path.exists(EXEC_PATH):
        with open(EXEC_PATH) as f:
            ex = json.load(f)
        names = {"dbbench": "db_bench trace", "xbench": "X-Bench", "ycsb": "YCSB"}
        sides = [s_ for s_ in EXEC_SIDES if ex.get(s_)]
        L += ["", "## 2. Execution on RocksDB", "",
              f"`tectonic-cli execute` (same binary, options and harness as R1.O7), {len(ex[sides[0]])} trials per "
              "trace in rounds with rotated order; mean ± stdev; Diff to the db_bench trace.", "",
              "| Metric | " + " | ".join(f"{names[s_]} | Diff" if s_ != "dbbench" else names[s_] for s_ in sides) + " |",
              "| --- |" + " --- |" * (2 * len(sides) - 1)]

        def exrow(label, get, nd=2):
            vals = {s_: mean_sd([get(t) for t in ex[s_]]) for s_ in sides}
            cells = []
            for s_ in sides:
                cells.append(fmt(vals[s_], nd))
                if s_ != "dbbench":
                    p_ = pct(vals["dbbench"][0], vals[s_][0])
                    cells.append("-" if p_ is None else f"{p_:+.2f}%")
            L.append(f"| {label} | " + " | ".join(cells) + " |")

        exrow("Throughput (ops/s)", lambda t: t["overall"].get("Throughput (using start and end time)"), 0)
        exrow("End-to-end time (s)", lambda t: t["end_to_end_time_s"], 1)
        exrow("RocksDB read (GB)", lambda t: sum(t["rocksdb_statistics"][k] for k in r1o7.FIG12_READ_TICKERS) / r1o7.GIB, 3)
        exrow("RocksDB write (GB)", lambda t: sum(t["rocksdb_statistics"][k] for k in r1o7.FIG12_WRITE_TICKERS) / r1o7.GIB, 3)
        for k in r1o7.FIG12_READ_TICKERS + r1o7.FIG12_WRITE_TICKERS:
            exrow(f"`{k}` (GB)", lambda t, k=k: t["rocksdb_statistics"][k] / r1o7.GIB, 3)
        exrow("Point query hits (%)", lambda t: 100 * t["read_stats"].get("Point query hits", 0) /
              max(1, t["operations"].get("Point Query", {}).get("Count", 0)), 2)
        exrow("Range query keys returned (M)", lambda t: t["read_stats"].get("Range query keys returned", 0) / 1e6, 2)
        exrow("Block cache hits (M)", lambda t: t["rocksdb_statistics"].get("rocksdb.block.cache.hit", 0) / 1e6, 2)
        exrow("Block cache misses (M)", lambda t: t["rocksdb_statistics"].get("rocksdb.block.cache.miss", 0) / 1e6, 2)
        exrow("SST size at the end (MB)", lambda t: t["rocksdb"].get("SST total size (MB)"), 1)
        ops = sorted({op for s_ in sides for op in ex[s_][0]["operations"]})
        for op in ops:
            for label, metric in LAT:
                exrow(f"{op} {label} latency, whole run (µs)",
                      lambda t, op=op, metric=metric: t["operations"].get(op, {}).get(metric), 1)
        L += ["", "### Per-phase latency (µs, mean over the trials): load and the run in "
              f"{RUN_WINDOWS} windows of equal size", "",
              "| Window | Operation | Percentile | " + " | ".join(names[s_] for s_ in sides) + " |",
              "| --- | --- | --- |" + " --- |" * len(sides)]
        for w in ex[sides[0]][0]["windows"]:
            for op in ops:
                for label, metric in LAT[:2]:
                    vals = [mean_sd([t["windows"].get(w, {}).get("operations", {}).get(op, {}).get(metric) for t in ex[s_]])[0]
                            for s_ in sides]
                    if all(v is None for v in vals):
                        continue
                    L.append(f"| {w} | {op} | {label} | " + " | ".join(fmt(v, 1) for v in vals) + " |")
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(L) + "\n")
    log(f"  report: {REPORT_PATH}")


def main():
    global _log_fh
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["dbbench", "dbbench-scanlog", "scan-lengths", "characterize", "spec", "xbench", "distance", "ycsb", "traces", "execute", "report"], default="dbbench")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    _log_fh = open(LOG_PATH, "a")
    log(f"\n{'=' * 70}\n  run_mixgraph_fidelity.py  stage={args.stage}  {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'=' * 70}")
    if args.stage == "dbbench":
        stage_dbbench(args)
    if args.stage == "dbbench-scanlog":
        stage_dbbench_scanlog(args)
    if args.stage == "scan-lengths":
        stage_scan_lengths(args)
    if args.stage == "characterize":
        stage_characterize(args)
    if args.stage == "spec":
        stage_spec(args)
    if args.stage == "xbench":
        stage_xbench(args)
    if args.stage == "distance":
        stage_distance(args)
    if args.stage == "ycsb":
        stage_ycsb(args)
    if args.stage == "traces":
        executor_traces()
    if args.stage == "execute":
        stage_execute(args)
    if args.stage == "report":
        stage_report(args)


if __name__ == "__main__":
    main()
