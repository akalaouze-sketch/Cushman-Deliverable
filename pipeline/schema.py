"""Normalized, source-attributed JSON schema shared by the whole pipeline.

Every snapshot is timestamped (generated_at, UTC) so the frontend and the
agent layer always know exactly what data a report was built from. The agent
layer is required to ground its commentary strictly in these records.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

SCHEMA_VERSION = "1.0"


@dataclass
class Observation:
    date: str       # ISO date, e.g. "2025-03-01"
    value: float


@dataclass
class SeriesRecord:
    indicator: str                       # e.g. "unemployment_rate"
    market: str                          # "Austin" | "San Antonio" | "US"
    source: str                          # provenance, e.g. "FRED"
    series_id: str                       # source identifier (FRED series id)
    title: str = ""                      # source-provided human title
    units: str = ""
    frequency: str = ""
    observations: list[Observation] = field(default_factory=list)
    latest: Optional[Observation] = None
    error: Optional[str] = None          # set if this series failed to resolve


@dataclass
class Snapshot:
    schema_version: str
    generated_at: str                    # ISO8601 UTC, stamped by the caller
    source: str
    history_years: int
    series: list[SeriesRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
