#!/usr/bin/env python3
"""
run_io_tool_match.py — which measurement tool reports the bytes RocksDB's statistics report?
=============================================================================================
R1.O7 follow-up (2026-10-30). The R1.O7 plots compare iostat (device bytes over time) with
RocksDB statistics tickers (bytes RocksDB reads and writes); the two do not agree because the
page cache sits between them. This script reruns R1.O7 trials exactly as
run_ycsb_derivatives_fidelity.py ran them (same executor command, trace in /dev/shm, page cache
dropped, fresh database directory, `-c statistics=1`) and measures every trial at four layers:

  1. bpftrace on vfs_read/vfs_readv/vfs_iter_read/vfs_write/vfs_writev/vfs_iter_write, bytes per
     file name per second, for the executor's cgroup only (file-system layer, above the page cache);
  2. /proc/<pid>/io of the executor every second: rchar/wchar (bytes through read/write system calls),
     read_bytes/write_bytes/cancelled_write_bytes (bytes the process caused to be read from or
     written to storage, and dirty bytes it dropped before writeback);
  3. cgroup v2 io.stat of the executor's cgroup, device 8:0 (block layer, writeback attributed);
  4. /proc/diskstats of sda (device, exact) and `iostat -m -x -d -y sda 1` (device, as R1.O7).

Modes per trial: "bpftrace" (all four) and "plain" (2-4, no bpftrace) to show bpftrace does not
change what RocksDB does. Runs as root (drop_caches, cgroup, bpftrace); the executor runs as the
invoking user through setpriv, in cgroup /sys/fs/cgroup/io-tool-match.

Usage (on the run machine): sudo python3 run_io_tool_match.py --work /home/cc/r1o7-io-tool-match
"""
import os
import re
import sys
import json
import time
import shutil
import signal
import hashlib
import argparse
import datetime
import threading
import subprocess

DEVICE = "sda"
CGROUP = "/sys/fs/cgroup/io-tool-match"
SHM_TRACE = "/dev/shm/r1o7_trace.txt"  # as R1.O7: the executor reads its trace from RAM
SHM_SCAN_LOG = "/dev/shm/r1o7_range_query_keys.txt"  # kept off the device, moved after the trial
WORKLOADS = ("ivs-uniform", "ivs-uniform-x10", "ts-energy")
SIDES = ("ycsb-derivative", "xbench")
# (trial number, mode): trial 1 without bpftrace, trials 2-3 with it
TRIALS = ((1, "plain"), (2, "bpftrace"), (3, "bpftrace"))

# Bytes per (phase, op, file name) per second, for the executor's cgroup only. The phase counts the
# executor's large writes (>= 4 KiB) to its stdout pipe: the first is the "rocksdb.stats" dump, printed
# right before the statistics tickers, so phase 0 is everything RocksDB did before its counters were read.
BPFTRACE_PROGRAM = """
BEGIN {{ @phase = 0; }}
kretfunc:vmlinux:vfs_write /cgroup == cgroupid("{cg}") && retval >= 4096 && str(args->file->f_path.dentry->d_name.name) == ""/ {{ @phase = @phase + 1; printf("MARK %lld %d\\n", nsecs, retval); }}
""" + "".join(
    f'kretfunc:vmlinux:{fn} /cgroup == cgroupid("{{cg}}") && retval > 0/ {{{{ $p = @phase; '
    f'@b[$p, "{op}", str(args->file->f_path.dentry->d_name.name)] = sum(retval); '
    f'@n[$p, "{op}", str(args->file->f_path.dentry->d_name.name)] = count(); }}}}\n'
    for fn, op in (("vfs_read", "r"), ("vfs_readv", "r"), ("vfs_iter_read", "r"),
                   ("vfs_write", "w"), ("vfs_writev", "w"), ("vfs_iter_write", "w"))) + """
interval:ms:1000 {{ printf("T %lld\\n", nsecs); print(@b); print(@n); clear(@b); clear(@n); }}
"""
BPF_LINE = re.compile(r"^@([bn])\[(\d+), ([rw]), (.*)\]: (\d+)$")


def log(msg):
    line = f"[{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def read_proc_io(pid):
    try:
        with open(f"/proc/{pid}/io") as f:
            return {k: int(v) for k, v in (line.split(": ") for line in f.read().split("\n") if line)}
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None


def read_diskstats():
    """Sectors read and written by DEVICE (fields 6 and 10 of /proc/diskstats), in bytes."""
    with open("/proc/diskstats") as f:
        for line in f:
            p = line.split()
            if p[2] == DEVICE:
                return {"read_bytes": int(p[5]) * 512, "write_bytes": int(p[9]) * 512}
    raise RuntimeError(f"{DEVICE} not in /proc/diskstats")


