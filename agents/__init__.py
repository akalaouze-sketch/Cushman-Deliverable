"""Cushman & Wakefield MarketBeat Intelligence agent layer (server-side).

Agents:
- pdf_parser_agent  — extract + cross-model verify structured data from MarketBeat PDFs
- visuals_agent     — turn parsed data into chart specs for the report
- analyst_agent     — write MarketBeat-voice commentary grounded strictly in the data
- assistant_agent   — interactive "MarketBeat Research Assistant": feedback + template tweaks
All share llm.py (Claude + OpenAI). Keys come from .env, never the browser.
"""
