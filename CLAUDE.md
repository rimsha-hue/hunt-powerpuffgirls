# CLAUDE.md: Power Hunt

Operating guide for Claude Code on this repository. Read this first, then `docs/HANDOVER.md`
(current state and next steps), `docs/ARCHITECTURE.md` (how the code fits together) and
`docs/SPEC.md` (the original product and engineering spec, the source of truth for features).
`docs/BUILD_LOG.md` records how phases 0 to 5 were built: steps, files, tests, issues, Docker notes.

## 1. What this is

Power Hunt is a human-in-the-loop job application agent for NASCON '26 Agentika 2.0
("Human + AI: Better Together"). It finds jobs, scores fit, tailors the CV and prepares each
application, and proves every claim it writes. Product rule: **No source, no claim.** Every fact
about the user traces to evidence (a resume line, a GitHub repo, a portfolio page, or the user's
own confirmation). When the system cannot prove something, it asks the user.

- Deadline: 17 October 2026, 12:45 PM PKT. Deploy target: Alternate Clouds, one Docker container.
- Judging: technical execution 25, usefulness 20, theme 15, creativity 15, automation depth 15,
  UX 10. Grayhat (security engineers) are judges, so security and reliability are visible.
- Status: Phases 0 to 5 and the Phase 7 eval are done (catch rate 95.0%, false alarms 2.8% on
  the held-out half; README "Claim checker eval"). Next: Phase 6 (Coach and growth plans).
  See `docs/HANDOVER.md` for the exact state and known issues.

## 2. How to work here

**Build phase by phase** (spec section 19). Finish a phase, verify it, open a PR, stop for review.
Under time pressure follow the spec's cut line: phases 0 to 4 plus the eval (phase 7) first.

