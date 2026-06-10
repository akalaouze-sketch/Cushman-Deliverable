"""Research agent #4 — the MarketBeat Research Assistant (chat).

Backs the search bar in the report. Gives concise, structured feedback/opinions on
the current report and, when asked to change it, returns concrete template
adjustments the frontend can apply. Grounded in the report context it is given.
"""
from __future__ import annotations

import os

from agents.llm import CLAUDE_MODEL, claude_json  # CLAUDE_MODEL = Sonnet 4.6 (used only for web/complex)

# Quick lookups answer from the data we already pass in — run them on the FASTEST model with
# NO web search, so most questions are near-instant. Web search (slow) only fires when the
# question clearly needs current/external info; those go to Sonnet for better synthesis.
ASSISTANT_FAST_MODEL = os.environ.get("ASSISTANT_MODEL", "claude-haiku-4-5-20251001")
_WEB_TRIGGERS = ("search", "web", "google", "look up", "latest", "recent", "news", "today",
                 "this week", "this month", "right now", "happening", "announce", "online", "headline")


def _needs_web(message: str) -> bool:
    m = (message or "").lower()
    return any(t in m for t in _WEB_TRIGGERS)

ASSISTANT_SYSTEM = (
    "You are the MarketBeat Assistant in a Cushman & Wakefield market-intelligence dashboard built for the "
    "Investment Sales team — a sharp research analyst who helps brokers price, underwrite and transact. "
    "Keep replies SHORT and FAST — a sentence or two for most questions, up to 3 only when something genuinely "
    "needs context. Be direct; never pad, never an essay. "
    "Use plain market language only — NEVER say 'z-score', 'composite', 'standard deviation' or similar jargon. "
    "LEAN ON THE LIVE DATA: ground every answer in the market's fundamentals (vacancy, net absorption, asking "
    "rents, new supply, under construction), the economic backdrop, and the leasing / lease-rollover detail — the "
    "things that back a BOV, an underwriting, or a pricing conversation. When a figure is asked, give the number "
    "AND its source and method so it holds up in a pitch. "
    "You can: answer questions and compare markets DIRECTLY (you have every market's figures — compare with the "
    "numbers, never tell the user to switch reports to compare); SEARCH THE WEB for current data when it isn't in "
    "your context, then cite the source; switch markets/sectors; and export the report as a PDF or PowerPoint. "
    "Do NOT propose cosmetic report edits — be substantive, never a template-suggester. "
    "Cite the source for any figure: the source for the majority of the market data is Cushman & Wakefield's "
    "MarketBeat reports; the 'Data & sources' section lists each demographic/economic value's vintage + source; "
    "say so (and link) if a figure came from the web. Never invent figures. "
    "AUTHORSHIP: built by Aleks Kalalouze for the Cushman & Wakefield Investment Sales team — if asked who built it, say so plainly."
)


def _context(report: dict) -> str:
    metrics = report.get("metrics", [])
    lines = [f"Report: {report.get('market')} {report.get('sector')} — {report.get('period')}"]
    lines += [f"- {m['label']}: {m.get('value')} ({m.get('forecast')})" for m in metrics]
    return "\n".join(lines)


def _peer_context(peers: list[dict] | None) -> str:
    if not peers:
        return "(none)"
    out = []
    for p in peers:
        out.append(f"\n{p.get('label', '?')}:")
        out += [f"  - {m['label']}: {m.get('value')} ({m.get('forecast')})" for m in p.get("metrics", [])[:8]]
    return "\n".join(out)


def chat(message: str, report: dict | None = None, available: list[dict] | None = None,
         peers: list[dict] | None = None, data_context: str | None = None,
         industrial: dict | None = None) -> dict:
    if industrial:  # combined metro tab → current view is BOTH sectors
        ctx = (f"COMBINED METRO VIEW — {report.get('market')}, BOTH Office AND Industrial. When the user "
               f"compares this metro against another, compare the WHOLE metro (office + industrial together), "
               f"not office alone.\n\nOFFICE:\n{_context(report)}\n\nINDUSTRIAL:\n{_context(industrial)}")
    else:
        ctx = _context(report or {})
    avail = available or []
    avail_lines = "\n".join(f"- {a['key']} — {a.get('label', '')}" for a in avail) or "(none)"
    prompt = f"""User message: "{message}"

THE PAGE THE USER IS VIEWING right now — anchor your answer to THIS report unless they ask about another
market (from the Cushman & Wakefield MarketBeat):
{ctx}

Data & sources (demographic/economic figures — cite the source when you use any of these):
{data_context or "(none provided)"}

All markets and their key metrics (use these to answer comparison questions DIRECTLY — you already
have every market's figures, so do not tell the user to switch to compare):
{_peer_context(peers)}

Reports you can switch to (key — label):
{avail_lines}

Return ONLY JSON:
{{
  "reply": "<a short, direct answer — a sentence or two for most questions, up to 3 when it needs context; never an essay. Anchor to the current page; for comparisons use the other markets' figures directly. Cite sources for figures. Plain language — no 'z-score'.>",
  "switch_to": "<exact key from the list above if the user asks to view a different market/sector, else null>",
  "export": "<pdf | pptx | null — set if the user asks to export/download/generate a deck, PDF or PowerPoint; default pdf if the format is unspecified>"
}}
If the user asks to see a different market or sector (e.g. "show San Antonio industrial"), set switch_to to
that key. If they ask to export/download the report (e.g. "make a PowerPoint", "give me the PDF"), set
export to "pdf" or "pptx" (their stated preference, default "pdf") and in the reply tell them to choose
PDF or PowerPoint from the buttons that will appear. Otherwise set switch_to and export to null.
Do NOT propose cosmetic template edits."""
    web = _needs_web(message)  # most questions answer from the data we pass in → no web search → instant
    model = CLAUDE_MODEL if web else ASSISTANT_FAST_MODEL  # Sonnet for web/complex, Haiku for quick lookups
    try:
        result = claude_json(ASSISTANT_SYSTEM, prompt, model=model,
                             max_tokens=600 if web else 300, web_search=web)
    except Exception:
        try:  # never let a tool error hang the bar — answer fast, no web search
            result = claude_json(ASSISTANT_SYSTEM, prompt, model=ASSISTANT_FAST_MODEL, max_tokens=300)
        except Exception as exc:
            return {"reply": f"(assistant error: {exc})", "switch_to": None, "export": None}
    result.setdefault("switch_to", None)
    result.setdefault("export", None)
    return result
