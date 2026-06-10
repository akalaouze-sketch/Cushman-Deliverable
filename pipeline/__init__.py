"""Cushman & Wakefield MarketBeat data pipeline.

Standalone scripts that pull source data (FRED macro indicators, Cushman &
Wakefield MarketBeat PDF reports) and normalize it into timestamped,
source-attributed JSON under data/json/. The app only ever reads that JSON —
it never calls these sources directly. See README.md for the architecture.
"""
