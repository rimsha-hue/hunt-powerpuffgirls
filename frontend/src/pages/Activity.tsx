import { useQuery } from '@tanstack/react-query'
import { Bot, ShieldCheck, User } from 'lucide-react'
import { ErrorNote, PageHeader, Panel, PanelTitle, Skeleton } from '../components/ui'
import { api, type Activity as ActivityData, type ActivityCounters } from '../lib/api'
import { cx } from '../lib/cx'

const timeFormat = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
})

const COUNTERS: Array<{ key: keyof ActivityCounters; label: string; note: string }> = [
  { key: 'checked_automatically', label: 'Claims checked automatically', note: 'Proven by your ledger, no question needed' },
  { key: 'sent_to_you', label: 'Claims sent to you', note: 'Only you know the truth on these' },
  { key: 'unverified_shipped', label: 'Unverified claims shipped', note: 'In anything sent or submitted' },
  { key: 'applications_sent', label: 'Applications sent', note: 'Emails sent and forms you submitted' },
  { key: 'plans_verified', label: 'Skills proven by repos', note: 'Growth plans that passed every check' },
]

function Counter({ label, note, value, good }: { label: string; note: string; value: number; good?: boolean }) {
  return (
    <div className={cx('panel flex flex-col gap-1 p-4', good ? 'bg-verified' : 'bg-card')}>
      <span className="font-mono text-4xl font-medium">{value}</span>
      <span className="font-semibold">{label}</span>
      <span className="text-xs">{note}</span>
    </div>
  )
}

export default function Activity() {
  const activity = useQuery({ queryKey: ['activity'], queryFn: () => api<ActivityData>('/api/activity') })
  const data = activity.data
  const most = Math.max(1, ...(data?.questions.map((q) => q.questions) ?? [1]))

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Activity">
        Everything the squad did and every decision you made, in order. Nothing leaves without passing your gates.
      </PageHeader>

      {activity.isPending && <Skeleton className="h-40" />}
      {activity.error && <ErrorNote>{activity.error.message}</ErrorNote>}

      {data && (
        <>
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5" aria-label="Counters">
            {COUNTERS.map(({ key, label, note }) => (
              <Counter
                key={key}
                label={label}
                note={note}
                value={data.counters[key]}
                good={key === 'unverified_shipped' && data.counters[key] === 0}
              />
            ))}
          </section>

          <div className="grid gap-6 lg:grid-cols-[1fr_1.4fr]">
            <Panel>
              <PanelTitle kicker="Learning loop">Questions per application</PanelTitle>
              <p className="mb-3 text-sm">
                Each answer you give joins your ledger, so the next application should ask you less.
              </p>
              {data.questions.length === 0 ? (
                <p className="text-sm">No applications yet.</p>
              ) : (
                <ol className="flex flex-col gap-2">
                  {data.questions.map((q) => (
                    <li key={q.application_id} className="text-sm">
                      <div className="flex justify-between gap-2">
                        <span className="truncate font-semibold">
                          {q.job_title} <span className="font-normal">at {q.company}</span>
                        </span>
                        <span className="font-mono">{q.questions}</span>
                      </div>
                      <div className="mt-1 h-2.5 rounded-full border-2 border-ink bg-card">
                        <div
                          className="h-full rounded-full bg-bubbles"
                          style={{ width: `${(q.questions / most) * 100}%` }}
                        />
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </Panel>

            <Panel>
              <PanelTitle kicker="Audit log">Timeline</PanelTitle>
              {data.events.length === 0 ? (
                <p className="text-sm">Nothing yet.</p>
              ) : (
                <ol className="flex flex-col divide-y-2 divide-ink/10">
                  {data.events.map((event) => {
                    const Icon = event.actor === 'user' ? User : Bot
                    return (
                      <li key={event.id} className="flex items-start gap-3 py-2 text-sm">
                        <Icon className="mt-0.5 size-4 shrink-0" aria-label={event.actor === 'user' ? 'You' : event.actor} />
                        <span className="flex-1">
                          {event.label}
                          {event.gate && (
                            <span className="ml-2 inline-flex items-center gap-1 rounded-full border-2 border-ink bg-signal px-1.5 font-mono text-xs">
                              <ShieldCheck className="size-3" aria-hidden /> Gate {event.gate}
                            </span>
                          )}
                        </span>
                        <time className="shrink-0 font-mono text-xs" dateTime={event.created_at}>
                          {timeFormat.format(new Date(event.created_at))}
                        </time>
                      </li>
                    )
                  })}
                </ol>
              )}
            </Panel>
          </div>
        </>
      )}
    </div>
  )
}
