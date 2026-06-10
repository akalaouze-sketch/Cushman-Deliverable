#!/usr/bin/env python3
"""Research the 3 key city/civic announcements that affect each market's asset type
(web search via Tavily + Claude extraction). Writes data/json/announcements_<key>.json.

Usage: python scripts/research_announcements.py <key|all>
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
from agents.llm import claude_json, CLAUDE_ANALYST_MODEL  # noqa: E402

DATA = ROOT / "data/json"
TKEY = os.environ.get("TAVILY_API_KEY", "")

SYS = ("You identify major CITY / civic / infrastructure development announcements (not private leases) "
       "that affect a metro's commercial real estate, grounded strictly in the supplied web results. "
       "Never invent a project, date, or source.")


def tavily(q, n=6):
    body = json.dumps({"api_key": TKEY, "query": q, "max_results": n, "include_answer": False}).encode()
    req = urllib.request.Request("https://api.tavily.com/search", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read()).get("results", [])


def research(key):
    p = json.loads((DATA / f"parsed_{key}.json").read_text())
    market, sector = p.get("market", ""), (p.get("sector") or "").strip()
    focus = ("industrial, logistics, manufacturing, ports, highways and mega-sites"
             if "industrial" in sector.lower()
             else "downtown districts, transit, office towers, parks and mixed-use")
    res = []
    for q in [f"{market} major city development project announcement 2025 2026 {focus}",
              f"{market} {sector} market new development infrastructure announcement"]:
        try:
            res += tavily(q)
        except Exception:
            pass
        time.sleep(0.6)
    snip = "\n".join(f"- {r['title']} ({r.get('url')}): {r.get('content', '')[:200]}" for r in res[:10])
    prompt = (f"From these results about {market}, pick the 3 MOST IMPORTANT recent city/civic development "
              f"or infrastructure announcements that affect the {sector} real-estate market "
              f"(e.g. transit, new districts, parks, major campuses/mega-sites, ports). Return ONLY JSON:\n"
              '{"announcements":[{"title":"<project name>","detail":"<one line on what & why it matters>",'
              '"type":"transit|civic|development|infrastructure|megasite","date":"YYYY-MM or YYYY or null",'
              '"source_headline":"<headline>","source_url":"<url>"}]}\n'
              "Only real, named projects present in the results.\n\nRESULTS:\n" + snip)
    try:
        out = claude_json(SYS, prompt, model=CLAUDE_ANALYST_MODEL, max_tokens=900)
        ann = [a for a in out.get("announcements", []) if a.get("title")][:3]
    except Exception:
        ann = []
    (DATA / f"announcements_{key}.json").write_text(json.dumps({"announcements": ann}, indent=2))
    for a in ann:
        print(f"  [{a.get('type')}] {a['title'][:32]:32} {a.get('date') or '-':8} {(a.get('source_url') or '')[:46]}")
    return ann


keys = sys.argv[1:]
if keys == ["all"]:
    keys = ["dallas-fort-worth_office_q1-2026", "dallas-fort-worth_industrial_q1-2026"]
for k in keys:
    print(f"=== {k} ===")
    research(k)
