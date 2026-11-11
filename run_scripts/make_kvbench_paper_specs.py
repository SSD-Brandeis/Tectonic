#!/usr/bin/env python3
"""
make_kvbench_paper_specs.py
===========================
Writes KVBench I-V as the KVBench paper defines them (example-specs/kvbench/kvbench.pdf, Table 5: operation
percentages and distributions), with Fig. 7's KVBench operation counts (the paper gives percentages only):
  example-specs/kvbench/{i,ii,iii,iv,v}.spec.json        full size (--scale 1, the default; Table 2, authors 2026-11-02)
  example-specs/kvbench-0.1x/{i,ii,iii,iv,v}.spec.json   0.1x (--scale 10): Table 2's KV-IV row (only iv is used)
  example-specs/kvbench-1-20/{i,ii,iii,iv,v}.spec.json   1/20 (--scale 20): the corrected Fig. 7's KVBench I-V, run
                                                         at 1/20 so that KV-Bench's IV finishes (KVBENCH_ARGS) Decided with the authors,
2026-11-02: the previous specs (git history) used Beta(0.1, 5) for the Zipfian updates of III and IV and
a 10-prefix skew (`hot_range`) for I's empty point queries and V's Zipfian key prefixes.

Paper Table 5, and how each workload is written here. Parameters the paper does not give are KVBench's
defaults (beta alpha = beta = 1, Zipf alpha = 1, 62 x 62 key prefixes):
  I    20% non-empty PQ (uniform), 80% empty PQ (ZD = beta), preloaded. Beta(1, 1) is uniform: uniform keys.
  II   50% insert, 10% point delete, 15% empty PQ, 25% update, interleaved, all uniform. 1/5 of the inserts
       preload, as before.
  III  50% update (Zipfian), 25% non-empty PQ (ED = uniform, Table 5), 25% empty PQ (uniform), preloaded.
  IV   50% update (Zipfian), 50% range delete, preloaded; selectivity 1e-6 as Fig. 7's KVBench arguments
       (one key per range delete in both generators).
  V    95% insert with Zipfian key prefixes, 5% non-empty PQ (uniform). The prefix is a Zipf(3844, 1) number
       written with 4 digits (3,844 = 62 x 62 prefixes, as KVBench), then 508 uniform characters: 512 B keys.
       All inserts first (Load Phase), then the point queries, as KVBench writes V (authors, 2026-11-11; before,
       one group mixed them).
Zipfian updates select from the preloaded keys: zipf n = the number of preloaded keys, s = 1.
Keys and values are 512 B (KVBench: --entry_size 1024, key size 512).

The matching KVBench arguments at FIG7_SCALE: KVBENCH_ARGS below (used by run_scripts/run_fig7_corrected.py).
Usage: python3 run_scripts/make_kvbench_paper_specs.py [--scale 1|10|20]
"""
import os
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 10: 0.1x, for Table 2's KV-IV (authors, 2026-11-02): KV-Bench's generator needs about 2 days per full-size IV, so
# the whole KV-IV row of Table 2 (adaptive, fixed and KV-Bench's generator) is at 0.1x
SPEC_DIRS = {1: f"{ROOT}/example-specs/kvbench", 10: f"{ROOT}/example-specs/kvbench-0.1x",
             20: f"{ROOT}/example-specs/kvbench-1-20"}
FIG7_SCALE = 20  # the corrected Fig. 7 runs KVBench I-V at 1/FIG7_SCALE of Fig. 7's operation counts


