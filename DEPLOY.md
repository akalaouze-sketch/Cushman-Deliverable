# Deploying to Render

The app ships as a Docker web service. Everything it needs at runtime (parsed reports,
the FRED snapshot, house views, source PDFs) is committed under `data/`. Secrets stay
out of the repo and are entered in the Render dashboard. The site is open (no password).

## One-time setup

1. **Push this repo to GitHub** (already done; for future remotes):
   ```bash
   git push origin main
   ```

2. **Create the service on Render** → New → **Blueprint** → connect the repo.
   Render reads `render.yaml` and provisions the `cushman-marketbeat-intelligence` web service
   (Docker runtime, health check at `/api/health`).

3. **Enter the secrets** (Render dashboard → the service → Environment), all `sync: false`:
   | Key | Value |
   |-----|-------|
   | `ANTHROPIC_API_KEY` | your Anthropic key |
   | `OPENAI_API_KEY` | your OpenAI key |
   | `FRED_API_KEY` | your FRED key |
   | `TAVILY_API_KEY` | your Tavily key (optional — assistant web search) |

   `OPENAI_VERIFY_MODEL=gpt-4.1` is set automatically by the blueprint.

4. **Feedback email (so the ✉ Feedback widget reaches you).** Easiest: go to
   **web3forms.com**, enter your email (no account needed), and they email you an
   **access key** — set it as `WEB3FORMS_KEY` in Render's Environment. Submissions then
   email that address. (Alternatives: `RESEND_API_KEY`, or SMTP via
   `FEEDBACK_SMTP_USER`/`FEEDBACK_SMTP_PASS` with a Gmail App Password.) If none is set,
   feedback is only logged on the server (lost on redeploy) — so set one.

5. **Deploy.** First build installs Chromium (for the deck-PDF export) + Python deps.
   When it's live, open the URL → the dashboard loads directly (no login).

## Notes
- **Updating data**: regenerate locally (e.g. `python scripts/pull_fred.py`,
  `python scripts/build_msa_income.py`), commit the changed `data/` files, and push —
  Render redeploys.
- **One worker**: the app writes file caches under `data/json`; a single gunicorn worker
  (with threads) avoids cache write races.
