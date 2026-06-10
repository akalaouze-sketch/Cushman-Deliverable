# Server layer — NEXT MILESTONE (not yet implemented)

Backend API that:

- Reads the normalized snapshots from `data/json/` (never calls FRED/PDFs live).
- Runs the Analyst/Critic refine loop server-side and exposes only approved output.
- Serves the React frontend and the AI chat endpoint.
- Hosts the slide-deck generator (market snapshot, key trends, opportunity callout).

Built after the data layer is confirmed.
