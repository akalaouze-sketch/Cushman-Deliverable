"""Research agent #2 — visuals.

Turns verified data into chart/KPI specs the frontend renders, then VALIDATES that
every value in a spec traces back to a source figure (parsed metric or FRED point).
Specs failing validation are dropped and reported — no chart shows a number that
isn't backed by data. An optional LLM step writes a one-line grounded caption.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from agents.llm import claude_text


def _num(s):
    m = re.search(r"-?\d[\d,]*\.?\d*", str(s))
    return float(m.group().replace(",", "")) if m else None


def kpi_cards(parsed: dict) -> list[dict]:
    return [{"label": m["label"], "value": m.get("value"), "forecast": m.get("forecast"),
             "key": m.get("key")} for m in parsed.get("metrics", [])]


def fred_trend(fred_snapshot: dict, indicator: str, market: str, title: str) -> dict | None:
    for s in fred_snapshot.get("series", []):
        if s.get("indicator") == indicator and s.get("market") == market and not s.get("error"):
            obs = s.get("observations", [])
            if not obs:
                return None
            return {"type": "line", "title": title, "unit": s.get("units", ""),
                    "series_id": s.get("series_id"),
                    "points": [{"x": o["date"], "y": o["value"]} for o in obs]}
    return None


def validate(spec: dict, parsed: dict) -> bool:
    """A KPI/chart is valid only if its values come from a known source."""
    if spec.get("type") == "kpi":
        keys = {m.get("key") for m in parsed.get("metrics", [])}
        return spec.get("key") in keys
    if spec.get("type") == "line":
        return bool(spec.get("series_id")) and bool(spec.get("points"))
    return False


def caption(chart: dict) -> str:
    try:
        pts = chart.get("points", [])
        first, last = pts[0]["y"], pts[-1]["y"]
        trend = "rose" if last > first else "fell" if last < first else "held"
        return claude_text(
            "You write one-sentence, factual chart captions for Cushman & Wakefield research. No new numbers.",
            f"Chart '{chart['title']}' {trend} from {first} to {last} ({chart['unit']}). "
            f"Write one neutral caption. Do not introduce figures beyond these.",
            max_tokens=80,
        )
    except Exception:
        return ""


def build_specs(parsed: dict, fred_snapshot: dict | None = None, with_captions: bool = False) -> dict:
    cards = [{"type": "kpi", **c} for c in kpi_cards(parsed)]
    cards = [c for c in cards if validate(c, parsed)]

    charts, dropped = [], []
    if fred_snapshot:
        market = parsed.get("market", "")
        for indicator, title in [("employment_total_nonfarm", f"{market} — Total Nonfarm Employment"),
                                  ("unemployment_rate", f"{market} — Unemployment Rate")]:
            spec = fred_trend(fred_snapshot, indicator, market, title)
            if spec and validate(spec, parsed):
                if with_captions:
                    spec["caption"] = caption(spec)
                charts.append(spec)
            elif spec is None:
                dropped.append(f"{indicator}/{market}")

    return {"market": parsed.get("market"), "sector": parsed.get("sector"),
            "period": parsed.get("period"), "kpis": cards, "charts": charts,
            "dropped": dropped}
