import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellOff, BellRing, Download, ExternalLink, KeyRound, Pencil, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { Button, ErrorNote, Panel, PanelTitle, PasswordField, Skeleton } from '../components/ui'
import { api, ApiError, json, setCsrfToken, type Profile as ProfileData } from '../lib/api'
import { meKey, useMe } from '../lib/session'
import { useToast } from '../lib/toast'

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 border-b-2 border-ink/10 py-2 last:border-0 sm:grid-cols-[11rem_1fr]">
      <dt className="text-sm font-semibold">{label}</dt>
      <dd className="min-w-0 break-words text-sm">{children}</dd>
    </div>
  )
}

const missing = <span className="text-ink/60">Not set</span>

/** Change (or, for a Google-only account, add) the password. Other devices sign out. */
function PasswordPanel({ hasPassword, isDemo }: { hasPassword: boolean; isDemo: boolean }) {
  const toast = useToast()
  const form = useRef<HTMLFormElement>(null)
  const [mismatch, setMismatch] = useState(false)
  const change = useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api<void>('/api/auth/password/change', { method: 'POST', body: json(body) }),
    onSuccess: () => {
      form.current?.reset()
      toast('Password saved. Every other device was signed out.')
    },
  })

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const next = String(data.get('new_password') ?? '')
    const same = next === String(data.get('confirm') ?? '')
    setMismatch(!same)
    if (same) change.mutate({ current_password: String(data.get('current_password') ?? ''), new_password: next })
  }

  const fields = change.error instanceof ApiError ? change.error.fields : {}
  return (
    <Panel>
      <PanelTitle kicker="Security">{hasPassword ? 'Change your password' : 'Add a password'}</PanelTitle>
      {isDemo ? (
        <p className="text-sm font-semibold">The shared demo account's password can't be changed.</p>
      ) : (
        <form ref={form} onSubmit={submit} className="flex flex-col gap-3">
          {!hasPassword && <p className="text-sm">You sign in with Google. A password lets you sign in with email too.</p>}
          {hasPassword && (
            <PasswordField label="Current password" name="current_password" autoComplete="current-password" required />
          )}
          <PasswordField
            label="New password"
            name="new_password"
            autoComplete="new-password"
            minLength={10}
            required
            hint="At least 10 characters."
          />
          <PasswordField label="Type it again" name="confirm" autoComplete="new-password" minLength={10} required />
          {mismatch && <ErrorNote>The two passwords don't match.</ErrorNote>}
          {change.error && <ErrorNote>{fields.new_password ?? change.error.message}</ErrorNote>}
          <Button type="submit" variant="secondary" disabled={change.isPending} className="w-fit">
            <KeyRound className="size-4" aria-hidden /> {change.isPending ? 'Saving…' : 'Save password'}
          </Button>
        </form>
      )}
    </Panel>
  )
}

/** Job digest emails on or off; account emails (resets, alerts) always arrive. */
function DigestSwitch({ on }: { on: boolean }) {
  const qc = useQueryClient()
  const toast = useToast()
  const save = useMutation({
    mutationFn: (value: boolean) => api<ProfileData>('/api/profile', { method: 'PUT', body: json({ digest_emails: value }) }),
    onSuccess: (profile) => {
      qc.setQueryData(['profile'], profile)
      toast(profile.digest_emails ? 'Job digests are on.' : 'Job digests are off.')
    },
    onError: (err) => toast(err.message, 'error'),
  })
  return (
    <Panel>
      <PanelTitle kicker="Emails">What we send you</PanelTitle>
      <p className="mb-3 text-sm">
        Job digests list new jobs that match your search, a few times a day at most. Account emails, like password
        resets and sign-in alerts, always arrive.
      </p>
      <Button
        variant="secondary"
        role="switch"
        aria-checked={on}
        onClick={() => save.mutate(!on)}
        disabled={save.isPending}
      >
        {on ? <BellRing className="size-4" aria-hidden /> : <BellOff className="size-4" aria-hidden />}
        Job digests {on ? 'on' : 'off'}
      </Button>
    </Panel>
  )
}

