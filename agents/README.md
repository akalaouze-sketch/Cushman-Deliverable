# Agent layer — NEXT MILESTONE (not yet implemented)

Server-side only. Planned components, per the project spec:

- **Analyst** — generates market commentary grounded *strictly* in the
  normalized JSON under `data/json/` (no free-floating claims).
- **Critic** — reviews every claim against the source data and flags anything
  unverifiable.
- **Refine loop** — Analyst reruns until the Critic approves, max 3 iterations.
  Only the final approved output is surfaced to the frontend.
- **Market Pulse** — web-search tool pulls latest Dallas/Fort Worth CRE
  headlines each session, summarized into a feed.

This loop never runs in the browser. Built after the data layer (FRED + PDF)
is confirmed.
