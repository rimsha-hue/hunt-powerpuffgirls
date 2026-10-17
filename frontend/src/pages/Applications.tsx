import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, BellRing, ExternalLink } from 'lucide-react'
import { Link } from 'react-router'
import { Button, ErrorNote, PageHeader, Panel, PanelTitle, Skeleton } from '../components/ui'
import { api, json, STAGES, type JobStatus, type PipelineItem, type Stage } from '../lib/api'
import { useToast } from '../lib/toast'

const KEY = ['applications'] as const

/** Outcomes the user records once an application is out, in the order they usually happen. */
const OUTCOMES: Array<{ value: JobStatus; label: string }> = [
  { value: 'interview', label: 'Interview' },
  { value: 'assessment', label: 'Assessment' },
  { value: 'offer', label: 'Offer' },
  { value: 'rejected', label: 'Rejected' },
]
const WAITING: Stage[] = ['applied', 'follow_up', 'interview', 'assessment']
const PREPARING: Stage[] = ['picked', 'tailoring', 'ready']

function day(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function Card({ item, onOutcome }: { item: PipelineItem; onOutcome: (status: JobStatus) => void }) {
  const { job } = item
  const next = OUTCOMES.filter((o) => o.value !== job.status)
  return (
    <article className="panel flex flex-col gap-2 bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to={`/jobs/${job.id}`} className="font-bold leading-snug hover:underline">
            {job.title}
          </Link>
          <p className="text-sm font-semibold">{job.company}</p>
        </div>
        {item.ats_after != null && (
          <span className="shrink-0 rounded-full border-2 border-ink bg-white px-2 py-0.5 font-mono text-xs">
            ATS {item.ats_after}
          </span>
        )}
      </div>

      <p className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-xs">
        {item.external && <span>Applied on their site</span>}
        {item.sent_at && <span>Sent {day(item.sent_at)}</span>}
        {item.follow_up_due && WAITING.includes(item.stage) && (
          <span className={item.stage === 'follow_up' ? 'font-bold' : undefined}>
            Follow up {item.stage === 'follow_up' ? 'now' : `on ${day(item.follow_up_due)}`}
          </span>
        )}
      </p>

      {PREPARING.includes(item.stage) && (
        <Link
          to={`/jobs/${job.id}/workspace`}
          className="mt-1 inline-flex items-center justify-center gap-2 rounded-lg border-3 border-ink bg-blossom px-3 py-1.5 text-sm font-semibold shadow-comic-sm"
        >
          {item.stage === 'ready' ? 'Send it' : 'Open the Tailor'} <ArrowRight className="size-4" aria-hidden />
        </Link>
      )}

      {WAITING.includes(item.stage) && (
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-semibold">Heard back?</span>
          {next.map((o) => (
            <Button key={o.value} variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => onOutcome(o.value)}>
              {o.label}
            </Button>
          ))}
          <a
            href={job.apply_url ?? job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-xs font-semibold underline"
          >
            Posting <ExternalLink className="size-3.5" aria-hidden />
          </a>
        </div>
      )}
    </article>
  )
}

export default function Applications() {
  const qc = useQueryClient()
  const toast = useToast()
  const items = useQuery({ queryKey: KEY, queryFn: () => api<PipelineItem[]>('/api/applications') })

  const outcome = useMutation({
    mutationFn: ({ id, status }: { id: number; status: JobStatus }) =>
      api(`/api/jobs/${id}/status`, { method: 'POST', body: json({ status }) }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY })
      void qc.invalidateQueries({ queryKey: ['jobs'] })
    },
    onError: (err) => toast(err.message, 'error'),
  })

  const rows = items.data ?? []
  const due = rows.filter((r) => r.stage === 'follow_up').length

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Tailor and apply">
        Every job you picked: open the Tailor to work on its CV, send it, then track the reply. Applied on an
        employer's site yourself? Mark it on the job's page and track it here too.
        {rows.length > 0 && (
          <p className="mt-3 flex flex-wrap gap-2">
            {STAGES.filter((s) => rows.some((r) => r.stage === s.value)).map((s) => (
              <a
                key={s.value}
                href={`#stage-${s.value}`}
                className="rounded-full border-2 border-ink bg-white px-2 py-0.5 font-mono text-xs font-medium"
              >
                {s.label} {rows.filter((r) => r.stage === s.value).length}
              </a>
            ))}
          </p>
        )}
      </PageHeader>

      {due > 0 && (
        <p className="flex items-center gap-2 rounded-lg border-3 border-ink bg-buttercup px-4 py-2 font-semibold">
          <BellRing className="size-5" aria-hidden />
          {due === 1 ? '1 application is' : `${due} applications are`} due a follow-up.
        </p>
      )}

      {items.error && <ErrorNote>{items.error.message}</ErrorNote>}

      {items.isPending ? (
        <div className="grid gap-4 md:grid-cols-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <Panel className="text-center">
          <p className="font-display text-3xl tracking-wide">Nothing yet</p>
          <p className="mt-2 font-semibold">
            Pick a job on the{' '}
            <Link to="/jobs" className="underline">
              Jobs page
            </Link>{' '}
            and it shows up here.
          </p>
        </Panel>
      ) : (
        STAGES.filter((s) => rows.some((r) => r.stage === s.value)).map((s) => (
          <section key={s.value} id={`stage-${s.value}`} className="flex flex-col gap-3">
            <PanelTitle kicker={s.hint || 'Stage'}>{s.label}</PanelTitle>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {rows
                .filter((r) => r.stage === s.value)
                .map((item) => (
                  <Card
                    key={item.job.id}
                    item={item}
                    onOutcome={(status) => outcome.mutate({ id: item.job.id, status })}
                  />
                ))}
            </div>
          </section>
        ))
      )}
    </div>
  )
}
