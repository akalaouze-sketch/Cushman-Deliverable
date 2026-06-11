# Cushman submarket map — handoff

## Status
- Project dir: /Users/a.kalalouze/cushman-dashboard (Flask + vanilla JS). Git remote origin = github.com/akalaouze-sketch/Cushman-Deliverable (private), branch main, all work committed & pushed. A Render Blueprint deploy is in progress (render.yaml; env keys pasted in the Render dashboard).
- Already done this round: removed all calendar dates/timestamps from the UI; removed the region-grow bleed over roads; forced interstate shields on top of labels.
- Maps are PRE-BUILT PNGs committed in frontend/assets/ — the deployed app just serves them; regenerate locally with the build script and commit the PNGs.

## How the submarket map is built (scripts/build_submarket_map.py)

The script recolors the Cushman & Wakefield MarketBeat submarket map (the raster on the final PDF page) so each submarket region is shaded by its **vacancy**, while preserving the report's exact outlines, roads, and labels. It runs per sector (`office`, `industrial`) via `build(sector)` and writes `frontend/assets/submap_<sector>.png` plus a downsampled `submap_<sector>_regions.json` hit-test grid.

### Pipeline (in order)

1. **Extract the base raster map** — `_map_image(pdf_name)` opens the PDF, takes page index `3` (`doc[3]`), and picks the first embedded image whose decoded `width > 1000px` (the full submarket map, not a logo/icon). Returns it as an RGB `PIL.Image`. In `build`, this is loaded into `base` as a `float32` array of shape `(H, W, 3)`.

2. **Geodesic multi-source BFS fill** — every region pixel is assigned to the nearest submarket label by *geodesic* (not Euclidean) distance. A teal mask is computed in `build` as `teal = (b - r > 14) & (g - r > 8)` — the colourable region pixels. Each seed centroid (from `SEEDS`) is snapped to the nearest teal pixel via `_snap_teal`, then a 4-neighbour BFS (using a `deque`) spreads outward **only through teal pixels**. Because roads are white (not teal), the flood stops exactly at the white roads and follows the real submarket outlines, yet still fills each region completely. The result is the integer label array `lab` (region id per pixel, `-1` where unfilled).

3. **Vacancy tint** — vacancy is read from the submarket JSON; for multi-name regions an inventory-weighted average is computed, and `_vcolor(v, vmin, vmax)` maps it onto a single-hue teal ramp: pale teal `(214, 234, 240)` at low vacancy → deep teal `(13, 95, 117)` at high vacancy (linear `_lerp` over the normalized value `n`). Each region pixel is composited over a lightened greyscale of the original map:
   `out = light_grey*(1 - BLEND) + _vcolor(vacancy)*BLEND` with `BLEND = 0.62`.
   `light_grey = 255 - (255 - base.mean(axis=2)) * 0.5` lifts the greyscale so the tint stays light and the highways/labels show through.

4. **Soft region outlines** — a subtle line is drawn wherever the region id changes between neighbouring pixels (computed by comparing `lab` shifted left/right/up/down). Those boundary pixels (where `lab >= 0`) are set to `(96, 114, 134)`, delineating each area without harshness.

5. **`_relabel`: erase + redraw labels crisply** — the baked-in JPEG labels blur once the regions underneath are recolored, so each one is erased and redrawn sharply. The erase is **anchored on dark text**: within a search window it finds the label's dark letter strokes (`dark = mine & (reg_lum - lum > 13)`) plus only the white halo hugging them (`near_text` via a `MaxFilter(13)` dilation of the dark mask, intersected with light, low-chroma pixels), fills those with the region's representative colour, then redraws the wrapped, uppercased name with `_font(27)` in dark `(26, 38, 54)` and a 4px white stroke at the dark-text centroid.

6. **Shields force-pasted on top** — `_shield_mask` detects the compact white highway-sign pills; after labels are drawn, the original (un-tinted) shield pixels from `out_arr` are pasted back over the finished canvas (`cv[shield] = out_arr[shield]`), so a redrawn label can never cover an interstate shield.

