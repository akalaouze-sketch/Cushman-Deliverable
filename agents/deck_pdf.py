"""Render a generic `deck` (from deck_agent) to a polished PDF via headless Chrome.

Earnings-deck style: title slide + content slides with multiple charts, an
optional KPI table, and a light-gray highlights box. Mirrors deck_pptx exactly.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

from agents.svg_chart import spec_svg

ROOT = Path(__file__).resolve().parent.parent
CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN"),  # set in the Docker image (Render)
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"),
]


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else ""


def _arrow(f):
    return {"up": "↑", "down": "↓"}.get(f, "→")


_DOWN_GOOD = ("vacanc", "concession", "under develop", "under construct", "sublease")
_UP_GOOD = ("absorption", "rent", "preleas", "occup", "employ")


def _arrow_color(label, f):
    """Green when the forecast is GOOD for that metric (vacancy ↓, rent ↑), red when bad, gray when flat."""
    if not f or f == "flat":
        return "#9aa6ac"
    l = (label or "").lower()
    good = (f == "down") if any(k in l for k in _DOWN_GOOD) else \
           (f == "up") if any(k in l for k in _UP_GOOD) else None
    return "#1FA37A" if good else ("#D6492F" if good is False else "#9aa6ac")


def _stats_html(stats):
    if not stats:
        return ""
    cells = "".join(f'<div class="stat"><div class="sv">{s.get("value", "")}</div>'
                    f'<div class="sl">{s.get("label", "")}</div></div>' for s in stats)
    return f'<div class="stats">{cells}</div>'


def _events_html(events):
    if not events:
        return ""
    rows = "".join(f'<div class="ev">{e}</div>' for e in events if e)
    return f'<div class="events"><h4>Key transactions</h4>{rows}</div>' if rows else ""


def _charts_html(charts, big=False):
    if not charts:
        return ""
    cells = "".join(
        f'<div class="chart"><div class="ctitle">{c.get("title","")}</div>'
        f'<div class="cbox">{spec_svg(c)}</div></div>'
        for c in charts)
    return f'<div class="charts{" big" if big else ""}">{cells}</div>'


def _kpi_table(kpis):
    rows = "".join(
        f'<tr><td>{k["label"]}</td><td class="v">{k.get("value") or "—"}</td>'
        f'<td class="a" style="color:{_arrow_color(k.get("label"), k.get("forecast"))}">{_arrow(k.get("forecast"))}</td></tr>'
        for k in kpis)
    return f'<table class="kpi"><tr><th>Fundamentals</th><th class="v">Value</th><th></th></tr>{rows}</table>'


def _kpi_compare(rows):
    body = "".join(
        f'<tr><td>{r["label"]}</td><td class="v">{r.get("office","—")}</td><td class="v">{r.get("industrial","—")}</td></tr>'
        for r in rows)
    return f'<table class="kpi"><tr><th>Fundamentals</th><th class="v">Office</th><th class="v">Industrial</th></tr>{body}</table>'


def _box(box):
    if not box or not box.get("lines"):
        return ""
    items = "".join(f"<li>{ln}</li>" for ln in box["lines"] if ln)
    return f'<div class="box"><b>{box.get("heading","Highlights")}</b><ul>{items}</ul></div>'


def _slide(deck, slide, logo, n):
    foot = f'<div class="foot"><span>Cushman &amp; Wakefield MarketBeat · Better never settles.</span><span>{n} · {deck["title"]}</span></div>'
    if slide.get("title_slide"):
        summary = f'<div class="lead">{slide["summary"]}</div>' if slide.get("summary") else ""
        return f"""<section class="slide title"><div class="brandbar"></div>
          <img class="logo" src="data:image/png;base64,{logo}">
          <h1>{slide.get("title","")}</h1>
          <div class="sub">{slide.get("subtitle","")}</div>
          <div class="headline">{slide.get("headline","")}</div>
          {summary}{_stats_html(slide.get("stats"))}{foot}</section>"""
    left = ""
    if slide.get("kpis"):
        left = f'<div class="left">{_kpi_table(slide["kpis"])}</div>'
    elif slide.get("kpi_compare"):
        left = f'<div class="left">{_kpi_compare(slide["kpi_compare"])}</div>'
    # a charts-only slide (no table/text/stats/box) gets larger charts that fill the slide
    big = bool(slide.get("charts")) and not (left or slide.get("text") or slide.get("stats")
                                             or slide.get("box") or slide.get("events"))
    body = f'<div class="row">{left}<div class="rightcol">{_charts_html(slide.get("charts"))}</div></div>' if left \
        else _charts_html(slide.get("charts"), big=big)
    text = f'<div class="lead">{slide["text"]}</div>' if slide.get("text") else ""
    return f"""<section class="slide"><div class="brandbar"></div>
      <img class="logo" src="data:image/png;base64,{logo}">
      <div class="stitle">{slide.get("title","")}</div><div class="ssub">{slide.get("subtitle","")}</div>
      {text}{_stats_html(slide.get("stats"))}{body}{_events_html(slide.get("events"))}
      {_box(slide.get("box"))}{foot}</section>"""


def build_html(deck: dict) -> str:
    logo = _b64(ROOT / "frontend/assets/cw-logo.png")
    slides = "".join(_slide(deck, s, logo, i + 1) for i, s in enumerate(deck["slides"]))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  @page {{ size: 13.333in 7.5in; margin: 0; }}
  * {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  body {{ margin: 0; font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; color: #23303a; }}
  .slide {{ width: 13.333in; height: 7.5in; padding: 0.5in 0.7in 0.4in; position: relative; page-break-after: always; overflow: hidden; }}
  .brandbar {{ position: absolute; top: 0; left: 0; right: 0; height: 0.14in; background: #1B1C3A; }}
  .logo {{ height: 0.32in; }}
  h1 {{ font-weight: 300; font-size: 42pt; margin: 1.1in 0 0.08in; color: #1b1b1b; }}
  .title .sub {{ color: #5d6b73; font-size: 14pt; }}
  .title .headline {{ font-size: 15pt; line-height: 1.4; margin-top: 0.4in; max-width: 9in; color: #2b3640; }}
  .stitle {{ font-size: 21pt; font-weight: 300; margin: 0.16in 0 0.02in; color: #1b1b1b; }}
  .ssub {{ color: #5d6b73; font-size: 11pt; margin-bottom: 0.12in; }}
  .lead {{ font-size: 11.5pt; line-height: 1.5; color: #2b3640; max-width: 11.9in; margin: 0 0 0.16in; }}
  .title .lead {{ font-size: 13pt; line-height: 1.45; max-width: 9.4in; margin-top: 0.32in; color: #2b3640; }}
  .stats {{ display: flex; gap: 0.28in; margin: 0.1in 0 0.18in; }}
  .title .stats {{ margin-top: 0.5in; }}
  .stat {{ flex: 1; background: #E9F3F7; border-left: 4px solid #00A9CE; border-radius: 7px; padding: 0.15in 0.2in; }}
  .stat .sv {{ font-size: 20pt; font-weight: 800; color: #1B1C3A; letter-spacing: -0.5px; line-height: 1.05; }}
  .stat .sl {{ font-size: 9pt; color: #5d6b73; margin-top: 5px; line-height: 1.25; }}
  .events {{ margin-top: 0.16in; }}
  .events h4 {{ font-size: 12pt; margin: 0 0 6px; color: #1B1C3A; font-weight: 700; }}
  .events .ev {{ font-size: 10.5pt; padding: 5px 0; border-bottom: 1px solid #e8edee; color: #2b3640; }}
  .charts {{ display: flex; gap: 0.5in; align-items: flex-start; }}
  .chart {{ flex: 1; }}
  .ctitle {{ font-size: 12.5pt; font-weight: 700; margin-bottom: 4px; }}
  .cbox {{ height: 2.4in; }}
  .charts.big {{ gap: 0.6in; margin-top: 0.35in; }}
  .charts.big .ctitle {{ font-size: 14pt; margin-bottom: 8px; }}
  .charts.big .cbox {{ height: 4.0in; }}
  .cbox svg {{ width: 100%; height: 100%; }}
  .row {{ display: flex; gap: 0.4in; }}
  .left {{ flex: 0 0 4.4in; }} .rightcol {{ flex: 1; }}
  table.kpi {{ width: 100%; border-collapse: collapse; }}
  table.kpi th {{ text-align: left; font-size: 11pt; padding: 6px 4px; border-bottom: 2px solid #1B1C3A; }}
  table.kpi th.v {{ text-align: right; }}
  table.kpi td {{ font-size: 11pt; padding: 7px 4px; border-bottom: 1px solid #e1e6e8; }}
  table.kpi td.v {{ text-align: right; font-variant-numeric: tabular-nums; }}
  table.kpi td.a {{ text-align: right; width: 22px; }}
  .box {{ position: absolute; left: 0.7in; right: 0.7in; bottom: 0.55in; background: #EEF1F4; padding: 0.14in 0.22in; border-left: 4px solid #1B1C3A; }}
  .box b {{ font-size: 12pt; }} .box ul {{ margin: 5px 0 0; padding-left: 18px; }}
  .box li {{ font-size: 10.5pt; line-height: 1.4; margin-bottom: 3px; }}
  .foot {{ position: absolute; bottom: 0.22in; left: 0.7in; right: 0.7in; display: flex; justify-content: space-between; color: #9aa6ac; font-size: 8.5pt; }}
</style></head><body>{slides}</body></html>"""


def render_pdf(deck: dict, out_pdf: Path) -> Path:
    out_pdf = Path(out_pdf)
    tmp = ROOT / "deliverables" / f"_deck_{out_pdf.stem}.html"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(build_html(deck))
    chrome = next((c for c in CHROME_CANDIDATES if c and Path(c).exists()), None)
    if not chrome:
        raise RuntimeError("Chrome/Chromium not found for PDF rendering")
    subprocess.run([chrome, "--headless", "--disable-gpu", "--no-sandbox",
                    "--disable-dev-shm-usage", "--no-pdf-header-footer",
                    f"--print-to-pdf={out_pdf}", f"file://{tmp}"],
                   check=True, capture_output=True, timeout=90)
    return out_pdf
