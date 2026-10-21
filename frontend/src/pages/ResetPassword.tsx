import { useMutation } from '@tanstack/react-query'
import { KeyRound } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { PublicCard } from '../components/PublicCard'
import { Button, ErrorNote, PasswordField } from '../components/ui'
import { api, ApiError, json } from '../lib/api'

export default function ResetPassword() {
  const [params] = useSearchParams()
  const token = params.get('t') ?? ''
  const [mismatch, setMismatch] = useState(false)
  const reset = useMutation({
    mutationFn: (password: string) =>
      api<void>('/api/auth/password/reset', { method: 'POST', body: json({ token, password }) }),
  })

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const password = String(form.get('password') ?? '')
    const same = password === String(form.get('confirm') ?? '')
    setMismatch(!same)
    if (same) reset.mutate(password)
  }

  const fieldError = reset.error instanceof ApiError ? reset.error.fields.password : undefined

  if (!token) {
    return (
      <PublicCard title="Reset your password">
        <ErrorNote>This link is incomplete. Open the link from the email again, or ask for a new one.</ErrorNote>
        <Link to="/forgot-password" className="mt-3 inline-block font-semibold underline">
          Ask for a new link
        </Link>
      </PublicCard>
    )
  }

  return (
    <PublicCard title="Choose a new password">
      {reset.isSuccess ? (
        <div className="flex flex-col gap-3" role="status">
          <p className="font-semibold">Your password is changed, and every device was signed out.</p>
          <Link
            to="/login"
            className="inline-flex w-fit items-center gap-2 rounded-lg border-3 border-ink bg-blossom px-4 py-2 font-semibold shadow-comic-sm"
          >
            Log in with your new password
          </Link>
        </div>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-4">
          <PasswordField
            label="New password"
            name="password"
            autoComplete="new-password"
            minLength={10}
            required
            hint="At least 10 characters."
          />
          <PasswordField label="Type it again" name="confirm" autoComplete="new-password" minLength={10} required />
          {mismatch && <ErrorNote>The two passwords don't match.</ErrorNote>}
          {reset.error && (
            <ErrorNote>
              {fieldError ?? reset.error.message}{' '}
              {reset.error instanceof ApiError && reset.error.status === 400 && (
                <Link to="/forgot-password" className="underline">
                  Ask for a new link
                </Link>
              )}
            </ErrorNote>
          )}
          <Button type="submit" disabled={reset.isPending} className="w-full">
            <KeyRound className="size-4" aria-hidden />
            {reset.isPending ? 'Saving…' : 'Save new password'}
          </Button>
        </form>
      )}
    </PublicCard>
  )
}
