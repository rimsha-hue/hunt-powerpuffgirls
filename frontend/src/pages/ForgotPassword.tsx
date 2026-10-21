import { useMutation } from '@tanstack/react-query'
import { MailCheck, Send } from 'lucide-react'
import { type FormEvent } from 'react'
import { Link } from 'react-router'
import { PublicCard } from '../components/PublicCard'
import { Button, ErrorNote, Field } from '../components/ui'
import { api, json } from '../lib/api'

export default function ForgotPassword() {
  const send = useMutation({
    mutationFn: (email: string) => api<void>('/api/auth/password/forgot', { method: 'POST', body: json({ email }) }),
  })

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    send.mutate(String(new FormData(event.currentTarget).get('email') ?? ''))
  }

  return (
    <PublicCard title="Forgot your password?">
      {send.isSuccess ? (
        <div className="flex flex-col gap-3" role="status">
          <p className="flex items-start gap-2 font-semibold">
            <MailCheck className="mt-0.5 size-5 shrink-0" aria-hidden />
            If an account uses that email, a reset link is on its way.
          </p>
          <p className="text-sm">
            The link works once and expires soon. Nothing arrived? Check your spam folder, or try again in a few
            minutes.
          </p>
          <Link to="/login" className="font-semibold underline">
            Back to log in
          </Link>
        </div>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-4">
          <p className="text-sm">Enter your account's email and we'll send you a link to choose a new password.</p>
          <Field label="Email" name="email" type="email" autoComplete="email" required />
          {send.error && <ErrorNote>{send.error.message}</ErrorNote>}
          <Button type="submit" disabled={send.isPending} className="w-full">
            <Send className="size-4" aria-hidden />
            {send.isPending ? 'Sending…' : 'Send reset link'}
          </Button>
        </form>
      )}
    </PublicCard>
  )
}
