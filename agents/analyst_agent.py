"""Research agent #3 — analyst (style-trained + critical-thinking pipeline).

Upgraded from a single draft to a 5-stage process so it writes like Cushman &
Wakefield MarketBeat AND reasons about what matters:

  0. GROUND   — load the style blueprint + 1-2 same-sector human exemplars
                (mined from the real PDFs by scripts/build_style_blueprint.py).
  1. PLAN     — extract the thesis + key facts + per-section intent (temp 0).
  2. DIVERGE  — generate several candidate headlines / bullet-sets / outlooks
                using ONLY the planned facts (higher temp for variety).
  3. JUDGE    — GPT-4.1 (different model family → less self-preference bias)
                picks the strongest of each.
  4. ASSEMBLE — combine winners; re-check that every figure is grounded in the
                source, and do one corrective rewrite if not.

Corpus is tiny (4 reports) so we use literal exemplars, not a vector store —
per the tooling research, that's deferred until ~20+ reports.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from agents.llm import CLAUDE_ANALYST_MODEL, claude_json, openai_json

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT_PATH = ROOT / "data/json/style_blueprint.json"
CORPUS_PATH = ROOT / "data/json/style_corpus.json"
VOICE_DIR = ROOT / "data/voice"          # the analyst's OWN writing samples (train the voice)
VOICE_PROFILE = ROOT / "data/json/voice_profile.json"  # distilled style guide from the samples


def _user_voice(max_chars: int = 4500) -> str:
    if not VOICE_DIR.exists():
        return ""
    chunks = [f.read_text()[:2200] for f in sorted(VOICE_DIR.glob("*"))
              if f.suffix.lower() in (".txt", ".md") and f.is_file()]
    return "\n\n---\n\n".join(chunks)[:max_chars]


def _voice_profile() -> str:
    try:
        return json.loads(VOICE_PROFILE.read_text()).get("guide", "")
    except Exception:
        return ""

ANALYST_SYSTEM = (
    "You are a Cushman & Wakefield research analyst. You write in Cushman & Wakefield's measured, "
    "data-anchored, third-person research voice. You ground every quantitative claim in the supplied "
    "facts or MarketBeat report text and never invent or estimate a figure that is not present in the source."
)
JUDGE_SYSTEM = (
    "You are a senior research editor evaluating candidate copy for a Cushman & Wakefield MarketBeat report. "
    "You judge on accuracy, Cushman & Wakefield research voice, specificity, and insight — not on position or length."
)

_DEFAULT_BLUEPRINT = {"voice": "Measured, data-anchored, neutral.", "headline": [], "bullets": [],
                      "outlook": [], "phrase_library": [], "forbidden": [], "checklist": []}


def _digits(s) -> str:
    return re.sub(r"[^0-9]", "", str(s)) if s is not None else ""


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def ungrounded_numbers(text: str, source_text: str, metrics: list[dict]) -> list[str]:
    src = _digits(source_text)
    allowed = {_digits(m.get("value")) for m in metrics if m.get("value")}
    bad = []
    for tok in re.findall(r"\$?\d[\d,]*\.?\d*", text):
        d = _digits(tok)
        if len(d) < 2 or d in src or any(d in a or a in d for a in allowed if a):
            continue
        bad.append(tok)
    return bad


def _exemplars(parsed: dict) -> str:
    corpus = _load(CORPUS_PATH, [])
    sector = (parsed.get("sector") or "").lower()
    same = [c for c in corpus if (c.get("sector") or "").lower() == sector]
    pick = (same or corpus)[:2]
    out = []
    for c in pick:
        bullets = "\n".join(f"  • {b}" for b in c.get("bullets", []))
        out.append(f"[{c.get('market')} {c.get('sector')}]\n{bullets}\n  Outlook: {c.get('outlook','')}")
    return "\n\n".join(out) if out else "(no exemplars available)"


def _facts(metrics: list[dict]) -> str:
    return "\n".join(f"- {m['label']}: {m.get('value')} (forecast: {m.get('forecast')})" for m in metrics)


def plan(parsed: dict, source_text: str) -> dict:
    prompt = f"""Plan the takeaways for {parsed.get('market')} {parsed.get('sector')} — {parsed.get('period')}.
