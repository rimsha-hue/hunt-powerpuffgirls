# Build Prompt: Power Hunt

Put this file in the repository root as `CLAUDE.md`, then tell Claude Code: "Read CLAUDE.md and build Phase 0, then stop for my review." Build one phase at a time and review each before continuing.

---

## 1. What we are building

**Power Hunt** is a human-in-the-loop job application agent. It finds jobs, scores the fit, tailors the CV, and prepares each application (email or web form). Before anything leaves the user's hands, it proves every claim it wrote.

**Product rule: No source, no claim.** Every fact Power Hunt writes about the user must trace back to evidence: a resume line, a GitHub repo, a certificate link, or the user's own confirmation. When Power Hunt cannot prove something, it asks the user instead of guessing.

**Event context.** NASCON '26, Agentika 2.0, theme "Human + AI: Better Together". The deadline is 17 October, 12:45 PM PKT. It must be deployed on Alternate Clouds (clouds.alternatefutures.ai) from a GitHub repo, using a single Docker container. Judging rubric: technical execution 25, usefulness 20, theme 15, creativity 15, automation depth 15, UX 10. Judges include Grayhat (an engineering agency), so security and reliability are visible scoring factors.

**Engineering principles**
1. **Prefer existing free-tier tools over custom code.** Only the domain logic is ours: the evidence ledger, claim routing, ATS scoring and the human gates.
2. **Money-like truths are deterministic code.** The LLM writes language; code decides what is true, what score something gets, and what status it has.
3. **Every external call has a timeout, a retry policy and a fallback.** A demo must never die because a free API had a bad minute.
4. **Quality bar:** typed code, tests for every domain module, no secrets in the repo, and a frontend that looks like a funded product.

## 2. Human gates (the theme, made explicit)

| Gate | What the human does | Why a human is needed |
|---|---|---|
| 1. Choose | Clicks "Apply now" on a job (from the email or the UI) | It is a career decision, not a matching problem |
| 2. Verify | Answers every claim Power Hunt could not prove | Only the user knows the truth |
| 3. Approve | Reviews and approves the final email or form answers | It goes out under the user's name |
| 4. Submit (forms only) | Presses submit on the employer's own site | Third-party forms have CAPTCHAs and terms of use; Power Hunt prepares, the human submits |

Power Hunt never sends an email, submits a form, or adds a claim to the ledger without the matching gate being passed. Every gate decision is written to the audit log.

## 3. User flows

### Flow A: Discover, then notify
1. The **Scout** runs on a schedule (default every 6 hours) and on demand ("Fetch now"). It pulls jobs for the user's saved roles and locations.
2. Jobs are deduplicated: first by the source's job id, then by a normalized hash of `company + title + location`.
3. The **Matcher** scores each new job: a match score from 0 to 100, missing skills, and the ATS score "before" (section 6).
4. The **Classifier** determines the apply method for each job: `email`, `form` or `unknown`.
5. The **Notifier** sends one email digest listing the new jobs above the user's threshold. Each job card shows the title, company, match score, ATS score, apply method, and an **Apply now** button.
6. **Apply now** is a signed link: `/apply/{job_id}?t={token}`.
   - The token is single-use, expires after 72 hours, and is bound to the user and the job.
   - Clicking it opens the Apply Workspace for that job.
   - If the user has no session, the token logs them in for that job only.

### Flow B: Apply Workspace (common to both methods)
1. The **Tailor** produces a tailored CV for this job:
   - It reorders and rewrites bullets toward the job description's keywords.
   - It never invents facts.
   - Its output is structured JSON: one record per bullet, holding the bullet text and the ids of the resume lines it relies on.
2. The **Verifier** extracts every claim from the tailored CV and routes it (section 5) as Verified, Inferred or Needs you.
3. The UI shows:
   - The original and tailored CV side by side.
   - A badge on every claim; hovering a badge shows its source.
   - The ATS score before and after.
   - A "Needs you" panel listing the unverified claims.
