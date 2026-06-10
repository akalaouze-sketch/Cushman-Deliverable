#!/usr/bin/env python3
"""Extract design assets from a Cushman & Wakefield MarketBeat report PDF: render page 1
for reference and pull out embedded images (logo, building photo). Outputs to
frontend/assets/raw/."""
from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parent.parent
pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/pdfs/cw-marketbeat-dallas-fort-worth-office-q1-2026.pdf"
out = ROOT / "frontend/assets/raw"
out.mkdir(parents=True, exist_ok=True)

doc = fitz.open(pdf)
# full-page render of page 1 for visual reference
doc[0].get_pixmap(dpi=150).save(str(out / "page1.png"))
print("rendered page1.png")

seen = set()
for pno in range(doc.page_count):
    for img in doc.get_page_images(pno):
        xref = img[0]
        if xref in seen:
            continue
        seen.add(xref)
        pix = fitz.Pixmap(doc, xref)
        if pix.colorspace and pix.colorspace.name not in ("DeviceRGB", "DeviceGray"):
            pix = fitz.Pixmap(fitz.csRGB, pix)
        if pix.n > 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        name = f"p{pno+1}_x{xref}_{pix.width}x{pix.height}.png"
        pix.save(str(out / name))
        print(f"  {name}")
