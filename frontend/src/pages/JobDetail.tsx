import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, Check, ExternalLink, Paperclip, Target, X } from 'lucide-react'
import { motion } from 'motion/react'
import { Link, useParams } from 'react-router'
import { Highlighted } from '../components/Highlighted'
import { MethodBadge, ScoreChip } from '../components/JobBits'
import { Button, ErrorNote, Panel, PanelTitle, Skeleton } from '../components/ui'
import { api, json, type Job, type JobDetail as Detail, type JobKeyword } from '../lib/api'
import { useCalm } from '../lib/calm'
import { cx } from '../lib/cx'
import { useMe } from '../lib/session'
import { postedLabel } from '../lib/time'
import { useToast } from '../lib/toast'

const ATTACHMENTS: Record<string, string> = {
  cv: 'CV',
  cover_letter: 'Cover letter',
  portfolio: 'Portfolio',
  transcript: 'Transcript',
  references: 'References',
  work_samples: 'Work samples',
}
const CHECKS: Record<string, string> = {
  standard_headings: 'Standard section headings',
  contact_info: 'Contact details',
  two_pages_max: 'Two pages or less',
  no_tables_or_images: 'No tables or images',
}

function KeywordChip({ keyword }: { keyword: JobKeyword }) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded-full border-2 border-ink px-2 py-0.5 text-xs font-semibold',
        keyword.found ? 'bg-buttercup' : 'bg-white',
      )}
    >
      {keyword.found ? <Check className="size-3.5" aria-hidden /> : <X className="size-3.5" aria-hidden />}
      <span className="sr-only">{keyword.found ? 'In your CV:' : 'Missing:'}</span>
      {keyword.term}
      {keyword.weight > 1 && <span className="font-mono text-[10px]">×2</span>}
    </span>
  )
}

