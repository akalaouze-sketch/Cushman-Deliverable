#!/usr/bin/env python3
"""Pull Census ACS demographics into data/json/census_latest.json.

Needs a free Census key in CENSUS_API_KEY (.env). Prints what it got so we can
verify the variables/year resolve live (the API requires a key, so this is the
first point we can confirm the exact queries work).

Usage:  python scripts/pull_census.py [--year 2023]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from agents.census import MSA, acs_metro  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2023)
    args = ap.parse_args()

    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if not key:
        sys.exit("No CENSUS_API_KEY set. Get a free key at "
                 "https://api.census.gov/data/key_signup.html and add it to .env")

    out = {}
    for market in MSA:
        rec = acs_metro(market, key, year=args.year)
        if rec:
            out[market] = rec
            print(f"  {market:12} pop {rec.get('population_m')}M · median age {rec.get('median_age')} "
                  f"· income ${rec.get('median_household_income')} · bachelor's+ {rec.get('pct_bachelors_plus')}%")
        else:
            print(f"  {market:12} FAILED — check key/variables/year")

    if not out:
        sys.exit("No data returned. Is the key activated? Try --year 2022.")
    path = ROOT / "data/json/census_latest.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {path}  ({len(out)}/{len(MSA)} metros)")


if __name__ == "__main__":
    main()
