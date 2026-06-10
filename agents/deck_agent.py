"""Slide-deck agent — plans an earnings-style, multi-graph broker deck.

Decides WHICH charts to make from the available data (fundamentals, the
vision-extracted supply/demand series, and FRED employment), then emits a
generic `deck` structure that both renderers consume:

  deck = {title, subtitle, slides: [ {title, subtitle, charts:[spec], kpis:[...],
          box:{heading, lines}} | {title_slide: True, ...} ]}

A chart spec = {type:'column'|'line', title, categories:[...], unit, percent:bool,
                series:[{name, values:[...], color:'#hex'}]}.

Works for a single market or a combined metro (Office + Industrial).
"""
from __future__ import annotations

import json
from pathlib import Path

from agents.analyst_agent import ungrounded_numbers
from agents.llm import CLAUDE_ANALYST_MODEL, claude_json

_DATA = Path(__file__).resolve().parent.parent / "data/json"

# Cushman & Wakefield MarketBeat palette
NAVY = "#1B1C3A"
TEAL = "#00A9CE"
TAN = "#E8A23D"   # gold — secondary chart series / highlights
BLUE = "#0B7C97"  # darker teal for the second series / contrast
SLATE = "#1B1C3A"

OPP_SYSTEM = (
    "You are a Cushman & Wakefield broker-side advisor preparing a client meeting. You give a concise, "
    "actionable opportunity callout grounded strictly in the provided figures — never invent numbers."
)


def _facts(metrics):
    return "\n".join(f"- {m['label']}: {m.get('value')} (forecast: {m.get('forecast')})" for m in metrics)


def opportunity_callout(parsed: dict, source_text: str, max_iters: int = 2) -> str:
    metrics = parsed.get("metrics", [])
    if not source_text:
        return ""
    base = f"""For a broker client meeting on {parsed.get('market')} {parsed.get('sector')} — {parsed.get('period')},
write a 2-3 sentence "Opportunity" callout: the single most actionable takeaway for a tenant or investor,
tied to the fundamentals. Use ONLY figures present in the facts or report text. Return ONLY JSON
{{"opportunity": "..."}}.

FACTS:
{_facts(metrics)}

REPORT TEXT:
\"\"\"{source_text}\"\"\""""
    note, text = "", ""
    for _ in range(max_iters):
        result = claude_json(OPP_SYSTEM, base + note, model=CLAUDE_ANALYST_MODEL, max_tokens=600)
        text = result.get("opportunity", "")
        bad = ungrounded_numbers(text, source_text, metrics)
        if not bad:
            break
        note = f"\n\nThe prior draft used figures not in the source: {', '.join(bad)}. Use only sourced figures."
    return text


# ---- chart planning ----

def _years(chart):
    return [str(r.get("yr")) for r in (chart or {}).get("rows", [])]


def _col(chart, key):
    return [r.get(key) for r in (chart or {}).get("rows", [])]


def fred_annual(fred: dict, indicator: str, market: str, years: list[str]):
    """Latest FRED value per calendar year, aligned to the chart's year labels."""
    market = (market or "").replace("/", "-")  # MarketBeat prints "Dallas/Fort Worth"; FRED uses "Dallas-Fort Worth"
    for s in (fred or {}).get("series", []):
        if s.get("indicator") == indicator and s.get("market") == market and not s.get("error"):
            by_year = {}
            for o in s.get("observations", []):
                by_year[o["date"][:4]] = o["value"]
            return [by_year.get("".join(ch for ch in y if ch.isdigit())) for y in years]
    return None


def supply_demand_chart(chart, label="", inv_msf=None):
    yrs = _years(chart)
    if not yrs:
        return None
    abs_p, sup_p = _pct_of_inv(chart, "abs", inv_msf), _pct_of_inv(chart, "sup", inv_msf)
    if abs_p and sup_p:  # both flows as % of total inventory (signed — absorption goes negative)
        return {"type": "column", "percentSigned": True, "title": f"Net absorption & new supply{label}",
                "subtitle": "% of total inventory", "categories": yrs, "series": [
                    {"name": "Net Absorption", "values": abs_p, "color": TAN},
                    {"name": "New Supply", "values": sup_p, "color": BLUE}]}
    unit = (chart or {}).get("unit_left", "s.f. (millions)")
    return {"type": "column", "title": f"Net absorption & new supply{label}", "unit": unit,
            "categories": yrs, "series": [
                {"name": "Net Absorption", "values": _col(chart, "abs"), "color": TAN},
                {"name": "New Supply", "values": _col(chart, "sup"), "color": BLUE}]}


