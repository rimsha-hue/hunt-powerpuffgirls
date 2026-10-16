import { useQuery } from '@tanstack/react-query'
import { LogIn, UserPlus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate, useSearchParams } from 'react-router'
import { Logo } from '../components/Logo'
import { Button, ErrorNote, Field, PasswordField } from '../components/ui'
import { api } from '../lib/api'
import { useCalm } from '../lib/calm'
import { cx } from '../lib/cx'
import { useAuthMutation, useMe } from '../lib/session'

const GOOGLE_ERRORS: Record<string, string> = {
  google_cancelled: 'Google sign-in was cancelled.',
  google_failed: "We couldn't verify that Google sign-in. Please try again.",
  google_unavailable: 'Google sign-in is not available right now. Use email instead.',
}

/** Google's multicolour "G", as its sign-in branding guidelines require. */
function GoogleG() {
  return (
    <svg viewBox="0 0 48 48" className="size-5" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  )
}

/** The intro clip as a full-bleed backdrop; a still frame in Calm mode or with reduced motion. */
function LoginBackdrop() {
  const calm = useCalm()
  const media = 'absolute inset-0 size-full object-cover object-[30%_50%] lg:object-[20%_50%]'
  return (
    <div className="absolute inset-0 bg-blush" aria-hidden="true">
      {calm ? (
        <img src="/media/intro-poster.jpg" alt="" className={media} />
      ) : (
        <video className={media} src="/media/intro.mp4" poster="/media/intro-poster.jpg" autoPlay muted loop playsInline />
      )}
      {/* Soft wash so the form side stays calm to read; the trio stays visible on the left. */}
      <div className="absolute inset-0 bg-gradient-to-b from-blush/30 to-blush/60 lg:bg-gradient-to-r lg:from-transparent lg:via-blush/20 lg:to-blush/80" />
    </div>
  )
}

export default function Login() {
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const { data: me } = useMe()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const login = useAuthMutation('login')
  const signup = useAuthMutation('signup')
  const active = mode === 'login' ? login : signup
  const providers = useQuery({
    queryKey: ['auth-providers'],
    queryFn: () => api<{ password: boolean; google: boolean }>('/api/auth/providers'),
    staleTime: Infinity,
  })
  const googleError = GOOGLE_ERRORS[params.get('error') ?? '']

  if (me) return <Navigate to={me.onboarded ? '/ledger' : '/onboarding'} replace />

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    active.mutate(
      {
        email: String(form.get('email') ?? ''),
        password: String(form.get('password') ?? ''),
        ...(mode === 'signup' ? { name: String(form.get('name') ?? '') } : {}),
      },
      { onSuccess: (result) => navigate(result.onboarded ? '/ledger' : '/onboarding', { replace: true }) },
    )
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4 py-10 lg:justify-end lg:pr-[8vw]">
      <LoginBackdrop />
      <div className="relative w-full max-w-md">
        <div className="mb-6 flex justify-center">
          <Logo size="lg" />
        </div>
        <div className="panel bg-card p-6">
          {providers.data?.google && (
            <>
              {/* A full-page navigation: the OAuth flow must leave the app and come back. */}
              <a
                href="/api/auth/google/start"
                className="lift flex w-full items-center justify-center gap-3 rounded-lg border-3 border-ink bg-white px-4 py-2.5 font-semibold shadow-comic-sm"
              >
                <GoogleG />
                Continue with Google
              </a>
              <div className="my-5 flex items-center gap-3 font-mono text-xs font-medium uppercase" aria-hidden>
                <span className="h-0.5 flex-1 bg-ink/20" />
                or use email
                <span className="h-0.5 flex-1 bg-ink/20" />
              </div>
            </>
          )}
          {googleError && (
            <div className="mb-4">
              <ErrorNote>{googleError}</ErrorNote>
            </div>
          )}
          <div className="mb-5 grid grid-cols-2 gap-2" role="tablist">
            {(['login', 'signup'] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                onClick={() => setMode(m)}
                className={cx(
                  'rounded-lg border-3 border-ink py-2 font-display text-xl tracking-wide',
                  mode === m ? 'bg-blossom shadow-comic-sm' : 'bg-white',
                )}
              >
                {m === 'login' ? 'Log in' : 'Sign up'}
              </button>
            ))}
          </div>

          <form onSubmit={submit} className="flex flex-col gap-4">
            {mode === 'signup' && <Field label="Your name" name="name" autoComplete="name" maxLength={200} />}
            <Field label="Email" name="email" type="email" autoComplete="email" required />
            <PasswordField
              label="Password"
              name="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              minLength={mode === 'signup' ? 10 : 1}
              required
              {...(mode === 'signup' ? { hint: 'At least 10 characters.' } : {})}
            />
            {active.error && <ErrorNote>{active.error.message}</ErrorNote>}
            <Button type="submit" disabled={active.isPending} className="mt-1 w-full">
              {mode === 'login' ? <LogIn className="size-5" aria-hidden /> : <UserPlus className="size-5" aria-hidden />}
              {active.isPending ? 'One moment…' : mode === 'login' ? 'Log in' : 'Create account'}
            </Button>
          </form>
        </div>
        <p className="mx-auto mt-4 w-fit rounded-full border-2 border-ink bg-card px-4 py-1 text-center text-sm font-semibold">
          No source, no claim. Your squad proves everything it writes.
        </p>
      </div>
    </div>
  )
}