Return ONLY JSON: {{"thesis": "<one-sentence core storyline>", "key_facts": ["<fact with its figure>", "..."],
"section_intents": {{"headline": "<what it must convey>", "bullets": "<the 3 angles>", "outlook": "<the forward view>"}}}}
Identify the SINGLE most important storyline and the 3-5 facts that support it. Use only figures present below.

FACTS:
{_facts(parsed.get('metrics', []))}

REPORT TEXT:
\"\"\"{source_text}\"\"\""""
    return claude_json(ANALYST_SYSTEM, prompt, model=CLAUDE_ANALYST_MODEL)


def diverge(parsed: dict, source_text: str, the_plan: dict, blueprint: dict) -> dict:
    prompt = f"""Write candidate copy for {parsed.get('market')} {parsed.get('sector')} — {parsed.get('period')}.
Return ONLY JSON:
{{"headlines": ["<h1>", "<h2>", "<h3>"],
  "bullet_sets": [["<b1>", "<b2>", "<b3>"], ["<b1>", "<b2>", "<b3>"]],
  "outlooks": ["<o1>", "<o2>"]}}

Plan: {json.dumps(the_plan)}

Write in this Cushman & Wakefield research voice: {blueprint.get('voice')}
Headline rules: {blueprint.get('headline')}
Bullet rules: {blueprint.get('bullets')}
Outlook rules: {blueprint.get('outlook')}
Avoid these words: {blueprint.get('forbidden')}

Here is how Cushman & Wakefield wrote comparable MarketBeat reports (match this style, do NOT copy the figures):
{_exemplars(parsed)}

Use ONLY figures that appear in the facts or report text below. Make the candidates genuinely different in angle.

FACTS:
{_facts(parsed.get('metrics', []))}

REPORT TEXT:
\"\"\"{source_text}\"\"\""""
    return claude_json(ANALYST_SYSTEM, prompt, model=CLAUDE_ANALYST_MODEL, max_tokens=2500)


def judge(cands: dict, the_plan: dict, blueprint: dict) -> dict:
    prompt = f"""Pick the strongest candidate in each category for this Cushman & Wakefield MarketBeat report.
Core thesis: {the_plan.get('thesis')}
Cushman & Wakefield research voice: {blueprint.get('voice')}

HEADLINES:
{json.dumps(list(enumerate(cands.get('headlines', []))), indent=1)}
BULLET SETS:
{json.dumps(list(enumerate(cands.get('bullet_sets', []))), indent=1)}
OUTLOOKS:
{json.dumps(list(enumerate(cands.get('outlooks', []))), indent=1)}

Score each on accuracy, Cushman & Wakefield research voice, specificity and insight; ignore ordering. Return ONLY JSON:
{{"headline_index": <int>, "bullet_set_index": <int>, "outlook_index": <int>, "rationale": "<one sentence>"}}"""
    return openai_json(JUDGE_SYSTEM, prompt, temperature=0.0)


def _correct(draft: dict, bad: list[str], parsed: dict, source_text: str) -> dict:
    prompt = f"""This draft used figures NOT found in the source: {', '.join(bad)}.
Rewrite it using ONLY figures present in the facts or report text. Return ONLY JSON
{{"headline": "...", "bullets": ["...", "...", "..."], "outlook": "..."}}.

DRAFT: {json.dumps(draft)}
FACTS:
{_facts(parsed.get('metrics', []))}
REPORT TEXT:
\"\"\"{source_text}\"\"\""""
    return claude_json(ANALYST_SYSTEM, prompt, model=CLAUDE_ANALYST_MODEL)


EVENTS_SYSTEM = (
    "You extract notable commercial real-estate transactions from a market report, grounded strictly "
    "in the provided text. You never invent deals, tenants, properties, or figures."
)