def vacancy_chart(chart, label=""):
    yrs = _years(chart)
    if not yrs:
        return None
    return {"type": "line", "title": f"Total vacancy rate{label}", "percent": True, "unit": "%",
            "categories": yrs, "series": [{"name": "Total Vacancy", "values": _col(chart, "vac"), "color": TEAL}]}


def employment_chart(fred, market, years):
    emp = fred_annual(fred, "employment_total_nonfarm", market, years)
    if not emp or not any(v is not None for v in emp):
        return None
    return {"type": "line", "title": f"{market} total nonfarm employment", "unit": "000s",
            "categories": years, "series": [{"name": "Employment (000s)", "values": emp, "color": SLATE}]}


def fred_yoy_annual(fred, indicator, market, years):
    """Year-over-year % growth per calendar year, aligned to the chart's year labels.
    Seasonally honest: compares each year's LATEST available month to the SAME month a
    year earlier. A partial current year therefore uses e.g. Apr-over-Apr — NOT
    Apr-2026-over-Dec-2025, which on a seasonal series reads as a spurious ~-4% drop."""
    market = (market or "").replace("/", "-")  # MarketBeat slash -> FRED hyphen
    for s in (fred or {}).get("series", []):
        if s.get("indicator") == indicator and s.get("market") == market and not s.get("error"):
            by_ym, latest_mo = {}, {}   # 'YYYY-MM'->value ; 'YYYY'->latest 'MM' present
            for o in s.get("observations", []):
                d, v = o["date"], o["value"]
                if v is None:
                    continue
                by_ym[d[:7]] = v
                y, m = d[:4], d[5:7]
                if y not in latest_mo or m > latest_mo[y]:
                    latest_mo[y] = m
            out = []
            for y in years:
                yr = "".join(ch for ch in str(y) if ch.isdigit())
                m = latest_mo.get(yr)
                cur = by_ym.get(f"{yr}-{m}") if m else None
                prev = by_ym.get(f"{int(yr) - 1}-{m}") if (m and yr) else None
                out.append(round((cur / prev - 1) * 100, 1) if (cur and prev) else None)
            return out
    return None


def demand_driver_chart(parsed, fred):
    """Demand driver: sector-relevant job growth (line, right axis) over net
    absorption (bars, left) — employment leads leasing."""
    chart = parsed.get("chart") or {}
    yrs = _years(chart)
    if not yrs:
        return None
    industrial = "industrial" in (parsed.get("sector") or "").lower()
    indicator = "employment_industrial" if industrial else "employment_office_using"
    jobs = fred_yoy_annual(fred, indicator, parsed.get("market", ""), yrs)
    if not jobs or not any(v is not None for v in jobs):
        return None
    kind = "Industrial" if industrial else "Office-using"
    abs_p = _pct_of_inv(chart, "abs", _inv_msf(parsed.get("market"), parsed.get("sector")))
    return {
        "type": "combo",
        "title": f"{kind} jobs vs net absorption",
        "subtitle": "Demand driver — employment growth leads leasing",
        "categories": yrs,
        "leftPercent": bool(abs_p),  # left axis = net absorption as % of inventory
        "series": [
            {"name": "Net absorption (% of inv.)" if abs_p else "Net absorption (M s.f.)",
             "type": "column", "axis": "left", "values": abs_p or _col(chart, "abs"), "color": TAN},
            {"name": f"{kind} job growth (YoY %)", "type": "line", "axis": "right",
             "percent": True, "values": jobs, "color": SLATE},
        ],
    }


