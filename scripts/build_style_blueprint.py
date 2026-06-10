#!/usr/bin/env python3
"""Mine a Cushman & Wakefield MarketBeat 'style blueprint' + human exemplar corpus from the report PDFs.

This is how the analyst gets "trained" on how the C&W MarketBeat research actually
writes. Run once (re-run when data/pdfs/ changes — inputs are hashed so it
self-invalidates).

Outputs:
  data/json/style_blueprint.json — per-section style rules (voice, phrasing,
                                   forbidden cliches, a validation checklist)
  data/json/style_corpus.json    — VERBATIM human bullets + outlook per report,
                                   used as few-shot exemplars (same-sector match)

Usage: python scripts/build_style_blueprint.py [--force]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from agents.llm import claude_json, CLAUDE_ANALYST_MODEL  # noqa: E402
from agents.pdf_parser_agent import extract_source_text  # noqa: E402

OUT = ROOT / "data/json"
BLUEPRINT = OUT / "style_blueprint.json"
CORPUS = OUT / "style_corpus.json"

SYSTEM = (
    "You are a writing-style analyst. You study Cushman & Wakefield MarketBeat commercial real "
    "estate research one-pagers and codify exactly how their analysts write — voice, structure, "
    "phrasing — and you copy human-written passages VERBATIM when asked."
)


def main() -> None:
    force = "--force" in sys.argv
    pdfs = sorted(p for p in (ROOT / "data/pdfs").glob("*.pdf") if "marketbeat" in p.name.lower())
    if not pdfs:
        sys.exit("no Cushman & Wakefield '*marketbeat*' PDFs in data/pdfs/")

    blocks, digest = [], hashlib.sha256()
    for p in pdfs:
        text = extract_source_text(p)[:3200]
        digest.update(text.encode("utf-8", "ignore"))
        blocks.append(f"### FILE: {p.name}\n{text}")
    inputs_hash = digest.hexdigest()[:16]

    if BLUEPRINT.exists() and not force:
        if json.loads(BLUEPRINT.read_text()).get("_inputs_hash") == inputs_hash:
            print("blueprint up to date (use --force to rebuild)")
            return

    prompt = f"""Study these {len(pdfs)} Cushman & Wakefield MarketBeat research one-pagers. Return ONLY JSON:
{{
  "blueprint": {{
    "voice": "<1-2 sentences describing the C&W MarketBeat analytical voice>",
    "headline": ["<rule>", "..."],
    "bullets": ["<rule>", "..."],
    "outlook": ["<rule>", "..."],
    "phrase_library": ["<recurring MarketBeat phrasings, e.g. 'net absorption', 'asking rents'>"],
    "forbidden": ["<cliche/hype words MarketBeat avoids, e.g. 'skyrocketing'>"],
    "checklist": ["<final-pass checks, e.g. 'every figure traceable to data'>"]
  }},
  "corpus": [
    {{"market": "<city>", "sector": "<Office|Industrial>",
      "bullets": ["<verbatim highlight bullet 1>", "<2>", "<3>"],
      "outlook": "<verbatim outlook paragraph>"}}
  ]
}}
Copy the bullets and outlook VERBATIM from each report (they are real human text — do not paraphrase).

REPORTS:
{chr(10).join(blocks)}"""

    print(f"Analyzing {len(pdfs)} reports with {CLAUDE_ANALYST_MODEL} ...")
    result = claude_json(SYSTEM, prompt, model=CLAUDE_ANALYST_MODEL, max_tokens=4000)

    blueprint = result.get("blueprint", {})
    blueprint["_inputs_hash"] = inputs_hash
    blueprint["_n_reports"] = len(pdfs)
    OUT.mkdir(parents=True, exist_ok=True)
    BLUEPRINT.write_text(json.dumps(blueprint, indent=2))
    CORPUS.write_text(json.dumps(result.get("corpus", []), indent=2))
    print(f"wrote {BLUEPRINT.name} ({len(blueprint.get('bullets', []))} bullet rules) "
          f"and {CORPUS.name} ({len(result.get('corpus', []))} exemplars)")


if __name__ == "__main__":
    main()
