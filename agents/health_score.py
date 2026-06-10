"""City Health Score — a blended index of CRE fundamentals + demand drivers (internal helper).

Each component is standardized against its OWN history (z = (latest − mean) / std),
sign-oriented so positive = healthier, then combined as a weighted average — the
same construction used by serious composite indices (e.g. the Chicago Fed's CFNAI).

Honesty rules baked in:
- Partial-year flows (YTD absorption/supply) are ANNUALIZED before comparison to
  full-year history, so we compare like-for-like instead of understating them.
- Components without history (rent, migration) are reported as `pending`, never faked.
- We also compute the score at EACH past period, so the card can show the trajectory
  (markets in recovery legitimately trend up) and rank a market against its peers.

Weights live in DEFAULT_WEIGHTS and are meant to be tuned: this composite IS the
house view.
"""
from __future__ import annotations

import re
from statistics import mean, pstdev

# key -> label, weight, direction (+1 = healthier when high, -1 = healthier when low)
DEFAULT_WEIGHTS: dict[str, dict] = {
    "net_absorption":  {"label": "Net absorption",   "weight": 0.25, "dir": +1},
    "vacancy":         {"label": "Vacancy",          "weight": 0.25, "dir": -1},
    "asking_rent":     {"label": "Asking rent",      "weight": 0.20, "dir": +1},
    "employment":      {"label": "Office-using jobs", "weight": 0.20, "dir": +1},
    "supply_pressure": {"label": "New supply",       "weight": 0.10, "dir": -1},
}
MIN_HISTORY = 3
FLOW_FIELDS = {"abs", "sup"}  # flows get annualized; vacancy is a stock, left as-is


def _z(latest: float, series: list[float]):
    xs = [float(v) for v in series if v is not None]
    if len(xs) < MIN_HISTORY:
        return None
    sd = pstdev(xs)
    return (latest - mean(xs)) / sd if sd else None


def _fred_obs(fred, indicator, market):
    for s in (fred or {}).get("series", []):
        if s.get("indicator") == indicator and s.get("market") == market and not s.get("error"):
            return [(o["date"], o["value"]) for o in s.get("observations", []) if o.get("value") is not None]
    return []


def _emp_yoy(fred, indicator, market):
    """Return (all_yoy_values, {year -> latest YoY in that year}) — momentum, robust."""
    obs = _fred_obs(fred, indicator, market)
    vals = [v for _, v in obs]
    allv, by_year = [], {}
    for i in range(12, len(vals)):
        if vals[i - 12]:
            g = vals[i] / vals[i - 12] - 1.0
            allv.append(g)
            by_year[obs[i][0][:4]] = g  # later months overwrite → year-end value
    return allv, by_year


def _annual_factor(period: str) -> float:
    m = re.search(r"[Qq]([1-4])", period or "")
    return 4.0 / int(m.group(1)) if m else 1.0


def _label(z: float) -> str:
    if z >= 1.0:
        return "Strong"
    if z >= 0.35:
        return "Above average"
    if z > -0.35:
        return "Average"
    if z > -1.0:
        return "Below average"
    return "Weak"


def _to_score(z: float) -> int:
    return round(max(0.0, min(100.0, 50 + 20 * z)))


