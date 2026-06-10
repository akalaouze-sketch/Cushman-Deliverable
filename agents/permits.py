"""Permits feed — the forward development pipeline.

Austin: LIVE from the City of Austin open-data portal (Socrata, no key) — issued
commercial construction permits by month (dataset 3syk-w9eu, verified current).
San Antonio: FRED building permits (the city's ArcGIS permit layer isn't a clean
public endpoint, so we use the FRED series we already pull).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

AUSTIN_PERMITS = "https://data.austintexas.gov/resource/3syk-w9eu.json"


def _soda(params: dict) -> list[dict]:
    url = AUSTIN_PERMITS + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "cushman-marketbeat"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def austin_commercial_permits(since: str = "2024-07-01") -> list[dict]:
    """Monthly count of issued COMMERCIAL construction permits (live)."""
    rows = _soda({
        "$select": "date_trunc_ym(issue_date) as mo, count(1) as n",
        "$where": f"permit_class_mapped='Commercial' and issue_date>='{since}'",
        "$group": "mo", "$order": "mo", "$limit": 60,
    })
    out = [{"month": r["mo"][:7], "count": int(r["n"])} for r in rows if r.get("mo")]
    return [m for m in out if len(m["month"]) == 7]


def _fred_permits_monthly(fred: dict | None, market: str, n: int = 18) -> list[dict]:
    for s in (fred or {}).get("series", []):
        if s.get("indicator") == "building_permits" and s.get("market") == market and not s.get("error"):
            obs = [(o["date"][:7], o["value"]) for o in s.get("observations", []) if o.get("value") is not None]
            return [{"month": m, "count": int(v)} for m, v in obs[-n:]]
    return []


def permits_feed(slug: str, market: str, fred: dict | None = None) -> dict | None:
    """Unified feed for the current metro. Austin is live city data; SA is FRED."""
    try:
        if slug == "austin":
            monthly = austin_commercial_permits()
            if monthly:
                monthly = monthly[:-1]  # drop the in-progress current month (partial count)
            label, source, live = "Commercial permits issued", "City of Austin open data — live", True
        else:
            monthly = _fred_permits_monthly(fred, market)
            label, source, live = "Residential building permits", "FRED (Census BPS)", False
    except Exception:
        monthly = _fred_permits_monthly(fred, market)
        label, source, live = "Building permits", "FRED (fallback)", False
    monthly = [m for m in monthly if m.get("count") is not None][-15:]
    if len(monthly) < 3:
        return None
    ttm = sum(m["count"] for m in monthly[-12:])
    prev = sum(m["count"] for m in monthly[-15:-12]) if len(monthly) >= 15 else None
    recent3 = sum(m["count"] for m in monthly[-3:])
    yoy = None
    if prev:
        yoy = round((recent3 / prev - 1) * 100, 1)
    return {
        "label": label, "source": source, "live": live,
        "ttm": ttm, "yoy_pct": yoy,
        "chart": {
            "type": "column", "unit": "permits", "title": label,
            "subtitle": source,
            "categories": [m["month"] for m in monthly],
            "series": [{"name": "Permits issued", "type": "column",
                        "values": [m["count"] for m in monthly], "color": "#1f6f7e"}],
        },
    }