def _k(chart):
    """Vacancy pts per net M s.f. (Δvacancy / Σ(supply−absorption)) — also = 100/inventory."""
    rows = (chart or {}).get("rows", [])
    if len(rows) < 2:
        return None
    dv = sum((rows[i].get("vac") or 0) - (rows[i - 1].get("vac") or 0) for i in range(1, len(rows)))
    ns = sum((rows[i].get("sup") or 0) - (rows[i].get("abs") or 0) for i in range(1, len(rows)))
    k = dv / ns if ns else None
    return k if (k and k > 0) else None


def _inv_msf(market, sector):
    """Real total inventory (millions s.f.) for a market+sector, from cre_inventory.json.
    Chart flows are stored in millions of s.f., so % of inventory = value / inventory × 100."""
    if not market:
        return None
    market = market.replace("/", "-")  # cre_inventory.json is keyed by the FRED-style "Dallas-Fort Worth"
    s = "Industrial" if "indust" in (sector or "").lower() else "Office"
    try:
        rec = json.loads((_DATA / "cre_inventory.json").read_text()).get(f"{market}|{s}")
    except Exception:
        rec = None
    return (rec or {}).get("inventory_msf")


def _pct_of_inv(chart, field, inv_msf=None):
    """A flow (absorption/supply) as % of total inventory. Uses the REAL total inventory
    (inv_msf, in millions s.f.) when available — k = 100/inventory; falls back to the
    vacancy-derived proxy only if no sourced inventory figure is supplied."""
    k = (100.0 / inv_msf) if inv_msf else _k(chart)
    if not k:
        return None
    return [round(v * k, 2) if v is not None else None for v in _col(chart, field)]


def combo_charts(office: dict, industrial: dict, fred: dict | None, market: str) -> list[dict]:
    """Metro-wide comparison charts for the combined tab: Office vs Industrial on
    the same axes (vacancy, net absorption as % of inventory, and the job engine)."""
    oc = office.get("chart") or {}
    ic = industrial.get("chart") or {}
    yrs = _years(oc) or _years(ic)
    if not yrs:
        return []
    out = [
        {"type": "column", "percent": True, "title": "Total vacancy — Office vs Industrial",
         "subtitle": "Both sectors, same scale", "categories": yrs, "series": [
             {"name": "Office", "values": _col(oc, "vac"), "color": NAVY},
             {"name": "Industrial", "values": _col(ic, "vac"), "color": TAN}]},
    ]
    abs_o = _pct_of_inv(oc, "abs", _inv_msf(market, office.get("sector") or "Office"))
    abs_i = _pct_of_inv(ic, "abs", _inv_msf(market, industrial.get("sector") or "Industrial"))
    if abs_o or abs_i:  # net absorption as % of total inventory (signed — can go negative)
        out.append({"type": "column", "percentSigned": True, "title": "Net absorption — Office vs Industrial",
                    "subtitle": "% of total inventory (annual)", "categories": yrs, "series": [
                        {"name": "Office", "values": abs_o or _col(oc, "abs"), "color": NAVY},
                        {"name": "Industrial", "values": abs_i or _col(ic, "abs"), "color": TAN}]})
    else:
        out.append({"type": "column", "title": "Net absorption — Office vs Industrial",
                    "subtitle": "Millions s.f.", "categories": yrs, "series": [
                        {"name": "Office", "values": _col(oc, "abs"), "color": NAVY},
                        {"name": "Industrial", "values": _col(ic, "abs"), "color": TAN}]})
    off_jobs = fred_yoy_annual(fred, "employment_office_using", market, yrs)
    ind_jobs = fred_yoy_annual(fred, "employment_industrial", market, yrs)
    if (off_jobs and any(v is not None for v in off_jobs)) or (ind_jobs and any(v is not None for v in ind_jobs)):
        out.append({"type": "line", "percentSigned": True, "title": "Job growth — Office-using vs Industrial",
                    "subtitle": "YoY %, the demand engine for both", "categories": yrs, "series": [
                        {"name": "Office-using", "values": off_jobs, "color": NAVY},
                        {"name": "Industrial", "values": ind_jobs, "color": TAN}]})
    return out


def _kpi_rows(parsed):
    return [{"label": m["label"], "value": m.get("value"), "forecast": m.get("forecast")}
            for m in parsed.get("metrics", [])]


