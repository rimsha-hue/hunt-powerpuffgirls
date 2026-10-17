// Typed fetch wrapper: same-origin cookies, CSRF header on every state-changing request,
// and errors carrying the server's user-safe `detail` message.

let csrfToken: string | null = null

export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

export class ApiError extends Error {
  readonly status: number
  /** Per-field messages from a 422, keyed by field name (e.g. "target_locations"). */
  readonly fields: Record<string, string>
  constructor(status: number, message: string, fields: Record<string, string> = {}) {
    super(message)
    this.status = status
    this.fields = fields
  }
}

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

const clean = (msg: string): string => msg.replace(/^Value error, /, '')

async function toApiError(res: Response): Promise<ApiError> {
  const fields: Record<string, string> = {}
  let message: string | null = null
  try {
    const body: unknown = await res.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail
      if (typeof detail === 'string') message = detail
      if (Array.isArray(detail)) {
        for (const item of detail as Array<{ msg?: unknown; loc?: unknown }>) {
          if (typeof item.msg !== 'string') continue
          const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : undefined
          if (typeof field === 'string') fields[field] ??= clean(item.msg)
          message ??= clean(item.msg)
        }
      }
    }
  } catch {
    // Non-JSON error body: fall through to the generic message.
  }
  if (res.status === 429) message ??= 'Too many attempts. Wait a minute and try again.'
  if (res.status === 413) message ??= 'That is too large to send.'
  return new ApiError(res.status, message ?? `Something went wrong (${res.status}).`, fields)
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (UNSAFE.has(method) && csrfToken) headers.set('X-CSRF-Token', csrfToken)

  const res = await fetch(path, { ...init, method, headers, credentials: 'same-origin' })
  if (!res.ok) throw await toApiError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const json = (body: unknown): string => JSON.stringify(body)

// ---- Contracts (mirror app/api/schemas.py) ----

export interface User {
  id: number
  email: string
  name: string
  github_username: string | null
  portfolio_url: string | null
}

export interface Me {
  user: User
  csrf_token: string
  onboarded: boolean
  calm_mode: boolean
  is_demo: boolean
  /** Set when this session came from a signed Apply link: it opens that one job only. */
  scope_job_id: number | null
}

/** A place the server recognised; `key` is what gets stored and sent back. */
export interface Place {
  key: string
  label: string
  kind: 'remote' | 'country' | 'city'
}

export interface Salary {
  amount: number
  currency: string
  period: 'month' | 'year'
}

export interface Profile {
  name: string
  email: string
  github_username: string | null
  portfolio_url: string | null
  phone: string | null
  location: Place | null
  linkedin: string | null
  work_authorization: string | null
  notice_period: string | null
  salary: Salary | null
  target_roles: string[]
  target_locations: Place[]
  match_threshold: number
  calm_mode: boolean
  resume_version: number
}

export type FactType =
  | 'skill'
  | 'metric'
  | 'employer'
  | 'degree'
  | 'school'
  | 'certification'
  | 'project'
  | 'role'
  | 'personal'
  | 'publication'
  | 'award'
  | 'other'

export type FactSource = 'resume' | 'github' | 'certificate' | 'portfolio' | 'user_confirmed'

export interface Evidence {
  id: number
  claim_text: string
  type: FactType
  normalized_value: string
  source: FactSource
  source_ref: string
  source_label: string
  source_detail: string | null
  source_url: string | null
  context: string | null
  confirmed_by: 'system' | 'user'
  confidence: number
  created_at: string
}

export interface ResumeIngestion {
  resume_version: number
  line_count: number
  facts: Evidence[]
  rejected_by_code: number
  blocked: number
  llm_used: boolean
  llm_note: string | null
}

export interface GitHubIngestion {
  username: string
  repos_scanned: number
  facts: Evidence[]
}

export interface PortfolioIngestion {
  url: string
  method: 'direct' | 'rendered'
  line_count: number
  facts: Evidence[]
  rejected_by_code: number
  llm_note: string | null
}

/** One fact as the user thinks of it: the same claim, possibly proven by several sources. */
export interface FactGroup {
  key: string
  type: FactType
  claim_text: string
  context: string | null
  confirmed_by_user: boolean
  created_at: string
  sources: Evidence[]
}

export function groupFacts(rows: Evidence[]): FactGroup[] {
  const groups = new Map<string, FactGroup>()
  for (const row of rows) {
    const key = `${row.type}|${row.normalized_value}`
    const group = groups.get(key)
    if (group) {
      group.sources.push(row)
      group.context ??= row.context
      group.confirmed_by_user ||= row.confirmed_by === 'user'
    } else {
      groups.set(key, {
        key,
        type: row.type,
        claim_text: row.claim_text,
        context: row.context,
        confirmed_by_user: row.confirmed_by === 'user',
        created_at: row.created_at,
        sources: [row],
      })
    }
  }
  return [...groups.values()]
}

export type ApplyMethod = 'email' | 'form' | 'unknown'

export type JobStatus =
  | 'new'
  | 'shortlisted'
  | 'in_review'
  | 'approved'
  | 'applied'
  | 'interview'
  | 'assessment'
  | 'offer'
  | 'rejected'
  | 'expired'
  | 'skipped'

/** Status names as the user reads them. The stored names follow the spec's gates; "shortlisted"
 * there means the user picked the job, which on screen could read as a recruiter's decision. */
export const STATUS_LABELS: Record<JobStatus, string> = {
  new: 'New',
  shortlisted: 'Picked by you',
  in_review: "You're reviewing",
  approved: 'Ready to send',
  applied: 'Applied',
  interview: 'Interview',
  assessment: 'Assessment',
  offer: 'Offer',
  rejected: 'Rejected',
  expired: 'Closed',
  skipped: 'Skipped',
}

