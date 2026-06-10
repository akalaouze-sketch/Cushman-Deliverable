#!/usr/bin/env python3
"""Cushman & Wakefield · MarketBeat Intelligence server — ties the agents to the frontend.

- Serves the report template + assets.
- /api/report : verified metrics + analyst takeaways + visual specs (server-side).
- /chat       : the MarketBeat Assistant search bar.

The Analyst/Critic and PDF verification loops all run here, server-side — never in
the browser. Keys come from .env.

Run:  python server/app.py   (then open http://127.0.0.1:5002)
"""
from __future__ import annotations

import json
import os
import sys
import time
from functools import wraps
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from flask import Flask, Response, jsonify, request, send_file, send_from_directory  # noqa: E402

from agents import analyst_agent, assistant_agent, deck_agent, house_view_agent, trace, visuals_agent  # noqa: E402
from agents.demographics import demographics  # noqa: E402
from agents.economic_health import economic_health  # noqa: E402
from agents.leases import lease_wall  # noqa: E402
from agents.permits import permits_feed  # noqa: E402
from agents.deck_build import build_deck as build_deck_for  # noqa: E402
from agents.deck_pdf import render_pdf  # noqa: E402
from agents.deck_pptx import build_pptx  # noqa: E402
from agents.pdf_parser_agent import extract_source_text, narrative_paragraphs  # noqa: E402

FRONTEND = ROOT / "frontend"
DATA = ROOT / "data/json"
PDFS = ROOT / "data/pdfs"
DELIVER = ROOT / "deliverables"
DEFAULT_PARSED = DATA / "parsed_dallas-fort-worth_office_q1-2026.json"
DEFAULT_PDF = PDFS / "cw-marketbeat-dallas-fort-worth-office-q1-2026.pdf"

app = Flask(__name__, static_folder=None)


def traced_route(name):
    """Wrap a Flask view in a trace run so each request is a recoverable trace
    (the request's args become the run input; nested agent/LLM spans hang under it)."""
    def deco(fn):
        @wraps(fn)
        def inner(*a, **k):
            with trace.run(name, input={**request.args.to_dict(), **k} or None):
                return fn(*a, **k)
        return inner
    return deco


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


