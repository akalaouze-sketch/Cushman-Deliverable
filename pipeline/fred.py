"""Thin FRED (Federal Reserve Economic Data) API client.

Server-side only. Reads the key from FRED_API_KEY — it is never exposed to the
browser. Keeps no global state; create one client and reuse it for a pull.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

FRED_BASE = "https://api.stlouisfed.org/fred"

# FRED rate-limits bursts. Retry these statuses with exponential backoff.
_RETRY_STATUS = {429, 500, 502, 503, 504}


class FredError(RuntimeError):
    """Raised when FRED returns a non-200 or a series cannot be resolved."""


@dataclass
class FredClient:
    api_key: str
    session: requests.Session = field(default_factory=requests.Session)
    timeout: int = 30
    max_retries: int = 5
    retry_base_delay: float = 1.0   # seconds; doubles each retry
    min_interval: float = 0.4       # min spacing between requests to avoid 429s
    _last_request: float = field(default=0.0, repr=False)

    @classmethod
    def from_env(cls) -> "FredClient":
        key = os.environ.get("FRED_API_KEY", "").strip()
        if not key:
            raise FredError("FRED_API_KEY is not set — add it to .env")
        return cls(api_key=key)

    def _get(self, path: str, **params: Any) -> dict:
        params.update(api_key=self.api_key, file_type="json")
        url = f"{FRED_BASE}/{path}"
        last_err = ""
        for attempt in range(self.max_retries):
            # space requests out to stay under FRED's burst limit
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            resp = self.session.get(url, params=params, timeout=self.timeout)
            self._last_request = time.monotonic()
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in _RETRY_STATUS:
                last_err = f"FRED /{path} -> {resp.status_code}: {resp.text[:160].strip()}"
                time.sleep(self.retry_base_delay * (2 ** attempt))
                continue
            raise FredError(f"FRED /{path} -> {resp.status_code}: {resp.text[:200]}")
        raise FredError(last_err or f"FRED /{path} failed after {self.max_retries} retries")

    def series_meta(self, series_id: str) -> dict:
        """Resolve a series' human title, units and frequency."""
        data = self._get("series", series_id=series_id)
        seriess = data.get("seriess") or []
        if not seriess:
            raise FredError(f"no metadata for series {series_id}")
        return seriess[0]

    def observations(self, series_id: str, start: str | None = None) -> list[dict]:
        """Return [{date, value}] for a series, dropping missing (.) values."""
        params: dict[str, Any] = {"series_id": series_id, "sort_order": "asc"}
        if start:
            params["observation_start"] = start
        data = self._get("series/observations", **params)
        out: list[dict] = []
        for obs in data.get("observations", []):
            raw = obs.get("value")
            if raw in (None, ".", ""):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            out.append({"date": obs["date"], "value": value})
        return out
