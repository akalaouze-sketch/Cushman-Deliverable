"""Lease-expiration wall — the upcoming roll by year (direct vs sublease).

Reads lease data YOU control, so it works the moment you paste a CoStar export:
  data/leases_<key>.csv   (columns: tenant, building, expiry_year, sf, type)
  data/json/leases_<key>.json  ({"leases": [{tenant, building, expiry_year, sf, type}]})

Brokers live off this view — it's the wall of space coming available, and the
sublease share is the early-warning signal.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TAN = "#c9a77d"
RED = "#E2231A"


def _rows(key: str) -> list[dict]:
    csvp = DATA / f"leases_{key}.csv"
    jsonp = DATA / "json" / f"leases_{key}.json"
    if csvp.exists():
        return list(csv.DictReader(csvp.read_text().splitlines()))
    if jsonp.exists():
        return json.loads(jsonp.read_text()).get("leases", [])
    return []


def _clean(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        try:
            out.append({
                "year": int(str(r.get("expiry_year"))[:4]),
                "sf": float(str(r.get("sf")).replace(",", "").replace("$", "")),
                "type": (r.get("type") or "direct").strip().lower(),
            })
        except (TypeError, ValueError):
            continue
    return out


def lease_wall(key: str, horizon: int = 5) -> dict | None:
    leases = _clean(_rows(key))
    if not leases:
        return None
    start = min(l["year"] for l in leases)
    yrs = [start + i for i in range(horizon)]  # 5-yr window (e.g. 2026–2030)

    def total(y, sub):
        return round(sum(l["sf"] for l in leases
                         if l["year"] == y and (l["type"] == "sublease") == sub) / 1000, 1)

    direct = [total(y, False) for y in yrs]
    sublease = [total(y, True) for y in yrs]
    if not any(direct) and not any(sublease):
        return None
    src = "leases_%s.csv" % key
    return {
        "type": "column", "stacked": True, "unit": "000s s.f.",
        "title": "Lease expiration schedule",
        "subtitle": "Upcoming roll — direct vs sublease (000s s.f.)",
        "source": "Illustrative sample data — replace data/%s with a CoStar lease export." % src,
        "categories": [str(y) for y in yrs],
        "series": [
            {"name": "Direct", "type": "column", "values": direct, "color": TAN},
            {"name": "Sublease", "type": "column", "values": sublease, "color": RED},
        ],
    }