4. For each unverified claim, the user chooses one of four options:
   - **Confirm** with evidence (a link, a file, or a short note). The claim enters the ledger with `confirmed_by=user`.
   - **Edit** the wording.
   - **Remove** the claim.
   - **Mark false.** The claim is removed and added to a blocklist for this user, so it is never suggested again.
5. **Skill gaps:** for each required skill the ledger cannot support, the **Coach** proposes a growth plan instead of a claim (section 7).
6. The user approves the tailored CV. Power Hunt renders a PDF of it.

### Flow C: Apply by email
1. The **Composer** drafts the application email:
   - A subject line in the format the job post asks for, if it specifies one.
   - A short, humanized body (section 8).
2. **Attachment detection:** the Composer reads the job description for required items (CV, cover letter, portfolio, transcript, references, work samples) and builds a checklist.
   - Power Hunt attaches what it has: the tailored CV PDF, a cover letter PDF if required, and portfolio and GitHub links.
   - Anything missing appears as a "You need to add" item with an upload control.
   - The send button stays disabled until every required item is present or the user explicitly waives it.
3. The email body and cover letter pass through the same Verifier. Claims in them get badges too.
4. The user reviews everything on one screen: To, Subject, Body, the attachments, and the claim badges. The user can edit any of it.
5. On **Approve and send**, Power Hunt sends the email from the user's own mailbox. It stores the message id, sets the status to `Applied`, and sets `follow_up_due = applied_on + 7 days`.

### Flow D: Apply by form or website
1. **Question discovery**, tried in this order:
   1. **Greenhouse:** if the apply URL is on `boards.greenhouse.io` or `job-boards.greenhouse.io`, call `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true`. This needs no key and returns the labels, required flags and field types.
   2. **Lever:** if the apply URL is on `jobs.lever.co`, read the public postings API and the apply page.
   3. **Generic:** fetch the apply page through the SSRF-safe fetcher (section 10) and extract `<label>`/`<input>`/`<textarea>`/`<select>` pairs with BeautifulSoup.
   4. **Fallback:** the user pastes the questions into a text box.
2. **Question typing.** Each question is classified as one of:
   - `factual_profile`: name, email, phone, LinkedIn, location, work authorization, notice period, salary expectation.
   - `factual_ledger`: years of experience in X, degree, GPA.
   - `descriptive`: why this company, why this role, tell us about a challenge.
   - `analytical`: a case, an estimate, or a "how would you approach" question.
   - `file`: CV, cover letter, portfolio.
3. **Answering**, by question type:
   - **Factual:** answered from the profile and the ledger by code. If a value is missing, Power Hunt asks the user once and stores the answer in the profile, so it is never asked again.
   - **Descriptive:** the **Researcher** gathers company facts (section 9). The Composer writes a brief, humanized answer that connects the company facts to the user's ledger.
     - Every company fact must cite a fetched source URL.
     - Every user fact goes through the Verifier.
   - **Analytical:** a structured answer that states assumptions, gives the approach in two to four steps, then the result, then one risk or caveat. Numbers come only from the question, the company sources, or the user's ledger.
4. The user reviews every answer, with badges and sources, and edits or approves each one.
5. **Apply Pack:** a page listing every field with its final answer and a copy button, plus the file downloads. It has an **Open application form** button that opens the employer's page in a new tab.
6. **Autofill (stretch):** a per-application bookmarklet that embeds the approved answers and fills matching fields on the employer's form by label similarity. It never clicks submit.
7. After submitting, the user clicks **I submitted it**. Power Hunt sets the status to `Applied` and the follow-up date.

## 4. Evidence ledger

Every fact about the user is stored once, with its source.

