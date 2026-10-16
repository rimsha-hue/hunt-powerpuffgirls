# Handover: state of Power Hunt (16 October 2026)

Read with `CLAUDE.md` (how to work here), `ARCHITECTURE.md` (code map) and `SPEC.md` (features).

## 1. Where things stand

| Phase | Scope (spec section 19) | State |
|---|---|---|
| 0 | Skeleton, Dockerfile, CI, health | **Done**. Deploy blocked by the platform (see section 4). |
| 1 | Auth, demo seed, resume parsing, evidence ledger, GitHub ingestion, Onboarding and Ledger screens | **Done**, plus portfolio ingestion, Google sign-in, input guardrails, AI extraction fixed (decision log). |
| 2 | Scout with fallbacks, dedupe, Matcher, ATS scorer, Classifier, Jobs screen, digest email with signed Apply links | **Done.** Verified live: 3 JSearch queries, 27 new jobs, digest delivered with working Apply links. |
| 3 | Tailor, Verifier (three-way routing), Needs-you panel, learning loop, ATS after, CV PDF | **Done.** Verified live with Groq + bge-small: 7 bullets rewritten, 26 claims checked automatically, 1 sent to the user. |
| 4 | Email apply flow (attachments, humanized email, SMTP send) | **Next.** Not started |
| 5 | Form apply flow (Greenhouse, generic forms, Researcher, Apply Pack) | Not started |
| 6 | Coach and growth plans with GitHub verification; Activity screen | Not started |
| 7 | Claim eval (`eval/claims.jsonl`, `python -m eval.run`), malicious JD test, demo mode, MCP | Not started |

Cut line if time runs short: phases 0 to 4 and the eval first (spec section 19).

Merged PRs: #1 skeleton, #2 port, #3 Phase 1, #4 PDF/portfolio fixes, #5 Google sign-in + theme,
#6 guardrails + config hardening + Neon dev setup. Tests: 282 passing; CI green (ruff, mypy,
pytest, bandit, pip-audit, gitleaks, frontend lint/build/audit, Docker smoke test).

## 2. Running it

See README "Run locally". Short version: `docker compose up --build` (API on :8000 against Neon,
reads `.env`), and `npm run dev` in `frontend/` (:5173, hot reload). Docker Desktop must be
running. Node 22 is installed per-user on the owner's machine.

Demo account: email and password are `DEMO_USER_EMAIL` / `DEMO_USER_PASSWORD` in `.env`; created
automatically at startup. The demo resume is fictional (Ayesha Khan, `fixtures/demo_resume.docx`).

## 3. Known issues, most important first

1. **Deploy blocked by Alternate Clouds' builder**: `volume_placement_capacity` /
   "rebuild returned no job", even on a recreated service. Not our code (the image builds in CI
   and locally). Retry, or raise it with the organisers. Port 8000; set variables per README.
2. **Health checks take about 1 second** from Pakistan to Neon (US East, Ohio). Fine for now; a
   deploy in US East will be fast. Don't lower `DB_HEALTH_TIMEOUT_SECONDS` below 5.
3. Skill taxonomy has 370 skills; the spec asks for about 500. Grow it as Phase 2 job
   descriptions reveal gaps (`data/skills_taxonomy.json`, keep tests green).
4. Main JS bundle is about 160 KB gzipped (React + Motion). Acceptable; could use Motion's
   `LazyMotion` later.
5. `.env.bak` on the owner's machine holds a copy of the secrets (git-ignored). Delete after
   confirming `.env` works.
6. Uploaded resume files are not stored (only extracted lines). Intentional for privacy; Phase 3
   renders CVs from structured data, not the original file.

## 4. Deployment checklist (Alternate Clouds)

1. Service from `rimsha-hue/hunt-powerpuffgirls`, branch `main`, root `.`, build/start commands
   empty (the Dockerfile is used).
2. Ports: container `8000`.
3. Variables: copy from `.env`, but set `APP_ENV=production`, `LOG_JSON=true`, `APP_BASE_URL` to
   the public URL, a fresh 48-character `SECRET_KEY`, and `FORWARDED_ALLOW_IPS` to the platform's
   proxy addresses (ask the platform; never `*`). Tick "Secret" for every key.
4. Add the production callback `https://<public-url>/api/auth/google/callback` in Google Cloud
   if Google sign-in is configured.
5. Rebuild, then Deploy, then open `/health`.

## 5. Phase 2: what was built

- Tables `jobs`, `apply_tokens`, `notifications` (migration 0005). Statuses in
  `domain/status.py` (one transition function).
- Sources (`integrations/jobs/`): JSearch `/search-v2` (primary), Remotive, Arbeitnow, and
  `fixtures/recorded_jobs.json` (40 real postings recorded 16 Oct). Tiers: JSearch, then
  Remotive + Arbeitnow; recorded postings only when every live source failed, or in `DEMO_MODE`.
- Code decides relevance (`domain/jobs.py`): title vs target roles (abbreviations expanded, head
  noun checked), place vs target locations, age. Dedupe by source id, then sha256 of
  company + title + location.
- ATS (`domain/ats.py`): taxonomy skills weighted by section (requirements 2, else 1) plus up to
  five repeated 2-3 word YAKE phrases (job titles, pay and filler excluded); frozen per job.
- Matcher (`domain/matching.py`): 60% skills proven by the ledger (smoothed for short postings),
  30% title, 10% place. Open jobs are re-scored on every run; ATS "before" stays frozen.
