"""Cushman & Wakefield MarketBeat PDF parser — NEXT LAYER, not yet implemented.

Planned: read C&W MarketBeat PDFs from data/pdfs/, extract structured market
metrics (vacancy, net absorption, asking rents, deliveries) and emit the same
SeriesRecord/Snapshot schema as the FRED pull (see pipeline/schema.py) so the
app can read both from data/json/ with identical shape and source attribution.

Kept as a stub on purpose: the first milestone is scaffolding + FRED only.
"""
from __future__ import annotations


def parse_pdf(path: str):
    raise NotImplementedError(
        "PDF parsing is the next pipeline layer — not implemented in the FRED milestone."
    )