```
evidence(
  id, user_id,
  claim_text,            -- e.g. "Python", "reduced latency by 30%", "NUST", "BS Data Science"
  type,                  -- skill | metric | employer | degree | certification | project | role | personal
  normalized_value,      -- lowercased canonical form; numbers parsed with unit
  source,                -- resume | github | certificate | portfolio | user_confirmed
  source_ref,            -- resume_line_id | repo URL | certificate URL | note id
  confirmed_by,          -- system | user
  confidence,            -- 1.0 for exact sources; stored score for inferred links
  created_at
)
resume_lines(id, user_id, resume_version, section, line_no, text)
claim_blocklist(id, user_id, normalized_value, reason, created_at)
```

**Ingestion**
1. **Resume (PDF or DOCX).** Extract the text with `pdfplumber` (or `python-docx`) and split it into numbered lines, stored in `resume_lines`. The LLM extracts structured facts into a JSON schema, and each fact must carry the `line_id` it came from.
   - Code checks that the line exists and that the fact's key tokens appear in it.
   - Facts that fail this check are discarded.
2. **GitHub.** Read the user's public repos through the GitHub REST API: name, description, languages, topics, README text and pushed date. Repo languages and topics become `skill` evidence with `source=github`.
3. **Portfolio URL and certificates.** Fetch them through the SSRF-safe fetcher and extract any title, issuer and date.
4. **User confirmations** from the Verify gate.

**The ledger page** lists every fact, its source and when it was added. The user can delete any entry.

## 5. Claim routing

**Input:** a generated text (CV bullets, email body, cover letter, form answers).

**Claim extraction** combines two passes:
- **LLM pass:** the LLM returns a list of atomic claims, each with a cited `line_id` (or `null`) and a span offset into the text.
- **Code pass:** a deterministic scan finds every number or percentage, degree term, employer name and skill term in the text, so the LLM cannot hide a claim by not listing it.

**Routing per claim:**
1. **Verified:** the normalized value exactly matches a ledger entry of the same type.
   - Numbers must match the value and the unit; "30%" is not "35%".
   - Degrees and employers are dictionary matches.
   - Skills match exactly or through a curated synonym map (`skills_taxonomy.json`, e.g. "JS" to "JavaScript").
2. **Inferred:** not an exact match, but the LLM cited a `line_id` and all of these hold:
   - The cited line exists.
   - The cosine similarity between the claim and the cited line, from the embedding model, is at least `INFER_THRESHOLD` (start at 0.78, tune on the eval set).
   - The claim adds no new number, degree or employer absent from that line.
3. **Needs you:** everything else. Any claim matching the blocklist is removed automatically and logged.

**Output stored:** `claims(id, application_id, text, span_start, span_end, bucket, evidence_id, cited_line_id, similarity, user_decision, decided_at)`.

**Learning loop:** a user confirmation creates ledger evidence, so the same claim is Verified on the next application. The dashboard shows "Questions asked this application: N", and N should fall from job to job.

## 6. ATS score, before and after

The score is deterministic and computed by the same function for both versions.

1. **Keyword set per job, frozen when the job is first scored**, so before and after use the same list:
   - Taxonomy matches from `skills_taxonomy.json` (curated, about 500 skills with synonyms).
   - Top key phrases from YAKE.
   - Section weighting: terms under "Requirements" or "Must have" get weight 2, terms under "Nice to have" get weight 1.
2. **Coverage** = the sum of weights of keywords found in the CV (synonym-aware, whole-word matching) divided by the total weight.
3. **Format checks**, each a small penalty:
   - Standard section headings present.
   - Contact info present.
   - No tables or images in the generated PDF.
   - Length within two pages.
4. **Score** = `round(100 * (0.85 * coverage + 0.15 * format))`.
5. **UI:** a before-to-after gauge, plus the matched and missing keyword chips.
   - Missing keywords the user genuinely lacks link to the Coach.
   - Power Hunt must never raise the ATS score by adding a keyword the ledger cannot support. A test enforces this rule.

## 7. Growth plans: skill gaps become a plan, not a lie

