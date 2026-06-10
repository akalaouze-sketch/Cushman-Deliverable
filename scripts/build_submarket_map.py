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
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

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


def _font(size):
    import os
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/Library/Fonts/Arial Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(p):
            from PIL import ImageFont
            return ImageFont.truetype(p, size)
    from PIL import ImageFont
    return ImageFont.load_default()


def _wrap(name, width=12):
    """Break a submarket name into tidy short lines (split on '/' and spaces)."""
    words, out = name.replace("/", "/ ").split(), []
    cur = ""
    for w in words:
        t = (cur + " " + w).strip()
        if len(t) > width and cur:
            out.append(cur); cur = w
        else:
            cur = t
    if cur:
        out.append(cur)
    return [l.replace("/ ", "/").strip() for l in out]


def _fill_regions(lab, iters=46):
    """Grow the geodesic region ids into the -1 gaps (roads, labels) so every pixel
    knows its nearest region. Vectorised flood by iterative 4-neighbour dilation."""
    fl = lab.copy()
    for _ in range(iters):
        changed = False
        for ax, sh in ((0, 1), (0, -1), (1, 1), (1, -1)):
            nb = np.roll(fl, sh, axis=ax)
            if ax == 0:
                (nb[-1] if sh == 1 else nb[0]).fill(-1)
            else:
                (nb[:, -1] if sh == 1 else nb[:, 0]).fill(-1)
            m = (fl < 0) & (nb >= 0)
            if m.any():
                fl[m] = nb[m]
                changed = True
        if not changed:
            break
    return fl


