# Build Log: Power Hunt, phases 0 to 5 and 7

Context file for a Claude Code session that continues this project, especially one that runs the
stack in Docker. Read it after `CLAUDE.md` (rules), and alongside `docs/HANDOVER.md` (status),
`docs/ARCHITECTURE.md` (code map) and `docs/SPEC.md` (the product spec, the source of truth for
features). It records what each phase built, how it was built, the tests, the problems hit and
how they were fixed, and every file changed. State as of 17 October 2026 (deadline day, 12:45 PM PKT).

---

## 1. Snapshot

| Item | Value |
|---|---|
| Phases done | 0, 1, 2, 3, 4, 5, 7 (eval; MCP not built) |
| Next | Phase 6 (Coach and growth plans), then deploy |
| Tests | **470 passed** (`pytest -q --timeout=60`, about 2 minutes); eval baseline check passes |
| Static checks | ruff format and check, mypy strict on `app`, `tsc` (via `npm run build`), oxlint: all clean |
| Database | Neon Postgres in dev, Alembic head **0009**. Tests use SQLite built by the same migrations |
| Current branch | `feat/phase-7-eval` (on top of `feat/phase-5-form-apply`) |
| Deploy | Not live: the Alternate Clouds builder fails (`volume_placement_capacity`). Not caused by our code |

### Branch stack (each PR builds on the previous one; none merged into `main` after PR #6)

```
main (c43d75c, PR #6 merged)
 └─ 8abf401  Update GitHub Actions to Node 24 versions      branch chore/actions-node24 (owner's commit)
     └─ 45fe1df  fix: reliable LLM extraction with strict schemas and current models   fix/llm-extraction
         └─ 0046a35  feat: scout, ATS scoring, jobs screen and digest ...              feat/phase-2-scout
             └─ 65e28fb  chore: drop .env.example; sync_env reads settings ...         chore/drop-env-example
                 └─ 336602d  feat: tailor, verifier with three-way routing ...         feat/phase-3-tailor-verifier
                     └─ 7060e4c  feat: email apply flow with verified drafts ...       feat/phase-4-email-apply
                         └─ 820b35b  feat: form apply flow with question discovery ... feat/phase-5-form-apply
                             └─ feat: claim checker eval ...                           feat/phase-7-eval  (HEAD)
```

Merge in this order, or merge only `feat/phase-7-eval` (it contains everything). There is
no `gh` CLI on the owner's machine and the token is read-only, so the owner opens PRs in the
browser: `https://github.com/rimsha-hue/hunt-powerpuffgirls/pull/new/<branch>`.

Untracked and never committed: `POWER_HUNT_BUILD_PROMPT.md`, `.env`, `.env.bak`, `assets/`.

---

## 2. Working with Docker (read before running anything)

### Start the stack

```powershell
docker compose up --build        # API at http://localhost:8000, reads .env (Neon), reloads on app/ changes
cd frontend; npm run dev         # http://localhost:5173 with hot reload, proxies /api to :8000
```

`docker-compose.yml` runs `alembic upgrade head`, then `python -m app.seed` (failure is logged,
not fatal), then `uvicorn --reload --reload-dir app`. `WATCHFILES_FORCE_POLLING=true` is set
because Windows file events don't reach Linux containers.

### Docker facts that bite

1. **Read-only mounts:** `./app`, `./data`, `./fixtures`, `./alembic.ini` and `./frontend/dist`
   are mounted read-only over the image. `tests/`, `scripts/` (apart from the copied
   `start.sh`) and `pyproject.toml` are **not** mounted.
2. **`frontend/dist` shadows the image's build.** If you have not run `npm run build` on the host,
   the mount hides the frontend built into the image, and `:8000/` serves nothing useful. Run
   `cd frontend; npm run build` once, or use Vite on :5173.
3. **Dependency changes need `--build`.** The image installs only `[project.dependencies]` from
   `pyproject.toml` (no dev extras), in their own layer.
4. **The embedding model is downloaded at build time** (`BAAI/bge-small-en-v1.5` into
   `/srv/models`, `EMBEDDING_CACHE_DIR`), so startup needs no network. The first build is slow.
5. **Run tests on the host venv, not in the container.** The image has no pytest, ruff or mypy.
   ```powershell
   .\.venv\Scripts\ruff format app tests scripts
   .\.venv\Scripts\ruff check app tests scripts
   .\.venv\Scripts\mypy app
   .\.venv\Scripts\pytest -q --timeout=60
   ```
   Tests never touch Neon: fixtures pass `_env_file=None` and build a temporary SQLite DB with
   the real Alembic migrations. External APIs are mocked with `respx`, and DNS with an
   `UnreachableFetcher`.
6. **Port 8000** inside the container and on the platform. A mismatch with the platform's
   published port causes a Cloudflare 502.
7. **Container runs as `appuser`.** The only writable places are `/srv/var` (SQLite fallback) and
   `/srv/models` (Hugging Face lock files).
8. **The Windows selector-loop issue does not apply inside the container** (Linux). It only
   matters when running uvicorn or scripts directly on Windows against Neon. In the previous
   session the API ran outside Docker through a launcher that does this:
   ```python
   config = uvicorn.Config("app.main:app", host="127.0.0.1", port=8000, loop="none")
   asyncio.run(uvicorn.Server(config).serve(), loop_factory=asyncio.SelectorEventLoop)
   ```
   That launcher, on :8000, and Vite, on :5173, may still be running from the old session.
   **Stop whatever holds port 8000 before `docker compose up`.**
9. **`.env`** is read through `env_file`. Comments must be on their own lines (Docker reads
   `KEY=value # note` as the value). `EMAIL_REDIRECT_TO` is set in dev so every email goes to
   the owner's inbox; **never copy it to production**.

### Production entrypoint

`scripts/start.sh`: `alembic upgrade head`, then `python -m app.seed` (non-fatal), then
`uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers`. The Dockerfile is
multi-stage: Node 22 builds the frontend, then python:3.12-slim installs dependencies, downloads
the model, copies `alembic.ini`, `data/`, `fixtures/`, `scripts/start.sh`, `app/` and the
built frontend. A `HEALTHCHECK` hits `/ready`.

