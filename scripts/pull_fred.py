#!/usr/bin/env python3
"""Pull macro indicators from FRED into a timestamped JSON snapshot.

Standalone pipeline step — the app only ever reads the JSON this writes, never
FRED directly. Run it on a schedule or before building a report.

Usage:
    python scripts/pull_fred.py [--years N] [--out PATH]

Reads FRED_API_KEY from .env (or the environment). Writes
  data/json/fred_<UTC-timestamp>.json   (immutable snapshot)
  data/json/fred_latest.json            (pointer the app reads)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:  # load .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from pipeline.config import (  # noqa: E402
    DEFAULT_HISTORY_YEARS,
    FRED_MSA_POP_COUNTIES,
    FRED_NATIONAL_SERIES,
    FRED_SERIES,
)
from pipeline.fred import FredClient, FredError  # noqa: E402
from pipeline.schema import (  # noqa: E402
    SCHEMA_VERSION,
    Observation,
    SeriesRecord,
    Snapshot,
)


def _fetch(client: FredClient, indicator: str, market: str, series_id: str,
           start: str) -> SeriesRecord:
    rec = SeriesRecord(indicator=indicator, market=market, source="FRED",
                       series_id=series_id)
    try:
        meta = client.series_meta(series_id)
        rec.title = meta.get("title", "")
        rec.units = meta.get("units", "")
        rec.frequency = meta.get("frequency", "")
        obs = client.observations(series_id, start=start)
        rec.observations = [Observation(**o) for o in obs]
        if rec.observations:
            rec.latest = rec.observations[-1]
    except FredError as exc:
        rec.error = str(exc)
    return rec


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull FRED macro indicators.")
    parser.add_argument("--years", type=int, default=DEFAULT_HISTORY_YEARS,
                        help="years of history to request")
    parser.add_argument("--out", type=Path, default=None,
                        help="override output path for the snapshot")
    args = parser.parse_args()

    try:
        client = FredClient.from_env()
    except FredError as exc:
        sys.exit(f"error: {exc}")

    now = datetime.now(timezone.utc)
    start = date(now.year - args.years, 1, 1).isoformat()

    snapshot = Snapshot(schema_version=SCHEMA_VERSION, generated_at=now.isoformat(),
                        source="FRED", history_years=args.years)

    print(f"Pulling FRED series (history from {start})...")
    for indicator, markets in FRED_SERIES.items():
        for market, series_id in markets.items():
            print(f"  {indicator:26} {market:12} {series_id}")
            snapshot.series.append(_fetch(client, indicator, market, series_id, start))
    for indicator, series_id in FRED_NATIONAL_SERIES.items():
        print(f"  {indicator:26} {'US':12} {series_id}")
        snapshot.series.append(_fetch(client, indicator, "US", series_id, start))
    for market, counties in FRED_MSA_POP_COUNTIES.items():  # current MSA pop = sum of counties
        for series_id in counties:
            print(f"  {'msa_pop_county':26} {market:12} {series_id}")
            snapshot.series.append(_fetch(client, "msa_pop_county", market, series_id, start))

    out_dir = ROOT / "data" / "json"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out or (out_dir / f"fred_{stamp}.json")
    payload = json.dumps(snapshot.to_dict(), indent=2)
    out_path.write_text(payload)
    (out_dir / "fred_latest.json").write_text(payload)

    ok = sum(1 for s in snapshot.series if not s.error)
    failed = [s.series_id for s in snapshot.series if s.error]
    print(f"\nwrote {out_path}")
    print(f"{ok}/{len(snapshot.series)} series resolved")
    if failed:
        print("FAILED (verify IDs in pipeline/config.py): " + ", ".join(failed))


if __name__ == "__main__":
    main()
