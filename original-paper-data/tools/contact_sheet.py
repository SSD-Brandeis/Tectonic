#!/usr/bin/env python3
"""Tiles PNG files into one labelled contact sheet: contact_sheet.py OUT.png COLS IN1.png IN2.png ..."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

out, cols, files = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
ims = [Image.open(f).convert("RGB") for f in files]
w = max(i.width for i in ims)
h = max(i.height for i in ims) + 16
rows = (len(ims) + cols - 1) // cols
sheet = Image.new("RGB", (cols * w, rows * h), "white")
draw = ImageDraw.Draw(sheet)
for k, (im, f) in enumerate(zip(ims, files)):
    x, y = (k % cols) * w, (k // cols) * h
    draw.text((x + 4, y + 2), Path(f).stem[-70:], fill="red")
    sheet.paste(im, (x, y + 16))
sheet.save(out)