export default function JobDetail() {
  const { id } = useParams()
  const { data: me } = useMe()
  const scoped = me?.scope_job_id != null
  const qc = useQueryClient()
  const toast = useToast()
  const calm = useCalm()
  const key = ['job', id] as const

  const job = useQuery({ queryKey: key, queryFn: () => api<Detail>(`/api/jobs/${id}`) })

  const choose = useMutation({
    mutationFn: () => api<Job>(`/api/jobs/${id}/apply`, { method: 'POST' }),
    onSuccess: (updated) => {
      qc.setQueryData<Detail>(key, (old) => (old ? { ...old, ...updated } : old))
      void qc.invalidateQueries({ queryKey: ['jobs'] })
    },
    onError: (err) => toast(err.message, 'error'),
  })
  const restore = useMutation({
    mutationFn: (status: 'new' | 'skipped') =>
      api<Job>(`/api/jobs/${id}/status`, { method: 'POST', body: json({ status }) }),
    onSuccess: (updated) => {
      qc.setQueryData<Detail>(key, (old) => (old ? { ...old, ...updated } : old))
      void qc.invalidateQueries({ queryKey: ['jobs'] })
    },
    onError: (err) => toast(err.message, 'error'),
  })

  if (job.isPending) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-36" />
        <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      </div>
    )
  }
  if (job.error) return <ErrorNote>{job.error.message}</ErrorNote>
  const j = job.data
  const place = j.remote && !j.location ? 'Remote' : j.location
  const skills = j.keywords.filter((k) => k.kind === 'skill')
  const phrases = j.keywords.filter((k) => k.kind === 'phrase')
  const chosen = j.status !== 'new' && j.status !== 'skipped'

  return (
    <div className="flex flex-col gap-6">
      {scoped ? (
        <p className="rounded-lg border-3 border-ink bg-signal px-4 py-2 text-sm font-semibold">
          You opened this job from your digest email, so this session covers this job only.{' '}
          <Link to="/login" className="underline">
            Log in
          </Link>{' '}
          to see everything else.
        </p>
      ) : (
        <Link to="/jobs" className="inline-flex w-fit items-center gap-1 text-sm font-semibold hover:underline">
          <ArrowLeft className="size-4" aria-hidden /> All jobs
        </Link>
      )}

      <section className="panel hearts flex flex-wrap items-center gap-5 p-6">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-xs font-medium uppercase tracking-widest">
            {j.source} · posted {postedLabel(j.posted_at ?? j.fetched_at)}
          </p>
          <h1 className="mt-1 text-3xl font-extrabold leading-tight sm:text-4xl">{j.title}</h1>
          <p className="mt-1 text-lg font-semibold">
            {j.company}
            {place && <span className="font-normal"> · {place}</span>}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <MethodBadge method={j.apply_method} />
            <span className="rounded-full border-2 border-ink bg-ink px-2 py-0.5 font-mono text-xs font-medium uppercase text-card">
              {j.status.replace('_', ' ')}
            </span>
          </div>
        </div>
        <div className="flex gap-3">
          <ScoreChip label="Match" value={j.match_score} size="lg" />
          <ScoreChip label="ATS" value={j.ats_before} size="lg" />
        </div>
      </section>

      <div className="grid items-start gap-6 lg:grid-cols-[1fr_22rem]">
        <Panel className="min-w-0">
          <PanelTitle kicker="Job description">What they asked for</PanelTitle>
          <p className="mb-3 text-xs">
            <mark className="rounded bg-buttercup px-1 text-ink">In your CV</mark>{' '}
            <mark className="rounded bg-blush px-1 text-ink underline decoration-dotted">Not in your CV yet</mark>
          </p>
          <div className="max-h-[36rem] overflow-y-auto whitespace-pre-line break-words text-sm leading-relaxed">
            <Highlighted text={j.description || 'The posting has no description.'} keywords={j.keywords} />
          </div>
        </Panel>

        <div className="flex flex-col gap-6">
          <Panel>
            <PanelTitle kicker="Gate 1: you choose">Apply?</PanelTitle>
            {chosen ? (
              <motion.div
                initial={calm ? false : { scale: 1.3, rotate: -4, opacity: 0 }}
                animate={{ scale: 1, rotate: 0, opacity: 1 }}
                transition={{ duration: 0.18 }}
                className="rounded-lg border-3 border-ink bg-buttercup p-3 text-sm font-semibold"
              >
                <Target className="mb-1 size-5" aria-hidden />
                On your list. The Tailor writes a CV for this job and the Verifier checks every claim before anything
                goes out.
                <Link
                  to={`/jobs/${j.id}/workspace`}
                  className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border-3 border-ink bg-blossom px-4 py-2 font-semibold shadow-comic-sm"
                >
                  Open the workspace <ArrowRight className="size-4" aria-hidden />
                </Link>
              </motion.div>
            ) : (
              <div className="flex flex-col gap-2">
                <Button onClick={() => choose.mutate()} disabled={choose.isPending || j.status === 'skipped'}>
                  <Target className="size-4" aria-hidden />
                  Apply now
                </Button>
                {!scoped &&
                  (j.status === 'skipped' ? (
                    <Button variant="secondary" onClick={() => restore.mutate('new')}>
                      Bring it back
                    </Button>
                  ) : (
                    <Button variant="ghost" onClick={() => restore.mutate('skipped')}>
                      Skip this job
                    </Button>
                  ))}
              </div>
            )}
          </Panel>

          <Panel>
            <PanelTitle kicker="How to apply">{j.apply_method === 'email' ? 'By email' : 'On their site'}</PanelTitle>
            {j.apply_email && (
              <p className="mb-2 break-all font-mono text-sm">
                <span className="font-sans font-semibold">To: </span>
                {j.apply_email}
              </p>
            )}
            <a
              href={j.apply_url ?? j.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm font-semibold underline"
            >
              Open the posting <ExternalLink className="size-4" aria-hidden />
            </a>
            <p className="mt-3 mb-1 text-sm font-semibold">They ask for</p>
            <ul className="flex flex-wrap gap-2">
              {j.required_attachments.map((item) => (
                <li
                  key={item}
                  className="inline-flex items-center gap-1 rounded-full border-2 border-ink bg-white px-2 py-0.5 text-xs font-semibold"
                >
                  <Paperclip className="size-3.5" aria-hidden />
                  {ATTACHMENTS[item] ?? item}
                </li>
              ))}
            </ul>
          </Panel>

          <Panel>
            <PanelTitle kicker="Matcher">Fit {j.match_score}</PanelTitle>
            <p className="text-sm">
              From the skills your ledger proves, how well the title fits your target roles, and the place.
            </p>
            {j.missing_skills.length > 0 && (
              <>
                <p className="mt-3 mb-1 text-sm font-semibold">Skills your ledger can't prove yet</p>
                <p className="text-sm">{j.missing_skills.join(', ')}</p>
              </>
            )}
          </Panel>

          <Panel>
            <PanelTitle kicker="ATS before tailoring">ATS {j.ats_before}</PanelTitle>
            <p className="text-sm">
              Keyword coverage {Math.round(j.ats_coverage * 100)}%. Required terms count double (×2).
            </p>
            {skills.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {skills.map((k) => (
                  <KeywordChip key={k.term} keyword={k} />
                ))}
              </div>
            )}
            {phrases.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {phrases.map((k) => (
                  <KeywordChip key={k.term} keyword={k} />
                ))}
              </div>
            )}
            <ul className="mt-3 flex flex-col gap-1 text-sm">
              {Object.entries(j.ats_checks).map(([check, ok]) => (
                <li key={check} className="flex items-center gap-2">
                  {ok ? <Check className="size-4" aria-hidden /> : <X className="size-4" aria-hidden />}
                  <span className="sr-only">{ok ? 'Passed:' : 'Failed:'}</span>
                  {CHECKS[check] ?? check}
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  )
}
