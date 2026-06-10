"""Demographics feed — population, growth, income for a metro.

Sourced from FRED's re-published Census data (no Census key needed). The direct
Census API now requires a key for every request; FRED carries the headline series
we need under the key we already have. A free Census key would later unlock richer
ACS extras (net migration, age, education).
"""
from __future__ import annotations

import json
from pathlib import Path

CENSUS_PATH = Path(__file__).resolve().parent.parent / "data/json/census_latest.json"


def _census(market: str) -> dict | None:
    try:
        return json.loads(CENSUS_PATH.read_text()).get(market)
    except Exception:
        return None


def _obs(fred: dict | None, indicator: str, market: str) -> list[tuple]:
    for s in (fred or {}).get("series", []):
        if s.get("indicator") == indicator and s.get("market") == market and not s.get("error"):
            return [(o["date"], o["value"]) for o in s.get("observations", []) if o.get("value") is not None]
    return []


def _pop_series(fred: dict | None, market: str) -> list[tuple]:
    """Current population. Prefer summing constituent counties (when the MSA-level
    series is frozen, e.g. AUSPOP@2022); else the MSA resident_population series."""
    counties = [s for s in (fred or {}).get("series", [])
                if s.get("indicator") == "msa_pop_county" and s.get("market") == market and not s.get("error")]
    if not counties:
        return _obs(fred, "resident_population", market)
    by_year: dict[str, float] = {}
    counts: dict[str, int] = {}
    for s in counties:
        for o in s.get("observations", []):
            if o.get("value") is not None:
                yr = o["date"][:4]
                by_year[yr] = by_year.get(yr, 0.0) + o["value"]
                counts[yr] = counts.get(yr, 0) + 1
    full = sorted(y for y, c in counts.items() if c == len(counties))  # only complete years
    return [(f"{y}-01-01", by_year[y]) for y in full]


def _project(series: list[tuple], target_year: int):
    """Project a yearly series forward to target_year via its 3-yr CAGR (or last YoY).
    Returns (actual_value, actual_year, projected_value, per-year rate %)."""
    yr = int(series[-1][0][:4])
    val = series[-1][1]
    rate = None
    if len(series) >= 4 and series[-4][1]:
        rate = (val / series[-4][1]) ** (1 / 3) - 1
    elif len(series) >= 2 and series[-2][1]:
        rate = val / series[-2][1] - 1
    n = max(0, target_year - yr)
    proj = val * (1 + rate) ** n if rate is not None else val
    return val, yr, proj, (round(rate * 100, 1) if rate is not None else None)


def _quarter(date_str: str) -> str:
    return f"Q{(int(date_str[5:7]) - 1) // 3 + 1} {date_str[:4]}"


def demographics(fred: dict | None, market: str) -> dict | None:
    # MarketBeats print "Dallas/Fort Worth" (slash); FRED stores "Dallas-Fort Worth" (hyphen).
    market = (market or "").replace("/", "-")
    pop = _pop_series(fred, market)                        # thousands of persons (annual, current)
    inc = _obs(fred, "median_household_income", market)    # dollars (annual)
    wage = _obs(fred, "avg_weekly_wage", market)           # $/week (QCEW, quarterly — freshest)
    out: dict = {}
    # projected metrics target the latest population year + 1; official figures lag, so the
    # current year is a clearly-labeled projection with its derivation shown, never an "actual".
    target = (int(pop[-1][0][:4]) + 1) if pop else None
    if pop:
        yr, val = pop[-1]
        growth = (val / pop[-2][1] - 1) * 100 if len(pop) >= 2 and pop[-2][1] else None
        cagr = ((val / pop[-6][1]) ** (1 / 5) - 1) * 100 if len(pop) >= 6 and pop[-6][1] else None
        _, _, pj, rate = _project(pop, target)
        out["population"] = {"value_m": round(val / 1000, 2), "year": yr[:4],
                             "yoy_pct": round(growth, 1) if growth is not None else None,
                             "cagr5_pct": round(cagr, 1) if cagr is not None else None,
                             "proj_m": round(pj / 1000, 2), "proj_year": str(target),
                             "proj_rate_pct": rate}
    if inc and target:
        v, y, pj, rate = _project(inc, target)
        out["median_income"] = {"value": int(v), "year": str(y), "proj": int(pj),
                                "proj_year": str(target), "proj_rate_pct": rate}
    if wage:
        wk = wage[-1][1]
        yoy = (wk / wage[-5][1] - 1) * 100 if len(wage) >= 5 and wage[-5][1] else None
        out["avg_wage"] = {"annual": int(round(wk * 52)), "weekly": int(round(wk)),
                           "period": _quarter(wage[-1][0]),
                           "yoy_pct": round(yoy, 1) if yoy is not None else None}
    # overlay richer ACS fields once a Census key has pulled them (median age,
    # education — signals FRED doesn't carry)
    c = _census(market)
    if c:
        if c.get("median_age") is not None:
            out["median_age"] = {"value": c["median_age"], "year": c.get("year")}
        if c.get("pct_bachelors_plus") is not None:
            out["bachelors_plus"] = {"value": c["pct_bachelors_plus"], "year": c.get("year")}
    return out or None
