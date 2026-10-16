# Power Hunt

A human-in-the-loop job application agent built for NASCON '26 Agentika 2.0 ("Human + AI: Better Together").
It finds jobs, scores the fit, tailors your CV and prepares each application, and proves every claim it writes before anything leaves your hands.

**No source, no claim.** Every fact Power Hunt writes about you traces back to a resume line, a GitHub repo, a portfolio page or your own confirmation. If it cannot prove something, it asks you.

| Document | For |
|---|---|
| [docs/SPEC.md](docs/SPEC.md) | The full product and engineering spec (features, phases, rules) |
| [docs/HANDOVER.md](docs/HANDOVER.md) | Current state, known issues, next steps, decisions |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Code map, data model, security and reliability design |
| [CLAUDE.md](CLAUDE.md) | Working rules and commands for Claude Code on this repo |

## Status

| Phase | Deliverable | State |
|---|---|---|
| 0 | FastAPI skeleton, Dockerfile, CI, `/health` | Done (deploy blocked by the platform's builder) |
| 1 | Auth (email + Google), demo seed, resume/portfolio/GitHub evidence ledger, Onboarding and Ledger screens, input guardrails | Done |
| 2 | Scout, ATS score, Jobs screen, digest email with signed Apply links | Done |
| 3 | Tailor, Verifier (Verified, Inferred, Needs you), Needs-you gate, learning loop, CV PDF | Done |
| 4 | Email apply flow: attachments, humanized email, send after approval | Done |
| 5 | Form apply flow: Greenhouse questions, company research, Apply Pack | Done |
| 6 | Coach and growth plans | Not started |
| 7 | Claim checker eval (numbers below), malicious job description test, CI baseline | Done; MCP server not built |

## Run locally

### Prerequisites

- **Python 3.12** with a virtual environment in `.venv`
- **Node 22** (the owner's machine has it per-user in `%LOCALAPPDATA%\Programs\node-v22.23.3-win-x64`)
- **Docker Desktop**, running
- A `.env` file (see Configuration below)

### First-time setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
cd frontend; npm ci; cd ..
.\.venv\Scripts\python scripts\sync_env.py --demo-account   # writes .env with every setting; fill in the keys
```

### Day-to-day development

Two terminals:

```powershell
# Terminal 1: API on http://localhost:8000, using the database in .env (Neon).
# Runs migrations, seeds the demo account, reloads when files in app/ change.
docker compose up --build

# Terminal 2: frontend with hot reload on http://localhost:5173 (proxies /api to :8000)
cd frontend
npm run dev
```

Open **http://localhost:5173** while developing. `npm run build` also makes the latest UI available at http://localhost:8000.

Stop with `Ctrl+C`, or `docker compose down`.

### Checks (the same as CI)

```powershell
.\.venv\Scripts\ruff format app tests eval scripts
.\.venv\Scripts\ruff check app tests eval scripts
.\.venv\Scripts\mypy app eval
.\.venv\Scripts\pytest -q --timeout=60      # offline: temporary SQLite databases, fakes for every external API
.\.venv\Scripts\python -m eval.run --check  # claim checker eval against eval/baseline.json
cd frontend; npx oxlint; npm run build
```

### Production image, exactly as deployed

```powershell
docker build -t power-hunt .
docker run --rm -p 8000:8000 --env-file .env -e APP_ENV=production power-hunt
```

Production refuses to start without a `SECRET_KEY` of at least 32 characters.

## Configuration

Every setting is declared and documented in one place, [app/config.py](app/config.py); there is no template file. `scripts/sync_env.py` writes them into `.env` with their defaults. Keep comments on their own lines: Docker and hosting dashboards treat anything after `=` as the value.

After pulling changes that add settings, add them to your `.env` without losing any values:

```powershell
.\.venv\Scripts\python scripts\sync_env.py                 # adds new keys, keeps yours, backs up to .env.bak
.\.venv\Scripts\python scripts\sync_env.py --demo-account  # also creates demo credentials if missing
```

| Needed for | Keys |
|---|---|
| Everything | `SECRET_KEY`, `DATABASE_URL` (Neon `postgresql://...?sslmode=require`) |
| Demo account | `DEMO_USER_EMAIL`, `DEMO_USER_PASSWORD` (created at startup) |
| AI extraction | `GROQ_API_KEY`, optionally `GEMINI_API_KEY`; models in `LLM_PRIMARY_MODEL`, `LLM_FALLBACK_MODEL`, `GEMINI_MODEL`, `GEMINI_FALLBACK_MODEL`, `LLM_REASONING_EFFORT` |
| Job search (Phase 2) | `RAPIDAPI_KEY` (JSearch, 200 requests a month free); `SCOUT_MAX_QUERIES` per run, `SCOUT_INTERVAL_HOURS`, `SCHEDULER_ENABLED`, `DEMO_MODE` (recorded jobs) |
| Manual Scout trigger | `INTERNAL_TOKEN`, sent as `X-Internal-Token` to `POST /internal/run-scout` |
| Company research (Phase 5) | `TAVILY_API_KEY` (without it, facts come from the job description) |
| Email (Phases 2 and 4) | `SMTP_USER`, `SMTP_APP_PASSWORD` (Gmail app password); `APPLY_LINK_HOURS`; development only: `EMAIL_REDIRECT_TO` sends every email to one inbox |
| Google sign-in (optional) | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`; redirect URI `{APP_BASE_URL}/api/auth/google/callback` |
| Higher GitHub limits (optional) | `GITHUB_TOKEN` (fine-grained, public repos, read-only) |

## Database

Migrations run automatically at container start (`alembic upgrade head`). To create one after changing `app/db/models.py`:

```powershell
$env:DATABASE_URL="sqlite+aiosqlite:///./scratch.db"
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\alembic revision --autogenerate -m "describe the change" --rev-id 0010
Remove-Item scratch.db; Remove-Item Env:DATABASE_URL
```

Review the generated file before committing (name constraints, handle existing data). To inspect data, use the Neon console's SQL editor, for example `select id, email, name, created_at from users;`.

## API

| Path | Purpose |
|---|---|
| `GET /health` | Database, LLM and scheduler state (503 when degraded) |
| `GET /ready` | Platform readiness probe |
| `/api/auth/*` | signup, login, logout, me, delete my data, Google sign-in |
| `/api/profile` | Read and update preferences (validated and normalised) |
| `/api/resume`, `/api/resume/sample`, `/api/github/sync`, `/api/portfolio/sync` | Build the evidence ledger |
| `/api/ledger` | List (search, type, source filters) and delete evidence |
| `/api/meta/config`, `/api/meta/locations`, `/api/meta/roles` | Client config and suggestions |
| `GET /docs` | OpenAPI UI (not in production) |

## Deploy (Alternate Clouds)

1. Service from this repo, branch `main`, root `.`, build and start commands empty (the Dockerfile is used).
2. Container port `8000`.
3. Variables from `.env`, with `APP_ENV=production`, `LOG_JSON=true`, the public `APP_BASE_URL`, a fresh `SECRET_KEY`, and `FORWARDED_ALLOW_IPS` set to the platform's proxy addresses (never `*`). Mark keys as Secret.
4. Rebuild, Deploy, then check `/health`.

## How the evidence ledger decides what is true

1. The resume (PDF or DOCX, checked by magic bytes) is turned into numbered logical lines. Two-column PDFs are read column by column, invisible text is ignored, wrapped lines are rejoined.
2. **Code pass:** skills (370 curated, with synonyms), degrees (only under Education, never future tense), GPA, metrics (one per clause, tagged with the project or role they belong to), certifications.
3. **LLM pass:** the model proposes employers, roles, certifications and projects, each citing a line. Untrusted text reaches it only inside delimited data blocks.
4. **Code decides:** a proposal is kept only if the cited line exists and contains its key tokens and every number it states.
5. The portfolio (fetched through an SSRF-safe fetcher) and GitHub repos add more evidence. A fact backed by several sources shows every source.

## Claim checker eval

Does the Verifier catch invented claims without flagging true ones? `python -m eval.run` answers
with numbers (`--show-misses` lists every miss and false alarm).

- **Cases** (`eval/claims.jsonl`): 112 tailored CV bullets, each citing a line of the demo resume.
  72 are true (7 verbatim, 56 paraphrased, 9 adding a skill the ledger proves) and 40 are planted
  fabrications in seven kinds: inflated numbers, fake degrees, fake employers, skills the user
  doesn't have, added seniority, claims injected by a job description, and meaning drift (a real
  number attached to a different outcome).
- **Ledger:** built from the demo resume exactly as ingestion builds it (code pass plus the LLM's
  proposed employers, roles and projects, each accepted only if its line supports it).
- **What runs:** the real Verifier, with the bge-small embedding model. The LLM claim pass is
  replayed from the claims recorded with each case, so the run is deterministic and needs no key.
- **Scoring:** a fabrication counts as caught only if a claim overlapping its planted words is
  routed Needs you. A true case is a false alarm if any of its claims is.
- **Tuning:** `INFER_THRESHOLD` was swept from 0.50 to 0.95 on one half of the cases (alternating
  within each label) and reported on the other half. The tuned value, **0.85**, is the default.

| Run | Catch rate (fabrications flagged) | False alarm rate (true claims flagged) |
|---|---|---|
| Held-out half, threshold 0.85 | **95.0%** (19/20) | **2.8%** (1/36) |
| All 112 cases, threshold 0.85 (CI baseline) | 92.5% (37/40) | 4.2% (3/72) |

Every inflated number, fake degree, fake employer, unheld skill, added seniority and injected claim
is caught (32/32). The misses are meaning drift: "Reduced LLM inference **latency** by 40%" when
the resume says cost. The number and the skills are proven and the sentence is nearly identical,
so neither code nor similarity can tell. That is the known limit of this design.

The first run found four Verifier defects, which were fixed before tuning (catch rate 87.5% to
100% and false alarms 4.6% to 0% on the 97 cases that existed then; the meaning-drift cases were
added afterwards):
1. An LLM claim was dropped whenever a code claim overlapped it, so "**Senior** backend and AI
   engineer" disappeared behind the skill "AI".
2. Roles and certifications counted as grounded when their words weren't on the source line
   ("As tech lead", "Google Developer Expert").
3. A count's unit was the first word after it, so "1,200 support agents" and "1,200 agents" never
   matched. Units are now the head noun plus any rate ("2M records daily" vs "per second"), and
   "tripled" reads as 3x.
4. "fine-tuned" wasn't a synonym of the skill Fine-Tuning.

CI runs `python -m eval.run --check`, which fails if the shipped threshold's catch rate falls or
its false alarm rate rises against `eval/baseline.json`. A malicious job description
(`fixtures/malicious_job.json`) is tested end to end in `tests/integration/test_malicious_job.py`.
