# Cushman & Wakefield MarketBeat Intelligence

Production-grade, Cushman & Wakefield MarketBeat-style CRE market intelligence
dashboard for **Dallas/Fort Worth** (Office + Industrial). Separate environment
from the HPI work.

## Architecture

Four layers, deliberately decoupled. The data pipeline runs as standalone
scripts that emit clean, timestamped JSON; everything downstream only reads that
JSON.

```
data/
  pdfs/        Cushman & Wakefield MarketBeat PDFs (source input)
  json/        normalized, timestamped snapshots (pipeline output, app input)
pipeline/      standalone data scripts -> data/json/
  fred.py        FRED API client                       [implemented]
  config.py      which series to pull                  [implemented]
  schema.py      normalized JSON schema                [implemented]
  pdf_parser.py  MarketBeat PDF -> same schema         [next milestone]
agents/        analyst + critic refine loop (server-side)   [next milestone]
server/        backend API; serves frontend + chat         [next milestone]
frontend/      React: cards, recharts, chat, Market Pulse   [next milestone]
scripts/
  pull_fred.py   CLI entrypoint for the FRED pull
```

### Architecture rules
- Data pipeline is separate from the UI — FRED and PDF scraping run standalone
  and output clean JSON; the app just reads it.
- The Analyst/Critic critic loop runs **server-side, never in the browser**.
- Every data pull is **timestamped** so you always know what a report was built from.

## Status: Milestone 1 — scaffolding + FRED integration

Implemented now: project structure, the normalized schema, and a working FRED
pull. The agent, server, and frontend layers are scaffolded with notes only.

## Setup

```bash
cd ~/cushman-dashboard
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in keys (already populated locally)
```

## Run the FRED pull

```bash
python scripts/pull_fred.py            # 10 years of history (default)
python scripts/pull_fred.py --years 5
```

Outputs:
- `data/json/fred_<UTC-timestamp>.json` — immutable snapshot
- `data/json/fred_latest.json` — pointer the app reads

The run prints how many series resolved; any that fail list their `series_id`
so you can correct them in `pipeline/config.py`.

## Parse a MarketBeat report

```bash
# defaults to the Dallas/Fort Worth Office MarketBeat (Q1 2026)
python scripts/parse_report.py
python scripts/parse_report.py data/pdfs/cw-marketbeat-dallas-fort-worth-industrial-q1-2026.pdf
```

## Environment (.env)

| Key | Used by |
|-----|---------|
| `FRED_API_KEY` | FRED macro pull |
| `ANTHROPIC_API_KEY` | Analyst + Critic agents (next milestone) |
| `OPENAI_API_KEY` | secondary LLM (next milestone) |

`.env` is gitignored; keys stay server-side.

Runs locally on port **5002** (`./run.sh` or `python server/app.py`).

_Better never settles._