export interface Job {
  id: number
  source: string
  title: string
  company: string
  location: string
  remote: boolean
  url: string
  posted_at: string | null
  fetched_at: string
  apply_method: ApplyMethod
  apply_email: string | null
  apply_url: string | null
  required_attachments: string[]
  match_score: number
  ats_before: number
  matched_skills: string[]
  missing_skills: string[]
  status: JobStatus
  status_changed_at: string
}

export interface JobKeyword {
  term: string
  kind: 'skill' | 'phrase'
  weight: number
  found: boolean
}

export interface JobDetail extends Job {
  description: string
  keywords: JobKeyword[]
  ats_checks: Record<string, boolean>
  ats_coverage: number
}

export interface ScoutRun {
  queries: number
  fetched: number
  relevant: number
  duplicates: number
  new_jobs: number
  rescored: number
  used_recorded: boolean
  sources: Array<{ name: string; fetched: number; error: string | null }>
  digest_sent: number
  digest_note: string | null
}

export type ClaimBucket = 'verified' | 'inferred' | 'needs_you' | 'blocked' | 'removed'

export interface CvBlock {
  kind: 'heading' | 'title' | 'bullet' | 'text'
  section: string
  text: string
  original: string
  line_no: number
  entry: number
  rewritten: boolean
}

/** cv, email, cover_letter, or a written form answer: "q:<index>". */
export type ClaimDocument = 'cv' | 'email' | 'cover_letter' | `q:${number}`

export interface Claim {
  id: number
  document: ClaimDocument
  block_index: number
  text: string
  type: FactType
  span_start: number
  span_end: number
  bucket: ClaimBucket
  origin: 'code' | 'llm'
  similarity: number | null
  user_decision: 'confirmed' | 'edited' | 'removed' | 'false' | null
  source: { label: string; detail: string | null; url: string | null } | null
  /** The role or degree an organisation claim is stated with; the pair is what gets checked. */
  paired_with: string | null
}

export interface AttachmentItem {
  kind: string
  label: string
  status: 'attached' | 'link' | 'uploaded' | 'missing' | 'waived'
  detail: string | null
}

export interface EmailState {
  recipient: string | null
  subject: string | null
  blocks: CvBlock[]
  signature: string
  cover_letter: CvBlock[]
  attachments: AttachmentItem[]
  note: string | null
  blockers: string[]
  sent_at: string | null
  follow_up_due: string | null
}

export type QuestionType =
  | 'factual_profile'
  | 'factual_ledger'
  | 'descriptive'
  | 'analytical'
  | 'file'
  | 'consent'
  | 'voluntary'

export interface FormAnswer {
  index: number
  label: string
  description: string
  required: boolean
  field_type: 'text' | 'textarea' | 'select' | 'multiselect' | 'file' | 'checkbox'
  options: string[]
  name: string
  type: QuestionType
  answer: string
  status: 'needs_you' | 'ready' | 'approved'
  source: { label: string; detail: string | null; url: string | null } | null
  company_fact: { fact: string; url: string } | null
  note: string | null
  blocks: CvBlock[]
}

export interface FormState {
  source: 'greenhouse' | 'lever' | 'page' | 'pasted' | null
  url: string | null
  answers: FormAnswer[]
  blockers: string[]
  submitted_at: string | null
  follow_up_due: string | null
}

export interface Application {
  id: number
  job_id: number
  status: 'draft' | 'cv_approved' | 'sent' | 'submitted'
  method: ApplyMethod
  note: string | null
  blocks: CvBlock[]
  claims: Claim[]
  ats: { before: number; after: number; matched: string[]; missing: string[]; checks: Record<string, boolean> }
  questions_asked: number
  needs_you: number
  checked_automatically: number
  blocked: number
  cv_approved_at: string | null
  updated_at: string
  email: EmailState | null
  form: FormState | null
}

export type ClaimAction =
  | { action: 'confirm'; url?: string; note?: string }
  | { action: 'edit'; text: string }
  | { action: 'remove' }
  | { action: 'false' }

// ---- Coach and growth plans (Phase 6) ----

export type PlanStatus = 'open' | 'in_progress' | 'verified'

export interface PlanStep {
  text: string
  done: boolean
}

export interface PlanCheck {
  name: 'owner' | 'fresh' | 'skill' | 'commits'
  passed: boolean
  detail: string
}

export interface Plan {
  id: number
  skill: string
  title: string
  why: string
  steps: PlanStep[]
  hours: number
  status: PlanStatus
  written_by: 'llm' | 'template'
  acceptance: string[]
  /** Other skills the plan touches that the ledger doesn't prove yet. */
  also_new: string[]
  repo_url: string | null
  checks: PlanCheck[]
  job_id: number | null
  job_title: string | null
  created_at: string
  verified_at: string | null
}

export interface PlanVerify {
  plan: Plan
  verified: boolean
}

// ---- Activity (Phase 6) ----

export interface ActivityCounters {
  checked_automatically: number
  sent_to_you: number
  unverified_shipped: number
  applications_sent: number
  plans_verified: number
  plans_open: number
}

export interface ActivityAsked {
  application_id: number
  job_title: string
  company: string
  questions: number
  created_at: string
}

export interface ActivityEvent {
  id: number
  actor: string
  action: string
  label: string
  gate: number | null
  entity: string
  created_at: string
}

export interface Activity {
  counters: ActivityCounters
  questions: ActivityAsked[]
  events: ActivityEvent[]
}
