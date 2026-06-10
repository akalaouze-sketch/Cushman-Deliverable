"""Assemble a `deck` for a report key (single market or combined metro).

Combined keys look like `<metro>_all_<period>` (e.g. austin_all_q1-2026) and pull
both the Office and Industrial parsed reports. Reuses cached bundle takeaways so
deck generation stays fast; computes the grounded Opportunity callout once.
"""
from __future__ import annotations

import json
from pathlib import Path

from agents import analyst_agent, deck_agent
from agents.demographics import demographics
from agents.economic_health import economic_health
from agents.pdf_parser_agent import extract_source_text

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/json"


def _load(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def _source(parsed: dict) -> str:
    pdf = Path(parsed.get("source_file", ""))
    return extract_source_text(pdf) if pdf.exists() else ""


def _takeaways(key: str, parsed: dict, src: str) -> dict:
    b = _load(DATA / f"bundle_{key}.json")
    if b:
        return {"headline": b.get("headline"), "bullets": b.get("bullets", []), "outlook": b.get("outlook")}
    return analyst_agent.write_takeaways(parsed, src) if src else {}


def build_deck(key: str):
    fred = _load(DATA / "fred_latest.json")
    if "_all_" in key:
        slug, period = key.split("_all_", 1)
        ok, ik = f"{slug}_office_{period}", f"{slug}_industrial_{period}"
        office, industrial = _load(DATA / f"parsed_{ok}.json"), _load(DATA / f"parsed_{ik}.json")
        if not office or not industrial:
            return None
        osrc = _source(office)
        ot, it = _takeaways(ok, office, osrc), _takeaways(ik, industrial, _source(industrial))
        opp = deck_agent.opportunity_callout(office, osrc) if osrc else ""
        return deck_agent.build_combined(office.get("market"), office.get("period"),
                                         office, industrial, fred, ot, it, opp)
    parsed = _load(DATA / f"parsed_{key}.json")
    if not parsed:
        return None
    src = _source(parsed)
    take = _takeaways(key, parsed, src)
    opp = deck_agent.opportunity_callout(parsed, src) if src else ""
    econ = economic_health(parsed, fred)
    demo = demographics(fred, parsed.get("market"))
    ev_raw = _load(DATA / f"events_{key}.json")
    events = ev_raw.get("events") if isinstance(ev_raw, dict) else (ev_raw if isinstance(ev_raw, list) else None)
    hv = _load(DATA / f"house_view_{key}.json") or None
    return deck_agent.build_single(parsed, take, fred, opp, econ=econ, demo=demo,
                                   events=events, house_view=hv, source_text=src)
