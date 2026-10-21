import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, LogOut, MailCheck, Radar, RotateCcw, Search, ShieldCheck, ShieldOff, Trash2 } from 'lucide-react'
import { useDeferredValue, useState } from 'react'
import { Button, ErrorNote, PageHeader, Panel, PanelTitle, Skeleton } from '../components/ui'
import { api, json, type AdminAuditEntry, type AdminOverview, type AdminUser, type AdminUsers } from '../lib/api'
import { cx } from '../lib/cx'
import { useMe } from '../lib/session'
import { useToast } from '../lib/toast'

type Tab = 'overview' | 'accounts' | 'audit'
const TABS: Array<{ value: Tab; label: string }> = [
  { value: 'overview', label: 'Overview' },
  { value: 'accounts', label: 'Accounts' },
  { value: 'audit', label: 'Audit log' },
]

const when = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
const day = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

function Tile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="panel flex flex-col gap-1 bg-card p-4">
      <span className="font-mono text-3xl font-medium">{value}</span>
      <span className="text-sm font-semibold">{label}</span>
    </div>
  )
}

function Status({ ok, children }: { ok: boolean; children: string }) {
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-full border-2 border-ink px-2 py-0.5 font-mono text-xs text-ink',
        ok ? 'bg-buttercup' : 'bg-signal',
      )}
    >
      {children}
    </span>
  )
}

