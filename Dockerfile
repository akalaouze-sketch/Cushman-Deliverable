FROM python:3.12-slim-bookworm

# Chromium is used (headless) to render the slide-deck PDF export. fonts-* give it
# real fonts so charts/text render cleanly on the Linux host.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        fonts-liberation \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CHROME_BIN=/usr/bin/chromium

WORKDIR /app

# Install deps first so Docker layer-caches them across code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Render injects $PORT. ONE worker: the app writes file-based caches (bundle_*.json,
# house_view_*.json) under data/json, so a single worker avoids cache write races;
# threads give concurrency. Long timeout covers synchronous LLM + PDF render calls.
ENV PORT=10000
CMD gunicorn --workers 1 --threads 8 --timeout 600 --bind 0.0.0.0:$PORT server.app:app