def _load(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def _list_reports() -> list[dict]:
    items, metros = [], {}
    for pf in sorted(DATA.glob("parsed_*.json")):
        d = _load(pf)
        key = pf.stem.replace("parsed_", "")
        parts = key.split("_")
        if len(parts) < 3:
            continue
        slug, sector, period = parts[0], parts[1], "_".join(parts[2:])
        items.append({"key": key, "market": d.get("market"), "sector": d.get("sector"),
                      "period": d.get("period"), "label": f"{d.get('market')} {d.get('sector')}".strip()})
        m = metros.setdefault((slug, period), {"market": d.get("market"), "period": d.get("period"), "sectors": set()})
        m["sectors"].add(sector)
    for (slug, period), info in metros.items():
        if {"office", "industrial"} <= info["sectors"]:
            items.append({"key": f"{slug}_all_{period}", "market": info["market"],
                          "sector": "Office & Industrial", "period": info["period"],
                          "label": f"{info['market']} — Office & Industrial"})
    return items


@app.get("/")
def index():
    # inject the Web3Forms key (kept in env, never committed) for client-side feedback email
    html = (FRONTEND / "report.html").read_text().replace(
        "__WEB3FORMS_KEY__", os.environ.get("WEB3FORMS_KEY", ""))
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.get("/assets/<path:fname>")
def assets(fname):
    return send_from_directory(FRONTEND / "assets", fname)


@app.get("/trace")
def trace_viewer():
    return Response((FRONTEND / "trace.html").read_text(), mimetype="text/html",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/traces")
def api_traces():
    return jsonify(trace.load_index(int(request.args.get("limit", "60"))))


@app.get("/api/trace/<run_id>")
def api_trace(run_id):
    data = trace.load_run(run_id)
    return (jsonify(data) if data else (jsonify({"error": "not found"}), 404))


def _report_bundle(key: str, fresh: bool = False):
    parsed = _load(DATA / f"parsed_{key}.json")
    if not parsed:
        return None
    cache = DATA / f"bundle_{key}.json"
    if cache.exists() and not fresh:
        return _load(cache)
    pdf = Path(parsed.get("source_file", ""))
    with trace.span("extract_source_text", kind="io", input={"pdf": str(pdf)}) as s:
        source = extract_source_text(pdf) if pdf.exists() else ""
        s.output({"chars": len(source)})
    with trace.span("write_takeaways", kind="agent"):
        takeaways = analyst_agent.write_takeaways(parsed, source) if source else {}
    with trace.span("build_specs", kind="agent"):
        specs = visuals_agent.build_specs(parsed, _load(DATA / "fred_latest.json"))
    bundle = {
        "market": parsed.get("market"), "sector": parsed.get("sector"), "period": parsed.get("period"),
        "kpis": specs.get("kpis", []),
        "headline": takeaways.get("headline"),
        "bullets": takeaways.get("bullets", []),
        "outlook": takeaways.get("outlook"),
        "narrative": narrative_paragraphs(pdf) if pdf.exists() else [],
        "chart": parsed.get("chart"),
    }
    cache.write_text(json.dumps(bundle))
    return bundle


def _house_view_for(key: str, parsed: dict, health: dict | None) -> dict:
    """Cached house view per report (generating it appends one dated entry to the
    track record). Delete data/json/house_view_<key>.json to refresh next quarter."""
    cache = DATA / f"house_view_{key}.json"
    if cache.exists():
        hv = _load(cache)
    else:
        hv = house_view_agent.generate(parsed, health, save_key=key) or {}
        if hv:
            cache.write_text(json.dumps(hv))
    if hv:
        hv = dict(hv)
        hv["history"] = house_view_agent.history(key)
    return hv


def _data_provenance(demo: dict | None) -> str:
    """Plain-English value + vintage + source for each demographic figure, so the
    assistant can always say where a number came from and how a projection was derived."""
    if not demo:
        return "(no demographic data available)"
    lines = []
    p = demo.get("population")
    if p:
        lines.append(
            f"Population: {p['value_m']}M actual ({p['year']}) — FRED, U.S. Census Bureau population "
            f"estimates summed across the metro's constituent counties. {p['proj_m']}M {p['proj_year']}E "
            f"is PROJECTED: {p['value_m']}M compounded at +{p.get('proj_rate_pct')}%/yr (its 3-yr CAGR).")
    mi = demo.get("median_income")
    if mi:
        lines.append(
            f"Median household income: ${mi['value']:,} actual ({mi['year']}) — TRUE METRO (MSA) figure, "
            f"a population-weighted aggregate of the constituent county SAIPE medians (FRED has no MSA-level "
            f"median-income series, so we weight each MSA county's Census SAIPE median by its population; "
            f"this replaced the old single-county proxy). ${mi['proj']:,} {mi['proj_year']}E is PROJECTED at "
            f"+{mi.get('proj_rate_pct')}%/yr.")
    w = demo.get("avg_wage")
    if w:
        lines.append(
            f"Average wage per job: ${w['annual']:,}/yr ({w['period']}) — BLS QCEW total-covered average "
            f"weekly wage, annualized (×52). This is the freshest income signal."
            + (f" Up {w['yoy_pct']}% year over year." if w.get("yoy_pct") is not None else ""))
    for key, lbl, src in (("median_age", "Median age", "U.S. Census ACS"),
                          ("bachelors_plus", "Bachelor's degree or higher", "U.S. Census ACS")):
        v = demo.get(key)
        if v:
            unit = "%" if key == "bachelors_plus" else ""
            lines.append(f"{lbl}: {v['value']}{unit} ({v.get('year')}) — {src}.")
    return "\n".join(lines)


def _permits_for(slug: str, market: str, fred: dict) -> dict | None:
    """Live permit feed, cached ~12h (the Austin Socrata pull is a network call).
    Falls back to a stale cache if a refetch fails."""
    cache = DATA / f"permits_{slug}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 43200:
        return _load(cache)
    feed = permits_feed(slug, market, fred)
    if feed:
        cache.write_text(json.dumps(feed))
        return feed
    return _load(cache) or None  # stale cache if the live pull failed


def _announcements_for(key: str) -> list[dict]:
    """Key city announcements for a report (researched via scripts/research_announcements.py)."""
    return _load(DATA / f"announcements_{key}.json").get("announcements", [])


def _events_for(key: str, parsed: dict) -> list[dict]:
    """Cached key-transaction markers for a report, merged with any hand-added
    deals in events_manual_<key>.json (so you can drop in ones it missed)."""
    cache = DATA / f"events_{key}.json"
    if cache.exists():
        ev = _load(cache).get("events", [])
    else:
        pdf = Path(parsed.get("source_file", ""))
        src = extract_source_text(pdf) if pdf.exists() else ""
        out = analyst_agent.extract_events(parsed, src) if src else {"events": []}
        cache.write_text(json.dumps(out))
        ev = out.get("events", [])
    manual = _load(DATA / f"events_manual_{key}.json").get("events", [])
    return ev + manual


def _metro_summary_for(office_key: str, office_parsed: dict, ind_parsed: dict) -> str:
    cache = DATA / f"summary_{office_key}.json"
    if cache.exists():
        return _load(cache).get("summary", "")
    s = analyst_agent.metro_summary(office_parsed, ind_parsed)
    cache.write_text(json.dumps({"summary": s}))
    return s


@app.get("/api/report")
@traced_route("api_report")
def api_report():
    # ?report=<key> ; combined metro key = <metro>_all_<period> ; ?fresh=1 bypasses cache
    key = request.args.get("report") or DEFAULT_PARSED.stem.replace("parsed_", "")
    fresh = request.args.get("fresh") == "1"
    combined = "_all_" in key
    ctx_key = key.replace("_all_", "_office_") if combined else key  # combined shows the Office view
    bundle = _report_bundle(ctx_key, fresh)
    if not bundle:
        return jsonify({"error": "no parsed report — run scripts/parse_report.py first"}), 404
    bundle = dict(bundle)
    if combined:
        bundle["sector"] = "— Office & Industrial"
        bundle["combined"] = True
    parsed = _load(DATA / f"parsed_{ctx_key}.json")
    fred = _load(DATA / "fred_latest.json")
    # metro-wide pieces (shown on every tab, including combined)
    bundle["demographics"] = demographics(fred, parsed.get("market"))
    bundle["permits"] = _permits_for(ctx_key.split("_")[0], parsed.get("market"), fred)
    bundle["lease_wall"] = lease_wall(ctx_key)
    bundle["news"] = _load(DATA / "dfw_news.json") or None  # live DFW CRE headlines (context only)

    if combined:  # interwoven Office & Industrial view: summary + comparisons + mixed deals
        ind_key = key.replace("_all_", "_industrial_")
        ind_parsed = _load(DATA / f"parsed_{ind_key}.json")
        bundle["summary"] = _metro_summary_for(ctx_key, parsed, ind_parsed)
        bundle["combo_charts"] = deck_agent.combo_charts(parsed, ind_parsed, fred, parsed.get("market"))
        oe, ie = _events_for(ctx_key, parsed), _events_for(ind_key, ind_parsed)
        for e in oe:
            e["sector"] = "Office"
        for e in ie:
            e["sector"] = "Industrial"
        bundle["events"] = sorted(oe + ie, key=lambda e: e.get("sort_ym") or "9999")
        # joint page reuses the office + industrial announcements (no new research needed)
        oa, ia = _announcements_for(ctx_key), _announcements_for(ind_key)
        seen, mixed = set(), []
        for a in oa + ia:
            if a.get("title") and a["title"] not in seen:
                seen.add(a["title"])
                mixed.append(a)
        bundle["announcements"] = mixed[:4]
        return jsonify(bundle)

    # single-sector view
    with trace.span("report_charts", kind="agent", input={"key": ctx_key}):
        bundle["charts"] = deck_agent.report_charts(parsed, fred)
    with trace.span("economic_health", kind="agent", input={"market": parsed.get("market")}) as s:
        health = economic_health(parsed, fred)  # metro economic index, ranked vs peer Texas metros
        s.output({"score": (health or {}).get("score"), "rank": (health or {}).get("peer_rank")})
    bundle["health"] = health
    bundle["submarkets"] = _load(DATA / f"submarkets_{ctx_key}.json") or None  # per-submarket map data
    with trace.span("events", kind="agent"):
        bundle["events"] = _events_for(ctx_key, parsed)  # key-transaction timeline
    bundle["announcements"] = _announcements_for(ctx_key)  # key city announcements
    with trace.span("house_view", kind="agent"):
        bundle["house_view"] = _house_view_for(ctx_key, parsed, health)  # opinionated, versioned
    trace.current_run().set_output({"market": bundle.get("market"), "sector": bundle.get("sector"),
                                    "kpis": len(bundle.get("kpis", [])), "charts": len(bundle.get("charts", []))})
    return jsonify(bundle)


@app.get("/api/reports")
def api_reports():
    return jsonify(_list_reports())


@app.post("/chat")
@traced_route("chat")
def chat():
    body = request.get_json(force=True, silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "empty message"}), 400
    trace.current_run().set_output({"message": message})
    key = body.get("report") or DEFAULT_PARSED.stem.replace("parsed_", "")
    ctx_key = key.replace("_all_", "_office_") if "_all_" in key else key  # combined → office for context
    report = _load(DATA / f"parsed_{ctx_key}.json")
    available = _list_reports()
    # every individual market's metrics, so the assistant can compare directly
    peers = []
    for it in available:
        if "_all_" in it["key"]:
            continue
        pp = _load(DATA / f"parsed_{it['key']}.json")
        if pp.get("metrics"):
            peers.append({"label": it["label"], "metrics": pp["metrics"]})
    # demographic/economic figures WITH provenance, so the assistant can cite sources
    fred = _load(DATA / "fred_latest.json")
    data_context = _data_provenance(demographics(fred, report.get("market")))
    eh = economic_health(report, fred)
    if eh:
        drivers = "; ".join(f"{c['label']} = {c.get('detail', '')} (weight {round(c['weight'] * 100)}%)"
                            for c in eh.get("components", []))
        data_context += (
            f"\n\nECONOMIC HEALTH INDEX — the dashboard's '{eh['title']}' card IS a real score of "
            f"{eh['score']}/100 ({eh['label']}, {eh['direction']}). It scores the demand drivers Cushman & Wakefield's "
            f"MarketBeat leads with, each as the metro's OUTPERFORMANCE of the U.S. (source: FRED — BLS employment & "
            f"unemployment, Census population): {drivers}. In plain words: (1) JOB GROWTH — metro total-nonfarm "
            f"employment YoY minus U.S. nonfarm YoY (out-growing the country = healthier); (2) POPULATION GROWTH — "
            f"metro population YoY minus the U.S. (Dallas/Fort Worth adds ~110k residents a year, far above the "
            f"nation — its defining demand driver); (3) UNEMPLOYMENT vs the U.S. — the U.S. rate minus the metro rate "
            f"(below the national rate = healthier). The metro is scored ON ITS OWN vs the U.S., NOT ranked against "
            f"other cities. Dallas/Fort Worth reads strong because it genuinely out-grows the U.S. on jobs and "
            f"population while sitting below the national jobless rate. When asked, explain it in those plain terms; "
            f"never use the words 'z-score' or 'composite'.")
    charts = deck_agent.report_charts(report, fred)
    if charts:
        cl = []
        for c in charts:
            sd = "; ".join(f"{s['name']} = {s['values']}" for s in c.get("series", []))
            cl.append(f"'{c['title']}'" + (f" — {c['subtitle']}" if c.get("subtitle") else "")
                      + f", x-axis {c.get('categories')}: {sd}")
        data_context += (
            "\n\nCHARTS ON THIS REPORT (you HAVE this data — explain these when asked):\n- " + "\n- ".join(cl)
            + "\nChart sources: vacancy, net absorption and new supply come from the Cushman & Wakefield MarketBeat (absorption & "
            "supply are shown as % of total inventory). 'Office-using job growth' / 'Industrial job growth' is FRED "
            "employment YoY — office-using = Professional & Business Services (AUST448PBSV / SANA748PBSV), industrial = "
            "Trade, Transportation & Utilities (AUST448TRADN / SANA748TRAD). The 'Office-using jobs vs net absorption' "
            "chart plots that employment growth against net absorption to show employment leading leasing — so you DO "
            "have office-using job-growth data and the demand-driver relationship; explain it, never say it's missing.")
    # per-submarket statistics (the Submarkets map) — BOTH sectors, so the assistant can answer about
    # any office OR industrial submarket and roll them up to Dallas/Fort Worth as a whole.
    metro = ctx_key.rsplit("_", 2)[0]  # e.g. "dallas-fort-worth"
    period = ctx_key.rsplit("_", 1)[-1]
    for sec in ("office", "industrial"):
        sub = _load(DATA / f"submarkets_{metro}_{sec}_{period}.json")
        if not (sub and sub.get("submarkets")):
            continue
        sl = []
        for s in sub["submarkets"]:
            sl.append(
                f"{s['name']}: vacancy {s.get('vacancy_pct')}%, YTD net absorption "
                f"{int(s.get('ytd_net_absorption_sf') or 0):,} sf, current-qtr net absorption "
                f"{int(s.get('qtr_net_absorption_sf') or 0):,} sf, under construction "
                f"{int(s.get('under_construction_sf') or 0):,} sf, YTD completions "
                f"{int(s.get('ytd_completions_sf') or 0):,} sf, YTD leasing {int(s.get('ytd_leasing_sf') or 0):,} sf, "
                f"vacant {int(s.get('vacant_sf') or 0):,} sf, inventory {(s.get('inventory_sf') or 0) / 1e6:.1f}M sf, "
                f"{sub.get('rent_primary_label', 'rent')} ${s.get('rent_primary')}, "
                f"{sub.get('rent_secondary_label', 'rent (2)')} ${s.get('rent_secondary')}")
        data_context += (
            f"\n\nSUBMARKET STATISTICS — {sub['sector'].upper()} MarketBeat 'Market Statistics' for {sub['market']} "
            f"({sub['period']}). You HAVE every submarket's full figures for BOTH office and industrial — quote them "
            f"directly for any submarket (vacancy, inventory, vacant, current-qtr & YTD net absorption, YTD leasing, "
            f"under construction, completions, rents), compare submarkets, and roll them up to the metro total when "
            f"asked about Dallas/Fort Worth as a whole. The map shades each submarket by vacancy in C&W's teal "
            f"palette (pale = low, deep teal = high). {sub['sector']} submarkets:\n- " + "\n- ".join(sl))
    data_context += (
        "\n\nLease-expiration wall (Development pipeline / upcoming roll): the figures shown are "
        "ILLUSTRATIVE PLACEHOLDER values, entered manually — real lease/tenant rollover data requires a "
        "CoStar subscription I do not currently have. If I had CoStar access I would populate this with "
        "actual lease-expiration data. So the dashboard DOES have a lease-expiration schedule, but it is a "
        "placeholder demonstrating the feature — never tell the user the metric is missing; explain it is "
        "sample data and would be replaced with CoStar lease data."
        "\n\n'% of total inventory' on the absorption & new-supply charts uses REAL total inventory from the "
        "Cushman & Wakefield MarketBeat (Q1 2026): Dallas/Fort Worth office 232.0M s.f., Dallas/Fort Worth industrial "
        "850.0M s.f. Each flow (net absorption, new supply) is divided by that market+sector's total inventory — "
        "when asked how a % was derived, cite the inventory figure used.")
    # on a combined tab, give the assistant BOTH sectors so it compares the whole metro
    industrial = _load(DATA / f"parsed_{key.replace('_all_', '_industrial_')}.json") if "_all_" in key else None
    result = assistant_agent.chat(message, report, available, peers=peers, data_context=data_context,
                                  industrial=industrial or None)
    if result.get("switch_to") not in {a["key"] for a in available}:
        result["switch_to"] = None
    if result.get("export") not in ("pdf", "pptx"):
        result["export"] = None
    return jsonify(result)


FEEDBACK_TO = os.environ.get("FEEDBACK_TO", "a.kalaouze@gmail.com")


def _send_feedback_email(text: str, report: str | None) -> bool:
    """Email feedback to Aleks. Uses Resend (RESEND_API_KEY) if set, else SMTP
    (FEEDBACK_SMTP_USER/PASS, e.g. a Gmail app password). The visitor's text only ever
    goes in the BODY — never in a header — so there's no header-injection surface.
    Returns True only if an email was actually accepted for delivery."""
    subject = "Cushman & Wakefield MarketBeat — new feedback"
    body = (f"New feedback from the Cushman & Wakefield · MarketBeat Intelligence dashboard.\n\n"
            f"Report: {report or '—'}\nTime: {time.strftime('%Y-%m-%d %H:%M %Z')}\n\n{text}")

    # NOTE: Web3Forms is submitted CLIENT-SIDE (the browser) — its free plan blocks
    # server-side calls — so it's handled in the frontend, not here. These server paths
    # (Resend / SMTP) are the alternatives for anyone who prefers server-side delivery.
    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        try:
            import requests
            r = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                json={"from": os.environ.get("FEEDBACK_FROM", "Cushman & Wakefield MarketBeat <onboarding@resend.dev>"),
                      "to": [FEEDBACK_TO], "subject": subject, "text": body},
                timeout=15)
            if r.status_code < 300:
                return True
        except Exception:
            pass

    user, pw = os.environ.get("FEEDBACK_SMTP_USER"), os.environ.get("FEEDBACK_SMTP_PASS")
    if user and pw:
        try:
            import smtplib
            import ssl
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = subject, user, FEEDBACK_TO
            msg.set_content(body)
            host = os.environ.get("FEEDBACK_SMTP_HOST", "smtp.gmail.com")
            port = int(os.environ.get("FEEDBACK_SMTP_PORT", "587"))
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, pw)
                s.send_message(msg)
            return True
        except Exception:
            pass
    return False


