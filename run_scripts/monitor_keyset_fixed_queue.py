#!/usr/bin/env python3
"""
monitor_keyset_fixed_queue.py
=============================
Status of the fixed-VecKeySet runs of `run_scripts/run_keyset_fixed_queue.py` on every run machine,
run from the local machine (2026-10-26: checked every 4 hours).

For every machine: copies `results/`, `logs/`, `queue/` and the run logs back to the local
experiment folder, then prints whether its runner is alive, the job it is running (last progress
line, elapsed time, VmHWM so far), its finished runs, its unstarted jobs and a failure if the runner
died with one.

--rebalance: a machine with no runner whose own queue still has unstarted jobs gets its runner
restarted; a machine with nothing left to do takes the last unstarted fixed (`vec`) job of the
machine with the most unstarted jobs (the line there becomes a comment) and starts its runner.
Algorithm 1 baselines stay on their machine, next to their fixed run. A failed job is not retried.
One run per machine is never broken: the runner holds a lock and runs one job at a time.

Usage: python3 run_scripts/monitor_keyset_fixed_queue.py [--rebalance]
"""
import re
import sys
import json
import subprocess

EXP = "data/revision/R1.O8-R2.O2_keyset-selection/table4-fixed-to-completion-multimachine"
LOCAL = f"/home/cc/Tectonic/{EXP}"
REMOTE = f"/home/cc/Tectonic/{EXP}"
# ssh alias -> hostname (the queue and result files are named after the hostname)
MACHINES = {"revision_2": "revision-2", "fixedkeyset1": "fixedkeyset-1", "fixedkeyset2": "fixedkeyset-2"}
RUNNER = ("cd /home/cc/Tectonic && tmux new-session -d -s keyset-fixed "
          f"\"python3 -u run_scripts/run_keyset_fixed_queue.py 2>&1 | tee -a {REMOTE}/logs/console.$(hostname).out\"")


def ssh(alias, cmd, check=True):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", alias, cmd],
                          capture_output=True, text=True, timeout=300, check=check).stdout


def parse_queue(text):
    return [tuple(l.split("#", 1)[0].split()[:3]) for l in text.splitlines() if l.split("#", 1)[0].split()]


def state(alias, host):
    subprocess.run(["rsync", "-a", "--exclude", "tmp/", "--exclude", "bin/", f"{alias}:{REMOTE}/", f"{LOCAL}/"],
                   check=True, timeout=600)
    with open(f"{LOCAL}/queue/{host}.txt") as f:
        queue = parse_queue(f.read())
    try:
        with open(f"{LOCAL}/results/runs.{host}.json") as f:
            runs = json.load(f)
    except FileNotFoundError:
        runs = []
    done = {(r["workload"], r["keyset"], str(r["trial"])): r for r in runs}
    with open(f"{LOCAL}/logs/console.{host}.out") as f:
        console = f.read()
    alive = ssh(alias, "tmux has-session -t keyset-fixed 2>/dev/null && echo yes || echo no").strip() == "yes"
    started = re.findall(r"\] (\S+) (\S+) trial (\d+): /usr/bin/time", console)
    current = started[-1] if alive and started and started[-1] not in done else None
    failed = None
    # a failure since the runner last started (its first line names the binary's sha256)
    if not alive and "Traceback" in console.rsplit("sha256", 1)[-1]:
        failed = console.strip().splitlines()[-1][:300]
    pending = [j for j in queue if j not in done and j != current]
    return {"alias": alias, "host": host, "alive": alive, "current": current, "done": runs,
            "pending": pending, "failed": failed}


def progress(s):
    w, k, t = s["current"]
    base = f"{LOCAL}/runs/{w}/{k}/trial{t}"
    # the group is on stderr (progress bar), the progress per 10% of a group on stdout
    with open(f"{base}.stderr.log") as f:
        groups = re.findall(r"\[Generating\] Section \d+ \| Group \d+", f.read())
    with open(f"{base}.stdout.log") as f:
        ops = re.findall(r"Generating operation \d+ \(\d+%\)", f.read())
    group, op = (groups or [""])[-1], (ops or [""])[-1]
    with open(f"{base}.rss.log") as f:
        last = f.read().strip().splitlines()[-1].split()
    return f"{group.strip()} {op}, {float(last[0]) / 3600:.1f} h, VmHWM {int(last[2]) / 1024:.0f} MiB"


def move(src, dst, job):
    """Comment out `job` in src's queue and append it to dst's queue (both on their machines)."""
    line = " ".join(job)
    ssh(src["alias"], f"sed -i 's/^{re.escape(line)}$/# {line}  -> moved to {dst['host']} by the monitor/' "
                      f"{REMOTE}/queue/{src['host']}.txt")
    ssh(dst["alias"], f"echo '{line}  # moved from {src['host']} by the monitor' >> {REMOTE}/queue/{dst['host']}.txt")


def main():
    rebalance = "--rebalance" in sys.argv
    states = [state(a, h) for a, h in MACHINES.items()]
    for s in states:
        print(f"== {s['host']}: runner {'alive' if s['alive'] else 'stopped'}")
        if s["current"]:
            print(f"   running {' '.join(s['current'])}: {progress(s)}")
        for r in s["done"]:
            print(f"   done    {r['workload']} {r['keyset']} {r['trial']}: {r['wall_time_s']:.1f} s, "
                  f"{r['peak_rss_mib']:.1f} MiB")
        print(f"   pending {[' '.join(j) for j in s['pending']]}")
        if s["failed"]:
            print(f"   FAILED: {s['failed']}")
    if not rebalance:
        return
    for s in states:
        if s["alive"] or s["failed"]:
            continue
        if not s["pending"]:
            donors = sorted((d for d in states if d is not s and [j for j in d["pending"] if j[1] == "vec"]),
                            key=lambda d: len(d["pending"]), reverse=True)
            if not donors:
                continue
            job = [j for j in donors[0]["pending"] if j[1] == "vec"][-1]
            move(donors[0], s, job)
            donors[0]["pending"].remove(job)
            print(f"moved {' '.join(job)}: {donors[0]['host']} -> {s['host']}")
        ssh(s["alias"], RUNNER)
        print(f"started the runner on {s['host']}")


if __name__ == "__main__":
    main()
