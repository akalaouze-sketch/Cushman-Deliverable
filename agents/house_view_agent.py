"""House View — the analyst's OPINION, in your voice, versioned over time.

This is the one agent that takes a STANCE. It reads the verified metrics + the
Health Score and commits to a call (bullish / neutral / bearish) with a grounded
thesis, a conviction level, the signal that would change its mind, and where it
diverges from consensus.

It writes in YOUR voice when you drop writing samples (.txt/.md) into data/voice/ —
otherwise a measured, confident default. Every call is appended to
data/json/house_view_<key>.jsonl with a date, so the report can show a track
record ("my Q1 bear case on Dallas/Fort Worth office aged well").
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agents.llm import CLAUDE_ANALYST_MODEL, claude_json

ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "data/voice"
DATA = ROOT / "data/json"

HOUSE_SYSTEM = (
    "You write the factual REPORT TAKEAWAY for a Cushman & Wakefield MarketBeat market — a tight, grounded "
    "synthesis for an Investment Sales broker who will price, underwrite and transact in this market. This is NOT "
    "a personal opinion or a bull/bear call; you summarize what the report and the live data actually say. Every "
    "quantitative claim is grounded in the supplied figures — you never invent numbers. Lead with the fundamentals "
    "that matter for pricing and leasing (vacancy, net absorption, asking rents, the supply pipeline), note the "
    "demand backdrop, and flag what a broker should watch and the main risk to the read. Keep the HEADLINE "
    "MEASURED and factual — a clear read, not a sweeping or absolute declaration. Set 'stance' to \"neutral\". "
    "Use only plain market language: NEVER reference internal scoring artifacts — no 'z-score', "
    "'composite', 'standard deviation', or 'Health Score X/100' — in any field of your output."
)


def _voice_samples(max_chars: int = 4500) -> str:
    if not VOICE_DIR.exists():
        return ""
    chunks = [f.read_text()[:2200] for f in sorted(VOICE_DIR.glob("*"))
              if f.suffix.lower() in (".txt", ".md") and f.is_file()]
    return "\n\n---\n\n".join(chunks)[:max_chars]


def _facts(metrics: list[dict]) -> str:
    return "\n".join(f"- {m['label']}: {m.get('value')} ({m.get('forecast')})" for m in metrics)


def _user_view(key: str | None) -> str:
    """Aleks's OWN stated view for this report (his insights — captured interactively),
    which the house view must reflect. Stored in data/json/house_view_inputs.json."""
    if not key:
        return ""
    try:
        return json.loads((DATA / "house_view_inputs.json").read_text()).get(key, "")
    except Exception:
        return ""


def generate(parsed: dict, health: dict | None = None, save_key: str | None = None,
             prior: dict | None = None, report_text: str | None = None) -> dict | None:
    metrics = parsed.get("metrics", [])
    samples = _voice_samples()
    voice = (f'Write in THIS person\'s voice — match their tone, rhythm, and how they argue:\n"""{samples}"""'
             if samples else "Write in a measured, confident, first-person research voice.")
    uv = _user_view(save_key)
    view_directive = (
        f"\n\nALEKS'S OWN VIEW on this market (his actual take — the house view MUST reflect this stance and "
        f"reasoning; articulate and ground it in the figures, and do NOT contradict it):\n\"\"\"{uv}\"\"\""
        if uv else "")
    prior_directive = (
        f"\n\nThere is ALREADY a good house view here — REFINE it, don't rewrite. Keep its shape and KEEP "
        f"'what_would_change' essentially as-is; just weave Aleks's points above into the thesis/contrarian "
        f"and adjust the headline only as needed. Current view:\n"
        f"headline: {prior.get('headline')}\nthesis: {prior.get('thesis')}\n"
        f"what_would_change: {prior.get('what_would_change')}\ncontrarian: {prior.get('contrarian')}"
        if (prior and prior.get("headline")) else "")
    cw_directive = (
        f"\n\nFOR DIRECTIONAL CONSISTENCY ONLY — here is roughly what the Cushman & Wakefield MarketBeat "
        f"published. Keep your general stance broadly in line with their read; you may take a slightly "
        f"different or mildly contrarian angle, but do not DIRECTLY conflict with their core conclusion. "
        f"Do NOT borrow their specific named projects, deals, tenants, figures, or phrasing as your reasons, "
        f"and do not make it obvious you are echoing them — reach a similar general view independently, in "
        f"your OWN terms and with your own reasoning:\n"
        f"\"\"\"{report_text[:4000]}\"\"\""
        if report_text else "")
    hsum = ""
    if health:
        comps = "; ".join(f"{c['label']} {c.get('detail', '')}".strip() for c in health.get("components", []))
        hsum = (f"Economic backdrop: {health.get('label')} and {health.get('direction', '').lower()}. "
                f"Drivers — {comps}.")
    prompt = f"""Give your house view on {parsed.get('market')} {parsed.get('sector')} — {parsed.get('period')}.

{voice}{view_directive}{cw_directive}{prior_directive}

Commit to a STANCE and back it with the data. Return ONLY JSON:
{{"stance": "bullish|neutral|bearish", "headline": "<your one-line call — measured, not overly definitive or absolute>",
"thesis": "<2-3 sentences, grounded in the figures>", "conviction": "high|medium|low",
"what_would_change": "<the specific signal that would flip your view>",
"contrarian": "<where you differ from the obvious consensus, or null>"}}

Ground every number in these facts; never invent figures.
FACTS:
{_facts(metrics)}
{hsum}"""
    try:
        hv = claude_json(HOUSE_SYSTEM, prompt, model=CLAUDE_ANALYST_MODEL, max_tokens=1500)
    except Exception:
        return None
    if not isinstance(hv, dict) or not hv.get("headline"):
        return None
    hv["personalized"] = bool(samples)
    hv["period"] = parsed.get("period")
    hv["as_of"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if save_key:
        _append_history(save_key, hv)
    return hv


def _append_history(key: str, hv: dict) -> None:
    rec = {"as_of": hv.get("as_of"), "period": hv.get("period"), "stance": hv.get("stance"),
           "headline": hv.get("headline"), "conviction": hv.get("conviction")}
    (DATA / f"house_view_{key}.jsonl").open("a").write(json.dumps(rec) + "\n")


def history(key: str, limit: int = 8) -> list[dict]:
    path = DATA / f"house_view_{key}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out[-limit:]