def construction_pipeline_chart(parsed: dict) -> dict | None:
    """The MarketBeat 'Construction Pipeline' (Spec + Build-to-Suit, MSF) as an interactive
    stacked-bar chart. Data is vision-read from the MarketBeat chart and saved to
    data/json/construction_pipeline_<sector>.json (latest year cross-checks the under-construction total)."""
    sec = "industrial" if "indust" in (parsed.get("sector") or "").lower() else "office"
    f = _DATA / f"construction_pipeline_{sec}.json"
    if not f.exists():
        return None
    pts = json.loads(f.read_text()).get("points", [])
    if not pts:
        return None
    yrs = [p["year"] for p in pts]
    return {"type": "column", "stacked": True, "title": "Construction pipeline",
            "subtitle": "Under construction, MSF — Spec + Build-to-Suit (from the MarketBeat)",
            "categories": yrs, "series": [
                {"name": "Spec", "values": [p.get("spec") for p in pts], "color": NAVY},
                {"name": "BTS", "values": [p.get("bts") for p in pts], "color": TAN}]}


def report_charts(parsed: dict, fred: dict | None = None) -> list[dict]:
    """The on-screen report charts: vacancy + net absorption/supply (from the report's
    historical chart) and the MarketBeat Construction Pipeline (replacing the jobs-vs-absorption
    demand driver). All interactive."""
    chart = parsed.get("chart") or {}
    inv = _inv_msf(parsed.get("market"), parsed.get("sector"))
    out = [vacancy_chart(chart), supply_demand_chart(chart, inv_msf=inv), construction_pipeline_chart(parsed)]
    return [c for c in out if c]


def _stat_callouts(parsed):
    """Four headline KPIs as big at-a-glance callouts for the title slide."""
    m = {x.get("key"): x for x in parsed.get("metrics", [])}
    order = ["total_vacancy", "ytd_net_absorption", "overall_direct_asking_rent", "average_asking_rent",
             "class_a_direct_asking_rent", "under_construction", "ytd_leasing_activity"]
    out, seen = [], set()
    for key in order:
        x = m.get(key)
        if x and x.get("value") and key not in seen:
            out.append({"value": x["value"], "label": (x.get("label") or "").split(" (")[0]})
            seen.add(key)
        if len(out) == 4:
            break
    return out


DECK_COPY_SYSTEM = (
    "You are Cushman & Wakefield Research writing a concise, polished, CLIENT-FACING market summary deck. You explain a "
    "commercial real-estate market to an external client who wants to understand it quickly and credibly. "
    "Ground every figure in the data provided; never invent a number. Write FRESH copy in your own words — "
    "do NOT reuse the supplied house-view or report sentences verbatim."
)


def deck_copy(parsed, house_view=None, econ=None, demo=None, events=None, source_text=""):
    """Fresh, client-facing deck copy (distinct from the dashboard wording)."""
    market, sector, period = parsed.get("market"), parsed.get("sector"), parsed.get("period")
    hv = ec = dm = ""
    if house_view:
        hv = (f"\nOur house view ({house_view.get('stance')}): {house_view.get('headline')} — "
              f"{house_view.get('thesis', '')}")
    if econ:
        ec = ("\nEconomic Health " + str(econ.get("score")) + "/100 (" + str(econ.get("label")) + ", "
              + str(econ.get("direction")) + "): "
              + "; ".join(f"{c['label']} {c.get('detail', '')}" for c in econ.get("components", [])))
    if demo:
        p = demo.get("population") or {}
        mi = demo.get("median_income") or {}
        w = demo.get("avg_wage") or {}
        dm = (f"\nDemographics: metro population {p.get('value_m')}M ({p.get('yoy_pct')}% YoY), median "
              f"household income ${mi.get('value')}, average wage ${w.get('annual')}/yr.")
    ev = "; ".join(f"{e.get('title')} ({e.get('detail', '')})" for e in (events or [])[:4])
    prompt = f"""Write a CLIENT-FACING market summary for {market} {sector} — {period}.
Return ONLY JSON:
{{"headline": "<one crisp client headline (<=12 words), distinct wording from the house view>",
"summary": "<2-3 sentence executive summary of where this market stands now>",
"fundamentals": "<2 sentences on vacancy, net absorption, rents and supply>",
"drivers": "<2 sentences on demand drivers and the economic backdrop (jobs, population, income)>",
"outlook": "<2 sentences: where the market is heading and what to watch>"}}
Write fresh external-client copy; rephrase in your own words and do NOT copy the inputs verbatim. Use ONLY figures present below.

FUNDAMENTALS:
{_facts(parsed.get('metrics', []))}{hv}{ec}{dm}
Notable deals: {ev}
REPORT CONTEXT (do not copy verbatim):
\"\"\"{(source_text or '')[:2200]}\"\"\""""
    try:
        out = claude_json(DECK_COPY_SYSTEM, prompt, model=CLAUDE_ANALYST_MODEL, max_tokens=1000)
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _econ_stats(econ):
    if not econ:
        return []
    out = [{"value": f"{econ.get('score')}/100", "label": "Economic Health · " + str(econ.get("label", ""))}]
    for c in econ.get("components", [])[:3]:
        out.append({"value": c.get("detail", ""), "label": c.get("label", "")})
    return out


