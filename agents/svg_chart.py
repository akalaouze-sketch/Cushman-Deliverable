"""Static dual-axis SVG chart for print/PDF (no JS — renders identically in a PDF).

Mirrors the on-screen Chart.js chart: grouped bars (Net Absorption / New Supply,
left axis) + a Vacancy line (right axis). Axes auto-scale to the data.
"""
from __future__ import annotations

import math


def _nice_axis(lo: float, hi: float, percent: bool, has_neg: bool, want: int = 4):
    """Round axis bounds + step to clean values so tick labels are uniform (16,18,20…)
    instead of an odd set spread across raw bounds (17,20,22,25,27). Keeps breathing
    room below the low and above the high; floors at 0 only for all-positive percents."""
    span = (hi - lo) or 1.0
    lo2, hi2 = lo - span * 0.08, hi + span * 0.14
    rng = (hi2 - lo2) or 1.0
    raw = rng / want
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    norm = raw / mag
    step = (1 if norm < 1.5 else 2 if norm < 3 else 5 if norm < 7 else 10) * mag
    nlo = math.floor(lo2 / step) * step
    nhi = math.ceil(hi2 / step) * step
    if percent and not has_neg:
        nlo = max(0.0, nlo)
    ticks, v = [], nlo
    while v <= nhi + step * 1e-6:
        ticks.append(v)
        v += step
    return nlo, nhi, step, ticks


def spec_svg(spec: dict, w: int = 560, h: int = 300) -> str:
    """Render a generic chart spec (type column|line, multiple series) to SVG."""
    cats = spec.get("categories", [])
    series = [s for s in spec.get("series", []) if s.get("values")]
    vals = [v for s in series for v in s["values"] if v is not None]
    if not cats or not series or not vals:
        return ""
    is_col = spec.get("type") == "column"
    pad = {"t": 12, "r": 16, "b": 50, "l": 48}
    x0, x1, y0, y1 = pad["l"], w - pad["r"], h - pad["b"], pad["t"]
    raw_lo = min(vals + ([0.0] if is_col else []))
    raw_hi = max(vals + ([0.0] if is_col else []))
    has_neg = any(v is not None and v < 0 for s in series for v in s["values"])
    vmin, vmax, step, ticks = _nice_axis(raw_lo, raw_hi, bool(spec.get("percent")), has_neg)

    def Y(v):
        return y0 - (v - vmin) / ((vmax - vmin) or 1) * (y0 - y1)

    n = len(cats)
    band = (x1 - x0) / n
    p = []
    dec = 0 if step >= 1 else 1
    for v in ticks:
        vv = 0.0 if abs(v) < 1e-9 else v
        y = Y(vv)
        p.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="{"#9aa6ac" if abs(vv)<1e-9 else "#eef1f2"}"/>')
        lab = f"{vv:.{dec}f}%" if spec.get("percent") else f"{vv:.{dec}f}"
        p.append(f'<text x="{x0-6}" y="{y+3:.1f}" text-anchor="end" font-size="9" fill="#8a949a">{lab}</text>')
    if is_col:
        gw = band * 0.7
        bw = gw / len(series)
        for ci in range(n):
            cx = x0 + band * ci + (band - gw) / 2
            for si, s in enumerate(series):
                v = s["values"][ci] if ci < len(s["values"]) else None
                if v is None:
                    continue
                top, hgt = min(Y(v), Y(0)), abs(Y(v) - Y(0))
                p.append(f'<rect x="{cx+si*bw:.1f}" y="{top:.1f}" width="{bw*0.88:.1f}" height="{hgt:.1f}" fill="{s.get("color", "#1B1C3A")}"/>')
    else:
        for s in series:
            pts = [f"{x0+band*ci+band/2:.1f},{Y(v):.1f}" for ci, v in enumerate(s["values"]) if v is not None]
            if pts:
                p.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{s.get("color", "#1B1C3A")}" stroke-width="2.5"/>')
                p += [f'<circle cx="{x.split(",")[0]}" cy="{x.split(",")[1]}" r="2.6" fill="{s.get("color", "#1B1C3A")}"/>' for x in pts]
    for ci, c in enumerate(cats):
        p.append(f'<text x="{x0+band*ci+band/2:.1f}" y="{y0+15:.1f}" text-anchor="middle" font-size="9" fill="#5d6b73">{c}</text>')
    lx = x0
    for s in series:
        p.append(f'<rect x="{lx}" y="{h-20}" width="11" height="9" fill="{s.get("color", "#1B1C3A")}"/>')
        p.append(f'<text x="{lx+15}" y="{h-12}" font-size="10" fill="#44515a">{s["name"]}</text>')
        lx += 15 + len(s["name"]) * 6.2 + 18
    return f'<svg viewBox="0 0 {w} {h}" width="100%" style="font-family:sans-serif">{"".join(p)}</svg>'