def read_cgroup_io():
    with open(f"{CGROUP}/io.stat") as f:
        for line in f:
            p = line.split()
            if p and p[0] == "8:0":
                return {k: int(v) for k, v in (kv.split("=") for kv in p[1:])}
    return {"rbytes": 0, "wbytes": 0, "rios": 0, "wios": 0, "dbytes": 0, "dios": 0}


class Sampler(threading.Thread):
    """Every second: /proc/<pid>/io, /proc/diskstats, cgroup io.stat, with CLOCK_MONOTONIC ns."""

    def __init__(self):
        super().__init__(daemon=True)
        self.pid = None
        self.samples = []
        self.stop_event = threading.Event()

    def sample(self, tag=""):
        rec = {"t_ns": time.monotonic_ns(), "tag": tag, "diskstats": read_diskstats(), "cgroup": read_cgroup_io()}
        if self.pid:
            rec["proc_io"] = read_proc_io(self.pid)
        self.samples.append(rec)

    def run(self):
        nxt = time.monotonic()
        while not self.stop_event.is_set():
            self.sample()
            nxt += 1.0
            self.stop_event.wait(max(0.0, nxt - time.monotonic()))


class Iostat:
    """`iostat -m -x -d -y <device> 1` as in run_ycsb_derivatives_fidelity.py."""

    def __init__(self):
        self.samples = []
        self.proc = subprocess.Popen(["iostat", "-m", "-x", "-d", "-y", DEVICE, "1"], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        cols = None
        for line in self.proc.stdout:
            parts = line.split()
            if parts and parts[0] == "Device":
                cols = parts
            elif cols and parts and parts[0] == DEVICE:
                self.samples.append((float(parts[cols.index("rMB/s")]), float(parts[cols.index("wMB/s")])))

    def stop(self):
        self.proc.terminate()
        self.proc.wait()
        self.thread.join(timeout=5)
        return self.samples


class Bpftrace:
    def __init__(self, out_path):
        prog = BPFTRACE_PROGRAM.format(cg=CGROUP)
        self.prog_path = out_path + ".bt"
        with open(self.prog_path, "w") as f:
            f.write(prog)
        self.out = open(out_path, "w")
        self.proc = subprocess.Popen(["bpftrace", self.prog_path], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        if not self.ready.wait(60):
            raise RuntimeError("bpftrace did not attach")

    def _read(self):
        for line in self.proc.stdout:
            self.out.write(line)
            if line.startswith("Attaching"):
                self.ready.set()
        self.out.close()

    def stop(self):
        time.sleep(1.2)  # one more interval so the last second is printed
        self.proc.send_signal(signal.SIGINT)
        self.proc.wait(timeout=30)
        self.thread.join(timeout=10)


def parse_bpftrace(path):
    """Per-interval {"t_ns", "bytes": {"phase|op|file": n}, "calls": {...}}, the MARK times, and totals.
    Each interval's maps are printed right after its "T" line; maps printed at exit (SIGINT) follow
    the last "T" line and are counted with the last interval."""
    intervals, marks = [], []
    cur = {"t_ns": None, "bytes": {}, "calls": {}}
    for line in open(path):
        line = line.rstrip("\n")
        if line.startswith("T "):
            cur = {"t_ns": int(line[2:]), "bytes": {}, "calls": {}}
            intervals.append(cur)
        elif line.startswith("MARK "):
            marks.append(int(line.split()[1]))
        else:
            m = BPF_LINE.match(line)
            if m:
                kind, phase, op, name, n = m.groups()
                d = cur["bytes" if kind == "b" else "calls"]
                key = f"{phase}|{op}|{name}"
                d[key] = d.get(key, 0) + int(n)
    totals = {}
    for iv in intervals:
        for key, n in iv["bytes"].items():
            totals[key] = totals.get(key, 0) + n
    return intervals, marks, totals


def category(name):
    """RocksDB file kinds by name (RocksDB's own naming), the trace, everything else."""
    if name.endswith(".sst"):
        return "sst"
    if re.fullmatch(r"\d+\.log", name):
        return "wal"
    if name.startswith("MANIFEST-"):
        return "manifest"
    if name in ("LOG",) or name.startswith("LOG.old"):
        return "info_log"
    if name.startswith("OPTIONS-") or name in ("CURRENT", "IDENTITY", "LOCK") or name.endswith(".dbtmp"):
        return "db_meta"
    if name == os.path.basename(SHM_TRACE):
        return "trace"
    if name == os.path.basename(SHM_SCAN_LOG):
        return "scan_log"
    return "other"


def parse_tickers(stdout):
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"^(rocksdb\.[a-z0-9._]+) COUNT : (\d+)$", stdout, re.M)}