def _demo_line(demo):
    """One-line metro demographics summary for the economic-backdrop box."""
    if not demo:
        return ""
    p = demo.get("population") or {}
    mi = demo.get("median_income") or {}
    w = demo.get("avg_wage") or {}
    bits = []
    if p.get("value_m"):
        bits.append(f"Metro population {p['value_m']}M ({p.get('yoy_pct')}% YoY)")
    if mi.get("value"):
        bits.append(f"median household income ${mi['value']:,} (MSA)")
    if w.get("annual"):
        bits.append(f"average wage ${w['annual']:,}/yr (MSA)")
    return "  ·  ".join(bits)


def _event_lines(events, n=5):
    out = []
    for e in (events or [])[:n]:
        d = e.get("detail")
        out.append(f"{e.get('title')}" + (f" — {d}" if d else ""))
    return out


def build_single(parsed, takeaways, fred, opportunity="", econ=None, demo=None,
                 events=None, house_view=None, source_text=""):
    """A 5-slide, client-ready market summary: at-a-glance → fundamentals → supply &
    demand → economic backdrop → outlook. More charts + fresh client copy than the dashboard."""
    market, sector, period = parsed.get("market"), parsed.get("sector"), parsed.get("period")
    chart = parsed.get("chart") or {}
    inv = _inv_msf(market, sector)
    copy = deck_copy(parsed, house_view, econ, demo, events, source_text)

    vac = vacancy_chart(chart)
    sd = supply_demand_chart(chart, inv_msf=inv)
    driver = demand_driver_chart(parsed, fred)
    bullets = takeaways.get("bullets", [])
    headline = copy.get("headline") or (house_view or {}).get("headline") or takeaways.get("headline", "")
    outlook = copy.get("outlook") or takeaways.get("outlook", "")

    s1 = {"title_slide": True, "title": f"{market} {sector}",
          "subtitle": f"{period}  ·  Cushman & Wakefield MarketBeat — Market Summary",
          "headline": headline, "summary": copy.get("summary", ""),
          "stats": _stat_callouts(parsed)}

    s2 = {"title": f"{market} {sector} — Market fundamentals", "subtitle": f"{period} · Quarter in review",
          "kpis": _kpi_rows(parsed), "charts": [c for c in [vac] if c],
          "box": {"heading": "Highlights", "lines": bullets[:3]}}

    s3 = {"title": f"{market} {sector} — Supply & demand",
          "subtitle": "Net absorption & new supply as a share of total inventory, and the demand engine",
          "charts": [c for c in [sd, driver] if c]}

    s4 = {"title": f"{market} — Economic backdrop", "subtitle": "What underpins demand across the metro",
          "stats": _econ_stats(econ), "text": copy.get("drivers", ""),
          "box": ({"heading": "Demographics & economy",
                   "lines": [ln for ln in [_demo_line(demo), (econ or {}).get("note", "")] if ln]}
                  if (demo or econ) else None)}

    opp_lines = []
    if opportunity:
        opp_lines.append(opportunity)
    if house_view and house_view.get("what_would_change"):
        opp_lines.append("What we're watching — " + house_view["what_would_change"])
    s5 = {"title": f"{market} {sector} — Outlook & opportunity", "subtitle": "Our forward view",
          "text": outlook, "events": _event_lines(events),
          "box": ({"heading": "Opportunity", "lines": opp_lines} if opp_lines else None)}

    return {"title": f"{market} {sector}", "subtitle": f"{period} · Cushman & Wakefield MarketBeat — Market Summary",
            "slides": [s1, s2, s3, s4, s5]}


