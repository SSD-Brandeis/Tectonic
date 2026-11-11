#!/usr/bin/env python3
"""
Key-access patterns of the two mixgraph workload files executed in Fig. 16:
db_bench mixgraph Prefix_dist (`execute/dbbench.marked.txt`) and X-Bench (`execute/xbench.marked.txt`).

- mixgraph_access_count_cdf: CDF of the access count per KV-pair, one curve per operation type (Get, Put,
  Scan). Mixgraph issues no Deletes. A Seek counts once, for its start key.
- mixgraph_get_keyspace_heatmap: the Get count of every key, by key sequence (key id 0 .. 5M-1, i.e. sorted
  key order).

Only the run phase is counted (the 42M operations after the 5M-insert load group).

  python3 plot_scripts/plot_mixgraph_key_access.py --stage count   # reads the two files (~2.9 GB each)
  python3 plot_scripts/plot_mixgraph_key_access.py --stage plot
"""
from pathlib import Path as _Path
import sys
sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import json
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plot_style

# plot_style sets text.usetex with font.family = the LinLibertine_Mah.ttf face, but under usetex LaTeX typesets all
# text and ignores font.family: the PDFs came out in Computer Modern (pdffonts: CMR12/CMR17). Linux Libertine is
# selected in the LaTeX preamble instead, as the repo-root plot_style.py does for Fig. 7 (user, 2026-11-11).
plt.rcParams["text.latex.preamble"] = r"\usepackage{libertine}\usepackage[libertine]{newtxmath}"
plt.rcParams["font.family"] = "serif"


REPO = _Path(__file__).resolve().parents[1]
EXP = REPO / "data/revision/R1.O9-R2.O1_mixgraph"
OUT = EXP / "key-access-figs"
SIDES = {"dbbench": "db_bench", "xbench": "X-Bench"}
NUM_KEYS = 5_000_000
OPS = {b"BP": "Get", b"I": "Put", b"BR": "Seek"}
OP_LABEL = {"Get": "Get", "Put": "Put", "Seek": "Scan"}
OP_STYLE = {
    "Get": {"color": "tab:blue", "linestyle": "-"},
    "Put": {"color": "black", "linestyle": "--"},
    "Seek": {"color": "tab:green", "linestyle": "-."},
}


def count(side):
    """Per-key access counts of the run phase, one array per operation type."""
    keys = {op: [] for op in OPS.values()}
    groups_ended = 0
    with open(EXP / "execute" / f"{side}.marked.txt", "rb") as f:
        for line in f:
            code, _, rest = line.partition(b" ")
            if code == b"FE":
                groups_ended += 1
                continue
            if groups_ended == 0 or code == b"FS":
                continue  # load group, or a group marker
            op = OPS[code]  # any other code would raise: none expected
            keys[op].append(int(rest[:16]))
    counts = {op: np.bincount(np.asarray(k, dtype=np.int64), minlength=NUM_KEYS) for op, k in keys.items()}
    for op, c in counts.items():
        assert len(c) == NUM_KEYS, (side, op, len(c))
    return side, counts


def stage_count():
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {}
    with ProcessPoolExecutor(len(SIDES)) as ex:
        for side, counts in ex.map(count, SIDES):
            np.savez_compressed(OUT / f"key_access_counts_{side}.npz", **counts)
            summary[side] = {
                op: {
                    "operations": int(c.sum()),
                    "keys_accessed": int((c > 0).sum()),
                    "max_access_count": int(c.max()),
                    "keys_accessed_10_or_more_pct": round(100 * float((c >= 10).sum() / (c > 0).sum()), 2),
                    "keys_accessed_100_or_more_pct": round(100 * float((c >= 100).sum() / (c > 0).sum()), 2),
                }
                for op, c in counts.items()
            }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