1. **Trigger:** a required job skill with no ledger support.
2. **The Coach generates one project:**
   - A specific title.
   - Why it proves the skill.
   - A checklist of four to six concrete steps.
   - An estimated time.
   - The acceptance criteria Power Hunt will check.
3. **The user links a GitHub repo when done.** Power Hunt then verifies, through the GitHub API, that:
   - The repo is owned by the user's GitHub username.
   - Its `created_at` or first commit is after the plan's date.
   - Its languages or README contain the skill term or a synonym.
   - It has at least N commits (default 3).
4. **If all checks pass**, the skill enters the ledger with `source=github` and `source_ref=<repo URL>`, and its Needs-you badges become Verified on future applications.
5. **The growth plans page** shows each plan as Open, In progress or Verified, with its checklist.

## 8. Writing quality: humanized, brief, honest

**Style rules**, applied in the prompts and checked by code:
- Emails: 120 to 180 words.
- Form answers: 60 to 150 words unless the question gives a limit, which always wins.
- Vary sentence length, and use plain words and the user's own phrasing from the resume where possible.
- **Banned phrase list, checked by code** (rewrite once if hit, then flag for the user): "I am writing to express", "passionate", "leverage", "synergy", "delve", "thrilled", "dynamic", "fast-paced", "esteemed", "I believe I would be a great fit", plus em dashes and en dashes.
- Exactly one specific company detail per descriptive answer, cited from the Researcher.
- **Optional "Write like me":** the user pastes one past email or answer, and Power Hunt extracts tone markers (greeting style, sentence length, formality) and follows them.

## 9. Company research

- **Primary:** Tavily search (free tier, 1,000 credits a month) for `"{company}" about mission products news`, keeping the top 5 results. Fetch the company homepage and About page with Jina Reader (`https://r.jina.ai/{url}`) or the SSRF-safe fetcher.
- **Output:** a list of company facts, each `{fact, source_url, retrieved_at}`. Cache it per company for 7 days.
- **Rule:** a company fact used in an answer must be supported by text from a fetched source. The UI shows the source link on hover, just like ledger badges.
- **Fallback:** if search fails, use only the job description and the homepage. If both fail, ask the user for one sentence about why they want this company.

## 10. Tech stack (free tiers; confirm current limits at signup)

| Need | Tool | Notes |
|---|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic | One service |
| Agent orchestration | LangGraph with interrupts at each human gate | Use a Postgres checkpointer so a paused run survives restarts |
| LLM | Groq (pick a model marked Production in the console, never Preview) through LiteLLM | LiteLLM gives a fallback model and a second provider (e.g. Gemini free tier) behind one interface |
| Embeddings | `fastembed` with `BAAI/bge-small-en-v1.5` (ONNX, CPU, no torch) | Load once at startup |
| Jobs | JSearch on RapidAPI (primary), Remotive and Arbeitnow public APIs (no key, fallback) | Normalize every source to one schema |
| Company research | Tavily, Jina Reader | Cached |
| Database | Neon Postgres (free tier) in production, SQLite for local and tests | Alternate Clouds storage persistence is unconfirmed, so keep state in Neon |
| Email sending | Gmail SMTP with an App Password, for both notifications and applications, behind an `EmailProvider` interface | Applications must come from the user's own address. Resend as an optional notifications provider |
| Scheduler | APScheduler, in process | Plus a protected `POST /internal/run-scout` endpoint for a manual trigger |
| PDF | WeasyPrint rendering an HTML CV template | Install its system libraries in the Dockerfile |
| Keywords | YAKE plus the curated taxonomy | Deterministic |
| HTML parsing | BeautifulSoup and `httpx` | Through the SSRF-safe fetcher only |
| Frontend | Vite, React, TypeScript, Tailwind, shadcn/ui, TanStack Query, Framer Motion (light) | Built in a multi-stage Docker build and served by FastAPI as static files |
| MCP (should have) | FastMCP mounted at `/mcp` | Tools: `score_job`, `tailor_for_job`, `verify_claims`, `ats_score` |