def build_combined(market, period, office, industrial, fred,
                   office_take, ind_take, opportunity=""):
    oc, ic = office.get("chart") or {}, industrial.get("chart") or {}
    yrs = _years(oc) or _years(ic)

    vac_cmp = {"type": "line", "title": "Total vacancy — Office vs Industrial", "percent": True, "unit": "%",
               "categories": yrs, "series": [
                   {"name": "Office", "values": _col(oc, "vac"), "color": NAVY},
                   {"name": "Industrial", "values": _col(ic, "vac"), "color": TAN}]}
    abs_cmp = {"type": "column", "title": "Net absorption — Office vs Industrial", "unit": "s.f.",
               "categories": yrs, "series": [
                   {"name": "Office", "values": _col(oc, "abs"), "color": NAVY},
                   {"name": "Industrial", "values": _col(ic, "abs"), "color": TAN}]}
    emp = employment_chart(fred, market, yrs)

    def kpi_pairs():
        om = {m["key"]: m for m in office.get("metrics", [])}
        im = {m["key"]: m for m in industrial.get("metrics", [])}
        rows = []
        for key, label in [("ytd_net_absorption", "YTD net absorption"), ("total_vacancy", "Vacancy rate"),
                           ("under_construction", "Under construction"), ("ytd_leasing_activity", "YTD leasing activity")]:
            o, i = om.get(key), im.get(key)
            if o or i:
                rows.append({"label": label, "office": (o or {}).get("value", "—"),
                             "industrial": (i or {}).get("value", "—")})
        # rents differ by sector — show both labels' values
        rows.append({"label": "Asking rent", "office": (om.get("overall_direct_asking_rent") or {}).get("value", "—"),
                     "industrial": (im.get("average_asking_rent") or {}).get("value", "—")})
        return rows

    s_title = {"title_slide": True, "title": f"{market} — Office & Industrial",
               "subtitle": f"{period}  ·  Cushman & Wakefield MarketBeat — Market Briefing",
               "headline": office_take.get("headline", "")}
    s_fund = {"title": f"{market} — fundamentals: Office vs Industrial", "subtitle": f"{period} · Cushman & Wakefield MarketBeat",
              "kpi_compare": kpi_pairs(), "charts": [vac_cmp],
              "box": {"heading": "Highlights", "lines": office_take.get("bullets", [])[:2] + ind_take.get("bullets", [])[:1]}}
    s_supply = {"title": f"{market} — supply & demand", "subtitle": f"{period} · Cushman & Wakefield MarketBeat",
                "charts": [c for c in [supply_demand_chart(oc, " (Office)"), supply_demand_chart(ic, " (Industrial)")] if c],
                "box": {"heading": "Highlights", "lines": [
                    f"Office net absorption {(office.get('metrics') or [{}])[0].get('value','')}; vacancy "
                    f"{next((m.get('value') for m in office.get('metrics',[]) if m.get('key')=='total_vacancy'), '')}.",
                    f"Industrial net absorption {(industrial.get('metrics') or [{}])[0].get('value','')}; vacancy "
                    f"{next((m.get('value') for m in industrial.get('metrics',[]) if m.get('key')=='total_vacancy'), '')}."]}}
    box4 = ([office_take.get("outlook", "")] if office_take.get("outlook") else []) + \
           ([f"Opportunity — {opportunity}"] if opportunity else [])
    s_econ = {"title": f"{market} — economic backdrop & outlook", "subtitle": f"{period} · Cushman & Wakefield MarketBeat",
              "charts": [c for c in [emp, abs_cmp] if c], "box": {"heading": "Outlook", "lines": box4}}

    return {"title": f"{market} — Office & Industrial", "subtitle": f"{period} · Cushman & Wakefield MarketBeat",
            "slides": [s_title, s_fund, s_supply, s_econ]}
