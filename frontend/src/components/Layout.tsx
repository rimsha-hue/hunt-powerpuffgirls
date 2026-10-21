import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  BookCheck,
  Briefcase,
  FileStack,
  LogOut,
  MailCheck,
  Rocket,
  ShieldHalf,
  Sprout,
  Wind,
} from 'lucide-react'
import { NavLink, Outlet, useNavigate } from 'react-router'
import { api, json, type Me } from '../lib/api'
import { cx } from '../lib/cx'
import { meKey, useLogout, useMe } from '../lib/session'
import { useToast } from '../lib/toast'
import { Logo } from './Logo'
import { Button } from './ui'

const NAV = [
  { to: '/onboarding', label: 'Onboarding', icon: Rocket },
  { to: '/ledger', label: 'Evidence ledger', icon: BookCheck },
  { to: '/jobs', label: 'Jobs', icon: Briefcase },
  { to: '/applications', label: 'Tailor', icon: FileStack },
  { to: '/plans', label: 'Growth plans', icon: Sprout },
  { to: '/activity', label: 'Activity', icon: Activity },
]
// Shown to admins only; the server checks the role on every admin request regardless.
const ADMIN_NAV = { to: '/admin', label: 'Admin', icon: ShieldHalf }

function CalmToggle() {
  const { data: me } = useMe()
  const qc = useQueryClient()
  const toast = useToast()
  const mutation = useMutation({
    mutationFn: (calm: boolean) => api('/api/profile', { method: 'PUT', body: json({ calm_mode: calm }) }),
    onMutate: (calm) => {
      const previous = qc.getQueryData<Me | null>(meKey)
      if (previous) qc.setQueryData<Me>(meKey, { ...previous, calm_mode: calm })
      return { previous }
    },
    onError: (_err, _calm, context) => {
      qc.setQueryData(meKey, context?.previous ?? null)
      toast("Couldn't save Calm mode.", 'error')
    },
  })
  if (!me) return null
  const on = me.calm_mode
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => mutation.mutate(!on)}
      className={cx(
        'inline-flex items-center gap-1.5 rounded-full border-2 border-ink px-2.5 py-1 font-mono text-xs font-medium',
        on ? 'bg-bubbles' : 'bg-card',
      )}
      title="Calm mode turns every animation off"
    >
      <Wind className="size-4" aria-hidden />
      <span className="hidden xl:inline">Calm {on ? 'on' : 'off'}</span>
      <span className="sr-only xl:hidden">Calm mode {on ? 'on' : 'off'}</span>
    </button>
  )
}

/** Until the email is verified, nothing is sent from the account; say so and offer a new link. */
function VerifyBanner({ email }: { email: string }) {
  const toast = useToast()
  const resend = useMutation({
    mutationFn: () => api<void>('/api/auth/verify/resend', { method: 'POST' }),
    onSuccess: () => toast('A new link is on its way. Check your inbox.'),
    onError: (err) => toast(err.message, 'error'),
  })
  return (
    <section
      role="status"
      className="panel flex flex-wrap items-center gap-4 bg-signal p-5"
      aria-labelledby="verify-title"
    >
      <span className="grid size-12 shrink-0 place-items-center rounded-full border-3 border-ink bg-card">
        <MailCheck className="size-6" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <h2 id="verify-title" className="font-display text-2xl tracking-wide">
          Confirm your email
        </h2>
        <p className="text-sm font-semibold">
          We sent a link to <span className="break-all underline">{email}</span>. Until you open it, Power Hunt sends no
          digests and no applications from your account.
        </p>
      </div>
      <Button onClick={() => resend.mutate()} disabled={resend.isPending}>
        <MailCheck className="size-4" aria-hidden />
        {resend.isPending ? 'Sending…' : 'Send a new link'}
      </Button>
    </section>
  )
}

export function AppLayout() {
  const navigate = useNavigate()
  const logout = useLogout()
  const { data: me } = useMe()
  // An Apply-link session sees only its job: no navigation, no settings.
  const scoped = me?.scope_job_id != null

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b-3 border-ink bg-card/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 sm:px-6 lg:flex-nowrap">
          <NavLink to="/" aria-label="Power Hunt home" className="shrink-0">
            <Logo />
          </NavLink>
          <nav
            className="order-3 flex w-full gap-1 overflow-x-auto lg:order-none lg:w-auto lg:flex-1 lg:justify-center"
            aria-label="Main"
          >
            {!scoped && [...NAV, ...(me?.user.role === 'admin' ? [ADMIN_NAV] : [])].map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cx(
                    'inline-flex shrink-0 items-center gap-1.5 rounded-lg border-2 px-2.5 py-1.5 text-sm font-semibold',
                    isActive ? 'border-ink bg-blossom shadow-comic-sm' : 'border-transparent hover:border-ink',
                  )
                }
              >
                <Icon className="size-4" aria-hidden />
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-1.5">
            {!scoped && <CalmToggle />}
            {me && !scoped && (
              <NavLink
                to="/profile"
                aria-label="Profile"
                className={({ isActive }) =>
                  cx(
                    'inline-flex items-center gap-1.5 rounded-lg border-3 px-2 py-1.5 text-sm font-semibold',
                    isActive ? 'border-ink bg-blossom' : 'border-transparent hover:border-ink',
                  )
                }
              >
                <span
                  aria-hidden
                  className="grid size-6 place-items-center rounded-full border-2 border-ink bg-bubbles text-xs font-bold"
                >
                  {(me.user.name || me.user.email).slice(0, 1).toUpperCase()}
                </span>
                <span className="hidden xl:inline">Profile</span>
              </NavLink>
            )}
            {me && (
              <button
                type="button"
                onClick={() => logout.mutate(undefined, { onSettled: () => navigate('/login') })}
                aria-label="Log out"
                className="inline-flex items-center gap-1.5 rounded-lg border-3 border-transparent px-2 py-1.5 text-sm font-semibold hover:border-ink"
              >
                <LogOut className="size-4" aria-hidden />
                <span className="hidden xl:inline">Log out</span>
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="relative mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6">
        {me && !scoped && !me.is_demo && !me.user.email_verified && <VerifyBanner email={me.user.email} />}
        <div>
          <Outlet />
        </div>
      </main>
    </div>
  )
}