def extract_events(parsed: dict, source_text: str) -> dict:
    """Pull the marquee transactions named in the report into structured markers.
    Grounded: only deals explicitly in the text; no invented names or numbers."""
    if not source_text:
        return {"events": []}
    prompt = f"""From the report text for {parsed.get('market')} {parsed.get('sector')}, extract up to 5
NOTABLE transactions or developments that are EXPLICITLY named (leases, sales, deliveries, or projects
under construction). Return ONLY JSON:
{{"events": [{{"title": "<tenant or property name>", "detail": "<short, e.g. '225,000 s.f. lease'>",
"type": "lease|sale|development", "size_sf": <int or null>, "submarket": "<name or null>"}}]}}

Only include deals explicitly present in the text. Never invent a name, size, or submarket. If a figure
is not stated, use null. Order by size, largest first.

REPORT TEXT:
\"\"\"{source_text}\"\"\""""
    try:
        out = claude_json(EVENTS_SYSTEM, prompt, model=CLAUDE_ANALYST_MODEL, max_tokens=900)
    except Exception:
        return {"events": []}
    evs = out.get("events", []) if isinstance(out, dict) else []
    clean = [e for e in evs if isinstance(e, dict) and e.get("title")]
    return {"events": clean[:6]}


def metro_summary(office: dict, industrial: dict) -> str:
    """A short metro-wide overview interweaving the office and industrial sectors,
    grounded in both sectors' figures — for the combined (Office & Industrial) tab."""
    market = office.get("market") or industrial.get("market")
    period = office.get("period") or industrial.get("period")
    prompt = f"""Write a metro-wide overview (3-4 sentences) of {market} commercial real estate for {period},
INTERWEAVING the office and industrial sectors: how they compare, where they diverge, and what is driving
the metro as a whole. Ground every figure in the data below; never invent a number.

OFFICE metrics:
{_facts(office.get('metrics', []))}

INDUSTRIAL metrics:
{_facts(industrial.get('metrics', []))}

Return ONLY JSON: {{"summary": "<3-4 sentences>"}}"""
    try:
        out = claude_json(ANALYST_SYSTEM, prompt, model=CLAUDE_ANALYST_MODEL, max_tokens=600)
        return out.get("summary", "") if isinstance(out, dict) else ""
    except Exception:
        return ""


def write_takeaways(parsed: dict, source_text: str, max_iters: int = 2) -> dict:
    metrics = parsed.get("metrics", [])
    blueprint = _load(BLUEPRINT_PATH, _DEFAULT_BLUEPRINT)

    the_plan = plan(parsed, source_text)
    cands = diverge(parsed, source_text, the_plan, blueprint)

    # JUDGE (cross-model). Fall back to first candidate on any issue.
    try:
        pick = judge(cands, the_plan, blueprint)
    except Exception:
        pick = {}
    hi = pick.get("headline_index", 0) or 0
    bi = pick.get("bullet_set_index", 0) or 0
    oi = pick.get("outlook_index", 0) or 0

    def _at(lst, i, fallback):
        return lst[i] if isinstance(lst, list) and 0 <= i < len(lst) else (lst[0] if lst else fallback)

    result = {
        "headline": _at(cands.get("headlines", []), hi, ""),
        "bullets": _at(cands.get("bullet_sets", []), bi, []),
        "outlook": _at(cands.get("outlooks", []), oi, ""),
    }

    # ASSEMBLE + grounding guarantee (kept from the original critic).
    bad, used = [], 0
    for used in range(1, max_iters + 1):
        draft = " ".join([result.get("headline", ""), *result.get("bullets", []), result.get("outlook", "")])
        bad = ungrounded_numbers(draft, source_text, metrics)
        if not bad:
            break
        result = _correct(result, bad, parsed, source_text)

    result["_grounding"] = {"corrections": used - 1, "ungrounded_numbers": bad}
    result["_judge"] = {"rationale": pick.get("rationale"), "picked": {"h": hi, "b": bi, "o": oi}}
    result["_plan"] = {"thesis": the_plan.get("thesis")}
    return result