---

## 3. CI (GitHub Actions)

File: `.github/workflows/ci.yml`. It runs on PRs to `main` and pushes to `main`. Jobs:

| Job | Steps |
|---|---|
| backend | `pip install -e ".[dev]"`, ruff check, ruff format --check, `mypy app`, `pytest --cov=app`, bandit `-ll`, pip-audit |
| frontend | `npm ci`, `npm run lint`, `npm run build` (type check), `npm audit --audit-level=high` |
| secrets | gitleaks on the full history (`fetch-depth: 0`) |
| docker | builds the image, runs it with a dummy `SECRET_KEY`, curls `/ready`, `/health`, `/`, which must contain "Power Hunt" |

### Owner's action-version update (commit 8abf401, branch `chore/actions-node24`)

GitHub is retiring the Node 20 runtime for actions, so the owner moved every first-party action
to its Node 24 major version.

| Line | Before | After |
|---|---|---|
| 20, 45, 62, 73 | `actions/checkout@v4` | `actions/checkout@v5` |
| 21 | `actions/setup-python@v5` | `actions/setup-python@v6` |
| 46 | `actions/setup-node@v4` | `actions/setup-node@v5` |
| (unchanged) | `gitleaks/gitleaks-action@v2` | `gitleaks/gitleaks-action@v2` |