def drop_caches():
    subprocess.run(["sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)


def setup_cgroup():
    with open("/sys/fs/cgroup/cgroup.subtree_control") as f:
        if "io" not in f.read().split():
            with open("/sys/fs/cgroup/cgroup.subtree_control", "w") as g:
                g.write("+io")
    os.makedirs(CGROUP, exist_ok=True)


def execute_trial(work, cli, user, wl, side, trial, mode):
    db_dir = f"{work}/rocksdb_data"
    out_dir = f"{work}/db_state/{wl}/{side}/trial{trial}"
    os.makedirs(out_dir, exist_ok=True)
    if os.path.exists(db_dir):
        shutil.rmtree(db_dir)
    os.makedirs(db_dir)
    shutil.chown(db_dir, user)
    trace = f"{work}/workloads/{wl}/{side}.marked.txt"
    shutil.copyfile(trace, SHM_TRACE)
    os.chmod(SHM_TRACE, 0o644)
    if os.path.exists(SHM_SCAN_LOG):
        os.remove(SHM_SCAN_LOG)
    drop_caches()

    cmd = ["setpriv", f"--reuid={user}", f"--regid={user}", "--init-groups", "--",
           cli, "execute", "-i", SHM_TRACE, "-d", "rocksdb", "-p", db_dir, "-t", "1",
           "--merge-mode", "append", "-c", f"scan_log={SHM_SCAN_LOG};statistics=1"]
    log(f"  TRIAL {wl} | {side} | trial {trial} | {mode}")
    log(f"  $ {' '.join(cmd)}")

    bpf = Bpftrace(f"{out_dir}/bpftrace.txt") if mode == "bpftrace" else None
    iostat = Iostat()
    sampler = Sampler()
    time.sleep(1.5)  # iostat's first report and bpftrace's first interval
    sampler.sample("before")
    sampler.start()

    def enter_cgroup():
        with open(f"{CGROUP}/cgroup.procs", "w") as f:
            f.write(str(os.getpid()))

    t0 = time.perf_counter()
    t0_ns = time.monotonic_ns()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=enter_cgroup)
    sampler.pid = proc.pid
    outs = {}

    at_stats = {}

    def drain(name, stream):
        chunks = []
        for line in stream:
            # the executor prints "rocksdb.stats" right before the statistics tickers: snapshot the
            # process counters at that moment, before any work RocksDB does after its counters were read
            if name == "stdout" and not at_stats and line.startswith(b"=== RocksDB Internal Stats ==="):
                at_stats.update(t_ns=time.monotonic_ns(), proc_io=read_proc_io(proc.pid),
                                diskstats=read_diskstats(), cgroup=read_cgroup_io())
            chunks.append(line)
        outs[name] = b"".join(chunks)

    readers = [threading.Thread(target=drain, args=(n, s), daemon=True)
               for n, s in (("stdout", proc.stdout), ("stderr", proc.stderr))]
    for r in readers:
        r.start()
    os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)  # exited, not reaped: /proc/<pid>/io still readable
    wall = time.perf_counter() - t0
    t1_ns = time.monotonic_ns()
    final_io = read_proc_io(proc.pid)
    sampler.sample("exit")
    proc.wait()
    sampler.pid = None
    for r in readers:
        r.join()
    subprocess.run(["sync"], check=True)  # what is still dirty in the page cache belongs to this run
    time.sleep(2)
    sampler.sample("after_sync")
    sampler.stop_event.set()
    sampler.join()
    io_samples = iostat.stop()
    if bpf:
        bpf.stop()

    stdout, stderr = outs["stdout"].decode(), outs["stderr"].decode()
    with open(f"{out_dir}/cli_raw.log", "w") as f:
        f.write(stdout)
    with open(f"{out_dir}/cli_stderr.log", "w") as f:
        f.write(stderr)
    if proc.returncode != 0 or "[ERROR]" in stderr:
        log(stderr[-3000:])
        raise RuntimeError(f"executor failed: {wl} {side} trial {trial}")
    shutil.copyfile(f"{db_dir}/LOG", f"{out_dir}/rocksdb_LOG.txt")
    db_files = {n: os.path.getsize(f"{db_dir}/{n}") for n in sorted(os.listdir(db_dir))}
    scan_log_bytes = os.path.getsize(SHM_SCAN_LOG) if os.path.exists(SHM_SCAN_LOG) else 0
    if os.path.exists(SHM_SCAN_LOG):
        shutil.move(SHM_SCAN_LOG, f"{out_dir}/range_query_keys.txt")
    with open(f"{out_dir}/iostat.csv", "w") as f:
        f.write("second,read_MBps,write_MBps\n")
        for i, (rd, wr) in enumerate(io_samples):
            f.write(f"{i},{rd},{wr}\n")
    with open(f"{out_dir}/samples.json", "w") as f:
        json.dump({"t0_ns": t0_ns, "t1_ns": t1_ns, "samples": sampler.samples}, f)

    first = sampler.samples[0]
    last = sampler.samples[-1]
    rec = {
        "workload": wl, "side": side, "trial": trial, "mode": mode,
        "timestamp_utc": f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M:%S}",
        "wall_time_s": wall,
        "trace": trace, "trace_bytes": os.path.getsize(trace),
        "scan_log_bytes": scan_log_bytes,
        "rocksdb_statistics": parse_tickers(stdout),
        "proc_io_final": final_io,
        "at_stats": at_stats,
        "cgroup_io_delta": {k: last["cgroup"][k] - first["cgroup"][k] for k in last["cgroup"]},
        "diskstats_delta": {k: last["diskstats"][k] - first["diskstats"][k] for k in last["diskstats"]},
        "iostat_sum_MB": {"read": sum(r for r, _ in io_samples), "write": sum(w for _, w in io_samples)},
        "db_files_at_end": db_files,
    }
    if bpf:
        intervals, marks, totals = parse_bpftrace(f"{out_dir}/bpftrace.txt")
        by_cat = {}
        for key, n in totals.items():
            phase, op, name = key.split("|", 2)
            c = f"{'before_stats' if phase == '0' else 'after_stats'}|{op}|{category(name)}"
            by_cat[c] = by_cat.get(c, 0) + n
        rec["bpftrace_totals_by_file"] = totals
        rec["bpftrace_totals_by_category"] = by_cat
        rec["bpftrace_intervals"] = len(intervals)
        rec["bpftrace_marks_ns"] = marks
    log(f"  -> wall {wall:.2f}s  wchar {final_io['wchar'] / 2**30:.3f} GiB  rchar {final_io['rchar'] / 2**30:.3f} GiB"
        f"  diskstats w {rec['diskstats_delta']['write_bytes'] / 2**30:.3f} GiB")
    os.remove(SHM_TRACE)
    shutil.rmtree(db_dir)
    return rec


