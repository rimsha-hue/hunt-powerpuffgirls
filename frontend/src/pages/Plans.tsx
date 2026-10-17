import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  CircleCheck,
  CircleX,
  Clock,
  Download,
  ExternalLink,
  GitBranch,
  Lightbulb,
  Sprout,
  Trash2,
} from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { type FormEvent, useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router'
import { Button, ErrorNote, PageHeader, Panel, Skeleton, Stat } from '../components/ui'
import { api, json, type Plan, type PlanStatus, type PlanVerify } from '../lib/api'
import { useCalm } from '../lib/calm'
import { cx } from '../lib/cx'
import { useToast } from '../lib/toast'

const KEY = ['plans'] as const

const STATUS: Record<PlanStatus, { label: string; className: string }> = {
  open: { label: 'Open', className: 'bg-card' },
  in_progress: { label: 'In progress', className: 'bg-bubbles' },
  verified: { label: 'Verified', className: 'bg-verified' },
}

function StatusBadge({ status }: { status: PlanStatus }) {
  const { label, className } = STATUS[status]
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded-full border-2 border-ink px-2 py-0.5 font-mono text-xs font-medium text-ink',
        className,
      )}
    >
      {status === 'verified' && <Check className="size-3.5" aria-hidden />}
      {label}
    </span>
  )
}

function Progress({ plan, calm }: { plan: Plan; calm: boolean }) {
  const done = plan.steps.filter((s) => s.done).length
  const share = plan.status === 'verified' ? 1 : plan.steps.length ? done / plan.steps.length : 0
  return (
    <div
      className="h-3 overflow-hidden rounded-full border-2 border-ink bg-card"
      role="progressbar"
      aria-label="Plan progress"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(share * 100)}
    >
      <motion.div
        className={cx('h-full', plan.status === 'verified' ? 'bg-verified' : 'bg-buttercup')}
        initial={false}
        animate={{ width: `${share * 100}%` }}
        transition={calm ? { duration: 0 } : { duration: 0.5, ease: 'easeOut' }}
      />
    </div>
  )
}

function LevelUp({ skill }: { skill: string }) {
  return (
    <motion.div
      initial={{ y: -48, opacity: 0, rotate: -3 }}
      animate={{ y: 0, opacity: 1, rotate: 0 }}
      exit={{ opacity: 0 }}
      transition={{ type: 'spring', stiffness: 500, damping: 24 }}
      className="pointer-events-none absolute inset-x-0 -top-5 z-10 mx-auto w-fit rounded-xl border-3 border-ink bg-signal px-5 py-1 font-display text-3xl tracking-wide text-ink shadow-comic-sm"
      role="status"
    >
      Level up! {skill} proven
    </motion.div>
  )
}

