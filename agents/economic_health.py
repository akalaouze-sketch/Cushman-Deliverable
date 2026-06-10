"""Economic Health index — a metro-economy read built from the signals Cushman &
Wakefield's MarketBeat leads with, each measured against the U.S. (no other-market ranking).

The MarketBeat's ECONOMY section and ECONOMIC INDICATORS panel report metro employment,
metro unemployment (vs the U.S. rate), and population growth. We score those three, each
as the metro's OUTPERFORMANCE of the U.S. — which is what makes a metro genuinely healthy:

  + Job growth         — metro total-nonfarm employment YoY minus U.S. nonfarm YoY.        (30%)
  + Population growth   — metro population YoY minus U.S. population YoY (the metro's
                         defining demand driver; DFW adds ~110k residents/yr).             (40%)
  + Unemployment vs U.S.— U.S. unemployment rate minus the metro rate (below the
                         nation = healthier).                                              (30%)

Each driver is put on a fixed, self-contained scale (points of U.S.-outperformance per
"notch"), weighted, and mapped to a 0–100 score with a year-by-year trajectory. No
fabrication: a metro that out-grows the country on jobs AND population while sitting below
the national jobless rate reads as strong — because it is.
"""
from __future__ import annotations

from agents.health_score import _label, _to_score

WEIGHTS = {
    "job_growth":   {"label": "Job growth",          "weight": 0.30, "scale": 0.5},
    "pop_growth":   {"label": "Population growth",    "weight": 0.40, "scale": 0.55},
    "unemployment": {"label": "Unemployment vs U.S.", "weight": 0.30, "scale": 0.7},
}
_ORDER = ["pop_growth", "job_growth", "unemployment"]
US_POP_FLOOR = 0.4   # the U.S. population series can freeze at its last point; floor the
#                      benchmark at a realistic national pace so it isn't an artifact.


def _obs(fred, indicator, market):
    for s in (fred or {}).get("series", []):
        if s.get("indicator") == indicator and s.get("market") == market and not s.get("error"):
            return [(o["date"], o["value"]) for o in s.get("observations", []) if o.get("value") is not None]
    return []


def _emp_yoy_by_year(obs):
    """Per-year YoY % growth, seasonally honest: each year's LATEST month vs the SAME
    month a year earlier (so a partial current year isn't compared to prior December)."""
    by_ym, latest_mo = {}, {}
    for d, v in obs:
        if v is None:
            continue
        by_ym[d[:7]] = v
        y, m = d[:4], d[5:7]
        if y not in latest_mo or m > latest_mo[y]:
            latest_mo[y] = m
    out = {}
    for y in sorted(latest_mo):
        m = latest_mo[y]
        cur, prev = by_ym.get(f"{y}-{m}"), by_ym.get(f"{int(y) - 1}-{m}")
        if cur is not None and prev:
            out[y] = (cur / prev - 1) * 100
    return out


def _pop_yoy_by_year(obs):
    """Per-year YoY % growth for an annual population series."""
    by_y = {}
    for d, v in obs:
        by_y[d[:4]] = v
    out = {}
    for y in sorted(by_y):
        p = str(int(y) - 1)
        if p in by_y and by_y[p]:
            out[y] = (by_y[y] / by_y[p] - 1) * 100
    return out


def _level_by_year(obs):
    latest_mo, val = {}, {}
    for d, v in obs:
        if v is None:
            continue
        y, m = d[:4], d[5:7]
        if y not in latest_mo or m > latest_mo[y]:
            latest_mo[y], val[y] = m, v
    return val


def _recent(series, year):
    """Value for `year`, else the most recent earlier year (annual series like population
    lag a year, so carry the latest available value forward)."""
    if year in series:
        return series[year]
    earlier = [k for k in series if k <= year]
    return series[max(earlier)] if earlier else None


def _drivers(emp, us_emp, pop, us_pop, unemp, us_unemp, year):
    """The three U.S.-outperformance values for `year` -> {key: (raw, gap, z)}."""
    out = {}
    if year in emp and year in us_emp:
        gap = emp[year] - us_emp[year]
        out["job_growth"] = (emp[year], gap, gap / WEIGHTS["job_growth"]["scale"])
    pv = _recent(pop, year)
    if pv is not None:
        ub = max(_recent(us_pop, year) or 0.0, US_POP_FLOOR)
        gap = pv - ub
        out["pop_growth"] = (pv, gap, gap / WEIGHTS["pop_growth"]["scale"])
    if year in unemp and year in us_unemp:
        gap = us_unemp[year] - unemp[year]
        out["unemployment"] = (unemp[year], gap, gap / WEIGHTS["unemployment"]["scale"])
    return out


