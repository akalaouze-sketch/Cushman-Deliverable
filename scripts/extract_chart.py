#!/usr/bin/env python3
"""Vision-extract the supply/demand chart from a Cushman & Wakefield MarketBeat PDF
and merge it into the parsed JSON. Cross-checks the latest vacancy point against
the verified 'Total vacancy' metric as a sanity guard.

Usage: python scripts/extract_chart.py [path/to/report.pdf]
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

from agents.chart_vision_agent import extract_chart  # noqa: E402

DEFAULT_PDF = ROOT / "data/pdfs/cw-marketbeat-dallas-fort-worth-office-q1-2026.pdf"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "x").lower()).strip("-")


def _num(s):
    m = re.search(r"-?\d[\d,]*\.?\d*", str(s))
    return float(m.group().replace(",", "")) if m else None


def main() -> None:
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    crop = ROOT / "frontend/assets/raw" / f"chart_{pdf.stem}.png"
    print(f"Vision-extracting chart from {pdf.name} ...")
    chart = extract_chart(pdf, save_crop=crop)

    for r in chart["rows"]:
        print(f"  {r['yr']:9} abs={r['abs']!s:>6}  sup={r['sup']!s:>6}  vac={r['vac']}%")

    # find the matching parsed file and cross-check vacancy
    parsed_files = list((ROOT / "data/json").glob("parsed_*.json"))
    target = None
    for pf in parsed_files:
        d = json.loads(pf.read_text())
        if Path(d.get("source_file", "")).name == pdf.name:
            target = (pf, d)
            break
    if target:
        pf, d = target
        tv = next((m for m in d.get("metrics", []) if m.get("key") == "total_vacancy"), None)
        if tv and chart["rows"]:
            reported = _num(tv.get("value"))
            charted = _num(chart["rows"][-1]["vac"])
            ok = reported is not None and charted is not None and abs(reported - charted) <= 1.5
            print(f"\ncross-check: latest charted vacancy {charted}% vs reported {reported}% -> "
                  f"{'OK' if ok else 'MISMATCH (review the crop)'}")
        d["chart"] = chart
        pf.write_text(json.dumps(d, indent=2))
        print(f"merged chart into {pf.name}")
    else:
        out = ROOT / "data/json" / f"chart_{_slug(pdf.stem)}.json"
        out.write_text(json.dumps(chart, indent=2))
        print(f"no parsed file matched; wrote {out.name}")


if __name__ == "__main__":
    main()
