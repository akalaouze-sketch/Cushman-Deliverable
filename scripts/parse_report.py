#!/usr/bin/env python3
"""Run the PDF-parsing agent on a Cushman & Wakefield MarketBeat report and save verified JSON.

Usage:
    python scripts/parse_report.py [path/to/report.pdf]

Defaults to the Dallas/Fort Worth Q1 2026 office MarketBeat in data/pdfs/. Writes
data/json/parsed_<market>_<sector>_<period>.json.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from agents.pdf_parser_agent import run  # noqa: E402

DEFAULT_PDF = ROOT / "data/pdfs/cw-marketbeat-dallas-fort-worth-office-q1-2026.pdf"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "x").lower()).strip("-")


def main() -> None:
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    if not pdf.exists():
        sys.exit(f"error: {pdf} not found")

    print(f"Parsing {pdf.name} ...")
    result = run(pdf)

    v = result["verification"]
    print(f"\n{result.get('market')} {result.get('sector')} — {result.get('period')}")
    print(f"verified={v['verified']}  iterations={v['iterations']}")
    print("-" * 72)
    for m in result.get("metrics", []):
        c = v["confidence"].get(m["key"], {})
        flag = "OK " if c.get("checks", 0) >= 2 else "!! "
        print(f"  {flag}{m['label']:30} {str(m.get('value')):18} [{m.get('forecast')}] checks={c.get('checks',0)}/3")
    if v["remaining_issues"]:
        print("\nremaining issues:")
        for i in v["remaining_issues"]:
            print("  -", i)

    out_dir = ROOT / "data/json"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"parsed_{slug(result.get('market'))}_{slug(result.get('sector'))}_{slug(result.get('period'))}.json"
    (out_dir / name).write_text(json.dumps(result, indent=2))
    print(f"\nwrote data/json/{name}")


if __name__ == "__main__":
    main()
