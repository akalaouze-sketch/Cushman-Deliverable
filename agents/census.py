"""Census ACS feed — the richer demographics FRED doesn't carry.

The direct Census API now requires a key (set CENSUS_API_KEY in .env — free at
https://api.census.gov/data/key_signup.html). With the key this pulls, per metro:
median age, educational attainment (% bachelor's+), and MSA-level population/income.

Migration (true net migration) lives in the Population Estimates program, not ACS —
we verify and wire that once a key is available. Variable codes here are the
standard ACS table cells; the pull script prints what it gets so we confirm live.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

# MSA (CBSA) codes
MSA = {"Austin": "12420", "San Antonio": "41700"}
GEO = "metropolitan statistical area/micropolitan statistical area"

# ACS variable -> meaning (standard detailed-table cells)
VARS = {
    "B01003_001E": "population",
    "B01002_001E": "median_age",
    "B19013_001E": "median_household_income",
    "B15003_001E": "edu_total",          # population 25+
    "B15003_022E": "edu_bachelors",
    "B15003_023E": "edu_masters",
    "B15003_024E": "edu_professional",
    "B15003_025E": "edu_doctorate",
}
BACHELORS_PLUS = ["B15003_022E", "B15003_023E", "B15003_024E", "B15003_025E"]


def _fetch(year: int, code: str, key: str, dataset: str = "acs/acs1") -> list:
    params = {"get": ",".join(["NAME", *VARS.keys()]), "for": f"{GEO}:{code}", "key": key}
    url = f"https://api.census.gov/data/{year}/{dataset}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=45) as r:
        return json.loads(r.read())


def _num(v):
    try:
        f = float(v)
        return f if f > -1e6 else None  # Census uses large negatives for "n/a"
    except (TypeError, ValueError):
        return None


def acs_metro(market: str, key: str, year: int = 2023) -> dict | None:
    code = MSA.get(market)
    if not code or not key:
        return None
    data = None
    for ds in ("acs/acs1", "acs/acs5"):
        try:
            data = _fetch(year, code, key, ds)
            break
        except Exception:
            continue
    if not data or len(data) < 2:
        return None
    rec = dict(zip(data[0], data[1]))
    pop = _num(rec.get("B01003_001E"))
    age = _num(rec.get("B01002_001E"))
    inc = _num(rec.get("B19013_001E"))
    edu_total = _num(rec.get("B15003_001E"))
    bach = sum(_num(rec.get(c)) or 0 for c in BACHELORS_PLUS)
    out: dict = {"year": year, "_raw_name": rec.get("NAME")}
    if pop:
        out["population_m"] = round(pop / 1e6, 2)
    if age:
        out["median_age"] = round(age, 1)
    if inc:
        out["median_household_income"] = int(inc)
    if edu_total:
        out["pct_bachelors_plus"] = round(bach / edu_total * 100, 1)
    return out