7. **Crop to the coloured metro** — rows/cols with *substantial* colour (`colmask.sum() > 18`, so stray edge pixels don't defeat the crop) define the bounding box; a `pad = 24` margin is added so edge submarkets aren't flush to the border. The image and the region-id map are cropped identically.

### Key functions

- **`_map_image(pdf_name)`** — opens the PDF, page index `3`, returns the `>1000px` embedded image as RGB.
- **`_vcolor(v, vmin, vmax)`** — normalizes vacancy and `_lerp`s pale-teal `(214, 234, 240)` → deep-teal `(13, 95, 117)`. The `VAC_LIGHT`/`VAC_DARK` module constants mirror these endpoints for frontend legend sync.
- **`_is_teal(arr, x, y)` / `_snap_teal(teal, cx, cy)`** — `_is_teal` tests a single pixel for the region shade (`b - r > 16 and g - r > 8`); `_snap_teal` spirals outward (radius 0–130, step 3) from a seed to find the nearest teal pixel (labels/roads aren't teal). (Note: the per-pixel `_is_teal` threshold is `b-r>16`; the array-wide `teal` mask in `build` uses `b-r>14`.)
- **`_fill_regions(lab, iters=46)`** — grows region ids into the `-1` gaps (roads, labels) by iterative 4-neighbour dilation (`np.roll`, with off-edge neighbours forced to `-1`), producing `fl` so every pixel knows its nearest region. Used by `_relabel` to clip each erase to its own region so it never bleeds into a neighbour.
- **`_shield_mask(out_arr)`** — masks the small highway signs (SRT/DNT/PGBT/interstate shields). It thresholds white (`lum > 188 & |b - r| < 30`), erodes with `MinFilter(9)` (thin label halos vanish, compact pills survive), runs 8-connected component labelling, keeps only small compact blobs (`w <= 58 and h <= 48 and fill ratio > 0.42`), then dilates back with `MaxFilter(17)` for margin. A submarket label's halo is wide/irregular and is rejected.
- **`_relabel(out_arr, lab, fl, seeds, names, W, H)`** — the erase-and-redraw pass (step 5/6). Computes each region's representative colour `rc[i]`, protects shields, erases the dark-text + hugging halo per label inside a `BW, BH = 196, 122` window, recenters on the dark-text centroid, redraws wrapped names, and pastes shields back on top.
- **`_wrap(name, width=12)`** — breaks a submarket name into tidy short lines, splitting on `/` and spaces.
- **`_font(size)`** — returns a bold TrueType font, trying Arial Bold / Helvetica / DejaVuSans-Bold paths, falling back to PIL's default.

### The `SEEDS` dict

`SEEDS` is keyed by sector (`office`, `industrial`). Each entry holds the source `pdf`, the vacancy `json`, a `thresh`, and a `regions` list. Each region is `((cx, cy), [submarket name(s)])` with an optional bounding-box third element. The `(cx, cy)` label-centroid coordinates (on the ~1980×1815 map image) serve a **dual role**: they are the BFS seed points (snapped to teal, then flood-filled) **and** the label-redraw positions. When a region lists multiple names (e.g. `["CBD Core", "Arts District", "West End"]`), its tint is an inventory-weighted vacancy average, and the *first* name is used as the displayed label.

### Critical recent design decisions

- **(a) No region-grow-to-outline pass.** The code deliberately does **not** grow the fill past the geodesic boundary. The geodesic BFS already stops exactly at the white roads, so the fill is clean. An earlier "grow to the outline" pass bled region colour over the road edges and interstate shields and read as messy — so regions now stop crisply at the roads and the signs/roads sit clean on top.
- **(b) Label erase is anchored on dark text.** The erase removes the label's dark *letter strokes* plus only the white halo that hugs them. A submarket name has dark text; an airport airplane or faint icon is a white-only silhouette with **no** dark text, so it survives untouched (no dark strokes → nothing erased).
- **(c) Shields pasted back on top.** Compact white sign pills detected by `_shield_mask` (erode → connected components → keep-compact → dilate) are pasted from the pristine `out_arr` back over the finished canvas *after* labels are drawn, so a redrawn label can never cover an interstate shield.
- **(d) BW/BH erase window.** The per-label erase operates in a fixed `BW, BH = 196, 122` half-window around each label centre, clipped to the image bounds and to the label's own region (`fl == i`) and off-shields — bounding the erase so it stays local and never reaches into neighbouring regions.

## Frontend map interaction (frontend/report.html)

The submarket explorer is an interactive choropleth: a pre-rendered vacancy-shaded PNG of the metro on the left, paired with a live data side-panel on the right. There is no per-region SVG or DOM geometry — hit-testing is done against a downsampled region-id grid loaded alongside the image.

### 80/20 map + side-panel layout

The `.sm-split` flexbox row holds two children:

- `#sm-map` (`.sm-map`) — the map column, pinned to `flex: 0 0 79%; max-width: 79%`, `position: relative`, `cursor: pointer`. It contains a single `<img id="sm-img" class="sm-img">` that scales to `width: 100%; height: auto`.
- `#sm-side` (`.sm-side`) — the data panel, `flex: 1 1 0` (the remaining ~20%), with a teal top accent.

So the split is roughly 80/20 map-to-panel. On narrow viewports (`max-width: 720px`) `.sm-split` stacks vertically and the map goes full-width.

### Per-sector assets loaded by `renderSubmarketMap`

`renderSubmarketMap(sub)` runs whenever a sector report is populated. The active sector tab (Office / Industrial / Office & Industrial, built by `buildSectorTabs`) drives which report — and therefore which `sub` payload — is loaded; the function derives a `_smKey` of `"office"` or `"industrial"` from `sub.sector` and loads two assets keyed on it:

- **`/assets/submap_<sector>.png`** — set directly as `#sm-img`'s `src`. This is the vacancy-shaded basemap (pale teal = low vacancy, deep teal = high).
- **`/assets/submap_<sector>_regions.json`** — fetched lazily and cached in `_smGridCache[_smKey]` (so re-selecting a sector doesn't refetch). On success it populates `_smGrid`. The JSON has four fields:
  - `w`, `h` — width/height of the downsampled region-id grid (not the PNG's pixel size).
  - `names[]` — array of submarket names, indexed by region id.
  - `grid[]` — a flattened `h × w` array of region ids (one entry per grid cell); `-1` marks background/no-region cells.

The payload's own `sub.submarkets` are indexed by name into `_smByName` for stat lookup, and the vacancy min/max (`_smVmin`/`_smVmax`) drive the side-panel's default legend bar. Mousemove/click handlers are attached to `#sm-map` once (guarded by `_smHandlersOn`).

The combined **Office & Industrial** tab (key matches `_all_`) is a report-level view backed by both source sectors, but the map itself is always one of the two sector basemaps; there is no separate `submap_all` asset.

### Hover -> side-panel key-stat preview

`smHover(e)` resolves the submarket under the cursor via `_smRegionAt(e)` and, when the region changes (`_smCur.name !== s.name`), calls `smSideHover(s)`. That renders a compact preview into `#sm-side`: the submarket name, a large vacancy figure, and three key stats (YTD net absorption, under construction, and the sector-appropriate primary rent label from `_smRentLabels`), plus a hint to click for full figures.

### Click a region -> dive-deeper transposed stats table

`smClick(e)` resolves the region the same way and, on a hit, sets `_smCur` and calls `smDeep()`. `smDeep` replaces the side panel with the submarket's **full Market-Statistics row transposed into a vertical table** (`.ss-table`): the columns of the source stats row become rows — Inventory, Vacant, Vacancy rate, quarterly and YTD net absorption, YTD leasing, under construction, YTD completions, and both primary/secondary rents (`_smRows(s)`). A `← back` button (`smBack`) returns to the hover preview for the current region, or the default legend if none is selected. Because the whole map column has `cursor: pointer` and click is resolved positionally, the user clicks the area itself — no button to travel to.

### `_smRegionAt`: cursor position -> grid cell -> region

`_smRegionAt(e)` is the core hit-test. It reads the image's `getBoundingClientRect()` and converts the cursor into normalized `u, v` in `[0, 1]` across the rendered image (returning `null` if outside). When `_smGrid` is loaded it maps those fractions onto the grid:

```js
const gx = Math.min(_smGrid.w-1, Math.max(0, Math.floor(u*_smGrid.w)));
const gy = Math.min(_smGrid.h-1, Math.max(0, Math.floor(v*_smGrid.h)));
const idx = _smGrid.grid[gy*_smGrid.w + gx];
if(idx>=0){ const s=_smByName[_smGrid.names[idx]]; if(s) return s; }
```

The grid cell's region id indexes into `names[]`, which keys into `_smByName` to return the full submarket record — so hit-testing follows the true painted region outlines, not just a point. If the grid is missing or the cell is background (`idx < 0`), it falls back to a nearest-labelled-centroid search over `MAP_XY[_smKey]` (squared-distance in percentage coordinates).

### Grid cropped in lockstep with the PNG so hit-testing stays aligned

The region-id grid is generated by `scripts/build_submarket_map.py` from the same rendering pass as the PNG. After painting, the script crops the image to the coloured metro (with `pad = 24`) using `img.crop((cx0, cy0, cx1, cy1))`, and applies the **identical crop box** to the region-id `idmap` (`idmap = idmap[cy0:cy1, cx0:cx1]`) before either is written. Both the saved PNG and the grid therefore cover exactly the same pixel rectangle. The grid is then downsampled with **`step = 7`** (`grid = idmap[::step, ::step]`), and its post-downsample dimensions are written out as `w`/`h`. Because the crop is applied to image and idmap together — and the frontend hit-tests in normalized `[0,1]` coordinates that scale with whatever size the PNG is rendered at — the grid stays registered to the displayed image regardless of crop or display scale, keeping cursor-to-region mapping accurate.

## Open issues — needs visual review (the next session CAN see images)

Two OFFICE-map submarket fills still look wrong to the user, plus one recently-patched spot to confirm. Region-grid diagnostics show the fills are nearly solid (Las Colinas ~93% solid / ~7% interior grey, Mid Cities ~98% solid / ~2% grey), so the remaining problem is **almost certainly LABEL PLACEMENT or a COVERED SIGN — not a large hole in the fill.**

### Flagged regions (office map)

| Submarket | Seed (pre-crop coords) | Symptom / hypothesis |
| --- | --- | --- |
| **Mid Cities** | `(924, 1180)` | Fill ~98% solid. Suspect the label is mis-placed (sitting off the region or on a seam) or a sign/shield is covered. Not a hole. |
| **Las Colinas** | `(1073, 720)` | Fill ~93% solid (~7% interior grey). Suspect mis-placed label or a covered sign rather than the small interior grey. |
| **West Fort Worth** (covering the interstate spot) | — | Just addressed by pasting shields on top. **Confirm** no sign is now covered and the shields sit cleanly. |

### Regenerate the maps

```
cd /Users/a.kalalouze/cushman-dashboard && .venv/bin/python scripts/build_submarket_map.py
```

This rebuilds:
- `frontend/assets/submap_office.png`
- `frontend/assets/submap_industrial.png`
- `frontend/assets/*_regions.json`

### Inspect the output

Crop `frontend/assets/submap_office.png` around each seed and eyeball it.

**Coordinate caveat:** the saved PNG is **CROPPED to the coloured metro**, so the seed coords above are in the **PRE-crop frame**. The simplest path: regenerate and just eyeball the *whole* office map, or crop generously around the expected area rather than trusting the raw seed pixel.

### Run the app to see it in context

```
FLASK_PORT=5002 /Users/a.kalalouze/cushman-dashboard/.venv/bin/python /Users/a.kalalouze/cushman-dashboard/server/app.py
```

Then open:

```
http://127.0.0.1:5002/?nowelcome&report=dallas-fort-worth_office_q1-2026
```

### What the next session should do

1. **Regenerate** the maps with the build command above.
2. **Screenshot crops** around **Mid Cities**, **Las Colinas**, and **West Fort Worth**.
3. **Confirm** no sign/shield is covered and that labels sit cleanly inside their regions.
4. **Iterate**: if a label is mis-placed, nudge that submarket's `SEEDS` entry and rebuild. Since the fills are already ~93-98% solid, focus on label position and sign visibility, not the fill itself.

## Constraints
- Build runs locally on macOS. Deps available: fitz (PyMuPDF), numpy, PIL. NO scipy.
- Only edit scripts/build_submarket_map.py and frontend/report.html for the map. Do NOT touch the JLL project or unrelated parts of the app.
- Honour the user's "defensible metrics, no fabrication" rule for any data/score work.
