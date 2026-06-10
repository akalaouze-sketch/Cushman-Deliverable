#!/usr/bin/env python3
"""Date + order the key-transaction events for a report by searching the web (Tavily).
Adds {date, source_headline, source_url} to each event and sorts chronologically.

Usage: python scripts/order_events.py <report_key>   (or 'all')
"""
import json, re, sys, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
except ImportError: pass
import os
DATA = ROOT / "data/json"
KEY = os.environ.get("TAVILY_API_KEY", "")

def tavily(q, n=4):
    body = json.dumps({"api_key": KEY, "query": q, "max_results": n, "include_answer": False}).encode()
    req = urllib.request.Request("https://api.tavily.com/search", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read()).get("results", [])

def date_from(results):
    # these are current deals — only trust RECENT dates (>=2025); ignore stale articles
    for r in results:
        pd = (r.get("published_date") or "")[:7]
        if re.match(r"20\d\d-\d\d", pd) and pd >= "2025-01": return pd, r
    for r in results:
        m = re.search(r"/(20\d\d)/(\d\d)/", r.get("url", ""))
        if m and f"{m.group(1)}-{m.group(2)}" >= "2025-01": return f"{m.group(1)}-{m.group(2)}", r
    return None, (results[0] if results else None)


def period_ym(period):
    m = re.search(r"[Qq]([1-4]).*?(20\d\d)", period or "")
    return f"{m.group(2)}-{int(m.group(1)) * 3 - 1:02d}" if m else "2026-02"

def order(key):
    f = DATA / f"events_{key}.json"
    if not f.exists(): return print(f"  {key}: no events cache")
    parsed = json.loads((DATA / f"parsed_{key}.json").read_text())
    market = parsed.get("market", "")
    pym = period_ym(parsed.get("period", ""))
    events = json.loads(f.read_text()).get("events", [])
    for e in events:
        q = f"{e['title']} {market} {e.get('detail','')} {e.get('type','')}".strip()
        try:
            res = tavily(q)
        except Exception:
            res = []
        dt, top = date_from(res)
        e["date"] = dt                       # real article date (or None)
        e["sort_ym"] = dt or pym             # undated → the report quarter
        if top and dt:                       # only attach a source when the date is trustworthy
            e["source_headline"] = top.get("title")
            e["source_url"] = top.get("url")
        else:
            e.pop("source_headline", None)
            e.pop("source_url", None)
        print(f"  {e['title'][:34]:34} -> {dt or ('~' + pym):9}  {(top or {}).get('url','')[:58]}")
        time.sleep(0.7)
    events.sort(key=lambda e: e.get("sort_ym") or "9999-99")
    f.write_text(json.dumps({"events": events}, indent=2))
    print(f"  -> wrote {len(events)} ordered events")

keys = [k for k in sys.argv[1:]]
if keys == ["all"]:
    keys = [p.stem.replace("events_", "") for p in DATA.glob("events_*.json")]
for k in keys:
    print(f"=== {k} ===")
    order(k)