function Overview() {
  const toast = useToast()
  const overview = useQuery({ queryKey: ['admin', 'overview'], queryFn: () => api<AdminOverview>('/api/admin/overview') })
  const scout = useMutation({
    mutationFn: () => api<void>('/api/admin/scout', { method: 'POST' }),
    onSuccess: () => toast("The Scout is running for everyone. It spends the job APIs' quotas."),
    onError: (err) => toast(err.message, 'error'),
  })
  if (overview.isPending) return <Skeleton className="h-64" />
  if (overview.error) return <ErrorNote>{overview.error.message}</ErrorNote>
  const o = overview.data
  const s = o.system
  const emails = Object.entries(o.emails_today)
  return (
    <div className="flex flex-col gap-6">
      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" aria-label="Accounts">
        <Tile label="Accounts" value={o.users} />
        <Tile label="Verified emails" value={o.verified} />
        <Tile label="New this week" value={o.new_this_week} />
        <Tile label="Signed in this week" value={o.active_this_week} />
        <Tile label="Applications sent this week" value={o.applications_sent_this_week} />
        <Tile label="Skills proven by repos" value={o.plans_verified} />
        <Tile label="Scout runs today" value={o.scout_runs_today} />
        <Tile label="Admins / suspended" value={`${o.admins} / ${o.suspended}`} />
      </section>
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Panel>
          <PanelTitle kicker="System">How the service is running</PanelTitle>
          <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-sm">
            <dt className="font-semibold">Version</dt>
            <dd>
              {s.version} ({s.env})
            </dd>
            <dt className="font-semibold">Database</dt>
            <dd>
              <Status ok={s.database === 'ok'}>{s.database}</Status>
            </dd>
            <dt className="font-semibold">AI models</dt>
            <dd className="break-words">{s.llm_models.join(', ') || 'none configured'}</dd>
            <dt className="font-semibold">Similarity</dt>
            <dd>
              <Status ok={s.embeddings === 'embedding'}>{s.embeddings}</Status>
            </dd>
            <dt className="font-semibold">Scheduler</dt>
            <dd>
              <Status ok={s.scheduler === 'running'}>{s.scheduler}</Status>
            </dd>
            <dt className="font-semibold">Rate limit store</dt>
            <dd>{s.rate_limit_storage}</dd>
            {(
              [
                ['Job search (JSearch)', s.job_search],
                ['Company research', s.company_research],
                ['Email sending', s.email],
                ['Google sign-in', s.google_sign_in],
                ['GitHub token', s.github_token],
              ] as const
            ).map(([name, on]) => (
              <div key={name} className="contents">
                <dt className="font-semibold">{name}</dt>
                <dd>
                  <Status ok={on}>{on ? 'configured' : 'off'}</Status>
                </dd>
              </div>
            ))}
          </dl>
        </Panel>
        <Panel>
          <PanelTitle kicker="Email">Sent in the last 24 hours</PanelTitle>
          {emails.length === 0 ? (
            <p className="text-sm">No email sent.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm">
              {emails.map(([kind, count]) => (
                <li key={kind} className="flex justify-between border-b-2 border-ink/10 py-1 last:border-0">
                  <span>{kind.replace('_', ' ')}</span>
                  <span className="font-mono">{count}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-5">
            <Button variant="secondary" onClick={() => scout.mutate()} disabled={scout.isPending}>
              <Radar className="size-4" aria-hidden /> Run the Scout for everyone
            </Button>
          </div>
        </Panel>
      </div>
    </div>
  )
}

function AccountRow({ user, self }: { user: AdminUser; self: boolean }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const act = useMutation({
    mutationFn: ({ path, method = 'POST', body }: { path: string; method?: string; body?: unknown }) =>
      api<AdminUser | undefined>(`/api/admin/users/${user.id}${path}`, {
        method,
        ...(body ? { body: json(body) } : {}),
      }),
    onSuccess: (_result, { path }) => {
      void qc.invalidateQueries({ queryKey: ['admin'] })
      toast(path === '' ? `${user.email} was deleted.` : 'Done.')
    },
    onError: (err) => toast(err.message, 'error'),
  })
  const busy = act.isPending
  return (
    <li className="flex flex-col gap-2 border-b-2 border-ink/10 py-3 last:border-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 break-all font-semibold">{user.email}</span>
        {user.name && <span className="text-sm">{user.name}</span>}
        {user.role === 'admin' && <Status ok>admin</Status>}
        {user.demo && <Status ok>demo</Status>}
        {!user.verified && <Status ok={false}>not verified</Status>}
        {user.suspended && <Status ok={false}>suspended</Status>}
        {self && <span className="font-mono text-xs">(you)</span>}
      </div>
      <p className="font-mono text-xs">
        Joined {day.format(new Date(user.created_at))} · last sign-in{' '}
        {user.last_login_at ? when.format(new Date(user.last_login_at)) : 'never'} · {user.jobs} jobs ·{' '}
        {user.applications_sent} sent{user.google_linked ? ' · Google' : ''}
      </p>
      <div className="flex flex-wrap gap-2">
        {user.suspended ? (
          <Button variant="secondary" className="px-3 py-1 text-sm" disabled={busy} onClick={() => act.mutate({ path: '/restore' })}>
            <RotateCcw className="size-4" aria-hidden /> Restore
          </Button>
        ) : (
          <Button variant="secondary" className="px-3 py-1 text-sm" disabled={busy || self} onClick={() => act.mutate({ path: '/suspend' })}>
            <Ban className="size-4" aria-hidden /> Suspend
          </Button>
        )}
        <Button variant="secondary" className="px-3 py-1 text-sm" disabled={busy} onClick={() => act.mutate({ path: '/sign-out' })}>
          <LogOut className="size-4" aria-hidden /> Sign out everywhere
        </Button>
        {user.role === 'admin' ? (
          <Button
            variant="secondary"
            className="px-3 py-1 text-sm"
            disabled={busy || self}
            onClick={() => act.mutate({ path: '/role', method: 'PUT', body: { role: 'user' } })}
          >
            <ShieldOff className="size-4" aria-hidden /> Make user
          </Button>
        ) : (
          <Button
            variant="secondary"
            className="px-3 py-1 text-sm"
            disabled={busy || !user.verified}
            onClick={() => act.mutate({ path: '/role', method: 'PUT', body: { role: 'admin' } })}
          >
            <ShieldCheck className="size-4" aria-hidden /> Make admin
          </Button>
        )}
        {!user.verified && (
          <Button
            variant="secondary"
            className="px-3 py-1 text-sm"
            disabled={busy}
            onClick={() => act.mutate({ path: '/resend-verification' })}
          >
            <MailCheck className="size-4" aria-hidden /> Resend verification
          </Button>
        )}
        {!self && !user.demo &&
          (confirmDelete ? (
            <>
              <Button className="bg-signal px-3 py-1 text-sm" disabled={busy} onClick={() => act.mutate({ path: '', method: 'DELETE' })}>
                <Trash2 className="size-4" aria-hidden /> Yes, delete everything
              </Button>
              <Button variant="ghost" className="px-3 py-1 text-sm" onClick={() => setConfirmDelete(false)}>
                Keep it
              </Button>
            </>
          ) : (
            <Button variant="ghost" className="px-3 py-1 text-sm" disabled={busy} onClick={() => setConfirmDelete(true)}>
              <Trash2 className="size-4" aria-hidden /> Delete
            </Button>
          ))}
      </div>
    </li>
  )
}

function Accounts() {
  const { data: me } = useMe()
  const [q, setQ] = useState('')
  const query = useDeferredValue(q.trim())
  const users = useQuery({
    queryKey: ['admin', 'users', query],
    queryFn: () => api<AdminUsers>(`/api/admin/users?${new URLSearchParams({ q: query, limit: '100' })}`),
    placeholderData: keepPreviousData,
  })
  return (
    <Panel>
      <PanelTitle kicker="Accounts">{users.data ? `${users.data.total} accounts` : 'Accounts'}</PanelTitle>
      <p className="mb-3 text-sm">
        You manage accounts here; you can't read anyone's resume, CV, ledger or emails. Every action is recorded in the
        audit log.
      </p>
      <label className="mb-2 flex items-center gap-2 rounded-lg border-3 border-ink bg-white px-3 py-2">
        <Search className="size-4 shrink-0" aria-hidden />
        <span className="sr-only">Search accounts</span>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          maxLength={100}
          placeholder="Search by email or name"
          className="w-full bg-transparent outline-none"
        />
      </label>
      {users.isPending && <Skeleton className="h-40" />}
      {users.error && <ErrorNote>{users.error.message}</ErrorNote>}
      {users.data && (
        <ul>
          {users.data.users.map((user) => (
            <AccountRow key={user.id} user={user} self={user.id === me?.user.id} />
          ))}
        </ul>
      )}
    </Panel>
  )
}

function AuditLog() {
  const [action, setAction] = useState('')
  const filter = useDeferredValue(action.trim())
  const audit = useQuery({
    queryKey: ['admin', 'audit', filter],
    queryFn: () =>
      api<AdminAuditEntry[]>(`/api/admin/audit?${new URLSearchParams({ limit: '200', ...(filter ? { action: filter } : {}) })}`),
    placeholderData: keepPreviousData,
  })
  return (
    <Panel>
      <PanelTitle kicker="Audit log">Everything that happened</PanelTitle>
      <label className="mb-3 flex flex-col gap-1 text-sm font-semibold">
        Only this event (for example admin_suspend or application_sent)
        <input
          value={action}
          onChange={(e) => setAction(e.target.value)}
          maxLength={60}
          className="rounded-lg border-3 border-ink bg-white px-3 py-2 font-normal"
        />
      </label>
      {audit.isPending && <Skeleton className="h-40" />}
      {audit.error && <ErrorNote>{audit.error.message}</ErrorNote>}
      {audit.data && (
        <ol className="flex flex-col divide-y-2 divide-ink/10">
          {audit.data.map((entry) => (
            <li key={entry.id} className="flex flex-wrap items-baseline gap-x-3 py-2 text-sm">
              <time className="shrink-0 font-mono text-xs" dateTime={entry.created_at}>
                {when.format(new Date(entry.created_at))}
              </time>
              <span className="flex-1">{entry.label}</span>
              <span className="break-all font-mono text-xs">
                {entry.email ?? `account ${entry.user_id}`} · {entry.actor}
                {entry.entity_id ? ` · ${entry.entity} ${entry.entity_id}` : ''}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}

export default function Admin() {
  const [tab, setTab] = useState<Tab>('overview')
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Admin">
        Run the service: accounts, system health and the audit log. Users' resumes, CVs and emails stay private.
      </PageHeader>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Admin sections">
        {TABS.map(({ value, label }) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
            className={cx(
              'rounded-lg border-3 border-ink px-4 py-1.5 font-semibold',
              tab === value ? 'bg-blossom shadow-comic-sm' : 'bg-white',
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'overview' && <Overview />}
      {tab === 'accounts' && <Accounts />}
      {tab === 'audit' && <AuditLog />}
    </div>
  )
}