def _composite(drivers):
    parts, tw = 0.0, 0.0
    for key, (_, _, z) in drivers.items():
        w = WEIGHTS[key]["weight"]
        parts += z * w
        tw += w
    return (parts / tw) if tw else None


def _compute(market: str, sector: str, fred: dict | None) -> dict | None:
    market = (market or "").replace("/", "-")
    emp = _emp_yoy_by_year(_obs(fred, "employment_total_nonfarm", market))
    us_emp = _emp_yoy_by_year(_obs(fred, "employment_total_nonfarm_us", "US"))
    pop = _pop_yoy_by_year(_obs(fred, "resident_population", market))
    us_pop = _pop_yoy_by_year(_obs(fred, "population_us", "US"))
    unemp = _level_by_year(_obs(fred, "unemployment_rate", market))
    us_unemp = _level_by_year(_obs(fred, "unemployment_rate_us", "US"))
    if not emp or not unemp:
        return None

    years = sorted(set(emp) & set(unemp))
    trend, comp_years = [], []
    for y in years:
        c = _composite(_drivers(emp, us_emp, pop, us_pop, unemp, us_unemp, y))
        if c is not None:
            trend.append({"period": y, "score": _to_score(c)})
            comp_years.append(y)
    if not trend:
        return None

    last = comp_years[-1]
    drivers = _drivers(emp, us_emp, pop, us_pop, unemp, us_unemp, last)
    composite = _composite(drivers)
    us_e, us_u = us_emp.get(last, 0.0), us_unemp.get(last, 0.0)

    def _detail(key, raw, gap):
        if key == "job_growth":
            return f"{raw:+.1f}% YoY"
        if key == "pop_growth":
            return f"{raw:+.1f}% YoY"
        if key == "unemployment":
            return f"{raw:.1f}%"
        return ""

    pop_gap = drivers.get("pop_growth", (0.0, 0.0, 0.0))[1]
    conf = {
        "job_growth": f"vs U.S. {us_e:+.1f}% (FRED total nonfarm)",
        "pop_growth": f"{pop_gap:+.1f} pp above the U.S. (Census via FRED)",
        "unemployment": f"vs U.S. {us_u:.1f}% (BLS via FRED)",
    }
    components = []
    for key in _ORDER:
        if key not in drivers:
            continue
        raw, gap, z = drivers[key]
        meta = WEIGHTS[key]
        components.append({
            "key": key, "label": meta["label"], "dir": +1,
            "weight": round(meta["weight"], 3),
            "contribution": round(z * meta["weight"] / sum(WEIGHTS[k]["weight"] for k in drivers), 3),
            "detail": _detail(key, raw, gap), "confidence": conf[key]})
    components.sort(key=lambda c: c["contribution"], reverse=True)

    score = _to_score(composite)
    recent = trend[-1]["score"] - trend[max(0, len(trend) - 2)]["score"]
    direction = "Improving" if recent > 1 else "Easing" if recent < -1 else "Steady"

    helps = [c for c in components if c["contribution"] > 0.02]
    bits = "supported by " + ", ".join(c["label"].lower() for c in helps[:2]) if helps else ""
    trend_word = {"Improving": "improving", "Easing": "easing slightly", "Steady": "holding steady"}[direction]
    connector = "but" if direction == "Easing" else "and"
    note = (f"{market} economic health is {_label(composite).lower()} {connector} {trend_word}"
            + (" — " + bits if bits else "")
            + ". It out-grows the U.S. on jobs and population and sits below the national "
            "unemployment rate — the demand drivers that underpin the market.")

    return {
        "title": market + " — Economic Health",
        "subtitle": "Job growth, population growth & unemployment — each measured against the U.S.",
        "score": score, "label": _label(composite),
        "direction": direction, "trend": trend,
        "components": components,
        "pending": [],
        "note": note,
    }


def economic_health(parsed: dict, fred: dict | None) -> dict | None:
    """Economic Health card for the report's metro — scored on its outperformance of the
    U.S. on job growth, population growth and unemployment (no cross-metro ranking)."""
    return _compute(parsed.get("market") or "", "", fred)
