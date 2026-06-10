#!/usr/bin/env python3
"""Recolor the Cushman & Wakefield MarketBeat submarket MAP (the raster on the
final PDF page) so each submarket region is shaded by its VACANCY — keeping the
report's exact outlines, roads and labels.

Approach: the map regions are flat teal shades bounded by white roads, so we
flood-fill each region from a seed near its (baked-in) label and recolor it.
Seeds are auto-refined: around each label we try several candidate points and
pick the one whose flood-fill covers a sensible region-sized area (not a road
sliver, not the whole map). Color scale is diverging: low vacancy = RED, high
vacancy = BLUE, through a light neutral (no muddy orange).

Usage: python scripts/build_submarket_map.py
Writes frontend/assets/submap_<sector>.png for office + industrial.
"""
from __future__ import annotations

import io
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

DATA = ROOT / "data/json"
ASSETS = ROOT / "frontend/assets"
SENTINEL = (255, 0, 255)

# Approx label centres on the 1980x1815 map image, per map region -> the submarket
# name(s) in the stats table whose vacancy colors that region (a list = weighted avg).
SEEDS = {
    "office": {
        "pdf": "cw-marketbeat-dallas-fort-worth-office-q1-2026.pdf",
        "json": "submarkets_dallas-fort-worth_office_q1-2026.json",
        "thresh": 40,
        "regions": [
            ((1386, 300), ["Legacy/Frisco"]), ((1749, 443), ["Richardson/Plano"]),
            ((1040, 560), ["Lewisville/Carrollton"]), ((1430, 700), ["Far North Dallas"]),
            ((1073, 720), ["Las Colinas"]), ((314, 760), ["North Fort Worth"]),
            ((759, 760), ["Southlake/Westlake"]),
            ((1440, 820), ["LBJ Freeway"], (1330, 730, 1610, 930)),
            ((1271, 915), ["Preston Center"], (1185, 835, 1380, 1015)),
            ((1640, 930), ["North Central Expressway"], (1530, 835, 1830, 1050)),
            ((1310, 1050), ["West Love Field"], (1180, 980, 1420, 1140)),
            ((1440, 1110), ["Uptown/Turtle Creek"], (1360, 1030, 1560, 1180)),
            ((1469, 1190), ["CBD Core", "Arts District", "West End"], (1390, 1110, 1575, 1300)),
            ((1690, 1075), ["Deep Ellum/East Dallas"]),
            ((512, 1120), ["East Fort Worth"]),
            ((924, 1180), ["Mid Cities"]), ((360, 1230), ["Fort Worth CBD"]),
            ((150, 1300), ["West Fort Worth"]), ((363, 1480), ["South Fort Worth"]),
            ((1403, 1480), ["Southwest Dallas"]),
        ],
    },
    "industrial": {
        "pdf": "cw-marketbeat-dallas-fort-worth-industrial-q1-2026.pdf",
        "json": "submarkets_dallas-fort-worth_industrial_q1-2026.json",
        "thresh": 28,
        "regions": [
            ((578, 600), ["Far North/I-35"]), ((363, 923), ["Alliance"]), ((776, 1093), ["DFW Airport"]),
            ((1056, 916), ["Valwood/N Stemmons"]), ((1502, 550), ["Allen/McKinney"]),
            ((1452, 889), ["Richardson/Plano"]), ((1172, 1026), ["North Dallas/Metropolitan"]),
            ((1089, 1166), ["Walnut Hill/Stemmons"]), ((347, 1199), ["North Fort Worth"]),
            ((1469, 1133), ["Garland"]), ((1815, 1216), ["East Dallas Suburbs"]),
            ((116, 1359), ["West Fort Worth"]), ((512, 1392), ["East Fort Worth"]),
            ((1106, 1292), ["Brookhollow/Trinity"]), ((1320, 1349), ["Central Dallas"]),
            ((1106, 1392), ["Pinnacle/Turnpike"]), ((1518, 1416), ["Mesquite"]),
            ((314, 1449), ["Central Fort Worth"]), ((693, 1566), ["Great Southwest"]),
            ((1023, 1566), ["Redbird"]), ((1353, 1666), ["South Dallas"]),
            ((363, 1732), ["South Fort Worth"]), ((1300, 1790), ["Ellis County"]),
            ((300, 1790), ["Johnson County"]),
        ],
    },
}


def _map_image(pdf_name):
    doc = fitz.open(ROOT / "data/pdfs" / pdf_name)
    p = doc[3]
    xref = next(im[0] for im in p.get_images(full=True) if doc.extract_image(im[0])["width"] > 1000)
    return Image.open(io.BytesIO(doc.extract_image(xref)["image"])).convert("RGB")


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _vcolor(v, vmin, vmax):
    """low vacancy -> pale teal, high vacancy -> deep teal — Cushman & Wakefield's own
    single-hue palette, so the shade itself reads as vacancy. Blended over a greyscale of
    the map (see BLEND) so the highways/labels show through."""
    n = (v - vmin) / (vmax - vmin) if vmax > vmin else 0.5
    light, dark = (214, 234, 240), (13, 95, 117)
    return _lerp(light, dark, n)


BLEND = 0.62   # teal tint opacity over the greyscale map texture
VAC_LIGHT = (214, 234, 240)   # legend endpoints (kept in sync with the frontend)
VAC_DARK = (13, 95, 117)