def health_score(parsed: dict, fred: dict | None = None, weights: dict | None = None) -> dict | None:
    weights = weights or DEFAULT_WEIGHTS
    market = parsed.get("market") or ""
    sector = (parsed.get("sector") or "").replace("—", "").strip()
    industrial = "industrial" in sector.lower()
    rows = (parsed.get("chart") or {}).get("rows", [])
    if not rows:
        return None
    periods = [r.get("yr", "") for r in rows]
    factor = _annual_factor(parsed.get("period", ""))

    # Build each market-fundamental component as an annualized series (last point only).
    series: dict[str, list] = {}
    for key, field in (("net_absorption", "abs"), ("vacancy", "vac"), ("supply_pressure", "sup")):
        vals = [r.get(field) for r in rows]
        if field in FLOW_FIELDS and vals and vals[-1] is not None:
            vals = vals[:-1] + [vals[-1] * factor]  # annualize the partial last period
        series[key] = vals

    # Sector-aware employment momentum (FRED), with a per-year map for the trend.
    indicator = "employment_industrial" if industrial else "employment_office_using"
    emp_all, emp_by_year = _emp_yoy(fred, indicator, market)

    def composite_at(i: int):
        """Weighted z-composite using each component's value at period i."""
        parts, tw = 0.0, 0.0
        for key, vals in series.items():
            if i >= len(vals) or vals[i] is None:
                continue
            z = _z(vals[i], vals)
            if z is None:
                continue
            meta = weights[key]
            parts += meta["dir"] * z * meta["weight"]
            tw += meta["weight"]
        if emp_all:
            yr = re.search(r"(\d{4})", periods[i])
            g = emp_by_year.get(yr.group(1)) if yr else None
            if g is None:
                g = emp_all[-1] if i == len(periods) - 1 else None
            if g is not None:
                z = _z(g, emp_all)
                if z is not None:
                    parts += weights["employment"]["dir"] * z * weights["employment"]["weight"]
                    tw += weights["employment"]["weight"]
        return parts / tw if tw else None

    trend = [{"period": periods[i], "score": _to_score(c)}
             for i in range(len(rows)) if (c := composite_at(i)) is not None]
    if not trend:
        return None

    # Headline = latest period, decomposed into its drivers for the bars.
    emp_label = "Industrial jobs" if industrial else "Office-using jobs"
    last = len(rows) - 1
    comps, composite, tw = [], 0.0, 0.0
    for key, vals in series.items():
        z = _z(vals[last], vals) if vals[last] is not None else None
        if z is None:
            continue
        meta = weights[key]
        tw += meta["weight"]
    if emp_all:
        tw += weights["employment"]["weight"]
    for key, vals in series.items():
        z = _z(vals[last], vals) if vals[last] is not None else None
        if z is None:
            continue
        meta = weights[key]
        contribution = meta["dir"] * z * meta["weight"] / tw
        composite += contribution
        conf = "directional (~%dy one-pager" % len(rows) + (", YTD annualized)" if key != "vacancy" and factor != 1 else ")")
        comps.append({"key": key, "label": meta["label"], "z": round(z, 2), "dir": meta["dir"],
                      "weight": round(meta["weight"] / tw, 3), "contribution": round(contribution, 3),
                      "confidence": conf})
    if emp_all:
        z = _z(emp_all[-1], emp_all)
        if z is not None:
            contribution = weights["employment"]["dir"] * z * weights["employment"]["weight"] / tw
            composite += contribution
            comps.append({"key": "employment", "label": emp_label, "z": round(z, 2), "dir": +1,
                          "weight": round(weights["employment"]["weight"] / tw, 3),
                          "contribution": round(contribution, 3), "confidence": "robust (12y FRED, YoY)"})
    comps.sort(key=lambda c: c["contribution"], reverse=True)

    score = _to_score(composite)
    delta = trend[-1]["score"] - trend[0]["score"]
    recent = trend[-1]["score"] - trend[max(0, len(trend) - 2)]["score"]
    direction = "Improving" if recent > 1 else "Softening" if recent < -1 else "Stable"

    def _phrase(c):
        label, helping = c["label"].lower(), c["contribution"] > 0
        if c["dir"] == -1:
            return ("low " if helping else "elevated ") + label
        return label if helping else "weak " + label

    helps = [c for c in comps if c["contribution"] > 0.02]
    hurts = [c for c in comps if c["contribution"] < -0.02]
    bits = []
    if helps:
        bits.append("supported by " + ", ".join(_phrase(c) for c in helps[:2]))
    if hurts:
        bits.append("weighed down by " + ", ".join(_phrase(c) for c in hurts[-2:]))
    note = (market + " " + sector + " health is " + _label(composite).lower()
            + " and " + direction.lower() + (" — " + "; ".join(bits) if bits else "") + ".")

    return {
        "title": (market + " " + sector).strip() + " — Market Health",
        "score": score, "composite_z": round(composite, 2), "label": _label(composite),
        "direction": direction, "trend": trend, "trend_delta": delta,
        "components": comps,
        "pending": ["Asking rent (needs a rent time series)", "Population / net migration (Census — coming)"],
        "note": note,
    }