def svg_chart(rows: list[dict], w: int = 660, h: int = 340) -> str:
    rows = [r for r in rows if r] if rows else []
    if not rows:
        return ""
    pad = {"t": 16, "r": 54, "b": 46, "l": 50}
    x0, x1, y0, y1 = pad["l"], w - pad["r"], h - pad["b"], pad["t"]

    def fnum(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    abss = [fnum(r.get("abs")) for r in rows]
    sups = [fnum(r.get("sup")) for r in rows]
    vacs = [fnum(r.get("vac")) for r in rows]
    lmin, lmax = min(0.0, *abss, *sups), max(0.0, *abss, *sups)
    lspan = (lmax - lmin) or 1.0
    lmin -= lspan * 0.08
    lmax += lspan * 0.08
    rmin, rmax = min(vacs), max(vacs)
    rspan = (rmax - rmin) or 1.0
    rmin -= rspan * 0.18
    rmax += rspan * 0.18

    def lY(v):
        return y0 - (v - lmin) / (lmax - lmin) * (y0 - y1)

    def rY(v):
        return y0 - (v - rmin) / (rmax - rmin) * (y0 - y1)

    band = (x1 - x0) / len(rows)
    bw = band * 0.26
    p: list[str] = []
    for i in range(6):
        v = lmin + (lmax - lmin) * i / 5
        y = lY(v)
        col = "#9aa6ac" if abs(v) < 1e-9 else "#e7eaec"
        p.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="{col}"/>')
        p.append(f'<text x="{x0-7}" y="{y+3:.1f}" text-anchor="end" font-size="11" fill="#8a949a">{v:.1f}</text>')
        rv = rmin + (rmax - rmin) * i / 5
        p.append(f'<text x="{x1+7}" y="{rY(rv)+3:.1f}" font-size="11" fill="#8a949a">{rv:.0f}%</text>')
    for i, r in enumerate(rows):
        cx = x0 + band * i + band / 2
        a, s = fnum(r.get("abs")), fnum(r.get("sup"))
        p.append(f'<rect x="{cx-bw-2:.1f}" y="{min(lY(a),lY(0)):.1f}" width="{bw:.1f}" height="{abs(lY(a)-lY(0)):.1f}" fill="#00A9CE"/>')
        p.append(f'<rect x="{cx+2:.1f}" y="{min(lY(s),lY(0)):.1f}" width="{bw:.1f}" height="{abs(lY(s)-lY(0)):.1f}" fill="#E8A23D"/>')
        p.append(f'<text x="{cx:.1f}" y="{y0+18:.1f}" text-anchor="middle" font-size="11" fill="#5d6b73">{r.get("yr","")}</text>')
    pts = [(x0 + band * i + band / 2, rY(fnum(r.get("vac")))) for i, r in enumerate(rows)]
    p.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) +
             '" fill="none" stroke="#1B1C3A" stroke-width="2.5"/>')
    p += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="#1B1C3A"/>' for x, y in pts]
    return f'<svg viewBox="0 0 {w} {h}" width="100%" style="font-family:sans-serif">{"".join(p)}</svg>'
