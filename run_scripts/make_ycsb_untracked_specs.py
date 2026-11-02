#!/usr/bin/env python3
"""
make_ycsb_untracked_specs.py
============================
Writes `example-specs/ycsb-untracked/{a..f}.spec.json`, YCSB A-F with every operation untracked, from the
shipped `example-specs/ycsb/{a..f}.spec.json` (left unchanged). For R1.O8 / R2.O2: fixed EmptyKeySet (YCSB's
design: no keys stored) vs. Algorithm 1 (decided with the authors, 2026-10-31).

YCSB stores no keys: an operation picks a key number and builds the key as "user" + hash(key number)
(`CoreWorkload.buildKeyName`). Here every key is "usertable:user" + a one-to-one shuffle of the key number
(`scatter` over 10^15 numbers, exact in a double) written with 19 digits, as in the shipped specs:
- load: key numbers 0, 1, ..., 999,999 (`counter`), so the load is in random key order as YCSB's;
- reads, updates, scans, read-modify-writes: YCSB's scrambled Zipfian, Zipf(s 0.99) over 10^10 ranks folded
  onto the loaded key numbers by a second shuffle (`scatter` over 10^6), as shipped (`scramble` + `zipf`);
- D: inserts continue the key numbers; reads pick `latest`, the newest key numbers (not scrambled, as YCSB's
  SkewedLatestGenerator). X-Bench generates the keys of untracked operations before the group runs, so the run
  phase is 50 groups of 1,000 inserts and 19,000 reads, each reading below the newest key inserted before it;
- E: inserts continue the key numbers; scans start at a scrambled-Zipfian key and have a length uniform in
  1-100 (YCSB maxscanlength=100, scanlengthdistribution=uniform);
- F: read-modify-write as a read and an update of the same key (`untracked_read_modify_writes`), as YCSB;
- values: 1,090 B per insert, 109 B per update (YCSB's 10 fields of 100 B, field names included, and one field
  per update, writeallfields=false), as workload A's shipped spec; the shipped B-F had 1,024 / 128 B.
Op counts are as shipped.

Usage: python3 run_scripts/make_ycsb_untracked_specs.py
"""
import json
import copy

PREFIX = "usertable:user"
WIDTH = 19                      # digits after the prefix, as in the shipped specs
HASH_LEN = 10 ** 15             # key-number shuffle range, below 2^53 so every key number stays exact
RECORDS = 1_000_000             # YCSB recordcount: the shipped load phase
INSERT_LEN, UPDATE_LEN = 1090, 109
D_GROUPS = 50


def key(number):
    """The key of a key number, as YCSB's buildKeyName: prefix + hash(key number)."""
    shuffled = {"scatter": {"start": 0, "len": HASH_LEN, "value": number}}
    return {"segmented": {"segments": [PREFIX, {"number": {"value": shuffled, "width": WIDTH}}], "separator": ""}}


def counter(start):
    return {"counter": {"start": start}}


def scrambled_zipfian(zipf):
    return {"scatter": {"start": 0, "len": RECORDS, "value": {"zipf": zipf}}}


def picked(selection):
    if "zipf" in selection:
        assert selection.get("scramble")
        return scrambled_zipfian(selection["zipf"])
    if "latest" in selection:
        return {"latest": selection["latest"]}
    raise ValueError(selection)


def untracked(spec):
    load, run = spec["sections"][0]["groups"]
    assert load["inserts"]["op_count"] == RECORDS
    load["inserts"]["key"] = key(counter(0))
    load["inserts"]["val"] = {"uniform": {"len": INSERT_LEN}}
    out = {k: v for k, v in run.items() if k in ("name", "enable_granular_stats")}
    if "inserts" in run:
        out["inserts"] = {"op_count": run["inserts"]["op_count"], "key": key(counter(RECORDS)),
                          "val": {"uniform": {"len": INSERT_LEN}}}
    if "point_queries" in run:
        q = run["point_queries"]
        out["untracked_point_queries"] = {"op_count": q["op_count"], "key": key(picked(q["selection"]))}
    if "range_queries" in run:
        q = run["range_queries"]
        assert q["scan_length"] == 100
        out["untracked_range_queries"] = {"op_count": q["op_count"], "key": key(picked(q["selection"])),
                                          "scan_length": {"uniform": {"min": 1, "max": 101}}}
    if "updates" in run:
        u = run["updates"]
        out["untracked_updates"] = {"op_count": u["op_count"], "key": key(picked(u["selection"])),
                                    "val": {"uniform": {"len": UPDATE_LEN}}}
    if "merges" in run:
        m = run["merges"]
        out["untracked_read_modify_writes"] = {"op_count": m["op_count"], "key": key(picked(m["selection"])),
                                               "val": {"uniform": {"len": UPDATE_LEN}}}
    assert set(run) <= {"name", "enable_granular_stats", "inserts", "point_queries", "range_queries",
                        "updates", "merges"}, set(run)
    groups = [load, out]
    if "latest" in json.dumps(out):
        groups = [load] + split_latest(out)
    spec["sections"][0]["groups"] = groups
    spec["$schema"] = "../../workload_schema.json"
    return spec


def split_latest(run):
    """D's run phase as D_GROUPS groups; each group's reads pick below the newest key inserted before it."""
    inserts, reads = run["inserts"]["op_count"] // D_GROUPS, run["untracked_point_queries"]["op_count"] // D_GROUPS
    assert inserts * D_GROUPS == run["inserts"]["op_count"]
    assert reads * D_GROUPS == run["untracked_point_queries"]["op_count"]
    groups = []
    for g in range(D_GROUPS):
        first = RECORDS + g * inserts     # key numbers [0, first) exist when the group starts
        part = copy.deepcopy(run)
        part["name"] = f"{run.get('name', 'Execution Phase')} {g + 1}/{D_GROUPS}"
        part["inserts"]["op_count"] = inserts
        part["inserts"]["key"] = key(counter(first))
        part["untracked_point_queries"]["op_count"] = reads
        latest = copy.deepcopy(run["untracked_point_queries"]["key"]["segmented"]["segments"][1])
        latest["number"]["value"]["scatter"]["value"]["latest"]["n"] = first
        part["untracked_point_queries"]["key"]["segmented"]["segments"][1] = latest
        groups.append(part)
    return groups


def main():
    for w in "abcdef":
        with open(f"example-specs/ycsb/{w}.spec.json") as f:
            spec = untracked(json.load(f))
        with open(f"example-specs/ycsb-untracked/{w}.spec.json", "w") as f:
            json.dump(spec, f, indent=2)
            f.write("\n")


if __name__ == "__main__":
    main()
