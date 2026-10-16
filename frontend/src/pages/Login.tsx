import { LogIn, UserPlus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router'
import { Logo } from '../components/Layout'
import { Button, ErrorNote, Field } from '../components/ui'
import { cx } from '../lib/cx'
import { useAuthMutation, useMe } from '../lib/session'

export default function Login() {
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const { data: me } = useMe()
  const navigate = useNavigate()
  const login = useAuthMutation('login')
  const signup = useAuthMutation('signup')
  const active = mode === 'login' ? login : signup

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
    <div className="halftone flex min-h-screen items-center justify-center bg-signal px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>
        <div className="panel bg-cream p-6">
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
                  mode === m ? 'bg-coral shadow-comic-sm' : 'bg-white',
                )}
              >
                {m === 'login' ? 'Log in' : 'Sign up'}
              </button>
            ))}
          </div>

          <form onSubmit={submit} className="flex flex-col gap-4">
            {mode === 'signup' && <Field label="Your name" name="name" autoComplete="name" maxLength={200} />}
            <Field label="Email" name="email" type="email" autoComplete="email" required />
            <Field
              label="Password"
              name="password"
              type="password"
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
        <p className="mt-4 text-center text-sm font-semibold">No source, no claim. Your squad proves everything it writes.</p>
      </div>
    </div>
  )
}
