"""Vision sub-agent — read the MarketBeat supply/demand + vacancy charts.

A Cushman & Wakefield MarketBeat shows these as vector graphics (not text), so the
text parser can't see the plotted values. We render the right-hand chart column of
page 1 — which stacks the "SPACE DEMAND / DELIVERIES" chart (Net Absorption +
Construction Completions bars) above the "OVERALL VACANCY & ASKING RENT" chart
(the Vacancy line) — and have Claude vision transcribe the plotted series. Those
numbers then feed the SAME interactive Chart.js chart in the report.
"""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from agents.llm import EXTRACT_MODEL, claude_vision_json

CHART_SYSTEM = (
    "You read chart images from Cushman & Wakefield MarketBeat commercial real estate reports and "
    "transcribe the plotted values as precisely as the axes allow. You never guess beyond what the chart shows."
)

PROMPT = """This crop is the right-hand chart column of a Cushman & Wakefield MarketBeat report page.
It contains TWO stacked charts:
  1. "SPACE DEMAND / DELIVERIES" — per year, Net Absorption (bars) and Construction Completions (bars)
     on the LEFT axis in s.f. (millions). Net absorption can be negative.
  2. "OVERALL VACANCY & ASKING RENT" — per year, a Vacancy Rate LINE on the RIGHT axis in % (ignore the
     asking-rent bars).
Years run 2021, 2022, 2023, 2024, 2025, and 2026 (the latest may be labeled "2026 YTD").
Read each plotted value off the axes for EACH year. Return ONLY JSON:
{"unit_left":"s.f. (millions)","unit_right":"%","points":[
  {"year":"2021","net_absorption":<num>,"construction_completions":<num>,"vacancy":<num>}, ...]}
Give one decimal of precision. If a series has no value for a year, use null."""


def render_chart_png(pdf_path: str | Path, page: int = 0, dpi: int = 250,
                     out: Path | None = None) -> bytes:
    """Render the right-hand chart column of page 1 (both charts) to PNG bytes."""
    doc = fitz.open(pdf_path)
    pg = doc[page]
    r = pg.rect
    # MarketBeat page 1: charts live in the right ~36% of width, spanning roughly
    # from below the header band (~0.20h) to just above the footer (~0.94h).
    clip = fitz.Rect(r.x0 + r.width * 0.64, r.y0 + r.height * 0.20, r.x1, r.y0 + r.height * 0.94)
    pix = pg.get_pixmap(dpi=dpi, clip=clip)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out))
    return pix.tobytes("png")


def extract_chart(pdf_path: str | Path, save_crop: Path | None = None) -> dict:
    img = render_chart_png(pdf_path, out=save_crop)
    data = claude_vision_json(CHART_SYSTEM, PROMPT, img, model=EXTRACT_MODEL, max_tokens=1500)
    # normalize to the frontend's row shape {yr, abs, sup, vac}: net absorption -> abs,
    # construction completions (deliveries) -> sup (new supply), vacancy line -> vac.
    rows = []
    for p in data.get("points", []):
        rows.append({"yr": str(p.get("year")), "abs": p.get("net_absorption"),
                     "sup": p.get("construction_completions"), "vac": p.get("vacancy")})
    return {"unit_left": data.get("unit_left", "s.f. (millions)"),
            "unit_right": data.get("unit_right", "%"), "rows": rows}
