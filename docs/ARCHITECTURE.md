# Architecture

How the code fits together today (end of Phase 1). For the full product spec see `SPEC.md`.

## Runtime shape

One container: FastAPI (uvicorn) serves the JSON API under `/api`, health under `/health` and
`/ready`, and the built React app for every other path. State lives in Postgres (Neon). The
entrypoint `scripts/start.sh` runs `alembic upgrade head`, seeds the demo account
(`python -m app.seed`), then starts uvicorn.

```
browser ── /api, /health ──▶ FastAPI ──▶ services ──▶ domain (pure rules)
   │                            │            ├──▶ integrations ──▶ Groq / Gemini (LiteLLM), GitHub,
   └── everything else ──▶ React build      │                     public web (SafeFetcher), Jina Reader
                                            └──▶ SQLAlchemy async ──▶ Postgres (Neon) / SQLite in tests
```

## Code map

```
app/
  main.py            create_app(): lifespan (engine, DB probe, HTTP client, GitHub client, fetcher,
                     background GeoNames load), middleware, routers, SPA + cache headers
  config.py          Settings (pydantic-settings); every environment-specific value
  middleware.py      BodySizeLimitMiddleware (JSON vs multipart limits, 413)
  security.py        Security headers (strict CSP, nosniff, frame deny, HSTS in production)
  logging.py         structlog setup (JSON in production)
  seed.py            Demo account from fixtures/demo_profile.json + demo resume (idempotent)
  assets.py          Paths to shipped fixtures
  api/
    auth.py          signup, login, logout, me, delete-my-data, Google OAuth start/callback
    profile.py       GET/PUT profile (normalised through domain/profile.py)
    ledger.py        resume upload + sample, GitHub sync, portfolio sync, ledger list/delete
    meta.py          /api/meta/config, location and role suggestions
    health.py        /health (db, llm, scheduler) and /ready
    deps.py          DB session, current user + CSRF, shared clients from app.state
    ratelimit.py     RateLimiter (limits library) + rate_limit("rule") dependency
    schemas.py       Request/response contracts
  domain/            Pure, deterministic, unit tested
    taxonomy.py      Skill matching (data/skills_taxonomy.json, 370 skills with synonyms)
    normalize.py     Quantities with units, degrees, organisations, tokens
    resume.py        Logical lines (rejoin wraps, hyphenation), sections, contact info
    ledger.py        What counts as a fact; code extraction; checking LLM proposals; entry context
    uploads.py       Magic-byte file checks
    locations.py     GeoNames index: resolve and suggest places
    roles.py         Job title validation and suggestions (data/job_titles.json)
    contact.py       Names, phones (E.164), LinkedIn URLs
    profile.py       Profile enums/labels and normalize_changes()
  integrations/
    llm.py           LiteLLM client: provider chain, data blocks, schema validation + repair
    resume_text.py   PDF/DOCX text: invisible-text filter, column detection, word gaps
    github.py        Public repos, languages, topics (retries on 429/5xx)
    fetch.py         SafeFetcher (SSRF guard)
    webtext.py       HTML and Markdown to text
    google_oauth.py  Authorization code + PKCE + nonce, ID token checks
  services/
    ingestion.py     Resume, portfolio and GitHub ingestion into the evidence ledger
    auth.py          argon2 passwords, server-side sessions, Google account linking
    signing.py       HMAC-signed expiring cookie payloads
    audit.py         Audit log writer
  db/
    models.py        users, profiles, auth_sessions, resume_lines, evidence, claim_blocklist, audit
    session.py       Engine factory, DatabaseProbe, ping
    migrations/      Alembic (0001 to 0004)
frontend/src/
  pages/             Landing, Login, Onboarding, Ledger (app pages are lazy-loaded)
  components/        Layout (nav, Calm mode), Logo, ChipInput (combobox), FactList, Toast, ui
  lib/               api client (CSRF, field errors), session, config, calm, toast, cx
data/                skills_taxonomy.json, job_titles.json
fixtures/            demo_resume.docx, demo_profile.json
scripts/             start.sh, sync_env.py, make_demo_resume.py
tests/               unit/ (domain, integrations), integration/ (API end to end on SQLite)
```

## Data model

| Table | Purpose | Notes |
|---|---|---|
| `users` | Account | `password_hash` empty for Google-only accounts; `google_sub` unique |
| `profiles` | Preferences | Locations stored as keys; phone E.164; structured salary; enums |
| `auth_sessions` | Server-side sessions | Only the SHA-256 of the token is stored; per-session CSRF token; `scope_job_id` reserved for Phase 2 apply links |
| `resume_lines` | Numbered logical lines of the current resume | Evidence cites these ids |
| `evidence` | The ledger | `type`, `normalized_value`, `source` (resume, github, portfolio, ...), `source_ref` (line id or URL), `context` (entry a metric belongs to). Unique per (user, type, value, source) |
| `claim_blocklist` | Values the user marked false | Never re-enter the ledger (Verify gate, Phase 3) |
| `audit` | Every gate decision and ledger change | Ids and event names only |

All user-owned tables cascade on user delete ("Delete my data").

## Evidence pipeline

1. Upload: magic bytes and size checked; text extracted (PDF: invisible text dropped, columns
   split, tight word gaps).
2. `split_resume`: logical lines (wrapped bullets rejoined, hyphenation resolved, bullets flagged),
   sections from headings.
3. Code pass (`extract_facts_by_code`): taxonomy skills, degrees (Education only, not future
   tense), GPA, metrics one per clause with entry context, certifications by section.
4. LLM pass (optional): proposals with cited line ids, validated by `accept_proposed_facts`
   (line exists, key tokens and every number on it, degree rules).
5. Blocklisted values removed; evidence stored with its source.
Portfolio: same pipeline over the page text (SafeFetcher, Jina Reader fallback for JS pages).
GitHub: repo languages and topics through the taxonomy, plus project names.

## Security

- Sessions: random 32-byte tokens, hashed at rest, HttpOnly, Secure in production, SameSite=Lax.
- CSRF token required on every state-changing request (`X-CSRF-Token`).
- argon2 password hashing; constant-time behaviour for unknown emails; generic login errors.
- Google OAuth: PKCE S256, state, nonce in an HMAC-signed short-lived cookie; ID token audience,
  issuer, expiry, nonce and `email_verified` checked; links by Google id then verified email.
- Rate limits per rule from settings; 429 with `Retry-After`.
- Request size limits; upload type by magic bytes.
- SSRF-safe fetching; strict CSP with self-hosted fonts; HSTS in production.
- Production refuses to start with a missing or short `SECRET_KEY`.

## Reliability

- Every outbound call: timeout from settings, retries with backoff on 429/5xx (tenacity or
  LiteLLM), and a fallback (LLM provider chain; code-only extraction when the LLM is down;
  Jina Reader when a page needs JavaScript).
- `DatabaseProbe` keeps health checks from ever breaking DB connections.
- GeoNames index builds in a background thread at startup; only location lookups wait for it.
- Cache headers: hashed assets immutable for a year, `index.html` no-cache, media one day.
