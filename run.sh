#!/usr/bin/env bash
# Launch Cushman & Wakefield MarketBeat Intelligence: ensure deps, start the
# server, open the browser.
set -e
cd "$(dirname "$0")"

[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -c "import flask, anthropic, openai, pptx, fitz" 2>/dev/null \
  || ./.venv/bin/pip install -q -r requirements.txt

PORT="${FLASK_PORT:-5002}"
echo "Cushman & Wakefield MarketBeat Intelligence → http://127.0.0.1:$PORT   (Ctrl-C to stop)"
( sleep 1.5; open "http://127.0.0.1:$PORT" ) >/dev/null 2>&1 &
exec ./.venv/bin/python server/app.py
