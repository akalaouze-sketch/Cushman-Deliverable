#!/usr/bin/env python3
"""Extract the per-submarket MARKET STATISTICS table from a Cushman & Wakefield
MarketBeat PDF into clean JSON (one record per submarket) for the interactive map.

Each MarketBeat stats table prints, per submarket, 10 columns:
  inventory, vacant, vacancy%, current-qtr net absorption, YTD net absorption,
  YTD leasing, under construction, YTD completions, and two rent columns
  (office: asking rent all-classes / class A; industrial: overall / whse-dist net rent).

PyMuPDF lays the table out one value per line, so a submarket row = a NAME line
followed by exactly 10 VALUE lines. We skip the TOTAL / class-breakdown aggregate
rows (kept separately under "totals").

Usage: python scripts/extract_submarkets.py [path/to/marketbeat.pdf]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402  PyMuPDF

# common schema (sector-neutral so the map reads both the same way)
COLS = ["inventory_sf", "vacant_sf", "vacancy_pct", "qtr_net_absorption_sf",
        "ytd_net_absorption_sf", "ytd_leasing_sf", "under_construction_sf",
        "ytd_completions_sf", "rent_primary", "rent_secondary"]

_VALUE = re.compile(r"^\$?-?[\d,]+(?:\.\d+)?%?$")  # 18,181,131 | 37.0% | -75,808 | $28.85 | 0


def _is_value(line: str) -> bool:
    s = line.strip()
    return s == "-" or bool(_VALUE.match(s))


def _num(s: str):
    s = s.strip()
    if s in ("-", ""):
        return None
    return float(s.replace(",", "").replace("$", "").replace("%", ""))


def _is_total(name: str) -> bool:
    n = name.upper()
    return ("TOTAL" in n or n in ("CLASS A", "CLASS B", "CLASS C", "MANUFACTURING",
            "OFFICE SERVICE CENTER (FLEX)", "WAREHOUSE/DISTRIBUTION"))


def extract(pdf_path: str | Path) -> dict:
    doc = fitz.open(pdf_path)
    sector = "Industrial" if "INDUSTRIAL" in (doc[0].get_text("text").upper()) else "Office"
    # the stats page
    pg = next((i for i, p in enumerate(doc) if "MARKET STATISTICS" in p.get_text("text").upper()), 2)
    lines = [ln.strip() for ln in doc[pg].get_text("text").splitlines() if ln.strip()]
    start = next((i for i, ln in enumerate(lines) if "MARKET STATISTICS" in ln.upper()), 0)

    rows, totals, i = [], [], start + 1
    while i < len(lines):
        name = lines[i]
        vals = lines[i + 1:i + 1 + 10]
        if (not _is_value(name) and len(vals) == 10 and all(_is_value(v) for v in vals)
                and len(name) > 2 and not name.endswith(("(SF)", "RATE", "RENT"))):
            rec = {"name": name.strip()}
            for c, v in zip(COLS, vals):
                rec[c] = _num(v)
            (totals if _is_total(name) else rows).append(rec)
            i += 11
        else:
            i += 1

    rent_labels = (("Asking rent (all classes), $ p.s.f.", "Class A asking rent, $ p.s.f.")
                   if sector == "Office" else
                   ("Net rent (overall), $ p.s.f. NNN", "Warehouse/Distribution net rent, $ p.s.f. NNN"))
    market = "Dallas/Fort Worth"
    return {
        "market": market, "sector": sector, "period": "Q1 2026",
        "source": "Cushman & Wakefield MarketBeat — Market Statistics table",
        "rent_primary_label": rent_labels[0], "rent_secondary_label": rent_labels[1],
        "submarkets": rows, "totals": totals,
    }


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "x").lower()).strip("-")


def main() -> None:
    pdf = Path(sys.argv[1])
    data = extract(pdf)
    key = f"{_slug(data['market'])}_{_slug(data['sector'])}_{_slug(data['period'])}"
    out = ROOT / "data/json" / f"submarkets_{key}.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"{data['sector']}: {len(data['submarkets'])} submarkets + {len(data['totals'])} totals "
          f"-> data/json/{out.name}")
    print(f"{'SUBMARKET':28}{'INV (M sf)':>11}{'VAC %':>8}{'YTD ABS':>12}{'U/C (sf)':>12}{'RENT':>9}")
    for r in data["submarkets"]:
        inv = (r['inventory_sf'] or 0) / 1e6
        print(f"  {r['name'][:26]:26}{inv:>10.1f}{r['vacancy_pct']:>7.1f}%{r['ytd_net_absorption_sf'] or 0:>12,.0f}"
              f"{r['under_construction_sf'] or 0:>12,.0f}{(r['rent_primary'] or 0):>8.2f}")


if __name__ == "__main__":
    main()