def _shield_mask(out_arr):
    """Mask of the little highway SIGNS (SRT / DNT / PGBT / interstate shields) so the label
    erase never touches them. A sign is a SMALL, COMPACT, SOLID white pill; a submarket
    label's white halo is WIDE and irregular — so erode the white (thin halos vanish), find
    connected blobs, and keep only the small compact ones."""
    from PIL import ImageFilter
    lum = out_arr.mean(axis=2)
    rr, bb = out_arr[..., 0], out_arr[..., 2]
    white = (lum > 188) & (np.abs(bb - rr) < 30)
    core = np.asarray(Image.fromarray((white * 255).astype(np.uint8))
                      .filter(ImageFilter.MinFilter(9))) > 127
    H, W = core.shape
    keep = np.zeros((H, W), bool)
    seen = np.zeros((H, W), bool)
    for y0, x0 in zip(*np.where(core)):
        if seen[y0, x0]:
            continue
        comp, dq = [], deque([(y0, x0)])
        seen[y0, x0] = True
        minx = maxx = x0
        miny = maxy = y0
        while dq:
            y, x = dq.popleft()
            comp.append((y, x))
            minx, maxx = min(minx, x), max(maxx, x)
            miny, maxy = min(miny, y), max(maxy, y)
            for ny, nx in ((y+1, x), (y-1, x), (y, x+1), (y, x-1),
                           (y+1, x+1), (y-1, x-1), (y+1, x-1), (y-1, x+1)):
                if 0 <= ny < H and 0 <= nx < W and core[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    dq.append((ny, nx))
        w, h = maxx - minx + 1, maxy - miny + 1
        if w <= 58 and h <= 48 and len(comp) / (w * h) > 0.42:   # a compact sign pill
            for y, x in comp:
                keep[y, x] = True
    return np.asarray(Image.fromarray((keep * 255).astype(np.uint8))
                      .filter(ImageFilter.MaxFilter(17))) > 127   # dilate back + margin


def _relabel(out_arr, lab, fl, seeds, names, W, H):
    """The baked-in JPEG labels go blurry once we recolor the regions underneath them.
    So erase each original label and re-draw the name SHARPLY with a clean white outline.

    The erase is ANCHORED ON DARK TEXT: it removes the label's dark letter strokes plus
    only the white halo that hugs them. A submarket name has dark text; an airport airplane
    or a faint icon is a white-only silhouette with no dark text, so it is left untouched —
    and highway-sign pills are protected outright."""
    # representative colour of each region, used to paint out the old label
    rc = {}
    for i in range(len(names)):
        m = lab == i
        if m.any():
            rc[i] = out_arr[m].mean(axis=0)

    # Never erase a compact highway-sign pill (SRT / DNT / PGBT / interstate shields).
    shield = _shield_mask(out_arr)

    BW, BH = 196, 122        # search window around each label centre
    centers = {}             # corrected label centre (dark-text centroid) for the redraw
    for i, (sx, sy) in enumerate(seeds):
        if i not in rc:
            continue
        x0, y0 = max(0, int(sx) - BW), max(0, int(sy) - BH)
        x1, y1 = min(W, int(sx) + BW), min(H, int(sy) + BH)
        sub = out_arr[y0:y1, x0:x1]
        lum = sub.mean(axis=2)
        rr, bb = sub[..., 0], sub[..., 2]
        reg_lum = float(rc[i].mean())
        mine = (fl[y0:y1, x0:x1] == i) & ~shield[y0:y1, x0:x1]
        # the label's dark letter strokes (this is what distinguishes a name from a
        # white-only airplane/icon, which has no dark text)
        dark = mine & (reg_lum - lum > 13)
        near_text = np.asarray(Image.fromarray((dark * 255).astype(np.uint8))
                               .filter(ImageFilter.MaxFilter(13))) > 127   # reach the halo
        halo = mine & near_text & (lum - reg_lum > 10) & (np.abs(bb - rr) < 27)
        old = dark | halo
        # small fringe to swallow the faint sub-threshold halo edge, kept in-region/off-signs
        old = (np.asarray(Image.fromarray((old * 255).astype(np.uint8))
                          .filter(ImageFilter.MaxFilter(7))) > 127) & mine
        sub[old] = rc[i]
        out_arr[y0:y1, x0:x1] = sub
        ys_, xs_ = np.where(dark)
        if len(xs_):
            centers[i] = (x0 + float(xs_.mean()), y0 + float(ys_.mean()))

    canvas = Image.fromarray(out_arr.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(canvas)
    font = _font(27)
    for i, (sx, sy) in enumerate(seeds):
        if i not in rc:
            continue
        cx, cy = centers.get(i, (sx, sy))   # draw over where the old label actually was
        lines = _wrap(names[i].upper())
        lh = 31
        ty = cy - (len(lines) * lh) / 2.0
        for ln in lines:
            w_ = d.textlength(ln, font=font)
            d.text((cx - w_ / 2.0, ty), ln, font=font, fill=(26, 38, 54),
                   stroke_width=4, stroke_fill=(255, 255, 255))
            ty += lh
    return canvas


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

    # Grow each region a few px into the faint anti-aliased teal sliver at its edge, so the
    # fill reaches the PDF's road outline instead of stopping a hair short. Restricted to
    # genuinely teal-tinted pixels (b>r, g>r) so it NEVER bleeds onto a white road or grey
    # water — it only closes the thin uncoloured ring between a region and its boundary.
    near_teal = (b - r > 5) & (g - r > 2)
    for _ in range(3):
        moved = False
        for ax, sh in ((0, 1), (0, -1), (1, 1), (1, -1)):
            nb = np.roll(lab, sh, axis=ax)
            if ax == 0:
                (nb[-1] if sh == 1 else nb[0]).fill(-1)
            else:
                (nb[:, -1] if sh == 1 else nb[:, 0]).fill(-1)
            m = (lab < 0) & (nb >= 0) & near_teal
            if m.any():
                lab[m] = nb[m]
                moved = True
        if not moved:
            break

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

    # --- Crisp labels: the baked-in JPEG labels blur against the recolored regions, so
    #     ERASE each one (fill its dark-text + white-halo pixels with the region colour)
    #     and re-DRAW it sharply at the same spot with a clean white outline. The erase is
    #     clipped to each label's own region (fl) so it never bleeds into a neighbour. ---
    fl = _fill_regions(lab)
    img = _relabel(out_arr, lab, fl, seeds, names, W, H)
    idmap = lab

    # Crop to the coloured metro so there's no wasted grey basemap around it — the map fills
    # the frame, with a little padding so the edge submarkets (North/South Fort Worth, Ellis
    # County) aren't flush against the border. Use rows/cols with SUBSTANTIAL colour so a few
    # stray grown pixels at the edges don't defeat the crop.
    colmask = lab >= 0
    rows = np.where(colmask.sum(axis=1) > 18)[0]
    colsx = np.where(colmask.sum(axis=0) > 18)[0]
    pad = 24
    cy0, cy1 = max(0, int(rows.min()) - pad), min(H, int(rows.max()) + 1 + pad)
    cx0, cx1 = max(0, int(colsx.min()) - pad), min(W, int(colsx.max()) + 1 + pad)
    img = img.crop((cx0, cy0, cx1, cy1))
    idmap = idmap[cy0:cy1, cx0:cx1]
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