## 11. Agents

| Agent | Trigger | Does | Writes |
|---|---|---|---|
| Scout | Schedule, "Fetch now" | Pulls and dedupes jobs | `jobs` |
| Matcher | After Scout | Match score, missing skills, ATS before | `jobs` |
| Classifier | After Matcher | Decides the apply method; extracts the apply email or URL and required attachments | `jobs` |
| Notifier | After a batch | Digest email with signed Apply links | `notifications` |
| Tailor | Gate 1 | Tailored CV JSON with cited lines | `applications` |
| Verifier | After any generation | Routes claims | `claims` |
| Coach | Unsupported skills | Growth plans; verifies repos | `skill_plans`, `evidence` |
| Researcher | Form or email descriptive content | Company facts with sources | `company_facts` |
| Composer | After verification | Email, cover letter, form answers | `applications` |
| Courier | Gate 3 | Sends the email or builds the Apply Pack | `applications`, `audit` |

The LangGraph graph pauses (`interrupt`) at gates 2 and 3 and resumes when the UI posts the decision. Each node is a plain function with typed input and output, so it can be unit tested without the graph.

## 12. Data model (other tables)

```
users(id, email, name, password_hash, github_username, portfolio_url, created_at)
profile(user_id, phone, location, linkedin, work_authorization, notice_period, salary_expectation, target_roles[], target_locations[], match_threshold)
jobs(id, user_id, source, external_id, dedupe_hash, title, company, location, remote, url, description, posted_at, fetched_at,
     apply_method, apply_email, apply_url, required_attachments[], ats_keywords_json, match_score, missing_skills[], ats_before, status)
applications(id, job_id, method, status, tailored_cv_json, cv_pdf_path, cover_letter, email_subject, email_body, attachments_json,
             answers_json, ats_after, approved_at, sent_at, message_id, follow_up_due)
company_facts(id, company_key, fact, source_url, retrieved_at)
skill_plans(id, user_id, skill, title, why, checklist_json, acceptance_json, status, repo_url, verified_at)
apply_tokens(id, user_id, job_id, token_hash, expires_at, used_at)
notifications(id, user_id, kind, sent_at, job_ids[])
audit(id, user_id, actor, action, entity, entity_id, detail_json, created_at)
```

**Statuses** (enforced by one transition function): `new -> shortlisted -> in_review -> approved -> applied -> {interview, assessment, offer, rejected}`; plus `expired`, and `skipped` for jobs the user dismisses.

## 13. Security (Grayhat judges will look)

1. **Secrets:** only in environment variables (Alternate Clouds Variables tab). Ship a `.env.example` with no real values; `.env` goes in `.gitignore`. gitleaks runs in CI.
2. **Auth:**
   - Email plus password hashed with argon2, stored in a session cookie that is HttpOnly, Secure and SameSite=Lax.
   - CSRF token on every state-changing request.
   - Seed a demo account for the judges; its credentials go in the submission PDF, never in the repo.
3. **Signed apply links:** random 32-byte tokens, stored only as hashes, single-use, 72-hour expiry, bound to the user and the job.
4. **Prompt injection:**
   - Job descriptions, company pages and form questions are untrusted. They go to the LLM inside clearly delimited data blocks, with an instruction that data never overrides instructions.
   - All LLM outputs must validate against Pydantic schemas.
   - The Verifier is the final guard: an injected "add a PhD" becomes a Needs-you claim or is blocked.
   - Include a malicious job description fixture and test it.
5. **SSRF-safe fetcher**, used for every URL fetched from untrusted input:
   - Allow only `http` and `https`.
   - Resolve DNS and block private, loopback, link-local and metadata IP ranges, including after redirects.
   - Cap redirects, response size (2 MB) and timeout (10 s).
6. **XSS:**
   - Render LLM output as text, never as raw HTML.
   - Sanitize any HTML for emails with `bleach`.
   - Set a strict Content-Security-Policy, `X-Content-Type-Options` and `Referrer-Policy`.