def counts(scale):
    """Operation counts of Fig. 7's KVBench arguments, divided by scale."""
    full = {"I": {"I": 1_000_000, "Q": 1_000_000}, "II": {"I": 500_000, "D": 100_000, "U": 250_000, "Q": 150_000},
            "III": {"I": 1_000_000, "U": 500_000, "Q": 500_000}, "IV": {"I": 1_000_000, "U": 500_000, "R": 500_000},
            "V": {"I": 950_000, "Q": 50_000}}
    return {w: {op: n // scale for op, n in c.items()} for w, c in full.items()}


def kvbench_args(scale=FIG7_SCALE):
    c = counts(scale)
    return {
        "I": f"-I {c['I']['I']} -Q {c['I']['Q']} -Z 0.8 --ED 0 --ZD 2 --entry_size 1024",
        "II": f"-I {c['II']['I']} -D {c['II']['D']} -U {c['II']['U']} -Q {c['II']['Q']} -Z 1 --ID 0 --UD 0 --ED 0 --ZD 0 --entry_size 1024",
        "III": f"-I {c['III']['I']} -U {c['III']['U']} -Q {c['III']['Q']} -Z 0.5 --UD 3 --ED 0 --ZD 0 --entry_size 1024",
        "IV": f"-I {c['IV']['I']} -U {c['IV']['U']} -R {c['IV']['R']} -y 0.000001 --UD 3 --entry_size 1024",
        "V": f"-I {c['V']['I']} -Q {c['V']['Q']} -Z 0 --ID 3 --ED 0 --ZD 0 --entry_size 1024",
    }


KVBENCH_ARGS = kvbench_args()
KEY = {"uniform": {"len": 512}}
VAL = {"uniform": {"len": 512}}


def group(name, **ops):
    return {"name": name, "enable_granular_stats": True, **ops}


def spec(*groups):
    return {"$schema": "../../workload_schema.json", "sections": [{"groups": list(groups)}]}


def specs(scale):
    c = counts(scale)
    load = lambda n: group("Load Phase", inserts={"op_count": n, "key": KEY, "val": VAL})
    zipf = lambda n: {"zipf": {"n": n, "s": 1}}
    q_i = c["I"]["Q"]
    return {
        "i": spec(load(c["I"]["I"]), group("Execution Phase", point_queries={"op_count": q_i // 5},
                                           empty_point_queries={"op_count": q_i - q_i // 5, "key": KEY})),
        "ii": spec(load(c["II"]["I"] // 5), group("Execution Phase",
                   inserts={"op_count": c["II"]["I"] - c["II"]["I"] // 5, "key": KEY, "val": VAL},
                   point_deletes={"op_count": c["II"]["D"]}, empty_point_queries={"op_count": c["II"]["Q"], "key": KEY},
                   updates={"op_count": c["II"]["U"], "val": VAL})),
        "iii": spec(load(c["III"]["I"]), group("Execution Phase",
                    updates={"op_count": c["III"]["U"], "val": VAL, "selection": zipf(c["III"]["I"])},
                    point_queries={"op_count": c["III"]["Q"] // 2},
                    empty_point_queries={"op_count": c["III"]["Q"] - c["III"]["Q"] // 2, "key": KEY})),
        "iv": spec(load(c["IV"]["I"]), group("Execution Phase",
                   updates={"op_count": c["IV"]["U"], "val": VAL, "selection": zipf(c["IV"]["I"])},
                   range_deletes={"op_count": c["IV"]["R"], "selectivity": 1e-06, "range_format": "StartEnd"})),
        # V: all inserts first, then the point queries, as KVBench writes V (authors, 2026-11-11; until then one
        # group that mixed the inserts and the point queries)
        "v": spec(group("Load Phase",
                        inserts={"op_count": c["V"]["I"], "val": VAL, "key": {"segmented": {"separator": "", "segments": [
                            {"number": {"value": {"zipf": {"n": 3844, "s": 1}}, "width": 4}}, {"uniform": {"len": 508}}]}}}),
                  group("Execution Phase", point_queries={"op_count": c["V"]["Q"]})),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=1, choices=sorted(SPEC_DIRS))
    args = ap.parse_args()
    spec_dir = SPEC_DIRS[args.scale]
    os.makedirs(spec_dir, exist_ok=True)
    for name, s in specs(args.scale).items():
        with open(f"{spec_dir}/{name}.spec.json", "w") as f:
            json.dump(s, f, indent=2)
            f.write("\n")
        print(f"{spec_dir}/{name}.spec.json")
    for w, a in kvbench_args(args.scale).items():
        print(f"KVBench {w}: {a}")


if __name__ == "__main__":
    main()