@app.post("/feedback")
def feedback():
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "empty"}), 400
    report = body.get("report")
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M"), "report": report, "text": text}
    try:
        (DATA / "feedback.jsonl").open("a").write(json.dumps(rec) + "\n")  # local backstop
    except Exception:
        pass
    emailed = _send_feedback_email(text, report)
    return jsonify({"ok": True, "emailed": emailed})


# Rebuild a cached deck when any renderer/template is newer than the file, so a
# design change never silently serves a stale export.
_DECK_SRCS = [ROOT / "agents" / f for f in
              ("deck_pdf.py", "deck_pptx.py", "deck_agent.py", "deck_build.py", "svg_chart.py")]


def _deck_stale(out: Path, key: str) -> bool:
    if not out.exists():
        return True
    mt = out.stat().st_mtime
    srcs = list(_DECK_SRCS)
    ctx_key = key.replace("_all_", "_office_") if "_all_" in key else key
    srcs += [DATA / f"parsed_{ctx_key}.json", DATA / f"bundle_{ctx_key}.json"]
    return any(s.exists() and s.stat().st_mtime > mt for s in srcs)


@app.get("/deck.<fmt>")
@traced_route("deck")
def deck(fmt):
    if fmt not in ("pdf", "pptx"):
        return jsonify({"error": "format must be pdf or pptx"}), 404
    key = request.args.get("report") or DEFAULT_PARSED.stem.replace("parsed_", "")
    DELIVER.mkdir(parents=True, exist_ok=True)
    out = DELIVER / f"{key}.{fmt}"
    if request.args.get("fresh") == "1" or _deck_stale(out, key):
        d = build_deck_for(key)
        if not d:
            return jsonify({"error": "no parsed report — run scripts/parse_report.py first"}), 404
        render_pdf(d, out) if fmt == "pdf" else build_pptx(d, out)
    return send_file(out, as_attachment=True, download_name=f"CW_{key}.{fmt}")


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", "5002"))
    print(f"Cushman & Wakefield · MarketBeat Intelligence on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
