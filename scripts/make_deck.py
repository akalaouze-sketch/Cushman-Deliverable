#!/usr/bin/env python3
"""Generate a broker-meeting deck (PDF and/or editable PPTX) for a report key.

Usage:
    python scripts/make_deck.py [report_key] [--format pdf|pptx|both]

report_key examples: dallas-fort-worth_office_q1-2026, dallas-fort-worth_industrial_q1-2026,
or a COMBINED metro: dallas-fort-worth_all_q1-2026.
Output lands in deliverables/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from agents.deck_build import build_deck  # noqa: E402
from agents.deck_pdf import render_pdf  # noqa: E402
from agents.deck_pptx import build_pptx  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a broker deck (PDF / PPTX).")
    ap.add_argument("report", nargs="?", default="dallas-fort-worth_office_q1-2026", help="report key")
    ap.add_argument("--format", choices=["pdf", "pptx", "both"], default="both")
    args = ap.parse_args()

    print(f"Building deck for {args.report} ...")
    deck = build_deck(args.report)
    if not deck:
        sys.exit(f"error: could not build deck for '{args.report}' (missing parsed report?)")
    print(f"  {len(deck['slides'])} slides")

    out_dir = ROOT / "deliverables"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.format in ("pdf", "both"):
        print(f"  wrote {render_pdf(deck, out_dir / f'{args.report}.pdf').relative_to(ROOT)}")
    if args.format in ("pptx", "both"):
        print(f"  wrote {build_pptx(deck, out_dir / f'{args.report}.pptx').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