How it was done (PowerShell, rewritten as **UTF-8 without a BOM**, which matters because
PowerShell 5.1's `Set-Content -Encoding utf8` adds a BOM):

```powershell
$f = (Resolve-Path ".github\workflows\ci.yml").Path
$t = [IO.File]::ReadAllText($f)
$t = $t -replace 'actions/checkout@v4','actions/checkout@v5' `
        -replace 'actions/setup-python@v5','actions/setup-python@v6' `
        -replace 'actions/setup-node@v4','actions/setup-node@v5'
[IO.File]::WriteAllText($f, $t, (New-Object Text.UTF8Encoding $false))
Select-String -Path .github\workflows\ci.yml -Pattern "uses: actions/"   # check: 6 lines, new versions
```

Result: `.github/workflows/ci.yml | 12 ++++++------` (6 lines changed). All phase branches sit
on top of this commit. Note: its message is not in Conventional Commit form. If the owner wants
the history consistent, the PR title can be `ci: move actions to Node 24 versions`.

Phase 7 added the step "Claim eval baseline" (`python -m eval.run --check`) and `eval/` to ruff and mypy.

---

## 4. Cross-phase fix: LLM extraction and model upgrade (commit 45fe1df)

**Problem the owner reported:** the Groq prompt and extraction were unreliable, and Gemini was on
the old 2.5 Flash.

**Root causes found**
1. gpt-oss (a reasoning model) spent the whole 2048-token budget on hidden reasoning. Only 8 facts
   came back from the demo resume.
2. `openai/gpt-oss-20b` rejected `response_format: json_object`.
3. Free-form JSON drifted from the Pydantic schema (extra keys, missing fields, `$ref`s).
4. The extraction prompt did not ask for verbatim spans, but code rejects any word that is not on
   the cited line, so paraphrased facts were thrown away.

**Fix**
- `app/integrations/llm.py`: `strict_json_schema()` turns the Pydantic schema into a strict
  OpenAI-style schema. It inlines `$ref`s, sets `additionalProperties: false` and lists every key
  as required, and drops validation-only keywords. It sends `reasoning_effort` and a
  per-model `temperature` (`ModelSpec.temperature`; Gemini keeps its default) and uses
  `drop_params`. There is still one repair retry, then a user-facing error.
- `app/config.py`:
  - `llm_primary_model=openai/gpt-oss-120b` and `llm_fallback_model=openai/gpt-oss-20b`.
  - `gemini_model=gemini-3.8-flash` and a new `gemini_fallback_model=gemini-3.5-flash-lite`.
  - `llm_max_tokens=8192` (cap 32768) and `llm_reasoning_effort=low`.
- `app/services/ingestion.py`: the prompt asks for verbatim spans from the cited line.
- Chain: Groq 120b, then Groq 20b, then Gemini 3.8 Flash, then Gemini 3.5 Flash-Lite. An empty
  Gemini fallback is skipped.
- Result: 31 to 33 facts with 0 rejected on the demo resume, roles and employers included.

**Tests** (`tests/unit/test_llm.py`, 15): the chain order, strict schema inlining and closing, the
dropped keywords, reasoning effort and temperature being sent, the temperature omitted for Gemini,
the repair retry, the provider fallback, all providers down, untrusted text unable to close its
`<data>` block, and a null LLM when there are no keys.

**Secrets added by the owner** and checked by key name only (values never printed):
`SMTP_USER`, `SMTP_APP_PASSWORD`, `GITHUB_TOKEN`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.

---

## 5. Phases 0 and 1 (built by the teammate, merged before this session)

- **Phase 0** (PRs #1, #2): FastAPI skeleton, React placeholder, multi-stage Dockerfile, CI,
  `/health` and `/ready`. Default port later fixed (PR #2), now 8000.
- **Phase 1** (PRs #3, #4, #5, #6):
  - Auth: argon2 passwords, server-side sessions, a CSRF token per session, and Google sign-in
    (PKCE, nonce).
  - Demo seed (`app/seed.py`).
  - Resume parsing: two-column PDFs, invisible text and missing space glyphs are handled.
  - The evidence ledger: code pass plus an LLM pass validated against the cited line.
  - GitHub and portfolio ingestion.
  - Input guardrails: GeoNames locations, role titles, phone, LinkedIn, salary and uploads.
  - The Onboarding and Ledger screens, the Powerpuff theme and Calm mode.
  - Migrations 0001 to 0004. 282 tests at handover.

---

## 6. Phase 2: Scout, Matcher, ATS, Classifier, Jobs, digest (commit 0046a35)

**Done-when (spec):** the email arrives, and clicking Apply opens the right job.
**Verified live:** 3 JSearch queries returned 27 new jobs. The digest reached the owner's Gmail,
and its Apply link opened the right job.

### Steps
1. Migration `0005_jobs_apply_tokens_notifications.py` adds the tables `jobs`, `apply_tokens` and
   `notifications`.
2. `domain/status.py`: one transition function, `new -> shortlisted -> in_review -> approved ->
   applied -> {interview, assessment, offer, rejected}`, plus `expired` and `skipped` (which can
   be restored).
3. Sources (`integrations/jobs/base.py`, `sources.py`):
   - JSearch `/search-v2` is primary. Remotive and Arbeitnow (no keys) are the fallback.
   - `fixtures/recorded_jobs.json` (40 real postings) is used only when **every** live source
     failed, or in `DEMO_MODE`.
   - Each source has retries on 429 and 5xx and typed `SourceError`s.
4. `domain/jobs.py`:
   - Title relevance: abbreviations are expanded and the head noun is checked
     (`RELEVANCE_THRESHOLD=0.5`).
   - Place fit uses GeoNames keys. Age comes from `SCOUT_MAX_AGE_DAYS`.
   - Dedupe is by source id, then sha256 of company + title + location (case, spacing and
     legal suffix normalised).
   - The query budget covers every role and place pair.
5. `domain/ats.py`:
   - Keywords are taxonomy skills weighted by section (requirements 2, otherwise 1), plus up to 5
     repeated 2 to 3 word YAKE phrases. They are frozen per job.
   - Score = `round(100*(0.85*coverage + 0.15*format))`.
   - Format checks: standard sections, contact info, work section, length of 1100 words or fewer.
     An unknown tables check is left out of the score.
6. `domain/matching.py`: 60% skills proven by the ledger (with a smoothing prior), 30% title,
   10% place. Required gaps are listed first.
7. `domain/classify.py`: the apply email and the attachments are taken only from sentences about
   applying; footer addresses are ignored.
8. Services:
   - `services/scout.py` runs Scout, Matcher and Classifier for one user. Open jobs are
     re-scored when the ledger changes; ATS "before" stays frozen.
   - `services/runner.py`: a lock per user, so only one run happens at a time. Jobs are
     committed before the email is sent. APScheduler runs every `SCOUT_INTERVAL_HOURS`, and its
     first run waits a full interval.
   - `POST /internal/run-scout` needs `X-Internal-Token`.
9. `services/notifier.py` and `services/apply_links.py`:
   - The digest holds the top 10 new jobs at or above the user's threshold.
   - Each Apply link is a random token stored as a sha256 hash. It is single-use, lasts 72 hours
     (`APPLY_LINK_HOURS`) and is bound to the user and the job.
   - Without a session, a link opens a session scoped to that job only.
10. `integrations/email.py`: an `EmailProvider` interface plus Gmail SMTP, with `EMAIL_REDIRECT_TO`
    for dev. Dropped connections are retried; a bad login is not.
11. API `app/api/jobs.py`: list, detail, fetch (rate limited), status, Gate 1 `apply`,
    `/apply/{job_id}?t=`, and the internal trigger.
12. Frontend:
    - `pages/Jobs.tsx`: filters, Fetch now with speed lines, staggered cards.
    - `pages/JobDetail.tsx`: highlighted keywords, ATS chips and checks, attachments, and Gate 1.
    - Also `components/JobBits.tsx`, `lib/time.ts` and the API types in `lib/api.ts`.

### Files changed (46)
`CLAUDE.md`, `README.md`, `app/api/{auth,deps,health,jobs,schemas}.py`, `app/assets.py`,
`app/config.py`, `app/db/migrations/versions/0005_*.py`, `app/db/models.py`,
`app/domain/{ats,classify,jobs,matching,status}.py`, `app/integrations/email.py`,
`app/integrations/jobs/{__init__,base,sources}.py`, `app/main.py`,
`app/services/{apply_links,notifier,runner,scout}.py`, `docs/{ARCHITECTURE,HANDOVER}.md`,
`fixtures/recorded_jobs.json`, `frontend/src/{App.tsx, components/JobBits.tsx,
components/Layout.tsx, components/ui.tsx, lib/api.ts, lib/time.ts, pages/JobDetail.tsx,
pages/Jobs.tsx, pages/Landing.tsx, pages/Ledger.tsx, pages/Login.tsx}`, `frontend/vite.config.ts`,
`pyproject.toml` (yake, apscheduler), `tests/conftest.py`, `tests/test_app.py`,
`tests/integration/test_jobs_api.py`, `tests/unit/{test_job_sources,test_jobs_domain}.py`.

### Tests
- `test_jobs_domain.py` (38): status gates, relevant and irrelevant titles, a city with the same
  name in another country, the dedupe hash, the query budget, apply-sentence emails vs footers,
  section weights, the score formula with synonyms, format penalties, the smoothed match, and
  "only ledger evidence raises the score".
- `test_job_sources.py` (12): mapping, quota as a source error, retries, Remotive HTML to text,
  Arbeitnow fetched once, recorded postings, SMTP send and redirect, and a bad login not retried.
- `test_jobs_api.py` (14):
  - A fetch scores, dedupes and sends one digest. A second fetch adds nothing.
  - Rescoring on ledger change, and the threshold.
  - The recorded-jobs fallback: only when every source fails, not when sources found nothing.
  - Users are isolated.
  - Apply links: signed for one job, single use, bound to the job, and an existing full session
    is kept.
  - The internal trigger is disabled without a token.

### Issues and fixes
| Issue | Fix |
|---|---|
| JSearch `/search` returned 404 | Moved to `/search-v2` (`JSEARCH_API_URL`). The free tier is 200 requests a month; `SCOUT_MAX_QUERIES` caps each run |
| Remotive ignores `search`, Arbeitnow has no search | Relevance is decided in code (`domain/jobs.py`) |
| Postings naming one skill scored a 100% match | Skill fit smoothed toward 50% with a prior of weight 2. The ATS formula is unchanged |
| Single YAKE words ("build", "model") penalised every CV | Only repeated 2 to 3 word phrases are kept, with job titles, pay and filler excluded |
| Old fixtures shown when live sources merely found nothing | Recorded postings are used only when every live source **failed** |
| Dev reloads used up the JSearch quota | The scheduler's first run waits a full interval |
| Notifier `expunge` of token rows left stale state (review finding) | The token rows are deleted instead |
| SMTP retry predicate retried auth errors | Only dropped connections are retried |

---

## 7. Chore: drop `.env.example` (commit 65e28fb)

The owner asked for `.env` only. `.env.example` was deleted, and `scripts/sync_env.py` now builds
the list of keys from `Settings.model_fields` in `app/config.py`, using the comments there as
documentation. It adds missing keys with their defaults, moves inline comments onto their own
lines, keeps every existing value, lets a later duplicate win (as dotenv does), generates demo
credentials only if they are missing, and backs up to `.env.bak`.

Files: `.dockerignore`, `.env.example` (deleted), `.gitignore`, `CLAUDE.md`, `README.md`,
`scripts/sync_env.py`, `tests/unit/test_sync_env.py` (4 tests).

---

## 8. Phase 3: Tailor, Verifier, Needs-you gate, CV PDF (commit 336602d)

**Done-when:** a planted fabrication is caught, and a confirmed claim is Verified next time.
**Verified live** with Groq and bge-small: 7 bullets were rewritten, 26 claims were checked
automatically and 1 was sent to the user.

### Steps
1. Migration `0006_applications_and_claims.py` adds the tables `applications` and `claims`, and
   the column `resume_lines.bulleted`. Re-upload the resume once so old lines get their bullet flags.
2. `domain/cv.py`:
   - Every CV block is one resume line.
   - Only bullets in summary, experience and projects can be rewritten, one rewrite per line, and
     reordered only within their entry.
   - Titles, education and skills stay word for word.
   - `plain()` turns U+2011 hyphens, no-break spaces and "3×" into plain characters.
3. `domain/style.py`: banned phrases ("passionate", "leverage", ...) plus em and en dashes.
4. `domain/claims.py`:
   - The code pass finds numbers (not dates or version numbers), skills, degrees, known
     employers and "at Org".
   - `merge()`: code claims win overlaps.
   - `route()`:
     - blocked when the value is on the user's blocklist;
     - Verified on an exact ledger match;
     - Inferred when the source line exists, similarity is at least the threshold and the claim
       is grounded (adds no new number, skill, degree or employer);
     - otherwise Needs you.
5. `integrations/embeddings.py`: fastembed `BAAI/bge-small-en-v1.5`, loaded once, with a
   lexical fallback when it is disabled or missing.
6. Services:
   - `services/tailor.py`: an LLM rewrite toward the job keywords. A rewrite that breaks a style
     rule keeps the user's words. Without an LLM, bullets are reordered by relevance.
   - `services/verifier.py`: the code pass plus an LLM claim pass (spans located with
     `locate()`). Similarity is per clause (`clause_around`). Blocklisted values are cut from
     the text.
   - `services/applications.py`: Gate 2 decisions. **Confirm** creates `user_confirmed` evidence
     (the learning loop), **Edit** re-routes the claim, **Remove** drops it, and **Mark false**
     adds it to the blocklist. Approve requires zero open claims.
   - ATS "after" counts a keyword only if the ledger or the original resume supports it.
7. `integrations/pdf.py`: an fpdf2 CV with bundled Public Sans (`app/templates/fonts/`). It is
   text only and one column, and it downloads only after approval.
8. API `app/api/applications.py`: application, tailor, claim decision, approve, `cv.pdf`.
9. Frontend:
   - `pages/Workspace.tsx`: job text, the tailored CV with badges and source tooltips, the
     Needs-you panel (keys Y, E, R), the ATS gauge, the counters, and Approve and download.
   - Also `components/Highlighted.tsx`.
10. The Dockerfile pre-downloads the embedding model. `pyproject.toml` adds fastembed and fpdf2.
    `app/config.py` adds `infer_threshold=0.78` and the `embeddings_*` settings.

### Files changed (36)
`CLAUDE.md`, `Dockerfile`, `README.md`, `app/api/{applications,schemas}.py`, `app/config.py`,
`app/db/migrations/versions/0006_*.py`, `app/db/models.py`,
`app/domain/{ats,claims,cv,ledger,resume,style}.py`, `app/integrations/{embeddings,pdf}.py`,
`app/main.py`, `app/services/{applications,ingestion,tailor,verifier}.py`,
`app/templates/fonts/{LICENSE.md,PublicSans-Bold.ttf,PublicSans-Regular.ttf}`,
`docs/{ARCHITECTURE,HANDOVER}.md`, `frontend/src/{App.tsx, components/Highlighted.tsx,
lib/api.ts, pages/JobDetail.tsx, pages/Workspace.tsx}`, `pyproject.toml`, `tests/conftest.py`,
`tests/integration/test_workspace_api.py`, `tests/unit/{test_claims_and_cv,test_resume_and_uploads}.py`.

### Tests
- `test_claims_and_cv.py` (27 by the end of Phase 4):
  - Rewrites stay within their entry; a line is rewritten only once; typographic hyphens become
    plain.
  - Numbers, skills, degrees and employers are found; version numbers are not claims.
  - An exact ledger match is Verified. An inflated number, a missing skill, a fake degree and a
    fake employer all need the user. A paraphrase is Inferred. Blocklisted values are blocked.
  - Every number in a multi-number metric is needed.
  - **Property:** unsupported keywords never raise the ATS score.
  - Banned phrases are caught; the PDF is text only.
  - The job applied for is not a claim. "at Company" stops before a pronoun.
- `test_workspace_api.py` (5):
  - A fabrication is caught, answered, approved and learned (Verified next time).
  - An edit re-routes the claim; ATS after ignores the unproven skill.
  - Without an LLM, bullets keep the user's words; claims are private.

### Issues and fixes
| Issue | Fix |
|---|---|
| Resume date ranges ("2021 - 2023") were split into separate lines and entries | `domain/resume.py` keeps date ranges together |
| Models wrote U+2011 hyphens and no-break spaces, so claims and keywords stopped matching | `plain()` normalisation in `domain/cv.py` before any matching |
| A short claim ("Redis") against a long line had low similarity by construction | Similarity is taken between the whole rewritten bullet (and its clause) and the source line, the better of the two |
| Framing words diluted similarity ("..., showing a focus on cost") | Per-clause similarity (`clause_around`, clause edges `, ; : ( ) -`) |
| "at Nimbus Labs I built" captured "I" as part of the employer | Organisation words need two or more characters (`_ORG_WORD`) |
| Merging or moving lines could attach a result to the wrong job | One source line per bullet, reordered only within its entry |
| WeasyPrint needs system libraries that differ on Windows, CI and Docker | fpdf2 (pure Python) with bundled fonts |

---

## 9. Phase 4: email apply flow (commit 7060e4c)

**Done-when:** a real email with attachments reaches a test inbox after approval.
**Verified live:** Groq drafted the email, and one invented claim was flagged and answered. It
was sent through Gmail (redirected to the owner) with the CV PDF attached. The job became
Applied, with a follow-up in 7 days.

### Steps
1. Migration `0007_email_apply.py` adds:
   - `applications.email_blocks_json`, `cover_letter_blocks_json`, `recipient` and `compose_note`;
   - `claims.document` (cv, email or cover_letter);
   - the table `application_files` (uploads stored in Postgres, checked by magic bytes, size from
     `MAX_UPLOAD_MB`).
2. `domain/compose.py`:
   - The subject follows the posting's requested format, else "Application for {title}, {name}".
   - Sentences are split with paragraphs kept.
   - Length: email 120 to 180 words, cover letter 200 to 320.
   - The attachment plan lists items found, missing or waived; a portfolio without a link counts
     as missing.
3. `services/composer.py` (Composer and Courier):
   - Writes the email and, if the posting asks, a cover letter, from the **approved CV only**.
   - A style or length problem gets one rewrite, then a note for the user.
   - Without an LLM, it writes a plain letter from the user's CV lines.
   - Prose is verified sentence by sentence against the closest resume line
     (`MIN_SOURCE_SIMILARITY=0.35`).
   - The job's title and company are ignored as claims, and the signature is not verified.
4. Gate 3, `send()`:
   - Requires an approved CV, no open claim in any document, a recipient, and every attachment
     attached, uploaded or waived.
   - The status transition is checked **before** sending, and nothing is marked sent if SMTP
     fails. A sent application refuses to send again.
   - Reply-To is the user's address.
   - Afterwards the job is Applied, with `follow_up_due` = sent + 7 days.
5. `integrations/email.py` gains attachments and Reply-To. `integrations/pdf.py` gains the cover
   letter PDF.
6. API: compose, `PUT email`, uploads, waive, `cover-letter.pdf`, send (rate limited).
7. Frontend `components/EmailPanel.tsx`:
   - To, Subject and the body with badges, all editable.
   - The cover letter with a PDF preview.
   - The attachment checklist with upload or "Send without it".
   - The list of blockers, Approve and send, the envelope animation, and the Applied card.

### Files changed (23)
`CLAUDE.md`, `README.md`, `app/api/{applications,schemas}.py`, `app/db/migrations/versions/0007_*.py`,
`app/db/models.py`, `app/domain/{claims,compose,cv}.py`, `app/integrations/{email,pdf}.py`,
`app/main.py`, `app/services/{applications,composer,verifier}.py`, `docs/{ARCHITECTURE,HANDOVER}.md`,
`frontend/src/{components/EmailPanel.tsx, lib/api.ts, pages/Workspace.tsx}`,
`tests/integration/test_email_apply_api.py`, `tests/unit/{test_claims_and_cv,test_compose}.py`.

### Tests
- `test_compose.py` (11): the subject format or default, no subject instruction, sentences keep
  their paragraphs, the blocks round-trip, length and style rules, the attachment plan, a
  portfolio without a link counts as missing, and the JSON round-trip.
- `test_email_apply_api.py` (4): compose, verify, gate and send; waive, edit and re-verify;
  compose needs an approved CV; uploads are checked.

### Issues and fixes
| Issue | Fix |
|---|---|
| Emails have no one-to-one source line | Each sentence is checked against its closest resume line; the per-claim similarity is the better of sentence and clause |
| "the Senior Android Engineer role at Acme" became claims | `outside()` drops claims inside the job title or company ("ignore phrases") |
| The company name's words matched as employers | A company-word length rule plus the ignore phrases |
| `send()` could mark sent before checking the status path (review finding) | The transition is checked first; there is no partial state on SMTP failure |

---

## 10. Phase 5: form apply flow (commit 820b35b)

**Done-when:** a real Greenhouse job produces sourced answers.
**Verified live** on Anthropic's Greenhouse job 5186067008:
- 24 questions were found and typed;
- profile and ledger answers were sourced;
- two written answers each cite a Tavily company fact with its URL;
- consent questions were left to the user.

### Steps
1. Migration `0008_form_apply.py` adds:
   - the table `company_facts` (a 7-day cache per company);
   - `applications.form_source` and `form_url`;
   - `profiles.saved_answers` (answers to questions the profile has no field for).
2. Discovery (`integrations/forms.py`), tried in order:
   1. The Greenhouse board API, `?questions=true`, including compliance and demographic
      questions.
   2. The Lever apply page.
   3. A generic apply page through `SafeFetcher` (labels, radio and checkbox groups, selects).
   4. Otherwise the user pastes the questions.
3. Add a job by link: `POST /api/jobs/import` with `integrations/jobs/greenhouse.py`. A Greenhouse
   URL becomes a scored job through the same `build_job` as the Scout (`services/scout.py` was
   refactored for this). Other links are refused.
4. `domain/questions.py` types each question:
   - `factual_profile`, `factual_ledger`, `descriptive`, `analytical`, `file`;
   - `consent`: legal agreements, always the user's to answer and never remembered;
   - `voluntary`: EEO questions, answered with the "decline" option by default.
   It also handles word limits from the question, option picking, a saved-answer key that ignores
   case and punctuation, and sponsorship following work authorization.
5. Researcher:
   - `services/researcher.py` with `integrations/research.py` (Tavily) and `domain/research.py`.
   - A fact is a verbatim sentence that names the company, with the company's own site first.
   - Facts are cached for 7 days. The job description is the fallback; failing that, the user is
     asked.
6. Answers (`services/form_apply.py`):
   - Factual answers come from code: the profile, then saved answers, then the ledger. A website
     field takes the portfolio, else the GitHub profile.
   - A descriptive answer cites exactly one numbered fact. Length, style or citation problems get
     one rewrite, then a note.
   - An analytical answer has assumptions, 2 to 4 steps, a result and a risk.
   - Written answers are verified sentence by sentence as the document `q:<index>`.
7. `claims.from_context(claim, text, contexts, subject)` decides when a claim belongs to the
   company or the question rather than the candidate. Any one of these is enough:
   - the claim's clause restates a context text (60% word overlap, `CONTEXT_OVERLAP`) with every
     number in it;
   - the claim is a distinctive phrase (a number, or two or more words that aren't a skill)
     copied from the text, and its own sub-clause has no first person;
   - the claim's words appear in the text and its clause names the company (`subject`).
   `subject=job.company` is threaded through `verifier`, `composer` (compose and `edit_prose`),
   `form_apply` and `api/applications.py`.
8. Gate 4: every answer must be approved. The user then clicks "I submitted it", which sets
   Applied and the follow-up date. **Power Hunt never submits a form.**
9. API `app/api/forms.py` (prefix `/api/jobs/{job_id}/application`): `questions/discover`,
   `questions/paste`, `answers/draft`, `PUT answers/{index}`, `answers/{index}/approve`,
   `submitted`.
10. Frontend:
    - `components/FormPanel.tsx`: the "Apply on their form" tab, with type, status, source, the
      company fact link, edit or answer with "remember", and approve.
    - `pages/ApplyPack.tsx` (`/jobs/:id/pack`): copy buttons, CV and cover letter downloads,
      Open application form, I submitted it.
    - "Add job" by link on `pages/Jobs.tsx`.
11. Config: `tavily_api_key`, `tavily_api_url`, `research_max_results`.

### Files changed (32)
`CLAUDE.md`, `README.md`, `app/api/{applications,forms,jobs,schemas}.py`, `app/config.py`,
`app/db/migrations/versions/0008_form_apply.py`, `app/db/models.py`,
`app/domain/{claims,questions,research}.py`, `app/integrations/{forms,research}.py`,
`app/integrations/jobs/greenhouse.py`, `app/main.py`,
`app/services/{applications,composer,form_apply,researcher,scout,verifier}.py`,
`docs/{ARCHITECTURE,HANDOVER}.md`, `frontend/src/{App.tsx, components/FormPanel.tsx, lib/api.ts,
pages/ApplyPack.tsx, pages/Jobs.tsx, pages/Workspace.tsx}`,
`tests/integration/test_form_apply_api.py`, `tests/unit/test_forms_domain.py`.

### Tests
- `test_forms_domain.py` (38):
  - Question types; consent found in the description too; word limits; picking an option;
    decline; the saved key; direct fields; sponsorship follows work authorization; saved answers;
    the analytical layout.
  - Facts are whole sentences about the company, with the company's own site first.
  - Greenhouse ids and payload, a generic page, pasted questions.
  - `from_context`:
    - restating the company fact is not a claim about the user;
    - a claim about the user is kept;
    - the company's product is not the user's claim;
    - a phrase copied from a fact belongs to the company;
    - a multi-word skill stays the user's;
    - **a company number pinned on the user ("I served 300 factories") is still checked.**
- `test_form_apply_api.py` (5): the Greenhouse form end to end, an unreadable form falls back to
  pasting, drafting needs an approved CV, importing a Greenhouse job, and other links refused.

### Issues and fixes
| Issue | Fix |
|---|---|
| Restated company facts ("your $150 million commitment to the Genesis Mission") became Needs-you claims about the user | `from_context` with clause overlap and the numbers check |
| "Anthropic's work on Claude" made "Claude" a skill claim | The `subject` rule: the claim's words are in the fact **and** its clause names the company. "I built apps with Claude" stays the user's claim |
| The model could pin a company number on the user ("I served 300 factories" copied from a fact) | A copied phrase counts as the company's only if its own sub-clause (cut at that, which, who...) has no first person |
| Website fields were empty for users without a portfolio | Fall back to `https://github.com/{github_username}` |
| Tests made real DNS calls through the fetcher | An `UnreachableFetcher` fake is injected |
| `respx` routes clashed between tests | Named routes |
| Settings with keys in tests | `SecretStr` values built explicitly |
| A lambda assigned to a name (ruff E731) | Replaced with a nested `def` |

Not built: the autofill bookmarklet (stretch).

---

## 10b. Phase 7: claim checker eval, malicious job description, CI baseline (branch `feat/phase-7-eval`)

**Done-when (spec):** CI is green and the eval baseline is recorded.
**Result:** 112 cases. On the held-out half at the tuned `INFER_THRESHOLD` 0.85, the catch rate
is **95.0%** (19/20) and the false alarm rate is **2.8%** (1/36). Over all cases (the CI baseline)
it is 92.5% (37/40) and 4.2% (3/72). The README section "Claim checker eval" has the full write-up.

### Steps
1. `eval/claims.jsonl` holds 72 true cases and 40 fabricated ones.
   - **True:** 7 verbatim, 56 paraphrased, and 9 that add a skill the ledger proves.
   - **Fabricated:** inflated numbers (8), fake degrees (4), fake employers (5), unheld skills (7),
     added seniority (3), injected claims (5) and meaning drift (8).
   - Each case is one tailored bullet citing a demo-resume line (5, 8, 9, 10, 12, 13 or 16).
     A fabrication quotes its `planted` words. `claims` are the spans the LLM claim pass would
     return.
2. `eval/ledger.json`: the facts the LLM proposes from the demo resume (employers, roles,
   projects). The eval checks them with `accept_proposed_facts`, exactly as ingestion does.
3. `eval/run.py` (`python -m eval.run`):
   - Builds the ledger: the code pass over `fixtures/demo_resume.docx` plus the accepted proposals.
   - Runs the real `services.verifier.verify()` on each case. The LLM claim pass is replayed with
     `ReplayedClaims` and similarity is cached with `CachedSimilarity`.
   - A fabrication is caught only if a Needs-you claim overlaps its planted words. A true case is a
     false alarm if any of its claims needs the user.
   - Splits the cases into two halves, alternating within each label. Sweeps thresholds from 0.50
     to 0.95 on the tune half (best catch rate minus false alarm rate; ties go to the higher value)
     and reports on the held-out half.
   - Flags: `--show-misses`, `--check` (exit 1 if worse than `eval/baseline.json`),
     `--write-baseline`, `--similarity lexical`.
   - The embedding model must load; the eval refuses to fall back to lexical silently.
4. Verifier fixes the eval found. The first run gave 87.5% catch and 4.6% false alarms at 0.78:
   - `domain/claims.merge`: an LLM claim is kept unless code claims cover all of its words.
     Before, "Senior backend and AI engineer" vanished behind the skill "AI".
   - `domain/claims._grounded`: role, certification and degree claims need their words on the
     source line. Skills and degrees the ledger proves now ground a paraphrase.
   - `domain/normalize.find_quantities`:
     - The unit is the head noun past any modifiers ("1,200 support agents" gives `1200 agent`).
     - A rate joins the unit (`2000000 record/day`, as opposed to `/second`).
     - "doubled", "tripled" and "quadrupled" are 2x, 3x and 4x.
   - `data/skills_taxonomy.json`: Fine-Tuning gains the aliases "fine-tuned", "fine tuned",
     "fine-tune" and "finetuned".
   - Migration `0009_metric_units.py` recomputes stored metric evidence with the new key rules.
     It deletes duplicates first and moves updated rows through unique placeholders, so the unique
     constraint never collides. **Applied to Neon.**
5. Gate 2 safety with overlapping claims (`services/verifier.py`):
   - `_relocate` re-opens any claim that overlapped edited or removed words: it goes back to Needs
     you with its new text. Claims entirely inside the change are removed.
   - Blocked spans are cut as merged ranges (`_union`).
6. The frontend `Workspace.tsx` (`marksToShow`): where claims overlap, the Needs-you claim is drawn
   first, then the longer one. The others stay in the panel.
7. `app/config.py`: the `infer_threshold` default goes from 0.78 to **0.85**. The owner's `.env`
   line was updated from the old default to 0.85.
8. Malicious job description:
   - The fixture `fixtures/malicious_job.json` contains a fake `</data>` close, a "SYSTEM OVERRIDE",
     an instruction to add a PhD, Google and 95%, and an attacker email.
   - The test `tests/integration/test_malicious_job.py` scripts a model that obeys the injection
     completely and checks that:
     - the posting reaches the model only as data;
     - the PhD, Google, 95% and Kubernetes claims are all Needs you;
     - approval returns 409;
     - ATS "after" ignores Kubernetes;
     - no email is sent.
9. CI (`.github/workflows/ci.yml`):
   - ruff and mypy also cover `eval/`.
   - A new step, "Claim eval baseline", runs `python -m eval.run --check`. It downloads the
     embedding model, about 70 MB, at run time.

### Files changed
`.github/workflows/ci.yml`, `CLAUDE.md`, `README.md`, `app/config.py`,
`app/db/migrations/versions/0009_metric_units.py`, `app/domain/{claims,normalize}.py`,
`app/services/verifier.py`, `data/skills_taxonomy.json`, `docs/{ARCHITECTURE,BUILD_LOG,HANDOVER}.md`,
`eval/{__init__.py,run.py,claims.jsonl,ledger.json,baseline.json}`, `fixtures/malicious_job.json`,
`frontend/src/pages/Workspace.tsx`, `tests/integration/{test_malicious_job,test_migrations_and_text,
test_portfolio_api}.py`, `tests/unit/{test_claims_and_cv,test_eval,test_ledger,test_normalize,
test_verifier_decisions}.py`.

### Tests (470 passing, up from 445)
- `test_eval.py` (9): the spec minimums, every case citing a tailorable bullet, the ledger built
  from the resume, case validation, a balanced split, a lexical smoke check, the rate maths, the
  tie rule, and the baseline comparison.
- `test_claims_and_cv.py` (+5): an LLM claim the code covers is dropped, a longer one is kept,
  overlapping LLM claims keep the earlier one, a title the line lacks needs the user (one the line
  states is Inferred), and ledger skills ground a paraphrase.
- `test_normalize.py` (+5): modifiers don't change the unit, the noun phrase stops at a stopword,
  rates, and word multipliers.
- `test_verifier_decisions.py` (2): removing an inner claim re-opens the statement around it;
  removing the outer claim removes what it contained.
- `test_malicious_job.py` (2) and the migration test for 0009.
- Updated expectations for the new keys: `test_ledger.py` (`2000000 record/day`) and
  `test_portfolio_api.py` (`1200 agent`).

### Issues and fixes
| Issue | Fix |
|---|---|
| The first eval run was perfect after the fixes, which proved nothing: every fabrication was code-detectable, so the threshold was never tested | Added meaning-drift cases and harder paraphrases, written before running them. Changes stopped after that, so the numbers aren't fitted to the data |
| Rate words dropped from units ("per second" matched "daily") | The rate became part of the unit |
| "Tripled" was invisible to the code pass (a false alarm, and a hole for an invented "Doubled revenue") | Word multipliers became quantities |
| Removing "AI" inside "Senior backend and AI engineer" would have marked the outer claim removed while its words stayed in the CV | `_relocate` re-opens overlapping claims as Needs you |
| A Python scratch script with non-raw strings wrote `\b` as a backspace character into `normalize.py` and `CLAUDE.md` | Fixed byte by byte. Lesson added to CLAUDE.md: write code with backslashes through the editor tools |
| Git Bash `sed` dropped backslashes in a replacement, and a heredoc broke on quotes | Used the Edit and Write tools instead |
| `ruff` B007 on an unused loop variable | Renamed it to `_` |

### Known limits (reported honestly in the README)
- **Meaning drift** passes: a proven number attached to another outcome in a near-identical
  sentence ("inference **latency** by 40%" when the resume says cost). Neither code nor
  similarity can tell. A fix would need outcome-noun matching for metrics.
- One false alarm comes from the rate rule: "Automated daily ETL pipelines ... handling 2M records"
  (the rate is not next to the number).
- An apply email injected into a job description becomes the recipient. Gate 3's visible,
  editable To field is the protection. A warning when the email's domain differs from the
  company's would help.
- Not built from Phase 7: the MCP server, and demo mode for company research (jobs have had demo
  mode since Phase 2).

---

## 11. Environment and tooling issues (all phases)

| Issue | Fix or workaround |
|---|---|
| psycopg async fails on Windows' default Proactor loop | Use the Selector loop: `asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)`. Docker (Linux) is unaffected |
| `uvicorn --reload` hangs in a shell without a console and can't stop the old process | Use `docker compose up`, or the selector-loop launcher with manual restarts |
| Bash heredocs and `sed` broke Python files with quotes and backslashes | Edits went through small Python scripts that assert each old string exists exactly once, or through the editor tools |
| PowerShell 5.1 writes a BOM | Write with the editor tools or `[IO.File]::WriteAllText(..., UTF8Encoding $false)` |
| Ruff `--fix` removes imports that are unused at that moment | Add the import after the code that uses it; rerun mypy |
| Cancelling a DB call corrupts psycopg or hangs aiosqlite | `DatabaseProbe` (shielded task, bounded wait, never cancel) |
| Neon is slow from Pakistan (about 1 s per health check) | Keep `pool_pre_ping`, `pool_recycle`, the connect timeout and the probe; don't set `DB_HEALTH_TIMEOUT_SECONDS` below 5 |

---

## 12. Endpoint map (Phases 2 to 5)

```
GET  /api/jobs                       POST /api/jobs/fetch            POST /api/jobs/import
GET  /api/jobs/{id}                  POST /api/jobs/{id}/status      POST /api/jobs/{id}/apply   (Gate 1)
GET  /apply/{id}?t=                  POST /internal/run-scout  (X-Internal-Token)
GET  /api/jobs/{id}/application      POST /api/jobs/{id}/tailor      POST /api/claims/{id}/decision (Gate 2)
POST /api/jobs/{id}/application/approve          GET /api/jobs/{id}/application/cv.pdf
POST /api/jobs/{id}/application/compose          PUT /api/jobs/{id}/application/email
POST /api/jobs/{id}/application/attachments...   POST .../attachments/{kind}/waive
GET  /api/jobs/{id}/application/cover-letter.pdf POST /api/jobs/{id}/application/send  (Gate 3)
POST /api/jobs/{id}/application/questions/discover | questions/paste | answers/draft
PUT  /api/jobs/{id}/application/answers/{i}      POST .../answers/{i}/approve
POST /api/jobs/{id}/application/submitted  (Gate 4)
```
Every state-changing request needs `X-CSRF-Token`. LLM endpoints are rate limited.

Frontend routes: `/`, `/login`, `/onboarding`, `/ledger`, `/jobs`, `/jobs/:id`,
`/jobs/:id/workspace`, `/jobs/:id/pack`.

---

## 13. How to verify a phase end to end (what was done live)

1. `docker compose up --build`, then log in with `DEMO_USER_EMAIL` / `DEMO_USER_PASSWORD` from
   `.env`. Never print them.
2. **Phase 2:** on Jobs, click Fetch now. Jobs appear, and the digest arrives at the
   `EMAIL_REDIRECT_TO` inbox. Its Apply link opens that job only.
3. **Phase 3:** on a job, click Apply now, then open the workspace. Bullets are rewritten with
   badges. Answer the Needs-you claims, approve, and download the CV PDF.
4. **Phase 4:** on an email job, compose. Check the badges and the attachment checklist, then
   Approve and send. The mail arrives with the PDF, and the job shows Applied.
5. **Phase 5:** on Jobs, use Add job with a Greenhouse URL (for example
   `https://job-boards.greenhouse.io/anthropic/jobs/5186067008`). In the workspace, open
   "Apply on their form": discover the questions, draft, review, and approve each answer. Then
   use the Apply Pack and "I submitted it".
6. **Phase 7:** `.\.venv\Scripts\python -m eval.run --show-misses` prints the confusion matrices,
   the results by kind and every miss. `--check` is the CI gate. In the workspace, a tailored
   bullet that adds "Senior" or a number the resume lacks shows a Needs-you badge.

Check which `.env` keys are set without printing values:
```powershell
.\.venv\Scripts\python -c "from dotenv import dotenv_values as d; print(sorted(k for k,v in d('.env').items() if v))"
```

---

## 14. What's next

1. **Phase 6: Coach and growth plans.**
   - A required skill with no ledger support becomes one project plan: title, why, 4 to 6 steps,
     time, acceptance criteria.
   - GitHub repo verification checks four things: the owner, created after the plan, the skill
     in the languages or README, and 3 or more commits.
   - Plus the Activity screen and its counters.
2. **Phase 7 leftovers:** the MCP server (`score_job`, `tailor_for_job`, `verify_claims`,
   `ats_score`) and demo mode for company research.
3. **Deploy:**
   - Do **not** copy `EMAIL_REDIRECT_TO`.
   - Set `INTERNAL_TOKEN`, `APP_ENV=production`, `LOG_JSON=true`, the public `APP_BASE_URL`, a
     fresh 48-character `SECRET_KEY` and `FORWARDED_ALLOW_IPS`.
   - Add the Google callback for the public URL.
   - Rebuild, Deploy and check `/health`. Migration 0009 runs at startup.