- Classifier (`domain/classify.py`): apply email and attachments only from sentences about applying.
- `services/runner.py`: one run per user at a time, jobs committed before the email.
  APScheduler every `SCOUT_INTERVAL_HOURS`; `POST /internal/run-scout` with `X-Internal-Token`.
- Digest (`services/notifier.py`): top 10 new jobs at or above the threshold, each with a signed
  link `/apply/{job_id}?t=` (single use, 72 h). Without a session it opens a session scoped to
  that job; the frontend then shows only that job.
- Frontend: Jobs (filters, Fetch now with speed lines, staggered cards) and Job detail
  (highlighted keywords, ATS chips and checks, method, attachments, Gate 1).

Before deploying: do not copy `EMAIL_REDIRECT_TO` to production (a development setting that sends
every email to one inbox), and set `INTERNAL_TOKEN` if a cron will call `/internal/run-scout`.

## 5b. Phase 3: what was built

- Tables `applications` and `claims`, `resume_lines.bulleted` (migration 0006). Re-upload the
  resume once so old lines get their bullet flags.
- CV blocks (`domain/cv.py`): every block is one resume line. Only bullets in summary,
  experience and projects may be rewritten, one line per bullet, and reordered only within their
  entry, so a result can't move between jobs. Titles, education and skills stay word for word.
- Tailor (`services/tailor.py`): Groq rewrites toward the job's keywords; rewrites that break the
  style rules (`domain/style.py`) keep the user's words. Without an LLM, bullets are reordered by
  relevance.
- Verifier (`domain/claims.py`, `services/verifier.py`): code pass (numbers, skills, degrees,
  employers) plus an LLM claim pass; routing per spec section 5 with bge-small similarity between
  each rewritten bullet and its source line (`INFER_THRESHOLD`). Blocklisted values are cut from
  the text automatically.
- Gate 2 (`services/applications.py`): Confirm (link or note, becomes `user_confirmed` evidence
  and verifies the same claim everywhere), Edit (re-routed), Remove, Mark false (blocklist).
  Approve needs zero open claims; the PDF (`integrations/pdf.py`, fpdf2) downloads only then.
- ATS after counts a keyword only if the ledger or the original resume supports it (property test).
- Frontend: Apply Workspace (`/jobs/:id/workspace`): job text, tailored CV with badges and source
  tooltips, Needs-you panel (keys Y, E, R), ATS gauge, counters, Approve and download.

## 6. Useful snippets

LLM probe (prints model, OK or error type; never the key):

```python
import asyncio, litellm
from app.config import get_settings
from app.integrations.llm import build_llm
async def main():
    for spec in build_llm(get_settings()).models:
        try:
            r = await litellm.acompletion(model=spec.model, api_key=spec.api_key,
                messages=[{"role": "user", "content": 'Reply with {"ok": true}'}],
                response_format={"type": "json_object"}, max_tokens=64, timeout=30)
            print(spec.model, "OK", r.choices[0].message.content[:40])
        except Exception as exc:
            print(spec.model, type(exc).__name__)
asyncio.run(main())
```

Check which `.env` keys are set without printing values:

```powershell
.\.venv\Scripts\python -c "from dotenv import dotenv_values as d; print(sorted(k for k,v in d('.env').items() if v))"
```

## 7. Decisions log

| Decision | Why |
|---|---|
| Product name "Power Hunt"; Powerpuff Girls characters in videos | Owner's choice; logo and palette are original |
| Neon Postgres for dev and prod; SQLite only in tests | One database behaviour everywhere; tests stay offline |
| Rate limiting with `limits` + dependency instead of slowapi | Limits and storage must come from per-app settings |
| Locations via GeoNames keys, never free text | Jobs search and matching need exact places; rejects nonsense |
| Degrees only from Education, never future tense | A mentioned PhD must not verify a fabricated one |
| Invisible PDF text ignored | Evidence must be visible to a human reader |
| Resume files not stored | Privacy; CVs are rendered from structured data |
| Server-side sessions instead of JWT | Revocable (logout, delete my data), CSRF token per session |
| GeoNames index loads in the background | App ready immediately; only lookups wait |
| One-line commit messages, no AI attribution | Owner's rule |
| Recorded jobs only when every live source fails | Old postings shown when live sources merely found nothing would mislead |
| Match score smoothed toward 50% by a prior of weight 2 | Short postings naming one skill scored 100; the ATS formula stays exactly as specified |
| fpdf2 for the CV PDF instead of WeasyPrint | Pure Python: same output in Docker, CI and on Windows; text-only one-column layout is ATS-safe |
| Gates as statuses and API calls instead of LangGraph interrupts | Each gate already persists in Postgres; LangGraph added weight without a new capability under the deadline |
| Tailor rewrites one source line per bullet, within its entry | Merging or moving lines could attach a result to the wrong job |
| Similarity between the whole rewritten bullet and its source line | A short claim ("Redis") against a long line scores low by construction |
| YAKE phrases: 2-3 words, repeated, no job titles or filler | Single YAKE words ("build", "model") were noise that penalised every CV |
| LLM chain: Groq gpt-oss-120b, gpt-oss-20b, Gemini 3.8 Flash, Gemini 3.5 Flash-Lite; strict JSON schema; reasoning effort low; 8192 tokens | gpt-oss spent the 2048-token budget on reasoning (8 facts) and gpt-oss-20b rejected json_object. Now 31 to 33 facts with 0 rejected on the demo resume, roles and employers included. Extraction prompt asks for verbatim spans because code rejects any word not on the cited line |
