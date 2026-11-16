# Next steps (handoff, updated 16 Nov 2026)

Read `CLAUDE.md` first (rules: no patches, root-cause fixes, one-line commits with no
Co-Authored-By, a PR per change, CI green before merge). Checks run in Docker because the local
Python is 3.13 and the project needs 3.12 (see "How to run checks" at the end).

## Where things stand

Power Hunt was built for NASCON '26 Agentika 2.0 (deadline 17 Oct 2026), deployed on Alternate
Clouds, and placed in the **top 10 of 80+ teams**. `main` is at the merge of PR #26. Database
migrations run to **0016**.

### Built for the event (phases 0 to 7 plus later work)

- **Evidence ledger** from resume (two-column PDFs, hidden text), GitHub and portfolio. Facts are
  tied to their resume entry (`Evidence.context`), so a degree belongs to its school and a role to
  its employer; reworded duplicates across sources merge; course issuers are never employers;
  school acronyms are aliases; titled sections (certifications, awards, publications) are read one
  per line from resumes only, never from web pages.
- **Scout** (JSearch, Remotive, Arbeitnow, recorded fallback) with liveness checks on the
  employer's own site (Greenhouse/Lever APIs, else page wording); closed jobs move to Closed.
  Posted-date filter; digest with signed Apply links.
- **Tailor and Verifier** with the four human gates; degree-school and role-employer pairs checked
  together; intent sentences ("I look forward to...") are not claims; "revise with your
  instructions"; compare with your own words.
- **CV PDFs** in three ATS-friendly layouts (Harvard serif, Jake's compact, Modern), each read back
  and checked (readable, at most 2 pages, name first, sections in order) before download.
- **Email and form apply**, Apply Pack, **Coach** with project ideas and one-page proposals,
  **Tailor tab** (pipeline, follow-ups, applied elsewhere), **Profile**, grouped ledger.
- **Email verification** at signup, sign-in alerts, Google sign-in as verified, pre-takeover fix.
- Claim-checker eval: **95.0%** fabrications caught, **2.8%** false alarms (held-out half).

### Added after the event (teammate, PRs #25 and #26)

- **Account emails** (`ee71876`, migration 0015): welcome email, password reset
  (`/forgot-password`, `/reset-password`) and change, per-account login throttling on top of the
  per-IP limit, an email log, and a digest unsubscribe link (`/unsubscribe`).
- **Daily send limit**: `EMAIL_DAILY_LIMIT` (default 20) digests and applications per account per
  24 hours.
- **Needs-you limit** (`46f5e64`): past `MAX_QUESTIONS` (3), rewritten lines go back to the user's
  own words instead of their claims passing as Inferred. A line in the user's own words is never
  restored, so its questions stay.
- **Roles and admin console** (`65a1aa1`, migration 0016): user and admin roles, `/admin` for
  accounts, system health and the audit log. Admins come from `ADMIN_EMAILS` or the operator
  command `python -m app.manage role <email> admin` (`python -m app.manage admins` lists them).
- **Data rights** (`f580a32`): download my data, daily retention clean-up
  (`RETENTION_UNVERIFIED_DAYS`, `RETENTION_EMAIL_LOG_DAYS`, `RETENTION_TOKEN_DAYS`), privacy and
  terms pages (`/privacy`, `/terms`, `SUPPORT_EMAIL`).
- **CI**: documents the Pillow CVEs that can't be fixed until fastembed allows Pillow 13.

New settings since the event: `RATE_LIMIT_LOGIN_ACCOUNT`, `RATE_LIMIT_PASSWORD`, `ADMIN_EMAILS`,
`PASSWORD_RESET_MINUTES`, `EMAIL_DAILY_LIMIT`, `UNSUBSCRIBE_LINK_DAYS`,
`RETENTION_UNVERIFIED_DAYS`, `RETENTION_EMAIL_LOG_DAYS`, `RETENTION_TOKEN_DAYS`, `SUPPORT_EMAIL`.
Run `scripts/sync_env.py` to add them to `.env`, and set them on Alternate Clouds before the next
deploy (which also applies migrations 0015 and 0016 to Neon).

## Simplified for the deadline (to do properly)

1. **Needs-you checking.** Confirm is one click and accepts the user's word with no proof, and
   questions are capped at 3 per document. Proper version: typed evidence that is checked (a
   GitHub repo through the API: commits by the user and the skill in its languages or files; a
   certificate or portfolio URL fetched through SafeFetcher: the page shows the user's name and the
   skill or course; a note labelled "your word", ranked weaker), plus an **"Add a fact"** button on
   the ledger using the same flow.
2. **UI polish.** Only a first pass was done (one-row header, shared `PageHeader`, wider pages).
   A full pass with the owner's screenshots: consistent cards, spacing and empty states.

## Discussed, not done

1. **Seniority signal** (recommended; the owner has not approved it yet): level from the ledger
   (internships only = entry) and from the posting ("Senior", "5+ years"); down-rank and label
   "Stretch" with a toggle to hide, never a hard filter.
2. **GitHub ownership.** Repos the user contributed to but doesn't own are ignored (`list_repos`
   uses `type=owner`; the Coach checks the owner), and nothing proves the typed username is the
   user's. Fix: count repos where the user authored commits; link the account with GitHub OAuth;
   delete `source=github` evidence when the username changes.