def _is_teal(arr, x, y):
    if not (0 <= x < arr.shape[1] and 0 <= y < arr.shape[0]):
        return False
    r, g, b = (int(c) for c in arr[y, x])
    return (b - r > 16) and (g - r > 8)   # a region shade (blue+green tint over red)


def _snap_teal(teal, cx, cy):
    """Nearest teal pixel to (cx,cy) — spiral out (labels/roads aren't teal)."""
    H, W = teal.shape
    for rad in range(0, 130, 3):
        for dy in range(-rad, rad + 1, 3):
            for dx in (-rad, rad) if rad else (0,):
                x, y = cx + dx, cy + dy
                if 0 <= x < W and 0 <= y < H and teal[y, x]:
                    return (x, y)
        for dx in range(-rad, rad + 1, 3):
            for dy in (-rad, rad) if rad else (0,):
                x, y = cx + dx, cy + dy
                if 0 <= x < W and 0 <= y < H and teal[y, x]:
                    return (x, y)
    return None


def _teal_seed(arr, cx, cy):
    """Nudge to a clean region pixel near the label (labels sit on the fill; search
    a small window, preferring points just below the label text)."""
    for dy in range(0, 70, 4):
        for dx in (0, 10, -10, 20, -20, 32, -32, 46, -46):
            if _is_teal(arr, cx + dx, cy + dy):
                return (cx + dx, cy + dy)
    return None


def build(sector: str) -> None:
    cfg = SEEDS[sector]
    if not cfg["regions"]:
        print(f"{sector}: no seed config yet — skipping")
        return
    base = np.asarray(_map_image(cfg["pdf"])).astype(np.float32)
    H, W, _ = base.shape
    r, g, b = base[..., 0], base[..., 1], base[..., 2]
    teal = (b - r > 14) & (g - r > 8)                # the colourable region pixels
    light_grey = 255 - (255 - base.mean(axis=2)) * 0.5   # lifted greyscale so tints stay light

    sub = json.loads((DATA / cfg["json"]).read_text())
    vac = {s["name"]: s for s in sub["submarkets"]}
    vals = [s["vacancy_pct"] for s in sub["submarkets"]]
    vmin, vmax = min(vals), max(vals)

    seeds, names, colors = [], [], []
    for region in cfg["regions"]:
        (cx, cy), reg_names = region[0], region[1]
        recs = [vac[n] for n in reg_names if n in vac]
        if not recs:
            print(f"  ! no data for {reg_names}")
            continue
        tot = sum(r2["inventory_sf"] for r2 in recs) or 1
        v = sum(r2["vacancy_pct"] * r2["inventory_sf"] for r2 in recs) / tot
        seeds.append((cx, cy)); names.append(reg_names[0]); colors.append(_vcolor(v, vmin, vmax))
    seeds = np.array(seeds, np.float32)
    cols = np.array(colors, np.float32)

    # Assign every region pixel to the nearest submarket label by GEODESIC distance — a
    # multi-source BFS that spreads ONLY through teal pixels, so it stops at the white roads
    # and follows the real submarket outlines, yet still fills every region completely.
    lab = np.full((H, W), -1, np.int16)
    dq = deque()
    for ri, (sx, sy) in enumerate(seeds):
        s = _snap_teal(teal, int(sx), int(sy))
        if s is None:
            print(f"  ! seed off-region for {names[ri]}")
            continue
        if lab[s[1], s[0]] < 0:
            lab[s[1], s[0]] = ri
            dq.append(s)
    tH, tW = teal.shape
    while dq:
        x, y = dq.popleft()
        l = lab[y, x]
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < tW and 0 <= ny < tH and lab[ny, nx] < 0 and teal[ny, nx]:
                lab[ny, nx] = l
                dq.append((nx, ny))

    ys, xs = np.where(lab >= 0)
    out_arr = base.copy()
    lg = light_grey[ys, xs][:, None]
    out_arr[ys, xs] = lg * (1 - BLEND) + cols[lab[ys, xs]] * BLEND   # tint each pixel by its region

    # Soft area outlines: a subtle line wherever the submarket id changes, so each area is
    # delineated without being harsh. (The baked-in labels keep their original dark-text +
    # white-halo rendering — we deliberately do NOT paint over them, which is what made the
    # text read as "black over white lettering".)
    diff = np.zeros((H, W), bool)
    diff[:, :-1] |= lab[:, :-1] != lab[:, 1:]
    diff[:, 1:] |= lab[:, :-1] != lab[:, 1:]
    diff[:-1, :] |= lab[:-1, :] != lab[1:, :]
    diff[1:, :] |= lab[:-1, :] != lab[1:, :]
    out_arr[diff & (lab >= 0)] = (96, 114, 134)

    img = Image.fromarray(out_arr.clip(0, 255).astype(np.uint8))
    idmap = lab
    ok = len(names)

    ASSETS.mkdir(parents=True, exist_ok=True)
    out = ASSETS / f"submap_{sector}.png"
    img.save(out)
    # downsampled region-id grid so the frontend can hit-test the whole zone under the cursor
    step = 7
    grid = idmap[::step, ::step]
    gh, gw = grid.shape
    (ASSETS / f"submap_{sector}_regions.json").write_text(json.dumps({
        "w": gw, "h": gh, "names": names, "grid": grid.flatten().tolist()}))
    print(f"{sector}: recolored {ok}/{len(cfg['regions'])} regions, vmin/vmax={vmin}/{vmax} -> {out.name} "
          f"(+ region grid {gw}x{gh})")


if __name__ == "__main__":
    for sec in (sys.argv[1:] or ["office", "industrial"]):
        build(sec)