**Git**
- Never commit to `main`. Branch `feat/<thing>` or `fix/<thing>`, push, open a PR with `gh`.
- Commit messages: **one short Conventional Commit line**, e.g. `feat: scout with job fallbacks`.
  No body, **no `Co-Authored-By` trailer**, no AI attribution in commits or PRs (owner's rule).
- PR body: two or three plain sentences. CI must be green before merge.
- Don't commit `.env`, `.env.bak`, `assets/` (source media), or the two `*_BUILD_PROMPT.md` files.

**Speed.** The owner wants momentum. Make a decision, implement it properly, verify once with
the right test, move on. Don't re-check the same thing several ways.

**Quality bar** (owner's explicit rule): no patches. Find the root cause and fix it properly.
Typed code, tests for every domain rule, nothing hardcoded that varies by environment.

## 3. Commands

Windows machine, PowerShell. Python venv at `.venv`. Node 22 is installed per-user at
`%LOCALAPPDATA%\Programs\node-v22.23.3-win-x64` (on the user PATH; new terminals pick it up).

```powershell
# Backend checks (what CI runs)
.\.venv\Scripts\ruff format app tests eval scripts
.\.venv\Scripts\ruff check app tests eval scripts
.\.venv\Scripts\mypy app eval
.\.venv\Scripts\pytest -q --timeout=60
.\.venv\Scripts\python -m eval.run --check     # claim eval vs eval/baseline.json (--show-misses)

# Dev stack: API in Docker against Neon (reads .env), reloads on app/ changes
docker compose up --build        # http://localhost:8000
cd frontend; npm run dev         # http://localhost:5173 with hot reload, proxies /api to :8000
cd frontend; npm run build       # also served at :8000 (frontend/dist is mounted)
cd frontend; npx oxlint          # frontend lint

# Database migrations (alembic autogenerate, then review the file by hand)
$env:DATABASE_URL="sqlite+aiosqlite:///./scratch.db"; .\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\alembic revision --autogenerate -m "what changed" --rev-id 0010

# Add settings declared in app/config.py to .env without losing values (backs up to .env.bak)
.\.venv\Scripts\python scripts\sync_env.py
```

Tests never touch Neon: fixtures pass `_env_file=None` and a temporary SQLite database built by
the real Alembic migrations.

## 4. Architecture rules

Layers (details in `docs/ARCHITECTURE.md`):

| Layer | Path | Rule |
|---|---|---|
| Domain | `app/domain/` | Pure functions and dataclasses. No I/O, no DB, no HTTP, no LLM. Fully unit tested. |
| Integrations | `app/integrations/` | One adapter per external system. Timeouts, retries (tenacity), typed results, user-safe error classes. |
| Services | `app/services/` | Orchestrate domain + DB + integrations. |
| API | `app/api/` | Thin routers. Pydantic schemas in `schemas.py`. Dependencies in `deps.py`. |
| DB | `app/db/` | SQLAlchemy 2 async models, Alembic migrations, engine/probe in `session.py`. |

- **Deterministic truth.** The LLM writes language and *proposes* facts. Code decides what is
  true, what score something gets and what status it has (`app/domain/ledger.py`).
- **Configuration vs code.** Anything that varies by environment (URLs, keys, limits, timeouts,
  sizes, worker counts, trusted proxies) is a setting in `app/config.py`, documented there with
  a comment (there is no `.env.example`; `scripts/sync_env.py` reads the settings). Domain rules (what counts as evidence, weights in the ATS formula) are named
  constants in code with tests. The frontend never duplicates a server limit: it reads
  `/api/meta/config`.
- **Dependency injection.** Shared clients live on `app.state` (built in `create_app` lifespan)
  and reach routes through `deps.py`. `create_app(settings, llm=, fetcher=, locations=)` lets
  tests inject fakes.
- **Untrusted input.** Resume text, job descriptions, web pages and form questions reach the LLM
  only inside `<data>` blocks via `data_block()`; every LLM output validates against a Pydantic
  schema (one repair retry, then a user-facing error).
- **Every URL from user input** goes through `SafeFetcher` (`app/integrations/fetch.py`): public
  IPs only, pinned to the checked IP, redirects re-checked, size/time capped.

## 5. Input guardrails (owner requirement: validate everything everywhere)

Free text is never stored as-is. Pattern: Pydantic checks shape (types, lengths, enums,
`extra="forbid"`), then a pure domain normaliser checks meaning against reference data and
returns canonical values or **all** field errors at once (`app/domain/profile.py`
`normalize_changes`). Updates are atomic: one bad field saves nothing. Errors use FastAPI's 422
shape `{"detail":[{"loc":["body",field],"msg":...}]}` so the UI shows them per field.

| Input | Rule | Where |
|---|---|---|
| Locations | Resolve against GeoNames (34k cities, 252 countries) to keys `city:<id>`, `country:<ISO>`, `remote`. Unknown text is rejected with suggestions. | `domain/locations.py` |
| Role titles | Must contain a job head noun (engineer, analyst, intern...), length/charset/word limits. | `domain/roles.py` |
| Phone | libphonenumber, stored E.164, default region from settings. | `domain/contact.py` |
| LinkedIn | Canonical `https://www.linkedin.com/in/<handle>`. | `domain/contact.py` |
| Portfolio URL | http(s), public hostname (no IPs, localhost, .local); public IP re-checked at fetch time. | `domain/profile.py`, `integrations/fetch.py` |
| Salary | amount + ISO 4217 currency (from GeoNames country data) + month/year. | `domain/profile.py` |
| Notice period, work authorization | Enums; labels served by `/api/meta/config`. | `domain/profile.py` |
| Uploads | Magic bytes (PDF/DOCX), size from settings. | `domain/uploads.py` |
| Request bodies | JSON and multipart size limits in middleware (413). | `app/middleware.py` |

New user-facing input must follow the same pattern: shape in the schema, meaning in a domain
normaliser with tests, suggestions endpoint if the value set is large.

## 6. Hard-won lessons (don't repeat these)

- **Never cancel a database call midway.** Cancelling psycopg during connection setup corrupts
  the connection ("Explicit rollback() forbidden..."), and cancelling aiosqlite mid-connect hangs
  shutdown. Use `DatabaseProbe` (`app/db/session.py`): shielded task, bounded wait, no cancel.
- **Neon is far away and serverless.** From Pakistan a new connection takes seconds; idle compute
  sleeps. Keep `pool_pre_ping`, `pool_recycle`, the driver `connect_timeout`, and the probe.
- **`.env` comments go on their own lines.** Docker, CI and dashboards read `KEY=value # note` as
  the value. `KEY=   # note` is an empty value. `scripts/sync_env.py` handles both.
- **PowerShell 5.1 writes UTF-8 with a BOM** (`Set-Content -Encoding utf8`). Write JSON and code
  with the editor tools, not PowerShell redirection.
- **PDF parsing**: two-column resumes, missing space glyphs and hidden white/tiny text are real
  (the owner's own resume has all three). `integrations/resume_text.py` handles them; keep the
  tests in `tests/unit/test_pdf_layout.py` passing.
- **Degrees are evidence only in an Education section and never in future-tense sentences**
  ("plan to pursue a PhD"). An LLM-proposed degree is subject to the same rule.
- **GeoNames alternate names collide** ("Islamabad" is an old name of Chattogram). A place's own
  name always outranks alternates.
- **Ruff `--fix` removes imports that are unused at that moment.** Add the import after the code
  that uses it, or rerun mypy after fixing.
- **psycopg async needs the selector event loop on Windows.** `uvicorn --reload` uses it, plain
  `uvicorn` on Windows does not; a standalone script against Neon must call
  `asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)`. Uvicorn's reload also can't stop
  the old process when started from a shell without a console; prefer `docker compose up`.
- **LLM text is normalised before matching.** Models write U+2011 hyphens, no-break spaces and
  "3×"; `domain/cv.py` turns them plain, or claims and ATS keywords stop matching.
- **JSearch moved to `/search-v2`** and its free tier allows 200 requests a month. Each Scout run
  spends `SCOUT_MAX_QUERIES` per user; the scheduler's first run waits a full interval so dev
  reloads don't spend quota. Remotive ignores its `search` parameter and Arbeitnow has none, so
  relevance is decided in code (`domain/jobs.py`).
- **Run the claim eval after any change to `domain/claims.py`, `domain/normalize.py`,
  `services/verifier.py` or the taxonomy.** It found four Verifier bugs the unit tests missed.
  If you change the rules on purpose, rerun with `--write-baseline` and say why in the PR.
- **Claims may overlap** (an LLM claim around a code claim). Editing or removing part of a
  statement sends the claim around it back to Needs you (`verifier._relocate`); never let a
  partial edit hide the rest of a statement.
- **Metric keys are value + head noun + rate** ("1200 agent", "2000000 record/day"). If the
  key rules change, stored evidence must be recomputed by a migration (see 0009).
- **Don't write regexes through Python scripts with non-raw strings** (`\b` becomes a
  backspace). Use the editor tools for code with backslashes.
- **Rate limiting** uses the `limits` library through a dependency (`api/ratelimit.py`), not
  slowapi (slowapi can't take per-app settings). Client IP comes from uvicorn, which trusts
  `X-Forwarded-For` only from `FORWARDED_ALLOW_IPS`.

## 7. Style

- Python 3.12, type hints everywhere, mypy strict, ruff (line length 100).
- Comments explain *why*, not what. Docstrings on modules and non-obvious functions.
- Structured logs (structlog): event names and ids only. Never log resume text, emails, tokens,
  passwords or URLs with credentials.
- User-facing error messages are plain, specific and safe to display.
- Frontend: React 19 + TypeScript strict + Tailwind 4 + TanStack Query + Motion. Comic style:
  3px ink borders, hard shadows, Bangers only for headlines. Every animation respects Calm mode
  and `prefers-reduced-motion` (`useCalm()`). Check contrast (4.5:1) for any new colour pair;
  the palette and verified ratios are at the top of `frontend/src/index.css`.
- Theme: Powerpuff Girls characters appear in the owner's videos (approved by the owner). The
  logo, hearts motif and palette are original.
- Writing in prompts and UI copy: no em or en dashes, plain words (spec section 8).

## 8. Secrets and accounts

- `.env` holds real keys (Neon, Groq, Gemini, RapidAPI, Tavily, demo account). Never print,
  log or commit values. To check presence, print key names only.
- `.env.bak` (git-ignored) is a backup made by `sync_env.py`; delete it once `.env` is verified.
- Demo account credentials live in `.env` (`DEMO_USER_EMAIL`, `DEMO_USER_PASSWORD`); the account
  is created at startup by `app/seed.py`.
