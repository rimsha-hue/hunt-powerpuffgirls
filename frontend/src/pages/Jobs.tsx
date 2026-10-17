import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Link as LinkIcon, Radar, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { MethodBadge, ScoreChip } from '../components/JobBits'
import { Button, ErrorNote, FilterChips, Skeleton } from '../components/ui'
import { api, json, STATUS_LABELS, type Job, type ScoutRun } from '../lib/api'
import { useCalm } from '../lib/calm'
import { postedLabel } from '../lib/time'
import { useToast } from '../lib/toast'

type View = 'open' | 'applied' | 'skipped' | 'closed' | 'all'
type Method = 'all' | 'email' | 'form'
type MinMatch = '0' | '50' | '70'

const VIEWS: Array<{ value: View; label: string }> = [
  { value: 'open', label: 'Open' },
  { value: 'applied', label: 'Applied' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'closed', label: 'Closed' },
  { value: 'all', label: 'All' },
]
const METHODS: Array<{ value: Method; label: string }> = [
  { value: 'all', label: 'Any method' },
  { value: 'email', label: 'Email' },
  { value: 'form', label: 'Form' },
]
const MIN_MATCH: Array<{ value: MinMatch; label: string }> = [
  { value: '0', label: 'Any match' },
  { value: '50', label: '50+' },
  { value: '70', label: '70+' },
]

function summary(run: ScoutRun): string {
  const found = run.new_jobs === 1 ? '1 new job' : `${run.new_jobs} new jobs`
  const parts = [run.new_jobs ? `Found ${found}.` : 'No new jobs this time.']
  if (run.used_recorded) parts.push('Live job sources were down, so these come from recorded postings.')
  if (run.digest_sent) parts.push(`Digest emailed with ${run.digest_sent} Apply link${run.digest_sent === 1 ? '' : 's'}.`)
  else if (run.digest_note) parts.push(run.digest_note)
  return parts.join(' ')
}

/** Speed lines across the header while the Scout runs: decorative, never delays the result. */
function SpeedLines() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      {[18, 38, 58, 78].map((top, i) => (
        <motion.span
          key={top}
          className="absolute left-0 h-1 w-1/3 rounded-full bg-ink/70"
          style={{ top: `${top}%` }}
          initial={{ x: '-40%', opacity: 0 }}
          animate={{ x: '320%', opacity: [0, 1, 0] }}
          transition={{ duration: 0.7, delay: i * 0.08, ease: 'easeOut' }}
        />
      ))}
    </div>
  )
}

