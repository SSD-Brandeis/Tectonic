#!/usr/bin/env python3
"""
Table 6: workload properties of the db_bench prefix_dist and X-Bench prefix_dist workload files, and their
difference |X - D| / D. Reads the per-operation arrays of the run phase that run_mixgraph_fidelity.py writes
(results/arrays/{dbbench,xbench}.npz) and the key length from the executed workload files (execute/*.marked.txt).

  python3 run_scripts/make_table6_mixgraph.py            # prints the rows and writes results/table6_mixgraph.tex
"""
from pathlib import Path
import numpy as np

EXP = Path(__file__).resolve().parents[1] / "data/revision/R1.O9-R2.O1_mixgraph"
SIDES = {"dbbench": "db_bench", "xbench": "X-Bench"}


def key_length(side):
    """Length of the key of the first insert in the executed workload file."""
    with open(EXP / "execute" / f"{side}.marked.txt") as f:
        for line in f:
            if line.startswith("I "):
                return float(len(line.split()[1]))
    raise ValueError(f"no insert in {side}.marked.txt")


def properties(side):
    a = np.load(EXP / "results" / "arrays" / f"{side}.npz")
    typ, key = a["typ"], a["key"]
    return {
        "Get (M)": (typ == 0).sum() / 1e6,
        "Put (M)": (typ == 1).sum() / 1e6,
        "Scan (M)": (typ == 6).sum() / 1e6,
        "Accessed KV-pairs (K)": len(np.unique(key)) / 1e3,  # distinct keys queried at least once in the run phase
        "Mean key size (B)": key_length(side),
        "Mean value size (B)": a["vsz"][typ == 1].mean(),  # run-phase Puts
        "Mean scan length": a["scan"][typ == 6].mean(),  # keys each Scan asks for
    }


def main():
    d, x = properties("dbbench"), properties("xbench")
    rows = []
    for name in d:
        diff = abs(x[name] - d[name]) / d[name] * 100
        rows.append(f"{name:<22} & {d[name]:.2f} & {x[name]:.2f} & {diff:.2f} \\\\")
        print(f"{name:<22} {d[name]:>10.4f} {x[name]:>10.4f} {diff:>6.2f}%")
    out = EXP / "results" / "table6_mixgraph.tex"
    out.write_text("\\begin{tabular}{@{}l rrr@{}}\n\\toprule\n"
                   "\\textbf{Property} & \\textbf{db\\_bench} & \\textbf{\\bench{}} & \\textbf{diff. (\\%)} \\\\\n"
                   "\\midrule\n" + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