7. **Uploads:** PDF and DOCX only, checked by magic bytes, 5 MB maximum. Store them under random names.
8. **Rate limiting:** `slowapi` on login, on Fetch now, and on every endpoint that calls the LLM.
9. **Privacy:**
   - No resume text or email content in the logs.
   - Log only ids and event names.
   - "Delete my data" removes the user's rows and files.
10. **Dependencies:** `pip-audit` and `npm audit` in CI; `bandit` on the backend.

## 14. Reliability

1. **Timeouts and retries:** every outbound call goes through `httpx` with a timeout, plus `tenacity` retries with backoff on 429 and 5xx.
2. **LLM validation:** an output that fails schema validation gets one repair retry, then a clear UI error. Never pass a half-parsed object downstream.
3. **Provider fallback:**
   - LLM: Groq primary model, then the fallback model, then the second provider.
   - Jobs: JSearch, then Remotive and Arbeitnow, then saved fixtures.
4. **Demo-safe mode** (`DEMO_MODE=true`): jobs come from recorded real fixtures and company research from the cache, so a live demo never depends on a free quota.
5. **Idempotency:** the Scout dedupes on insert; sending checks `sent_at` first; apply tokens are single-use.
6. **Health:** `GET /health` reports the database, LLM provider reachability and scheduler state. `GET /ready` is for the platform.

## 15. Testing

1. **Unit tests (pytest)** for every domain module:
   - ATS scorer, claim router, number and unit normalizer, taxonomy synonyms.
   - Banned phrase checker, status transitions, token signing.
   - SSRF guard (private IPs, redirects to private IPs), attachment detection, question typing.
2. **The claim checker evaluation, a scored deliverable:**
   - `eval/claims.jsonl` with at least 60 true claims (some verbatim, some paraphrased with cited lines) and at least 25 planted fabrications (inflated numbers, fake degrees, fake employers, unheld skills, injected claims).
   - `python -m eval.run` prints the confusion matrix, the fabrication catch rate, and the false alarm rate on true claims.
   - Tune `INFER_THRESHOLD` on a split, report on the held-out part, and put the numbers in the README.
3. **Property test:** the ATS "after" score can only rise through keywords present in the ledger.
4. **Integration tests:** mock every external API with `respx`, then run Flow A to D end to end against SQLite.
5. **End-to-end (Playwright) smoke test:** log in as the demo user, open a job, answer one Needs-you claim, approve, and see the status change to Applied (email sending mocked).
6. **CI (GitHub Actions):** ruff, mypy on the domain modules, pytest, the eval run (fail if the catch rate drops below the recorded baseline), pip-audit, bandit, gitleaks, frontend type check and build.

## 16. Frontend: a convincing design

**Direction:** an original comic-book superhero style. The user is the hero; the agents are a sidekick squad. It is bold and playful on the surface, and precise underneath: every number and badge stays clean and readable. The style must be our own. Do not use any existing cartoon or comic franchise's characters, logos, names, color schemes or signature visuals.

- **Palette:**
  - Ink `#16121F` and cream `#FFF6E5` as the base.
  - Electric coral `#FF4D6D` as the hero accent.
  - Signal yellow `#FFD23F` for highlights and action bursts.
  - Deep violet `#5A2A9C` for the second surface.
  - Text on coral or yellow is always ink. Check every pair for 4.5:1 contrast.
- **Typography:**
  - Bangers (Google Fonts) only for big headlines and action words, never for body text or numbers.
  - Public Sans for body text.
  - JetBrains Mono for scores and labels.
- **Comic visual language:**
  - Thick 3 px ink outlines on cards, with a hard offset shadow (`box-shadow: 6px 6px 0 #16121F`).
  - Halftone dot textures (CSS radial gradients) on hero areas only.
  - Speech-bubble toasts and tooltips.
  - Panel-style layouts where each workflow stage reads like a comic panel.