def main():
    global LOG_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--cli", required=True)
    ap.add_argument("--user", default="cc")
    ap.add_argument("--workloads", nargs="+", default=list(WORKLOADS))
    args = ap.parse_args()
    if os.geteuid() != 0:
        sys.exit("run as root (sudo)")
    os.makedirs(f"{args.work}/logs", exist_ok=True)
    os.makedirs(f"{args.work}/results", exist_ok=True)
    LOG_PATH = f"{args.work}/logs/run.log"
    res_path = f"{args.work}/results/results.json"
    results = json.load(open(res_path)) if os.path.exists(res_path) else {"config": {}, "trials": []}
    setup_cgroup()
    results["config"] = {
        "host": os.uname().nodename, "kernel": os.uname().release,
        "cli": args.cli, "cli_sha256": sha256(args.cli),
        "bpftrace": subprocess.run(["bpftrace", "--version"], capture_output=True, text=True).stdout.strip(),
        "device": DEVICE, "cgroup": CGROUP,
        "vm": {k: open(f"/proc/sys/vm/{k}").read().strip() for k in
               ("dirty_expire_centisecs", "dirty_writeback_centisecs", "dirty_background_ratio", "dirty_ratio")},
        "trace_sha256": {},
    }
    log(f"CONFIG {json.dumps(results['config'])}")
    done = {(t["workload"], t["side"], t["trial"]) for t in results["trials"]}
    for wl in args.workloads:
        for side in SIDES:
            trace = f"{args.work}/workloads/{wl}/{side}.marked.txt"
            results["config"]["trace_sha256"][f"{wl}/{side}"] = sha256(trace)
            for trial, mode in TRIALS:
                if (wl, side, trial) in done:
                    continue
                rec = execute_trial(args.work, args.cli, args.user, wl, side, trial, mode)
                results["trials"].append(rec)
                with open(res_path + ".tmp", "w") as f:
                    json.dump(results, f, indent=1)
                os.replace(res_path + ".tmp", res_path)
                subprocess.run(["chown", "-R", f"{args.user}:{args.user}", f"{args.work}/results",
                                f"{args.work}/logs", f"{args.work}/db_state"], check=True)
    log("COMPLETED")


if __name__ == "__main__":
    main()
