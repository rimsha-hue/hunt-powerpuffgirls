// Typed fetch wrapper: same-origin cookies, CSRF header on every state-changing request,
// and errors carrying the server's user-safe `detail` message.

let csrfToken: string | null = null

export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

async function errorMessage(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail) && detail.length > 0) {
        const first = detail[0] as { msg?: unknown }
        if (typeof first.msg === 'string') return first.msg.replace(/^Value error, /, '')
      }
    }
  } catch {
    // Non-JSON error body: fall through to the generic message.
  }
  if (res.status === 429) return 'Too many attempts. Wait a minute and try again.'
  return `Something went wrong (${res.status}).`
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (UNSAFE.has(method) && csrfToken) headers.set('X-CSRF-Token', csrfToken)

  const res = await fetch(path, { ...init, method, headers, credentials: 'same-origin' })
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res))
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
}

export interface Profile {
  name: string
  email: string
  github_username: string | null
  portfolio_url: string | null
  phone: string | null
  location: string | null
  linkedin: string | null
  target_roles: string[]
  target_locations: string[]
  match_threshold: number
  calm_mode: boolean
  resume_version: number
}

export type FactType =
  | 'skill'
  | 'metric'
  | 'employer'
  | 'degree'
  | 'certification'
  | 'project'
  | 'role'
  | 'personal'

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