function JobCard({ job, index, onSkip }: { job: Job; index: number; onSkip: (job: Job) => void }) {
  const calm = useCalm()
  const navigate = useNavigate()
  const place = job.remote && !job.location ? 'Remote' : job.location
  return (
    <motion.article
      layout={!calm}
      initial={calm ? false : { opacity: 0, x: -24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={calm ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.25, delay: calm ? 0 : Math.min(index, 12) * 0.06 }}
      className="panel flex flex-col gap-3 bg-card p-4"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <Link to={`/jobs/${job.id}`} className="text-lg font-bold leading-snug hover:underline">
            {job.title}
          </Link>
          <p className="mt-0.5 text-sm font-semibold">
            {job.company}
            {place && <span className="font-normal"> · {place}</span>}
          </p>
        </div>
        <ScoreChip label="Match" value={job.match_score} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <MethodBadge method={job.apply_method} />
        <span className="rounded-full border-2 border-ink bg-white px-2 py-0.5 font-mono text-xs font-medium">
          ATS {job.ats_before}
        </span>
        {job.status !== 'new' && (
          <span className="rounded-full border-2 border-ink bg-ink px-2 py-0.5 font-mono text-xs font-medium uppercase text-card">
            {STATUS_LABELS[job.status]}
          </span>
        )}
        <span
          className="ml-auto font-mono text-xs"
          title={job.posted_at ? 'When the job source says it was posted' : 'The source gives no posting date'}
        >
          {job.posted_at ? `posted ${postedLabel(job.posted_at)}` : `found ${postedLabel(job.fetched_at)}`}
        </span>
      </div>

      {job.missing_skills.length > 0 && (
        <p className="text-sm">
          <span className="font-semibold">Gaps: </span>
          {job.missing_skills.slice(0, 4).join(', ')}
          {job.missing_skills.length > 4 && ` +${job.missing_skills.length - 4}`}
        </p>
      )}

      <div className="mt-auto flex gap-2 pt-1">
        <Button className="flex-1" onClick={() => navigate(`/jobs/${job.id}`)}>
          {job.status === 'new' ? 'Apply now' : 'Open'}
          <ArrowRight className="size-4" aria-hidden />
        </Button>
        {job.status === 'new' && (
          <Button variant="ghost" onClick={() => onSkip(job)} aria-label={`Skip ${job.title}`}>
            <X className="size-4" aria-hidden />
            Skip
          </Button>
        )}
      </div>
    </motion.article>
  )
}

export default function Jobs() {
  const [view, setView] = useState<View>('open')
  const [method, setMethod] = useState<Method>('all')
  const [minMatch, setMinMatch] = useState<MinMatch>('0')
  const [sweep, setSweep] = useState(0)
  const [link, setLink] = useState('')
  const navigate = useNavigate()
  const calm = useCalm()
  const qc = useQueryClient()
  const toast = useToast()
  const [postedFrom, setPostedFrom] = useState('')
  const [postedTo, setPostedTo] = useState('')
  const key = ['jobs', view, method, minMatch, postedFrom, postedTo] as const

  const jobs = useQuery({
    queryKey: key,
    queryFn: () => {
      const params = new URLSearchParams({ view, min_match: minMatch })
      if (method !== 'all') params.set('method', method)
      if (postedFrom) params.set('posted_from', postedFrom)
      if (postedTo) params.set('posted_to', postedTo)
      return api<Job[]>(`/api/jobs?${params.toString()}`)
    },
    placeholderData: keepPreviousData,
  })

  const fetchNow = useMutation({
    mutationFn: () => api<ScoutRun>('/api/jobs/fetch', { method: 'POST' }),
    onMutate: () => setSweep((n) => n + 1),
    onSuccess: (run) => {
      toast(summary(run))
      void qc.invalidateQueries({ queryKey: ['jobs'] })
    },
    onError: (err) => toast(err.message, 'error'),
  })

  const skip = useMutation({
    mutationFn: (job: Job) => api<Job>(`/api/jobs/${job.id}/status`, { method: 'POST', body: json({ status: 'skipped' }) }),
    // Optimistic: the card leaves at once and returns if the server refuses.
    onMutate: async (job) => {
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData<Job[]>(key)
      if (view === 'open') qc.setQueryData<Job[]>(key, (rows) => rows?.filter((r) => r.id !== job.id))
      return { previous }
    },
    onError: (err, _job, context) => {
      qc.setQueryData(key, context?.previous)
      toast(err.message, 'error')
    },
    onSuccess: () => toast('Skipped. Find it under Skipped if you change your mind.'),
    onSettled: () => void qc.invalidateQueries({ queryKey: ['jobs'] }),
  })

  const addByLink = useMutation({
    mutationFn: (url: string) => api<Job>('/api/jobs/import', { method: 'POST', body: json({ url }) }),
    onSuccess: (job) => {
      setLink('')
      void qc.invalidateQueries({ queryKey: ['jobs'] })
      navigate(`/jobs/${job.id}`)
    },
    onError: (err) => toast(err.message, 'error'),
  })

  const rows = jobs.data ?? []
  const filtered = view !== 'open' || method !== 'all' || minMatch !== '0' || Boolean(postedFrom || postedTo)

  return (
    <div className="flex flex-col gap-6">
      <section className="panel hearts relative flex flex-wrap items-end justify-between gap-4 overflow-hidden p-6">
        {!calm && <SpeedLines key={sweep} />}
        <div className="relative">
          <h1 className="font-display text-5xl tracking-wide">Jobs</h1>
          <p className="mt-1 max-w-2xl font-semibold">
            The Scout searches your target roles and places, scores each job against your evidence ledger, and
            emails you the strong matches. Nothing is sent to an employer until you approve it.
          </p>
        </div>
        <Button
          className="relative bg-signal"
          onClick={() => fetchNow.mutate()}
          disabled={fetchNow.isPending}
          aria-busy={fetchNow.isPending}
        >
          <Radar className="size-5" aria-hidden />
          {fetchNow.isPending ? 'Scouting…' : 'Fetch now'}
        </Button>
      </section>

      <div className="flex flex-col gap-2">
        <FilterChips label="Which jobs" options={VIEWS} value={view} onChange={setView} />
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          <FilterChips label="Apply method" options={METHODS} value={method} onChange={setMethod} />
          <FilterChips label="Minimum match" options={MIN_MATCH} value={minMatch} onChange={setMinMatch} />
          <fieldset className="flex flex-wrap items-center gap-2 text-sm">
            <legend className="sr-only">Posted between</legend>
            <span className="font-semibold">Posted</span>
            <label className="flex items-center gap-1">
              from
              <input
                type="date"
                value={postedFrom}
                max={postedTo || undefined}
                onChange={(e) => setPostedFrom(e.target.value)}
                className="rounded-lg border-2 border-ink bg-white px-2 py-0.5 font-mono text-xs"
              />
            </label>
            <label className="flex items-center gap-1">
              to
              <input
                type="date"
                value={postedTo}
                min={postedFrom || undefined}
                onChange={(e) => setPostedTo(e.target.value)}
                className="rounded-lg border-2 border-ink bg-white px-2 py-0.5 font-mono text-xs"
              />
            </label>
            {(postedFrom || postedTo) && (
              <button
                type="button"
                className="text-xs font-semibold underline"
                onClick={() => {
                  setPostedFrom('')
                  setPostedTo('')
                }}
              >
                Any date
              </button>
            )}
          </fieldset>
        </div>
        {view === 'open' && (
          <p className="text-xs text-ink/70">
            Open jobs are checked on their own site before you see them; ones that stopped taking applications move to
            Closed.
          </p>
        )}
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (link.trim()) addByLink.mutate(link.trim())
        }}
        className="flex flex-col gap-2 sm:flex-row"
      >
        <label className="sr-only" htmlFor="job-link">
          Greenhouse job link
        </label>
        <input
          id="job-link"
          type="url"
          value={link}
          onChange={(e) => setLink(e.target.value)}
          placeholder="Found a job yourself? Paste its Greenhouse link"
          className="min-w-0 flex-1 rounded-lg border-3 border-ink bg-white px-3 py-2"
        />
        <Button type="submit" variant="secondary" disabled={addByLink.isPending || !link.trim()}>
          <LinkIcon className="size-4" aria-hidden />
          {addByLink.isPending ? 'Adding…' : 'Add job'}
        </Button>
      </form>

      {jobs.error && <ErrorNote>{jobs.error.message}</ErrorNote>}

      {jobs.isPending ? (
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-52" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="panel bg-card p-8 text-center">
          <p className="font-display text-3xl tracking-wide">{filtered ? 'Nothing here' : 'No jobs yet'}</p>
          {!filtered && (
            <>
              <p className="mx-auto mt-2 max-w-md font-semibold">
                Press Fetch now and the Scout searches for your target roles. Set roles and places in{' '}
                <Link to="/onboarding" className="underline">
                  Onboarding
                </Link>
                .
              </p>
              <Button className="mt-4 bg-signal" onClick={() => fetchNow.mutate()} disabled={fetchNow.isPending}>
                <Radar className="size-5" aria-hidden />
                Fetch now
              </Button>
            </>
          )}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <AnimatePresence mode="popLayout">
            {rows.map((job, index) => (
              <JobCard key={job.id} job={job} index={index} onSkip={(j) => skip.mutate(j)} />
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}