/** The Coach's project ideas: what to build, each with a one-page proposal to download. */
function Ideas({ plan, onChange }: { plan: Plan; onChange: (next: Plan) => void }) {
  const toast = useToast()
  const [open, setOpen] = useState<number | null>(null)
  const suggest = useMutation({
    mutationFn: () => api<Plan>(`/api/plans/${plan.id}/ideas`, { method: 'POST' }),
    onSuccess: (next) => {
      onChange(next)
      setOpen(0)
    },
    onError: (err) => toast(err.message, 'error'),
  })
  const base = `/api/plans/${plan.id}/ideas`
  return (
    <section className="rounded-xl border-3 border-ink bg-bubbles/30 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-semibold">Project ideas</h3>
          <p className="text-xs">Concrete things to build that prove {plan.skill}, each with a one-page proposal.</p>
        </div>
        <Button variant={plan.ideas.length ? 'ghost' : 'primary'} onClick={() => suggest.mutate()} disabled={suggest.isPending}>
          <Lightbulb className="size-4" aria-hidden />
          {suggest.isPending ? 'Thinking…' : plan.ideas.length ? 'New ideas' : 'Suggest project ideas'}
        </Button>
      </div>
      {plan.ideas.length > 0 && (
        <ul className="mt-3 flex flex-col gap-2">
          {plan.ideas.map((idea, index) => (
            <li key={idea.title} className="rounded-lg border-2 border-ink bg-card p-3">
              <button
                type="button"
                className="flex w-full items-start justify-between gap-3 text-left"
                aria-expanded={open === index}
                onClick={() => setOpen(open === index ? null : index)}
              >
                <span>
                  <span className="block font-bold">{idea.title}</span>
                  <span className="block text-sm">{idea.pitch}</span>
                </span>
                <span className="shrink-0 font-mono text-xs">about {idea.hours} h</span>
              </button>
              {open === index && (
                <div className="mt-2 grid gap-2 text-sm md:grid-cols-2">
                  <p>
                    <span className="font-semibold">Problem: </span>
                    {idea.problem}
                  </p>
                  <p>
                    <span className="font-semibold">Stack: </span>
                    {idea.stack.join(', ')}
                  </p>
                  <div>
                    <p className="font-semibold">First version</p>
                    <ul className="list-disc pl-5">
                      {idea.scope.map((s) => (
                        <li key={s}>{s}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <p className="font-semibold">Milestones</p>
                    <ol className="list-decimal pl-5">
                      {idea.milestones.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ol>
                  </div>
                  <p className="md:col-span-2">
                    <span className="font-semibold">How it proves {plan.skill}: </span>
                    {idea.proof}
                  </p>
                </div>
              )}
              <p className="mt-2 flex gap-3 text-xs font-semibold">
                <a href={`${base}/${index}/proposal.pdf`} className="inline-flex items-center gap-1 underline">
                  <Download className="size-3.5" aria-hidden /> Proposal (PDF)
                </a>
                <a href={`${base}/${index}/proposal.md`} className="inline-flex items-center gap-1 underline">
                  <Download className="size-3.5" aria-hidden /> Markdown
                </a>
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function PlanCard({ plan, focused }: { plan: Plan; focused: boolean }) {
  const qc = useQueryClient()
  const toast = useToast()
  const calm = useCalm()
  const [repo, setRepo] = useState(plan.repo_url ?? '')
  const [celebrate, setCelebrate] = useState(false)

  useEffect(() => {
    if (!celebrate) return
    const timer = window.setTimeout(() => setCelebrate(false), 1000)
    return () => window.clearTimeout(timer)
  }, [celebrate])

  const replace = (next: Plan) => qc.setQueryData<Plan[]>(KEY, (rows) => rows?.map((p) => (p.id === next.id ? next : p)))

  const step = useMutation({
    mutationFn: ({ index, done }: { index: number; done: boolean }) =>
      api<Plan>(`/api/plans/${plan.id}/steps/${index}`, { method: 'PUT', body: json({ done }) }),
    // Optimistic: the box ticks at once and unticks if the server refuses.
    onMutate: ({ index, done }) => {
      const previous = qc.getQueryData<Plan[]>(KEY)
      replace({ ...plan, steps: plan.steps.map((s, i) => (i === index ? { ...s, done } : s)) })
      return { previous }
    },
    onError: (err, _vars, context) => {
      qc.setQueryData(KEY, context?.previous)
      toast(err.message, 'error')
    },
    onSuccess: replace,
  })

  const verify = useMutation({
    mutationFn: (url: string) =>
      api<PlanVerify>(`/api/plans/${plan.id}/verify`, { method: 'POST', body: json({ repo_url: url }) }),
    onSuccess: (result) => {
      replace(result.plan)
      if (result.verified) {
        if (!calm) setCelebrate(true)
        toast(`${plan.skill} is now in your ledger, proven by your repo.`)
        void qc.invalidateQueries({ queryKey: ['ledger'] })
        void qc.invalidateQueries({ queryKey: ['jobs'] })
        void qc.invalidateQueries({ queryKey: ['activity'] })
      } else {
        toast('Not every check passed yet. See what is missing below.', 'error')
      }
    },
    onError: (err) => toast(err.message, 'error'),
  })

  const remove = useMutation({
    mutationFn: () => api<void>(`/api/plans/${plan.id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.setQueryData<Plan[]>(KEY, (rows) => rows?.filter((p) => p.id !== plan.id))
      toast('Plan deleted.')
    },
    onError: (err) => toast(err.message, 'error'),
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (repo.trim()) verify.mutate(repo.trim())
  }

  const verified = plan.status === 'verified'
  return (
    <article id={`plan-${plan.id}`} className={cx('relative scroll-mt-24', focused && 'rounded-2xl ring-4 ring-violet')}>
      <AnimatePresence>{celebrate && <LevelUp skill={plan.skill} />}</AnimatePresence>
      <Panel className="flex flex-col gap-4">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="flex items-center gap-2 font-mono text-xs font-medium uppercase tracking-widest">
              <Sprout className="size-4" aria-hidden /> {plan.skill}
            </p>
            <h2 className="font-display text-3xl tracking-wide">{plan.title}</h2>
            <p className="mt-1 max-w-3xl text-sm">{plan.why}</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-full border-2 border-ink bg-white px-2 py-0.5 font-mono text-xs">
              <Clock className="size-3.5" aria-hidden /> about {plan.hours} h
            </span>
            <StatusBadge status={plan.status} />
          </div>
        </header>

        <Progress plan={plan} calm={calm} />

        <div className="grid gap-5 md:grid-cols-2">
          <section>
            <h3 className="mb-2 font-semibold">Steps</h3>
            <ol className="flex flex-col gap-2">
              {plan.steps.map((s, index) => (
                <li key={index}>
                  <label className="flex cursor-pointer items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4 accent-verified"
                      checked={s.done}
                      onChange={(e) => step.mutate({ index, done: e.target.checked })}
                    />
                    <span className={cx(s.done && 'line-through decoration-2')}>{s.text}</span>
                  </label>
                </li>
              ))}
            </ol>
            {plan.also_new.length > 0 && (
              <p className="mt-2 text-xs">
                <span className="font-semibold">Also new to you:</span> {plan.also_new.join(', ')}. Only{' '}
                {plan.skill} is checked.
              </p>
            )}
            {plan.written_by === 'template' && (
              <p className="mt-2 text-xs">A standard plan: the AI draft didn't meet the writing rules or was unavailable.</p>
            )}
          </section>

          <section>
            <h3 className="mb-2 font-semibold">What Power Hunt will check on GitHub</h3>
            <ul className="flex flex-col gap-1.5 text-sm">
              {plan.acceptance.map((criterion, i) => {
                const result = plan.checks[i]
                return (
                  <li key={criterion} className="flex items-start gap-2">
                    {result ? (
                      result.passed ? (
                        <CircleCheck className="mt-0.5 size-4 shrink-0 text-verified" aria-label="Passed" />
                      ) : (
                        <CircleX className="mt-0.5 size-4 shrink-0 text-heart" aria-label="Not yet" />
                      )
                    ) : (
                      <span className="mt-1.5 size-2 shrink-0 rounded-full bg-ink" aria-hidden />
                    )}
                    <span>
                      {criterion}
                      {result && !result.passed && <span className="block text-xs font-semibold">{result.detail}</span>}
                    </span>
                  </li>
                )
              })}
            </ul>
          </section>
        </div>

        {verified ? (
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            <Check className="size-4" aria-hidden /> Proven by
            {plan.repo_url && (
              <a href={plan.repo_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline">
                {plan.repo_url.replace('https://github.com/', '')} <ExternalLink className="size-3.5" aria-hidden />
              </a>
            )}
            . Future applications show {plan.skill} as Verified.
          </p>
        ) : (
          <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
            <label className="flex min-w-64 flex-1 flex-col gap-1 text-sm font-semibold">
              Your repo for this plan
              <input
                type="url"
                inputMode="url"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="https://github.com/you/project"
                maxLength={300}
                className="rounded-lg border-3 border-ink bg-white px-3 py-2 font-normal"
              />
            </label>
            <Button type="submit" disabled={!repo.trim() || verify.isPending}>
              <GitBranch className="size-4" aria-hidden /> {verify.isPending ? 'Checking…' : 'Verify on GitHub'}
            </Button>
          </form>
        )}

        <Ideas plan={plan} onChange={replace} />

        <footer className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span>
            {plan.job_id && plan.job_title ? (
              <>
                From a gap in{' '}
                <Link to={`/jobs/${plan.job_id}`} className="font-semibold underline">
                  {plan.job_title}
                </Link>
                {' · '}
                <Link to={`/jobs/${plan.job_id}/workspace`} className="font-semibold underline">
                  Back to the Tailor
                </Link>
              </>
            ) : (
              'Made by the Coach'
            )}
          </span>
          <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => remove.mutate()} disabled={remove.isPending}>
            <Trash2 className="size-3.5" aria-hidden /> Delete plan
          </Button>
        </footer>
      </Panel>
    </article>
  )
}

export default function Plans() {
  const location = useLocation()
  const plans = useQuery({ queryKey: KEY, queryFn: () => api<Plan[]>('/api/plans') })
  const focused = Number(location.hash.replace('#plan-', '')) || null

  useEffect(() => {
    if (focused && plans.data) document.getElementById(`plan-${focused}`)?.scrollIntoView({ block: 'start' })
  }, [focused, plans.data])

  const rows = plans.data ?? []
  const verified = rows.filter((p) => p.status === 'verified').length
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Growth plans"
        aside={<Stat>{plans.isPending ? '…' : `${rows.length} plans · ${verified} verified`}</Stat>}
      >
        A skill gap becomes a plan, not a lie. Build the project, link the repo, and Power Hunt checks it on GitHub.
        When every check passes, the skill joins your ledger.
      </PageHeader>

      {plans.isPending && <Skeleton className="h-64" />}
      {plans.error && <ErrorNote>{plans.error.message}</ErrorNote>}
      {plans.data && rows.length === 0 && (
        <Panel>
          <p className="font-semibold">No plans yet.</p>
          <p className="mt-1 text-sm">
            Open a job from{' '}
            <Link to="/jobs" className="font-semibold underline">
              Jobs
            </Link>{' '}
            and click a skill your ledger can't prove yet. The Coach turns it into a small project.
          </p>
        </Panel>
      )}
      {rows.map((plan) => (
        <PlanCard key={plan.id} plan={plan} focused={plan.id === focused} />
      ))}
    </div>
  )
}