def stage_plot():
    data = {side: np.load(OUT / f"key_access_counts_{side}.npz") for side in SIDES}

    # access count CDF per operation type; db_bench on top, X-Bench below, compact, no panel letters, x tick labels
    # and x label on the bottom panel only (user, 2026-11-11)
    x_max = max(int(d[op].max()) for d in data.values() for op in OP_STYLE)
    assert x_max < 1e5, x_max  # x ticks are 10^0 .. 10^5
    fig, axes = plt.subplots(2, 1, figsize=(3.8, 3.0), sharex=True)
    for ax, (side, name) in zip(axes, SIDES.items()):
        for op, style in OP_STYLE.items():
            c = np.sort(data[side][op][data[side][op] > 0])
            vals, idx = np.unique(c, return_index=True)
            cdf = np.append(idx[1:], len(c)) / len(c)
            ax.step(vals, cdf, where="post", label=OP_LABEL[op], linewidth=2.2, **style)
        ax.set_xscale("log")
        # name inside the empty lower-right corner instead of a title, to save height (user, 2026-11-11)
        ax.text(0.97, 0.1, name, transform=ax.transAxes, ha="right", va="bottom")
        plot_style.apply_plot_style(ax, xlabel="access count" if ax is axes[-1] else None)
        ax.set_xlim(1, 1e5)
        ax.set_xticks([10 ** k for k in range(6)])
        ax.set_ylim(0, 1.02)
        ax.set_yticks([0, 0.5, 1])
    axes[0].tick_params(labelbottom=False)
    # move the 10^0 label slightly right so it is not cut off at the left edge (user, 2026-11-11)
    first = axes[-1].xaxis.get_major_ticks()[0].label1
    first.set_transform(first.get_transform() + matplotlib.transforms.ScaledTranslation(6 / 72, 0, fig.dpi_scale_trans))
    handles, labels = axes[0].get_legend_handles_labels()
    fig.tight_layout(pad=0.3, h_pad=0.3)
    axes[-1].xaxis.labelpad = 2  # "access count" close to the tick labels (user, 2026-11-11)
    # one y label for both panels, centred on the two panels, not on the whole figure (user, 2026-11-11)
    # placed just left of the y tick labels (user, 2026-11-11: less space between "CDF" and the ticks)
    y_mid = (axes[0].get_position().y1 + axes[-1].get_position().y0) / 2
    renderer = fig.canvas.get_renderer()
    tick_left = min(t.get_window_extent(renderer).x0 for ax in axes for t in ax.get_yticklabels())
    x_fig = fig.transFigure.inverted().transform((tick_left - 4, 0))[0]
    fig.text(x_fig, y_mid, "CDF", rotation=90, ha="right", va="center", fontsize=plt.rcParams["axes.labelsize"])
    for ext in ("png", "pdf"):
        fig.savefig(f"{OUT}/mixgraph_access_count_cdf.{ext}", dpi=120, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)
    # legend as a separate file, one row (user, 2026-11-11)
    leg_fig = plt.figure(figsize=(4.4, 0.4))
    leg_fig.legend(handles, labels, loc="center", ncol=3, frameon=False, fontsize=16, handlelength=1.6, columnspacing=0.8)
    for ext in ("png", "pdf"):
        leg_fig.savefig(f"{OUT}/mixgraph_access_count_cdf_legend.{ext}", dpi=120, bbox_inches="tight", pad_inches=0.03)
    plt.close(leg_fig)

    # Get count by key sequence.
    # Each accessed key is one point (key id, Get count). The points are drawn as one image
    # (a cell is green if at least one key falls in it): a rasterized scatter came out shifted inside the PDF.
    # The y-axis is log scale (a few keys get up to ~7e4 Gets and would flatten the rest on a linear axis); the
    # image is placed in log10 space on a linear axis, with ticks labelled 10^k.
    y_max = max(int(d["Get"].max()) for d in data.values())
    y_top = np.log10(y_max * 1.5)
    nx, ny = 1600, 600
    # matplotlib 3.6.3 writes an image of <= 16 colours into the PDF as a palette image with fewer than 8 bits per
    # pixel and omits BitsPerComponent from the PNG decode parameters, so every PDF reader decodes the rows shifted
    # (checked with pdfimages). Green cells therefore get 17 shades, G channel +-8/255 (not visible), which makes
    # the palette 8-bit.
    shade = np.random.default_rng(0).integers(-8, 9, size=(ny, nx)) / 255
    green = np.array(matplotlib.colors.to_rgb("tab:green"))
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.0), sharey=True)
    for ax, (side, name) in zip(axes, SIDES.items()):
        g = data[side]["Get"]
        ids = np.nonzero(g)[0]
        h, _, _ = np.histogram2d(np.log10(g[ids]), ids, bins=[ny, nx], range=[[0, y_top], [0, NUM_KEYS]])
        # opaque RGB (white, green where a key is), embedded as is: a masked/transparent image rendered black in PDF
        rgb = np.where((h > 0)[..., None], green, 1.0)
        rgb[..., 1] = np.where(h > 0, green[1] + shade, 1.0)
        ax.imshow(rgb, origin="lower", aspect="auto", interpolation="none", extent=[0, NUM_KEYS, 0, y_top])
        ax.set_title(name)
        plot_style.apply_plot_style(ax, xlabel="key sequence", ylabel="KV-pair access count" if ax is axes[0] else None)
        ax.set_xlim(0, NUM_KEYS)
        ax.set_xticks(np.arange(0, NUM_KEYS + 1, 1_000_000))
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: "0" if v == 0 else f"{v / 1e6:g}M"))
        ax.set_ylim(0, y_top)
        ax.set_yticks(range(0, int(y_top) + 1))
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"$10^{{{int(v)}}}$"))
        ax.set_yticks(np.log10([m * 10 ** k for k in range(0, int(y_top) + 1) for m in range(2, 10)
                                if m * 10 ** k <= 10 ** y_top]), minor=True)
    plot_style.save_preview(fig, axes, f"{OUT}/mixgraph_get_keyspace_heatmap")
    plt.close(fig)
    print(f"plots in {OUT}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=["count", "plot", "all"], default="all")
    s = p.parse_args().stage
    if s in ("count", "all"):
        stage_count()
    if s in ("plot", "all"):
        stage_plot()
