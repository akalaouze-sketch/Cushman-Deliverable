#!/usr/bin/env python3
"""Aggregate DFW / Dallas–Fort Worth commercial-real-estate news from the last 30 days.

Sources:
  - GDELT DOC 2.0 API   (no key)
  - Google News RSS     (no key)
  - NewsAPI             (NEWSAPI_KEY in .env)
  - Direct RSS feeds:   The Real Deal, Bisnow, CoStar News, GlobeSt

Every result is filtered to a DFW geography AND a CRE keyword, de-duplicated by headline
similarity, then written out:
  - a total count + a per-source breakdown (printed)
  - data/news/dfw_cre_news_<date>.csv   (headline, source, date, url)
  - data/json/dfw_news.json             (top items for the dashboard's Economic Health card)

Run:  python scripts/dfw_cre_news.py
This is also wired to run itself each morning for 7 days then stop (see scripts/news_schedule.sh);
when the 7-day window is past it removes its own cron entry and exits.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

NEWS_DIR = ROOT / "data/news"
JSON_OUT = ROOT / "data/json/dfw_news.json"
WINDOW_DAYS = 30
UA = {"User-Agent": "Mozilla/5.0 (cushman-marketbeat news aggregator)"}

GEO_TERMS = ["dallas", "fort worth", "dfw", "dallas-fort worth", "dallas/fort worth", "dallas–fort worth"]
CRE_KEYWORDS = ["office", "retail", "multifamily", "industrial", "development", "leasing",
                "investment", "vacancy", "cap rate", "acquisition", "ground-up",
                "commercial real estate", "warehouse", "lease", "tenant", "sublease"]

# CRE-dedicated outlets: require only the DFW geography (they're already CRE).
CRE_FEEDS = {
    "The Real Deal": ["https://therealdeal.com/texas/feed/", "https://therealdeal.com/feed/"],
    "Bisnow": ["https://www.bisnow.com/rss/national", "https://www.bisnow.com/dallas-ft-worth/rss"],
    "CoStar News": ["https://www.costar.com/rss/news", "https://product.costar.com/home/news/rss"],
    "GlobeSt": ["https://www.globest.com/feed/", "https://www.globest.com/rss/"],
}

_cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)


def _get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _has_geo(text: str) -> bool:
    t = (text or "").lower()
    return any(g in t for g in GEO_TERMS)


def _has_cre(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in CRE_KEYWORDS)


def _recent(dt: datetime | None) -> bool:
    return dt is None or dt >= _cutoff   # keep undated items (rare) rather than drop silently


def _iso(dt: datetime | None) -> str:
    return dt.date().isoformat() if dt else ""


# ---------------------------------------------------------------- source fetchers
def from_gdelt() -> list[dict]:
    q = '(Dallas OR "Fort Worth") (office OR industrial OR multifamily OR "commercial real estate" OR leasing OR development)'
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query=" + urllib.parse.quote(q)
           + "&mode=ArtList&maxrecords=75&format=json&timespan=30d&sourcelang=eng&sort=DateDesc")
    arts = json.loads(_get(url)).get("articles", [])
    out = []
    for a in arts:
        try:
            dt = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
        out.append({"headline": a.get("title", ""), "source": "GDELT", "date": dt,
                    "url": a.get("url", ""), "desc": ""})
    return out


def from_google_news() -> list[dict]:
    import feedparser
    q = '(Dallas OR "Fort Worth") (commercial real estate OR office OR industrial OR multifamily OR leasing) when:30d'
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(_get(url))
    out = []
    for e in feed.entries:
        dt = _entry_dt(e)
        src = "Google News"
        if getattr(e, "source", None) and getattr(e.source, "title", None):
            src = f"Google News · {e.source.title}"
        out.append({"headline": e.get("title", ""), "source": src, "date": dt,
                    "url": e.get("link", ""), "desc": e.get("summary", "")})
    return out


def from_newsapi() -> list[dict]:
    key = os.environ.get("NEWSAPI_KEY")
    if not key:
        raise RuntimeError("NEWSAPI_KEY not set")
    q = '(Dallas OR "Fort Worth") AND (commercial real estate OR office OR industrial OR multifamily OR leasing OR development)'
    frm = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).date().isoformat()
    url = ("https://newsapi.org/v2/everything?q=" + urllib.parse.quote(q)
           + f"&from={frm}&sortBy=publishedAt&language=en&pageSize=100&apiKey={key}")
    data = json.loads(_get(url))
    if data.get("status") != "ok":
        raise RuntimeError(data.get("message", "newsapi error"))
    out = []
    for a in data.get("articles", []):
        try:
            dt = datetime.fromisoformat(a["publishedAt"].replace("Z", "+00:00"))
        except Exception:
            dt = None
        out.append({"headline": a.get("title", ""), "source": "NewsAPI · " + (a.get("source", {}).get("name") or "?"),
                    "date": dt, "url": a.get("url", ""), "desc": a.get("description") or a.get("content") or ""})
    return out


def _entry_dt(e):
    for k in ("published_parsed", "updated_parsed"):
        t = getattr(e, k, None)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def from_rss(name: str, urls: list[str]) -> list[dict]:
    import feedparser
    for url in urls:
        try:
            feed = feedparser.parse(_get(url))
            if feed.entries:
                return [{"headline": e.get("title", ""), "source": name, "date": _entry_dt(e),
                         "url": e.get("link", "")} for e in feed.entries]
        except Exception:
            continue
    raise RuntimeError(f"no working feed for {name}")


# ------------------------------------------------------------------- aggregate
def collect() -> tuple[list[dict], dict]:
    raw, status = [], {}
    general = [("GDELT", from_gdelt), ("Google News", from_google_news), ("NewsAPI", from_newsapi)]
    for label, fn in general:
        try:
            items = fn()
            # general sources need BOTH a DFW geography and a CRE keyword (title or description)
            items = [a for a in items
                     if _has_geo(a["headline"] + " " + a.get("desc", "")) and _has_cre(a["headline"] + " " + a.get("desc", ""))]
            raw += items
            status[label] = f"ok ({len(items)})"
        except Exception as e:
            status[label] = f"skipped: {e}"
    for name, urls in CRE_FEEDS.items():
        try:
            items = from_rss(name, urls)
            items = [a for a in items if _has_geo(a["headline"])]   # CRE outlet → geo filter only
            raw += items
            status[name] = f"ok ({len(items)})"
        except Exception as e:
            status[name] = f"skipped: {e}"

    # window + dedup by headline similarity
    raw = [a for a in raw if a["headline"] and _recent(a["date"])]
    raw.sort(key=lambda a: a["date"] or _cutoff, reverse=True)
    deduped, seen = [], []
    for a in raw:
        n = _norm(a["headline"])
        if not n:
            continue
        if any(SequenceMatcher(None, n, s).ratio() > 0.82 for s in seen):
            continue
        seen.append(n)
        deduped.append(a)
    return deduped, status


def _self_terminate_if_done() -> bool:
    """Once the 7-day window (data/news/until.txt) is past, remove our own cron entry so the
    morning runs stop. Returns True if the schedule was removed."""
    until = NEWS_DIR / "until.txt"
    if not until.exists():
        return False
    try:
        end = datetime.fromisoformat(until.read_text().strip()).date()
    except Exception:
        return False
    if datetime.now().date() < end:
        return False
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        new = "".join(ln for ln in cur.splitlines(keepends=True) if "dfw_cre_news" not in ln)
        subprocess.run(["crontab", "-"], input=new, text=True)
    except Exception:
        pass
    until.unlink(missing_ok=True)
    return True


def main() -> None:
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    items, status = collect()
    by_source: dict[str, int] = {}
    for a in items:
        root = a["source"].split(" · ")[0]
        by_source[root] = by_source.get(root, 0) + 1

    today = datetime.now().date().isoformat()
    csv_path = NEWS_DIR / f"dfw_cre_news_{today}.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["headline", "source", "date", "url"])
        for a in items:
            w.writerow([a["headline"], a["source"], _iso(a["date"]), a["url"]])

    JSON_OUT.write_text(json.dumps({
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(items), "by_source": by_source,
        "items": [{"headline": a["headline"], "source": a["source"].split(" · ")[0],
                   "date": _iso(a["date"]), "url": a["url"]} for a in items[:14]],
    }, indent=1))

    print(f"\nDFW CRE news — last {WINDOW_DAYS} days  ({datetime.now():%Y-%m-%d %H:%M})")
    print("-" * 60)
    for s, st in status.items():
        print(f"  {s:16} {st}")
    print("-" * 60)
    print(f"  TOTAL (deduped): {len(items)}")
    for s, n in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"     {s:28} {n}")
    print(f"\nwrote {csv_path.relative_to(ROOT)}  +  data/json/dfw_news.json")
    if _self_terminate_if_done():
        print("7-day window complete — removed the morning schedule.")


if __name__ == "__main__":
    main()