- **The squad:** each agent gets an original, simple icon-badge (a magnifier for Scout, a target for Matcher, a scissors-and-pen for Tailor, a shield for Verifier, a sprout for Coach, an envelope for Courier). Use geometric icons from Lucide; no characters or faces.
- **Badges:** Verified (green `#1F9D55`, check icon, the word "Verified"), Inferred (blue `#2B6CB0`, link icon, "Inferred"), Needs you (coral, question icon, "Needs you"). Never use color alone.

**Animations (Framer Motion; every one must respect `prefers-reduced-motion`):**
1. **Fetch now:** speed lines sweep across the Jobs header, and new job cards slide in panel by panel with a stagger of 60 ms.
2. **Claim verified:** the badge lands with a stamp effect (scale from 1.4 to 1, slight rotate, 180 ms).
3. **Fabrication caught:** a yellow starburst with a short action word ("BLOCKED!") pops over the claim for 600 ms, then settles into a Needs-you badge.
4. **ATS gauge:** the needle sweeps from the before score to the after score with a spring, and the number counts up.
5. **Application sent:** a short envelope launch with a speed trail, then the job card flips to Applied.
6. **Growth plan verified:** a progress bar fills, then a "LEVEL UP" banner drops in for one second.
7. **Page transitions:** a quick panel wipe (200 ms), never longer.

**Rules for the animations:**
- They decorate outcomes; they never delay them. Data appears immediately, and the animation plays on top.
- No animation runs longer than 800 ms, and none loops except loading states.
- The demo account has a "Calm mode" toggle that turns them all off, for judges who prefer it.

**Screens, in build order:**
1. **Onboarding:** upload a resume, add a GitHub username and portfolio, then set target roles, locations and the match threshold. Show the ledger being built live, fact by fact, with sources.
2. **Jobs:** cards or a table with match score, ATS before, apply method badge (Email or Form), posted date and an Apply now button. Filters and a "Fetch now" button.
3. **Apply Workspace (the hero screen):**
   - Left: the job description, with the matched keywords highlighted.
   - Center: the tailored CV, with badges and source tooltips.
   - Right: the Needs-you panel, the ATS before-to-after gauge, and the growth plan cards.
   - Bottom: the email composer (Flow C) or the form answers (Flow D).
4. **Apply Pack:** form answers with copy buttons, sources and an Open application form button.
5. **Evidence ledger:** a searchable table of facts and sources, with delete.
6. **Growth plans:** checklists, a repo link field and a Verify button.
7. **Activity:** the audit timeline, plus a counter: "claims checked automatically", "claims sent to you", "unverified claims shipped: 0".

**Details that sell it:**
- Skeleton loaders and optimistic updates.
- Keyboard shortcuts on the Needs-you panel (Y confirm, E edit, R remove).
- Responsive down to 390 px width.
- An empty state with a "Try with sample data" button that loads the demo resume and fixtures.

## 17. Deployment on Alternate Clouds

1. **Multi-stage Dockerfile:**
   - A Node stage builds the frontend.
   - The Python stage installs WeasyPrint's system libraries and the Python dependencies, downloads the embedding model during the build (so startup is fast), copies the built frontend, and runs `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`.
2. **The port must match the PORT set on the Source tab**; a mismatch causes a Cloudflare 502.
3. **Set every environment variable on the Variables tab** before building. A missing key crashes at startup.
4. **Run migrations at startup** (`alembic upgrade head`), then seed the demo user if absent.
5. **Order on every change:** push to GitHub, Rebuild, wait for success, Deploy.
6. **Read the Logs tab first when anything fails.**

