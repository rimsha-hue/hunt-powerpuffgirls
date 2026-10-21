import { useMutation } from '@tanstack/react-query'
import { BellOff } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'
import { PublicCard } from '../components/PublicCard'
import { Button, ErrorNote } from '../components/ui'
import { api } from '../lib/api'

/** Opened from the link in a digest email. It asks before acting, because mail scanners open links. */
export default function Unsubscribe() {
  const [params] = useSearchParams()
  const token = params.get('t') ?? ''
  const stop = useMutation({
    mutationFn: () => api<void>(`/api/email/unsubscribe?t=${encodeURIComponent(token)}`, { method: 'POST' }),
  })

  return (
    <PublicCard title="Job digest emails">
      {stop.isSuccess ? (
        <div className="flex flex-col gap-3" role="status">
          <p className="font-semibold">Done. You won't get job digest emails any more.</p>
          <p className="text-sm">
            Account emails, like password resets, still arrive. You can turn digests back on in your{' '}
            <Link to="/profile" className="font-semibold underline">
              profile
            </Link>
            .
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <p className="text-sm">Stop the emails that list new jobs matching your search? Your account stays as it is.</p>
          {stop.error && <ErrorNote>{stop.error.message}</ErrorNote>}
          <Button onClick={() => stop.mutate()} disabled={!token || stop.isPending} className="w-full">
            <BellOff className="size-4" aria-hidden />
            {stop.isPending ? 'One moment…' : 'Stop digest emails'}
          </Button>
        </div>
      )}
    </PublicCard>
  )
}