export default function Profile() {
  const { data: me } = useMe()
  const qc = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()
  const [confirming, setConfirming] = useState(false)
  const [params, setParams] = useSearchParams()
  // Arriving from the verification link: say how it went once, then drop the flag from the URL.
  useEffect(() => {
    const result = params.get('verified')
    if (!result) return
    if (result === '1') {
      toast('Email confirmed. Digests and sends are on.')
      void qc.invalidateQueries({ queryKey: meKey })
    } else {
      toast('That link has expired or was already used. Ask for a new one.', 'error')
    }
    setParams({}, { replace: true })
  }, [params, setParams, qc, toast])
  const profile = useQuery({ queryKey: ['profile'], queryFn: () => api<ProfileData>('/api/profile') })

  const remove = useMutation({
    mutationFn: () => api<void>('/api/auth/me', { method: 'DELETE' }),
    onSuccess: () => {
      setCsrfToken(null)
      qc.clear()
      qc.setQueryData(meKey, null)
      navigate('/')
    },
    onError: (err) => toast(err.message, 'error'),
  })

  if (!me || profile.isPending) return <Skeleton className="h-96" />
  if (profile.error) return <ErrorNote>{profile.error.message}</ErrorNote>
  const p = profile.data
  const signIn = [me.user.has_password && 'Email and password', me.user.google_linked && 'Google'].filter(Boolean)

  return (
    <div className="flex flex-col gap-6">
      <section className="panel hearts flex flex-wrap items-center gap-5 p-6">
        <span
          aria-hidden
          className="grid size-16 place-items-center rounded-full border-3 border-ink bg-bubbles font-display text-4xl"
        >
          {(p.name || p.email).slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="font-display text-5xl tracking-wide">{p.name || 'Your profile'}</h1>
          <p className="font-semibold">{p.email}</p>
          <p className="font-mono text-xs">
            Member since {new Date(me.user.created_at).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}
          </p>
        </div>
        <Link
          to="/onboarding"
          className="inline-flex items-center gap-2 rounded-lg border-3 border-ink bg-blossom px-4 py-2 font-semibold shadow-comic-sm"
        >
          <Pencil className="size-4" aria-hidden /> Edit details
        </Link>
      </section>

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Panel>
          <PanelTitle kicker="Account">Who you are</PanelTitle>
          <dl>
            <Row label="Name">{p.name || missing}</Row>
            <Row label="Email">
              {p.email}{' '}
              <span
                className={`ml-1 rounded-full border-2 border-ink px-2 py-0.5 font-mono text-[11px] ${me.user.email_verified ? 'bg-buttercup' : 'bg-signal'}`}
              >
                {me.user.email_verified ? 'verified' : 'not verified'}
              </span>
            </Row>
            <Row label="Sign-in">{signIn.join(' and ') || missing}</Row>
            <Row label="Phone">{p.phone || missing}</Row>
            <Row label="Location">{p.location?.label || missing}</Row>
          </dl>
        </Panel>

        <Panel>
          <PanelTitle kicker="Evidence sources">Where your facts come from</PanelTitle>
          <dl>
            <Row label="Resume">{p.resume_version ? `Version ${p.resume_version}` : missing}</Row>
            <Row label="GitHub">
              {p.github_username ? (
                <a
                  href={`https://github.com/${p.github_username}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 underline"
                >
                  {p.github_username} <ExternalLink className="size-3.5" aria-hidden />
                </a>
              ) : (
                missing
              )}
            </Row>
            <Row label="Portfolio">
              {p.portfolio_url ? (
                <a href={p.portfolio_url} target="_blank" rel="noopener noreferrer" className="break-all underline">
                  {p.portfolio_url}
                </a>
              ) : (
                missing
              )}
            </Row>
            <Row label="LinkedIn">
              {p.linkedin ? (
                <a href={p.linkedin} target="_blank" rel="noopener noreferrer" className="break-all underline">
                  {p.linkedin}
                </a>
              ) : (
                missing
              )}
            </Row>
          </dl>
        </Panel>

        <Panel>
          <PanelTitle kicker="Job search">What the Scout looks for</PanelTitle>
          <dl>
            <Row label="Target roles">{p.target_roles.join(', ') || missing}</Row>
            <Row label="Places">{p.target_locations.map((l) => l.label).join(', ') || missing}</Row>
            <Row label="Digest threshold">Match {p.match_threshold}+</Row>
            <Row label="Work authorisation">{p.work_authorization || missing}</Row>
            <Row label="Notice period">{p.notice_period || missing}</Row>
            <Row label="Salary">
              {p.salary ? `${p.salary.amount.toLocaleString()} ${p.salary.currency} a ${p.salary.period}` : missing}
            </Row>
          </dl>
        </Panel>

        <PasswordPanel hasPassword={me.user.has_password} isDemo={me.is_demo} />
        <DigestSwitch on={p.digest_emails} />

        <Panel>
          <PanelTitle kicker="Your data">Download or delete it</PanelTitle>
          <p className="mb-3 text-sm">
            A copy of everything stored about you, as one JSON file. Read how it's used in the{' '}
            <Link to="/privacy" className="font-semibold underline">
              privacy notice
            </Link>
            .
          </p>
          {/* A plain download: same-origin GET with the session cookie, saved by the browser. */}
          <a
            href="/api/auth/me/export"
            download
            className="mb-5 inline-flex w-fit items-center gap-2 rounded-lg border-3 border-ink bg-card px-4 py-2 font-semibold shadow-comic-sm"
          >
            <Download className="size-4" aria-hidden /> Download my data
          </a>
          <p className="mb-3 text-sm">
            Removes your account, resume, ledger, jobs, applications and plans for good. Emails already sent to employers
            can't be recalled.
          </p>
          {me.is_demo ? (
            <p className="text-sm font-semibold">The shared demo account can't be deleted.</p>
          ) : confirming ? (
            <div className="flex flex-wrap gap-2">
              <Button className="bg-signal" onClick={() => remove.mutate()} disabled={remove.isPending}>
                <Trash2 className="size-4" aria-hidden />
                {remove.isPending ? 'Deleting…' : 'Yes, delete my data'}
              </Button>
              <Button variant="ghost" onClick={() => setConfirming(false)}>
                Keep it
              </Button>
            </div>
          ) : (
            <Button variant="secondary" onClick={() => setConfirming(true)}>
              <Trash2 className="size-4" aria-hidden /> Delete my data
            </Button>
          )}
        </Panel>
      </div>
    </div>
  )
}