**Environment variables (names only):** `DATABASE_URL`, `SECRET_KEY`, `GROQ_API_KEY`, `LLM_PRIMARY_MODEL`, `LLM_FALLBACK_MODEL`, `GEMINI_API_KEY` (optional), `RAPIDAPI_KEY`, `TAVILY_API_KEY`, `GITHUB_TOKEN` (optional, raises rate limits), `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_APP_PASSWORD`, `APP_BASE_URL`, `DEMO_MODE`, `INFER_THRESHOLD`, `SCOUT_INTERVAL_HOURS`, `PORT`.

## 18. Repository layout

```
power-hunt/
  app/
    main.py              # FastAPI app, static frontend, routers, security headers
    config.py            # pydantic-settings
    db/                  # models, session, alembic
    domain/              # pure logic, fully unit tested
      ledger.py  claims.py  ats.py  normalize.py  taxonomy.py  style.py  status.py  attachments.py  questions.py
    agents/              # scout, matcher, classifier, notifier, tailor, verifier, coach, researcher, composer, courier
    graph/               # LangGraph wiring and interrupts
    integrations/        # llm.py jobs/ research.py github.py email.py fetch.py (SSRF-safe) pdf.py
    api/                 # routers: auth, jobs, apply, ledger, plans, activity, internal
    mcp/                 # FastMCP server
    templates/           # CV HTML, email HTML
  data/skills_taxonomy.json
  eval/claims.jsonl  eval/run.py
  fixtures/              # recorded jobs, a demo resume, a malicious job description
  frontend/              # Vite React app
  tests/
  Dockerfile  .env.example  README.md  .github/workflows/ci.yml
```

## 19. Build phases (stop for review after each)

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | Skeleton FastAPI app serving a placeholder React page, the Dockerfile, CI, `/health`. **Deploy it to Alternate Clouds now.** | The public URL shows the page |
| 1 | Auth, demo seed, resume upload and parsing, the evidence ledger with GitHub ingestion, the Onboarding and Ledger screens | Uploading the demo resume produces sourced facts |
| 2 | Scout with fallbacks, dedupe, Matcher, the ATS scorer, the Classifier, the Jobs screen, the digest email with signed Apply links | The email arrives, and clicking Apply opens the right job |
| 3 | Tailor, Verifier with three-way routing, the Needs-you panel, the learning loop, ATS after, the CV PDF | A planted fabrication is caught, and a confirmed claim is Verified next time |
| 4 | Flow C: attachment detection, humanized email, review screen, send through SMTP | A real email with attachments reaches a test inbox after approval |
| 5 | Flow D: Greenhouse question fetch, generic form parsing, question typing, Researcher, answers with sources, Apply Pack | A real Greenhouse job produces sourced answers |
| 6 | Coach and growth plans with GitHub verification; the Activity screen and counters | Linking a qualifying repo turns a gap into Verified |
| 7 | Eval run and its numbers in the README, the malicious job description test, demo mode, the MCP server, polish | CI is green and the eval baseline is recorded |

**Cut line if time runs short:** finish phases 0 to 4 and the eval (phase 7) first. Phase 5 can fall back to "paste the questions," and phase 6 can show plans without repo verification.

## 20. Definition of done

- [ ] Deployed on Alternate Clouds; the public URL works with the demo account.
- [ ] A digest email arrives after a fetch, and its Apply link opens the right workspace.
- [ ] Every claim in a tailored CV, email or form answer carries a Verified, Inferred or Needs-you badge with a visible source.
- [ ] No email is sent and no Apply Pack is finalized while a Needs-you claim is unresolved.
- [ ] An email application is sent with the tailored CV and the required attachments after approval.
- [ ] A form application produces sourced, humanized answers for descriptive and analytical questions.
- [ ] The ATS score is shown before and after, and it never rises from unsupported keywords.
- [ ] A skill gap produces a growth plan, and a linked repo can verify it.
- [ ] The eval prints its catch rate and false alarm rate, and the README reports them.
- [ ] The malicious job description test passes.
- [ ] CI is green: tests, eval baseline, pip-audit, bandit, gitleaks.
- [ ] No secrets appear in the repo, screenshots or video.
