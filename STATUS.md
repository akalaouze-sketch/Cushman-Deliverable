# Where we left off — Cushman & Wakefield MarketBeat Intelligence

_Resume notes so any new session (or you) can pick up instantly._

## Done
**Data pipeline (FRED):** `python scripts/pull_fred.py` → series resolve (Dallas/Fort Worth + US),
`data/json/fred_latest.json`. IDs verified.

**Report design:** `frontend/report.html` mirrors the C&W MarketBeat one-pager — deep navy header
band, letter-spaced "MARKETBEAT" wordmark, "Dallas/Fort Worth Office" title, teal section sub-heads,
Fundamentals/Forecast table w/ color arrows, **Chart.js** dual-axis absorption/vacancy chart,
disclaimer, "Better never settles." footer, pinned **MarketBeat Research Assistant** bar.

**Agent layer (all verified live):**
- `pdf_parser_agent.py` — **Opus** extraction + GPT-4.1 cross-verify, 3 checks/field,
  **sector-aware fields** (office vs industrial). All Q1-2026 reports extract clean.
  `python scripts/parse_report.py [pdf]`.
- `analyst_agent.py` — **style-trained + critical-thinking pipeline**: PLAN → DIVERGE →
  cross-model JUDGE (GPT-4.1) → ASSEMBLE, grounded in `style_blueprint.json` +
  same-sector exemplars (`style_corpus.json`). Keeps the 0-ungrounded-numbers guarantee.
  Rebuild style: `python scripts/build_style_blueprint.py [--force]`.
- `chart_vision_agent.py` — **vision** reads the supply/demand chart image (vector, not
  text) into real numbers; cross-checked vs the verified vacancy. `python scripts/extract_chart.py [pdf]`.
  Cross-checks pass.
- `visuals_agent.py` — validated KPI + chart specs (every value traces to source).
- `assistant_agent.py` — MarketBeat Research Assistant: grounded answers + structured `suggested_changes`.
- `server/app.py` — Flask: serves report, `/api/report`, `/chat`. `python server/app.py` → :5002.

## Test results (model decision)
Opus extraction tested on the Q1-2026 reports → all clean once sector-aware. The earlier
`!!` cells were genuinely-absent fields, not model errors → **writing model kept as-is** (Opus).

## Live wiring (done)
`report.html` fetches `/api/report` (served by Flask) and fills title, KPI table, bullets,
narrative + outlook, and the **interactive** Chart.js chart from real vision-extracted data.
Static content remains as the file:// fallback. `/api/report?report=<key>` switches markets;
bundles are cached to `data/json/bundle_*.json` (`?fresh=1` to rebuild). Axes auto-scale.
**Market switcher** dropdown (`/api/reports`) + deep-link (`/?report=<key>`); the MarketBeat Research
Assistant can switch markets on request (→ `switch_to` key, validated server-side).

## Deck export (done) — earnings-deck style, multi-graph
A dedicated deck agent PLANS which charts to make from the data, then renders an earnings-deck-style
deck (title slide + content slides with up to 2 native charts each + a highlights box + a grounded
"Opportunity" callout):
- `agents/deck_agent.py` — chart planning + generic slide structure (single market OR combined metro).
- `agents/deck_build.py` — assembles a deck for any key (reuses cached takeaways).
- `agents/deck_pptx.py` — editable PPTX (native charts/tables); `agents/deck_pdf.py` — matching PDF;
  `agents/svg_chart.py` `spec_svg()` — shared column/line renderer for the PDF.
- Combined metro keys `<metro>_all_<period>` → Office-vs-Industrial comparison (vacancy, absorption) +
  side-by-side supply/demand + employment. Single market → fundamentals table + supply/demand + vacancy/employment.
CLI `python scripts/make_deck.py <key> --format pdf|pptx|both`; server `/deck.pdf` `/deck.pptx`.
Export via the **MarketBeat Research Assistant** ("make me a PowerPoint") now shows a **PDF / PowerPoint
choice** — no auto-download. Downloads use the **CW_** filename prefix.

Dropdown now also has **combined** options ("Dallas/Fort Worth — Office & Industrial"). On screen a
combined key shows the Office primary view (relabeled); the **exported deck covers both sectors**.

## Next / open
- Auto-apply the assistant's `suggested_changes` to the template (it already returns them).
- Per-market/sector report generation (parser→analyst→visuals chain runs per PDF already).
- Frontend → React (Recharts); optional Plotly `/export` route for PDF/PNG. Grow style
  corpus → then DSPy/LangChain (deferred until ~20+ reports).
- Swap any stale population series to a current one.

## Env / housekeeping
- `.env`: ANTHROPIC, OPENAI (from cre-deliverable-app), FRED key. `temperature` is
  deprecated on `claude-opus-4-8` — don't pass it.
- Full autonomy set globally; VSCode extension picks it up on a fresh session.
- Stray `~/hpi-app` (same keys) still there — separate HPI work.
