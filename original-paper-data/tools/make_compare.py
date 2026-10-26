#!/usr/bin/env python3
"""
Side-by-side images of each paper figure (cropped from the PDF) above its regenerated panels
(reproduced/<fig>/*.pdf). Writes original-paper-data/reproduced/compare/<fig>_paper_vs_reproduced.png.
Run after reproduce_paper_figures.py.
"""

import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

PAPER = Path("/home/cc/Tectonic/original-paper-data")
PDF = PAPER / "X_Bench (5).pdf"
REP = PAPER / "reproduced"

# figure -> (PDF page, crop box as fractions of the page, regenerated panels)
FIGURES = {
    "fig7": (14, (0.08, 0.10, 0.92, 0.255), ["fig7/fig3", "fig7/fig3_mem", "fig7/fig1", "fig7/fig1_mem"]),
    "fig8": (14, (0.50, 0.265, 0.92, 0.40), ["fig8/concurrent_untracked_generation_resource_monitoring_latency",
                                             "fig8/concurrent_untracked_generation_resource_monitoring_peak_memory",
                                             "fig8/concurrent_untracked_generation_resource_monitoring_legend"]),
    "fig9": (15, (0.08, 0.10, 0.47, 0.215), ["fig9/cpu_utilization_pidstat_multithread_timeseries_threads16",
                                             "fig9/cpu_utilization_pidstat_multithread_by_threads",
                                             "fig9/cpu_utilization_pidstat_multithread_legend"]),
    "fig10": (15, (0.50, 0.355, 0.92, 0.47), ["fig10/fig2_sequential", "fig10/fig2_parallel"]),
    "fig11": (16, (0.24, 0.395, 0.49, 0.51), ["fig11/thread_modes/workload_accuracy_sequential_3runs"]),
    "fig12": (16, (0.50, 0.415, 0.92, 0.515), ["fig12/rocksdb_similarity_ycsba_10x_throughput",
                                               "fig12/rocksdb_similarity_ycsba_10x_latency",
                                               "fig12/rocksdb_similarity_ycsba_10x_data_movement"]),
}


def render(pdf: Path, out: Path, dpi: int, page: int | None = None) -> Image.Image:
    cmd = ["pdftoppm", "-png", "-r", str(dpi), "-singlefile"]
    if page:
        cmd += ["-f", str(page), "-l", str(page)]
    subprocess.run(cmd + [str(pdf), str(out)], check=True)
    return Image.open(f"{out}.png").convert("RGB")


def main():
    (REP / "compare").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pages = {}
        for fig, (page, box, panels) in FIGURES.items():
            if page not in pages:
                pages[page] = render(PDF, tmp / f"page{page}", 200, page)
            w, h = pages[page].size
            paper = pages[page].crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), int(h * box[3])))
            ims = [render(REP / f"{p}.pdf", tmp / p.replace("/", "_"), 110) for p in panels]
            row = Image.new("RGB", (sum(i.width + 10 for i in ims), max(i.height for i in ims)), "white")
            x = 0
            for i in ims:
                row.paste(i, (x, 0))
                x += i.width + 10
            canvas = Image.new("RGB", (max(paper.width, row.width), paper.height + row.height + 60), "white")
            draw = ImageDraw.Draw(canvas)
            draw.text((5, 2), f"{fig}: paper ({PDF.name}, page {page})", fill="red")
            canvas.paste(paper, (0, 18))
            draw.text((5, paper.height + 26), f"{fig}: regenerated with the repository's plot script", fill="red")
            canvas.paste(row, (0, paper.height + 44))
            canvas.save(REP / "compare" / f"{fig}_paper_vs_reproduced.png")
            print(f"{fig}: {REP / 'compare' / f'{fig}_paper_vs_reproduced.png'}")


if __name__ == "__main__":
    main()