3. **Eval coverage.** Only the Verifier is measured. In priority: a live claim-pass mode (real LLM,
   cached replies), the learning-loop metric (questions per application falling job after job),
   a ledger extraction eval with gold facts, a larger prompt-injection suite, splitting cases by
   source line, Wilson intervals.
4. **Spec extras never built:** MCP server (a small local stdio server exposing `ats_score`,
   `find_claims` and `job_fit` over the existing domain functions was scoped at about 30 minutes),
   Playwright smoke test, autofill bookmarklet, demo mode for company research.

## Audit findings still open

Closed since the audit: account pre-takeover, unverified sends, per-account daily send limit,
per-account login throttling, demo account deletion.

- **Medium:** double-send race in `composer.send`: a double click or a failed commit after SMTP can
  send twice. Mark the row "sending" with a conditional UPDATE and commit before sending.
- **Medium:** the demo account can still change its profile (only deletion and sending are
  blocked).
- **Medium:** DOCX decompression bomb: sum `ZipInfo.file_size` in `sniff_resume` and cap the total
  and the compression ratio.
- **Medium:** `FORWARDED_ALLOW_IPS` isn't a setting in `app/config.py`; behind the platform proxy
  every client may share one IP bucket. No per-user limit on `PUT /application/email` and
  `PUT /answers/{i}` (both call the LLM).
- **Medium:** Apply links replace an existing session, a GET uses up the token (email link
  scanners), and `redeem` can race.
- **Low:** `compose.py` passes user text as a `re.sub` replacement (use a function); names skip
  `normalize_person_name` at signup; PDF rendering and form parsing outside the CV download run on
  the event loop; uploaded attachments keep the user's file extension; `import_posting` uses
  `scalar_one_or_none` on a query that can match two rows; the frontend repeats some server
  limits; the Google OAuth timeout, `pool_recycle`, SMTP port and sender name belong in config.

## Repository housekeeping

- `HUNT_BUILD_PROMPT.md`, `POWER_HUNT_BUILD_PROMPT.md` and `Claude outputs/` (submission PDF,
  pitch deck and proposal, which hold personal details and the demo login) are local only and must
  never be committed. `submission/` is excluded in `.git/info/exclude`.
- `.gitignore` has `assets/`, which also matches `frontend/src/assets`. Existing files there are
  tracked, but a new file added there is silently ignored; changing the rule to `/assets/` fixes it.

## How to run checks (Docker, Python 3.12)

```sh
# Mount app, tests, eval, scripts, data, fixtures, pyproject.toml and alembic.ini into the api
# image at /srv, then:
cd /srv
python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['optional-dependencies']['dev']))" > /tmp/dev.txt
pip install -q -r /tmp/dev.txt
ruff format app tests eval scripts && ruff check app tests eval scripts
mypy app eval
python -m pytest -q --timeout=120
python -m eval.run --check
```

Run it with `docker run --rm -u root -e APP_ENV=development -e DATABASE_URL=sqlite+aiosqlite:////tmp/t.db -v ...`
(not `docker compose run`, which injects `.env` and breaks the "no key configured" tests).
Frontend: `cd frontend; npm run build; npx oxlint`. Migrations on Postgres: run a throwaway
`postgres:16` container and `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`.
