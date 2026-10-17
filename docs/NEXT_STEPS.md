# Next steps (handoff, 17 Oct 2026, about 08:00 PKT)

Deadline 12:45 PKT. Read `CLAUDE.md` first (rules: no patches, root-cause fixes, one-line commits
with no Co-Authored-By, PR per change, CI green before merge). Tests and checks run in Docker
because the local Python is 3.13 and the project needs 3.12 (see "How to run checks" below).

## State right now

- `main` has PR #15 (ledger dedupe, school facts, entry links, migration 0011) and PR #16 (closed
  postings, embeddings fix, workspace fixes, migration 0012). Neon is migrated to **0012**.
- **PR #17 is open** (`feat/applications-and-profile`): Applications tab (pipeline stages,
  follow-up due, outcome buttons, "I applied on their site myself"), Profile page (account,
  sign-in methods, sources, delete my data; the demo account can't be deleted), the grouped ledger.
  612 tests pass locally; wait for CI, then merge. No migration in it.
- Teammate: must `git pull` main before running against Neon.
- Owner's `.env`: `EMAIL_REDIRECT_TO` is set (from the teammate's file), so **every email goes to
  that one address**. Empty it to receive digests; it must be empty in production.

## What changed this session (so you don't redo it)

- Facts carry their resume entry (`Evidence.context`); unique key is (user, type, value, source,
  entry). `school` fact type; degree-school and role-employer pairs are checked together
  (`claims.pair_up`, `route(paired_with=)`, `Claim.pair_*`). Confirming a paired claim stores both
  under one user entry.
- `UTCDateTime` column type: every timestamp reads back timezone-aware.
- Job liveness: `integrations/liveness.py` (Greenhouse/Lever APIs, else page wording),
  `services/liveness.py`; runs after each Scout run (before the digest) and when a job is opened.
  Settings `JOB_OPEN_RECHECK_HOURS`, `JOB_OPEN_CHECKS_PER_RUN`, `JOB_OPEN_CHECK_CONCURRENCY`.
- Jobs: posted-date filter, Closed view, "posted" vs "found" dates, user-facing status names
  (`STATUS_LABELS`; "shortlisted" shows as "Picked by you").
- Embeddings: a blank `EMBEDDING_CACHE_DIR=` used to point fastembed at `/srv`; now blank means
  unset and the image sets `FASTEMBED_CACHE_PATH=/srv/models`. Check the next Tailor note no longer
  says "simpler word-overlap mode".
- Intent sentences ("I look forward to...") are no longer claims (`claims.is_intent`).
- Workspace: "Compare with your words (N changed)", "Open their form" link, Coach panel on the job
  page ("Grow into this job").

## Later in the session (about 08:30 PKT)

- PR #17 merged; Neon at **0013** (`skill_plans.ideas_json`).
- Branch `feat/coach-project-ideas`: Coach project ideas with PDF/Markdown one-page proposals
  (done), plus ledger fixes: titled sections (certifications, awards, publications) are read
  one-per-line from resumes only, never from portfolio pages; the extraction schema rejects bad
  items one by one instead of failing the whole answer; a school's acronym is an alias of one fact
  ("Name (ACR)"), merged in `reconcile` and understood by `Ledger.build`; nav tab renamed "Tailor"
  and plans link back to their job's Tailor. Re-sync the portfolio and re-upload the resume to
  rebuild old rows.
- Tailor "revise with instructions" (done, same branch): `POST /jobs/{id}/tailor` takes
  `{"preferences": "..."}` (max 500); it reaches the model as data and the Verifier checks the result.
- Owner's remaining asks, in order: CV PDF templates with automatic format checks, UI polish pass.
  Commit after each.

## To do, in priority order

1. **Merge PR #17** after CI.
2. **Email**: signup verification link (signed token like `apply_links`) and a login alert email;
   require a verified email before any digest or send. This also closes the two high audit findings
   (account pre-takeover via Google linking, and anyone sending email from the owner's Gmail). Add a
   per-user daily send limit setting.
3. **Needs-you evidence that is actually checked**: today a confirm takes any link or note unchecked.
   Typed evidence: GitHub repo (API: commits by the user, the skill in languages/files), certificate
   or portfolio URL (SafeFetcher: page shows the user's name and the skill), note = "your word"
   (label it weaker). Same flow for a new **"Add a fact"** button on the ledger.
4. **Coach project ideas**: after the steps, 2 to 3 concrete project ideas, each with a one-page
   proposal (problem, scope, stack, milestones, how it proves the skill), downloadable (PDF or MD).
5. **Tailor "revise with instructions"** box (single instruction, not a chatbot); the output goes
   through the Verifier like any rewrite.
6. **CV PDF**: today it is a text dump (`integrations/pdf.py`). Two or three ATS-safe templates,
   auto-picked by the original's layout, plus automatic checks before download (text round-trips,
   nothing clipped, page count, fonts embedded).
7. **Seniority signal** (owner asked for an opinion first; recommended, not yet approved): level from
   the ledger (internships only = entry), level from the posting ("Senior", "5+ years"); down-rank
   and label "Stretch" with a toggle to hide, never a hard filter.
8. **GitHub ownership**: collaborator repos are ignored (`list_repos` uses `type=owner`; Coach checks
   owner). Proper fix: count repos where the user authored commits; prove the account via GitHub
   OAuth. Delete `source=github` evidence when the username changes.
9. Remaining audit items (see the audit list below), the UI polish pass (same Powerpuff palette and
   fonts, more consistent cards and spacing), then **deploy to Alternate Clouds** (reserve 1 hour:
   build, set production env without `EMAIL_REDIRECT_TO`, Google callback for the public URL, check
   `/health` and `/ready`).

Optional spec items still not built: MCP server (`/mcp`), Playwright smoke test, autofill
bookmarklet, demo mode for company research.

## Audit findings still open (from this session's review)

- High: Google sign-in links to an unverified password account with the same email (pre-takeover).
- High: any account can send email from the owner's mailbox; digests go to unverified addresses.
- Medium: double-send race in `composer.send` (mark "sending" with a conditional UPDATE first).
- Medium: demo account can still change its profile and send email (delete is now refused).
- Medium: DOCX decompression bomb (sum `ZipInfo.file_size` in `sniff_resume`).
- Medium: no rate limit on `PUT /application/email` and `PUT /answers/{i}`; limits are per IP only;
  `FORWARDED_ALLOW_IPS` isn't a setting in `app/config.py`.
- Medium: Apply link replaces an existing session, GET burns the token (mail scanners), redeem race.
- Low: `compose.py` `re.sub` with user text as replacement (use a function); names skip
  `normalize_person_name` at signup; CPU work (PDF render, form parse) on the event loop; digest
  tokens flushed but not committed before sending; uploaded file names keep the user's extension;
  `import_posting` `scalar_one_or_none` can raise; frontend repeats server limits; a few values
  belong in config (Google OAuth timeout, `pool_recycle`, SMTP port and sender name).
- Eval: measures the Verifier only; proposals in priority: live claim-pass mode, learning-loop
  metric, ledger extraction eval, injection suite, split by source line, Wilson intervals.

## How to run checks (Docker, Python 3.12)

The scripts used this session live in the session scratchpad; recreate them like this:

```sh
# check.sh: mount app, tests, eval, scripts, data, fixtures, pyproject.toml, alembic.ini into the
# api image at /srv, then:
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
