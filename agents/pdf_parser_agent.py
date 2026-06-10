"""Research agent #1 — PDF parsing.

Extracts the structured "Market Fundamentals" metrics from a Cushman & Wakefield
MarketBeat report and runs an ITERATIVE, CROSS-MODEL verification loop so we trust
the numbers:

  1. Claude extracts each metric WITH the exact source sentence it came from.
  2. Three independent checks per metric:
       - grounding:  the quoted sentence actually appears in the PDF text
       - in-source:  the value's digits actually appear in the PDF text
       - cross-model: GPT-4.1 independently read the same value
  3. Any metric failing 2+ checks is fed back to Claude to re-extract.
  4. Repeat up to max_iters; emit per-field confidence.

Tools it uses: PyMuPDF + pdfplumber (text/tables), regex grounding checks,
and two LLMs (Claude + OpenAI) for the agreement check.
"""
from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF

from agents import trace
from agents.llm import claude_json, openai_json, EXTRACT_MODEL

# Sector-specific "MARKET FUNDAMENTALS" fields. The agent picks the set that
# matches the report so it never hunts for a field a sector doesn't report
# (e.g. industrial MarketBeats have no "Class A" asking-rent breakout). These mirror
# the headline stat panel a Cushman & Wakefield MarketBeat prints.
OFFICE_FIELDS = [
    ("ytd_net_absorption", "YTD Net Absorption", "s.f."),
    ("total_vacancy", "Vacancy Rate", "%"),
    ("overall_direct_asking_rent", "Overall Asking Rent (All Classes)", "$ p.s.f."),
    ("class_a_direct_asking_rent", "Class A Asking Rent", "$ p.s.f."),
    ("under_construction", "Under Construction", "s.f."),
    ("ytd_leasing_activity", "YTD Leasing Activity", "s.f."),
]
INDUSTRIAL_FIELDS = [
    ("ytd_net_absorption", "YTD Net Absorption", "s.f."),
    ("total_vacancy", "Vacancy Rate", "%"),
    ("average_asking_rent", "Asking Rent (NNN)", "$ p.s.f."),
    ("under_construction", "Under Construction", "s.f."),
    ("ytd_leasing_activity", "YTD Leasing Activity", "s.f."),
]
FIELD_SETS = {"Office": OFFICE_FIELDS, "Industrial": INDUSTRIAL_FIELDS}


def detect_sector(source: str) -> str:
    """Read the sector off the MarketBeat header (e.g. 'OFFICE Q1 2026')."""
    head = source[:800].lower()
    return "Industrial" if "industrial" in head else "Office"

EXTRACT_SYSTEM = (
    "You are a commercial real estate research data-extraction specialist. You read "
    "Cushman & Wakefield MarketBeat reports and extract printed figures EXACTLY as "
    "written. Never estimate or infer a number that is not printed. If a field is "
    "absent, set value to null."
)


def extract_source_text(pdf_path: str | Path) -> str:
    """Full text of the PDF (PyMuPDF), plus any tables pdfplumber can find."""
    doc = fitz.open(pdf_path)
    text = "\n".join(page.get_text("text") for page in doc)
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    for row in table:
                        text += "\n" + " | ".join(c or "" for c in row)
    except Exception:
        pass
    return text.strip()


def _digits(value) -> str:
    return re.sub(r"[^0-9]", "", str(value)) if value is not None else ""


def narrative_paragraphs(pdf_path: str | Path, max_paras: int = 4) -> list[str]:
    """The MarketBeat's body prose (for the live frontend) — block-level so paragraphs
    stay intact, skipping bullets, the outlook, the wordmark/footer, the disclaimer,
    and contact blocks. Keeps the ECONOMY/DEMAND/SUPPLY/PRICING prose."""
    doc = fitz.open(pdf_path)
    # Boilerplate fragments to drop: copyright, the C&W brand wordmark/tagline, the
    # MarketBeat masthead, the research-publication disclaimer, and contact lines.
    skip = ("COPYRIGHT", "CUSHMAN & WAKEFIELD", "BETTER NEVER SETTLES", "MARKETBEAT",
            "A CUSHMAN & WAKEFIELD RESEARCH PUBLICATION", "CUSHWAKE.COM", "TEL:")
    out: list[str] = []
    for block in doc[0].get_text("blocks"):
        text = re.sub(r"\s+", " ", block[4]).strip()
        up = text.upper()
        if len(text) < 160 or text[0] in "•·-":
            continue
        if any(s in up for s in skip) or text.startswith(("Looking ahead", "Outlook")):
            continue
        out.append(text)
        if len(out) >= max_paras:
            break
    return out


