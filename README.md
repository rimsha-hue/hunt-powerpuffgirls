# Power Hunt

A human-in-the-loop job application agent built for NASCON '26 Agentika 2.0 ("Human + AI: Better Together").
It finds jobs, scores the fit, tailors your CV and prepares each application. It proves every claim it wrote before anything leaves your hands.

**No source, no claim.** Every fact Power Hunt writes about you traces back to a resume line, a GitHub repo, a certificate or your own confirmation. If it cannot prove something, it asks you.

The full product and engineering spec lives in [CLAUDE.md](CLAUDE.md).

## Status

| Phase | Deliverable | State |
|---|---|---|
| 0 | FastAPI skeleton, placeholder React page, Dockerfile, CI, `/health` | Done |
| 1 to 7 | Auth and ledger, Scout and ATS, Tailor and Verifier, email flow, form flow, Coach, eval | Not started |

## Run locally

Requirements: Python 3.12, plus either Node 22 or Docker.

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8000      # API on :8000

cd frontend && npm install && npm run dev      # UI on :5173, proxies /health and /api to :8000
```

Or run the production image exactly as it deploys:

```bash
docker build -t power-hunt .
docker run -p 8000:8000 -e SECRET_KEY=change-me-to-something-long power-hunt
# open http://localhost:8000
```

## Checks

```bash
make lint test      # ruff, mypy (strict), pytest with coverage
make audit          # bandit and pip-audit
```

CI (`.github/workflows/ci.yml`) runs all of the above, plus gitleaks, frontend lint, type check, build and `npm audit`. It also builds the Docker image and smoke-tests `/ready`, `/health` and `/`.

## Endpoints

| Path | Purpose |
|---|---|
| `GET /health` | Database, LLM and scheduler state. Returns 503 when degraded. |
| `GET /ready` | Platform readiness probe (database reachable). |
| `GET /docs` | OpenAPI UI. Disabled when `APP_ENV=production`. |
| everything else | The React app (client-side routing). |

## Deploy on Alternate Clouds

1. Create a Neon Postgres database and copy its connection string.
2. In Alternate Clouds, create a service from this GitHub repo using the root `Dockerfile`.
3. **Source tab:** set the port to `8000`. It must match `PORT`, or Cloudflare returns a 502.
4. **Variables tab:** set at least `SECRET_KEY` (48+ random chars), `DATABASE_URL` (Neon), `APP_BASE_URL` (your public URL) and `PORT=8000`. The full list is in [.env.example](.env.example).
5. Rebuild, wait for success, then Deploy. Open `/health` on the public URL to confirm.
6. If anything fails, read the Logs tab first. Logs are JSON, with event names and ids only.

Order on every change: push to GitHub, Rebuild, wait, Deploy.

## Security baseline (Phase 0)

- Secrets come only from environment variables; `.env` is git-ignored and gitleaks runs in CI.
- The app refuses to start in production with the default `SECRET_KEY`.
- Strict CSP (self-hosted fonts, no third-party origins), `nosniff`, `Referrer-Policy`, `X-Frame-Options: DENY`, and HSTS in production.
- The container runs as a non-root user and has a health check.