def claude_extract(source: str, fields: list, issues: list[str] | None = None) -> dict:
    field_lines = "\n".join(f"  - {key}: {label} ({unit})" for key, label, unit in fields)
    fix = ""
    if issues:
        fix = "\n\nA previous attempt had these problems. Re-read the text and FIX them:\n" + \
              "\n".join(f"  - {i}" for i in issues)
    prompt = f"""Extract data from the Cushman & Wakefield MarketBeat report text below.
The headline figures live in the "MARKET FUNDAMENTALS" panel; also pull the Class A
market-average asking rent and the Under Construction total from the report text/tables.
Return ONLY JSON:
{{
  "market": "<city>", "sector": "<e.g. Office>", "period": "<e.g. Q1 2026>",
  "metrics": [
    {{"key": "<field key>", "label": "<field label>", "value": "<value exactly as printed, with units>",
      "forecast": "up|down|flat", "source_quote": "<exact sentence/fragment from the text containing this value>"}}
  ]
}}
Extract these fields (one metric object each):
{field_lines}

The "forecast" reflects the directional arrow in the report's 12-Month Forecast column when present.
The "source_quote" MUST be copied verbatim from the text.{fix}

REPORT TEXT:
\"\"\"
{source}
\"\"\""""
    return claude_json(EXTRACT_SYSTEM, prompt)


def openai_independent_values(source: str, keys: list[str]) -> dict:
    prompt = f"""Independently read this Cushman & Wakefield MarketBeat report text. For each
key, return the value EXACTLY as printed (include units), or null if not present. Return a
JSON object mapping key -> value. Do not infer.

Keys: {", ".join(keys)}

TEXT:
\"\"\"
{source}
\"\"\""""
    return openai_json("You are a meticulous CRE data checker. Report only printed values.", prompt)


def verify(metrics: list[dict], source: str, openai_vals: dict) -> tuple[list[str], dict]:
    src_lower = source.lower()
    src_digits = _digits(source)
    issues, confidence = [], {}
    for m in metrics:
        key, val, quote = m.get("key"), m.get("value"), (m.get("source_quote") or "")
        if val in (None, "null", ""):
            confidence[key] = {"value": None, "checks": 0, "note": "not found"}
            continue
        grounded = bool(quote) and quote.strip()[:24].lower() in src_lower
        in_source = bool(_digits(val)) and _digits(val) in src_digits
        ov = openai_vals.get(key)
        cross = ov not in (None, "null", "") and _digits(ov) and _digits(ov) == _digits(val)
        checks = sum([grounded, in_source, bool(cross)])
        confidence[key] = {"value": val, "checks": checks, "grounded": grounded,
                           "digits_in_source": in_source, "cross_model_match": bool(cross),
                           "openai_value": ov}
        if checks < 2:
            issues.append(f"{key}: '{val}' is weak (grounded={grounded}, in_source={in_source}, gpt='{ov}')")
    return issues, confidence


def run(pdf_path: str | Path, max_iters: int = 3) -> dict:
  with trace.run("parse_report", input={"pdf": str(pdf_path), "max_iters": max_iters}) as _run:
    with trace.span("extract_source_text", kind="io") as s:
        source = extract_source_text(pdf_path)
        s.output({"chars": len(source)})
    sector = detect_sector(source)
    fields = FIELD_SETS[sector]
    keys = [k for k, _, _ in fields]
    with trace.span("openai_independent_values", kind="agent", input={"keys": keys}):
        openai_vals = openai_independent_values(source, keys)

    issues, confidence, result, used = None, {}, {}, 0
    for used in range(1, max_iters + 1):
        with trace.span(f"extract+verify pass {used}", kind="agent",
                        input={"prior_issues": issues}) as s:
            result = claude_extract(source, fields, issues)
            issues, confidence = verify(result.get("metrics", []), source, openai_vals)
            s.output({"issues": issues, "resolved": not issues})
        if not issues:
            break

    result["source"] = "Cushman & Wakefield MarketBeat PDF"
    result["source_file"] = str(pdf_path)
    result["detected_sector"] = sector
    result["verification"] = {
        "iterations": used,
        "remaining_issues": issues or [],
        "confidence": confidence,
        "verified": not issues,
    }
    _run.set_output({"market": result.get("market"), "sector": result.get("sector"),
                     "period": result.get("period"), "verified": not issues, "iterations": used})
    return result
