import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Check,
  CircleHelp,
  Download,
  Eye,
  EyeOff,
  Link2,
  Pencil,
  ShieldCheck,
  Trash2,
  X,
  type LucideIcon,
} from 'lucide-react'
import { animate, motion, useMotionValue, useSpring, useTransform } from 'motion/react'
import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { EmailPanel } from '../components/EmailPanel'
import { FormPanel } from '../components/FormPanel'
import { Highlighted } from '../components/Highlighted'
import { SkillGap } from '../components/PlanButton'
import { Button, ErrorNote, FilterChips, Panel, PanelTitle, Skeleton } from '../components/ui'
import {
  api,
  ApiError,
  json,
  type Application,
  type Claim,
  type ClaimAction,
  type ClaimBucket,
  type CvBlock,
  type CvCheck,
  type CvTemplate,
  type JobDetail,
} from '../lib/api'
import { useCalm } from '../lib/calm'
import { cx } from '../lib/cx'
import { useToast } from '../lib/toast'

// Badge colours from the palette: ink on verified 4.9:1, card on inferred 5.4:1, ink on blossom 5.6:1.
const BADGES: Record<'verified' | 'inferred' | 'needs_you', { label: string; icon: LucideIcon; style: string }> = {
  verified: { label: 'Verified', icon: Check, style: 'bg-verified text-ink' },
  inferred: { label: 'Inferred', icon: Link2, style: 'bg-inferred text-card' },
  needs_you: { label: 'Needs you', icon: CircleHelp, style: 'bg-blossom text-ink' },
}

function isShown(bucket: ClaimBucket): bucket is 'verified' | 'inferred' | 'needs_you' {
  return bucket === 'verified' || bucket === 'inferred' || bucket === 'needs_you'
}

/** A claim inside its sentence, with a badge whose speech-bubble tooltip names the source. */
function ClaimMark({ claim, text, fresh }: { claim: Claim; text: string; fresh: boolean }) {
  const calm = useCalm()
  if (!isShown(claim.bucket)) return <>{text}</>
  const badge = BADGES[claim.bucket]
  const Icon = badge.icon
  const stamp = !calm && claim.bucket === 'verified' && claim.user_decision === 'confirmed'
  const caught = !calm && fresh && claim.bucket === 'needs_you'
  return (
    <span className="group relative inline">
      <span
        tabIndex={0}
        className={cx('rounded px-0.5 font-semibold underline decoration-2 underline-offset-2', badge.style)}
      >
        {text}
        <motion.span
          key={claim.bucket}
          initial={stamp ? { scale: 1.4, rotate: -8 } : false}
          animate={{ scale: 1, rotate: 0 }}
          transition={{ duration: 0.18 }}
          className="ml-0.5 inline-flex translate-y-0.5 align-baseline"
        >
          <Icon className="size-3.5" strokeWidth={3} aria-hidden />
        </motion.span>
        <span className="sr-only"> ({badge.label})</span>
      </span>
      {caught && (
        <motion.span
          className="pointer-events-none absolute -top-7 left-1/2 z-10 -translate-x-1/2 rounded-md border-3 border-ink bg-signal px-1.5 font-display text-sm tracking-wide text-ink"
          initial={{ scale: 0, rotate: -12, opacity: 1 }}
          animate={{ scale: [0, 1.2, 1], rotate: [-12, 4, 0], opacity: [1, 1, 0] }}
          transition={{ duration: 0.6, times: [0, 0.4, 1] }}
          aria-hidden
        >
          BLOCKED!
        </motion.span>
      )}
      <span
        role="tooltip"
        className="invisible absolute bottom-full left-0 z-20 mb-2 w-72 rounded-xl border-3 border-ink bg-white p-3 text-xs font-normal leading-snug text-ink no-underline shadow-comic-sm group-focus-within:visible group-hover:visible"
      >
        <span className="mb-1 block font-mono font-medium uppercase tracking-wider">
          {badge.label} · {claim.type}
        </span>
        {claim.paired_with && (
          <span className="mb-1 block">Checked together with “{claim.paired_with}”.</span>
        )}
        {claim.source ? (
          <>
            <span className="block font-semibold">{claim.source.label}</span>
            {claim.source.url ? (
              <a href={claim.source.url} target="_blank" rel="noopener noreferrer" className="break-all underline">
                {claim.source.url}
              </a>
            ) : (
              <span className="block">{claim.source.detail}</span>
            )}
          </>
        ) : (
          <span className="block">No source yet. Answer it in the Needs-you panel.</span>
        )}
      </span>
    </span>
  )
}

/** Claims can overlap ("Senior backend and AI engineer" around the skill "AI"). Only one badge fits
 * a stretch of text, so a Needs-you claim wins, then the longer claim; the rest stay in the panel. */
function marksToShow(claims: Claim[], length: number): Claim[] {
  const candidates = claims
    .filter((c) => isShown(c.bucket) && c.span_end > c.span_start && c.span_end <= length)
    .sort(
      (a, b) =>
        Number(b.bucket === 'needs_you') - Number(a.bucket === 'needs_you') ||
        b.span_end - b.span_start - (a.span_end - a.span_start),
    )
  const kept: Claim[] = []
  for (const claim of candidates) {
    if (!kept.some((k) => claim.span_start < k.span_end && k.span_start < claim.span_end)) kept.push(claim)
  }
  return kept.sort((a, b) => a.span_start - b.span_start)
}

function BlockText({ block, claims, fresh }: { block: CvBlock; claims: Claim[]; fresh: boolean }) {
  const parts: ReactNode[] = []
  let cursor = 0
  for (const claim of marksToShow(claims, block.text.length)) {
    parts.push(block.text.slice(cursor, claim.span_start))
    parts.push(
      <ClaimMark key={claim.id} claim={claim} text={block.text.slice(claim.span_start, claim.span_end)} fresh={fresh} />,
    )
    cursor = claim.span_end
  }
  parts.push(block.text.slice(cursor))
  return <>{parts}</>
}

function TailoredCv({ app, showOriginal, fresh }: { app: Application; showOriginal: boolean; fresh: boolean }) {
  const byBlock = new Map<number, Claim[]>()
  for (const claim of app.claims.filter((c) => c.document === 'cv')) byBlock.set(claim.block_index, [...(byBlock.get(claim.block_index) ?? []), claim])
  return (
    <div className="flex flex-col gap-1 text-sm leading-relaxed">
      {app.blocks.map((block, index) => {
        if (!block.text && !showOriginal) return null
        const claims = byBlock.get(index) ?? []
        if (block.kind === 'heading')
          return (
            <h3 key={index} className="mt-3 border-b-2 border-ink/30 font-bold uppercase tracking-wide">
              {block.text}
            </h3>
          )
        if (block.kind === 'title')
          return (
            <p key={index} className="mt-1 font-bold">
              {block.text}
            </p>
          )
        if (block.kind === 'bullet')
          return (
            <div key={index} className={cx('flex gap-2', block.section === 'summary' && 'pl-0')}>
              {block.section !== 'summary' && <span aria-hidden>•</span>}
              <div>
                {showOriginal && block.rewritten && (
                  <p className="mb-1 rounded-md border-2 border-dashed border-ink/40 bg-card px-2 py-1 text-ink/80">
                    <span className="mr-1 font-mono text-[11px] font-semibold uppercase">Your words</span>
                    <del className="decoration-ink/40">{block.original}</del>
                  </p>
                )}
                {block.text ? (
                  <BlockText block={block} claims={claims} fresh={fresh} />
                ) : (
                  <span className="italic">Removed.</span>
                )}
              </div>
            </div>
          )
        return (
          <p key={index} className={cx(block.section === 'header' && index === 0 && 'text-lg font-extrabold')}>
            {block.text}
          </p>
        )
      })}
    </div>
  )
}

/** Before-to-after ATS gauge: the needle sweeps with a spring and the number counts up. */
function AtsGauge({ before, after }: { before: number; after: number }) {
  const calm = useCalm()
  const value = useMotionValue(calm ? after : before)
  const spring = useSpring(value, { stiffness: 90, damping: 16 })
  const angle = useTransform(spring, (v) => -90 + (v / 100) * 180)
  const label = useTransform(spring, (v) => Math.round(v).toString())
  useEffect(() => {
    if (calm) {
      value.jump(after)
      return
    }
    const controls = animate(value, after, { duration: 0.6 })
    return () => controls.stop()
  }, [after, calm, value])
  return (
    <div className="flex items-center gap-4">
      <svg viewBox="0 0 120 70" className="w-36" role="img" aria-label={`ATS score ${before} before, ${after} after`}>
        <path d="M10 62 A50 50 0 0 1 110 62" fill="none" stroke="currentColor" strokeOpacity="0.15" strokeWidth="12" />
        <path
          d="M10 62 A50 50 0 0 1 110 62"
          fill="none"
          className="stroke-buttercup"
          strokeWidth="12"
          pathLength={100}
          strokeDasharray={`${after} 100`}
        />
        <motion.line x1="60" y1="62" x2="60" y2="20" stroke="currentColor" strokeWidth="4" strokeLinecap="round" style={{ rotate: angle, originX: '60px', originY: '62px' }} />
        <circle cx="60" cy="62" r="6" fill="currentColor" />
      </svg>
      <div className="font-mono">
        <p className="text-xs uppercase">ATS before → after</p>
        <p className="text-3xl font-bold">
          {before} → <motion.span>{label}</motion.span>
        </p>
      </div>
    </div>
  )
}

const DOCUMENT_LABELS: Record<'cv' | 'email' | 'cover_letter', string> = {
  cv: 'CV',
  email: 'Email',
  cover_letter: 'Cover letter',
}

/** The form answer a claim sits in ("q:3"), if any. */
function answerOf(app: Application, claim: Claim) {
  if (!claim.document.startsWith('q:')) return undefined
  return app.form?.answers[Number(claim.document.slice(2))]
}

function documentLabel(app: Application, claim: Claim): string {
  const answer = answerOf(app, claim)
  if (answer) return `Form: ${answer.label.slice(0, 40)}`
  return DOCUMENT_LABELS[claim.document as keyof typeof DOCUMENT_LABELS] ?? 'Answer'
}

function documentBlocks(app: Application, claim: Claim): CvBlock[] {
  if (claim.document === 'email') return app.email?.blocks ?? []
  if (claim.document === 'cover_letter') return app.email?.cover_letter ?? []
  const answer = answerOf(app, claim)
  if (answer) return answer.blocks
  return app.blocks
}

function NeedsYouCard({
  claim,
  label,
  block,
  busy,
  onAnswer,
  autoFocus,
}: {
  claim: Claim
  label: string
  block: CvBlock | undefined
  busy: boolean
  onAnswer: (action: ClaimAction) => void
  autoFocus: boolean
}) {
  const [mode, setMode] = useState<'idle' | 'edit'>('idle')
  // Edit rewrites the whole sentence the claim sits in; the new sentence is checked again.
  const [wording, setWording] = useState(block?.text ?? claim.text)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (autoFocus) ref.current?.focus()
  }, [autoFocus])

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget || busy) return
    const key = event.key.toLowerCase()
    if (key === 'y') onAnswer({ action: 'confirm' })
    else if (key === 'e') setMode('edit')
    else if (key === 'r') onAnswer({ action: 'remove' })
    else return
    event.preventDefault()
  }
  function edit(event: FormEvent) {
    event.preventDefault()
    onAnswer({ action: 'edit', text: wording, whole: Boolean(block) })
  }

  return (
    <div
      ref={ref}
      tabIndex={0}
      onKeyDown={onKey}
      className="rounded-xl border-3 border-ink bg-white p-3 focus-visible:bg-blush"
      aria-label={`Needs you: ${claim.text}. Press Y to confirm, E to edit, R to remove.`}
    >
      <p className="font-mono text-[11px] font-medium uppercase">
        {label} · {claim.type}
      </p>
      <p className="font-bold">{claim.text}</p>
      {block && <p className="mt-1 text-xs text-ink/80">“{block.text}”</p>}
      {mode === 'edit' && (
        <form onSubmit={edit} className="mt-2 flex flex-col gap-2">
          <textarea
            autoFocus
            rows={3}
            value={wording}
            onChange={(e) => setWording(e.target.value)}
            maxLength={600}
            className="rounded-lg border-3 border-ink px-2 py-1 text-sm"
          />
          <div className="flex gap-2">
            <Button type="submit" disabled={busy || !wording.trim()} className="px-3 py-1 text-sm">
              Save wording
            </Button>
            <Button variant="ghost" onClick={() => setMode('idle')} className="px-3 py-1 text-sm">
              Cancel
            </Button>
          </div>
        </form>
      )}
      {mode === 'idle' && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => onAnswer({ action: 'confirm' })} disabled={busy}>
            <Check className="size-3.5" aria-hidden /> Confirm <kbd className="font-mono">Y</kbd>
          </Button>
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => setMode('edit')} disabled={busy}>
            <Pencil className="size-3.5" aria-hidden /> Edit <kbd className="font-mono">E</kbd>
          </Button>
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => onAnswer({ action: 'remove' })} disabled={busy}>
            <Trash2 className="size-3.5" aria-hidden /> Remove <kbd className="font-mono">R</kbd>
          </Button>
          <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => onAnswer({ action: 'false' })} disabled={busy}>
            <X className="size-3.5" aria-hidden /> It's false
          </Button>
        </div>
      )}
    </div>
  )
}

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border-3 border-ink bg-card px-3 py-1.5 text-center">
      <p className="font-mono text-xl font-bold leading-tight">{value}</p>
      <p className="font-mono text-[10px] uppercase leading-tight">{label}</p>
    </div>
  )
}

const TEMPLATES: Array<{ value: CvTemplate; label: string }> = [
  { value: 'classic', label: 'Harvard (serif)' },
  { value: 'compact', label: "Compact (Jake's)" },
  { value: 'modern', label: 'Modern (accent)' },
]

/** Template choice, the format checks on the rendered PDF, and the download. */
function CvDownload({ jobId }: { jobId: number }) {
  const [template, setTemplate] = useState<CvTemplate>('classic')
  const check = useQuery({
    queryKey: ['cv-check', jobId, template],
    queryFn: () => api<CvCheck>(`/api/jobs/${jobId}/application/cv-check?template=${template}`),
  })
  const used = check.data?.template ?? template
  return (
    <div className="flex flex-col gap-3">
      <FilterChips label="CV template" options={TEMPLATES} value={template} onChange={setTemplate} />
      {check.isPending ? (
        <p className="text-sm">Checking the PDF…</p>
      ) : check.data ? (
        <ul className="flex flex-col gap-1 text-sm">
          {check.data.checks.map((c) => (
            <li key={c.name} className="flex items-start gap-1.5">
              {c.passed ? (
                <ShieldCheck className="mt-0.5 size-4 shrink-0 text-verified" aria-label="Passed" />
              ) : (
                <X className="mt-0.5 size-4 shrink-0" aria-label="Failed" />
              )}
              {c.detail}
            </li>
          ))}
          {used !== template && (
            <li className="font-semibold">
              {TEMPLATES.find((t) => t.value === template)?.label} didn't pass, so the download uses{' '}
              {TEMPLATES.find((t) => t.value === used)?.label}.
            </li>
          )}
        </ul>
      ) : null}
      <a
        href={`/api/jobs/${jobId}/application/cv.pdf?template=${template}`}
        className="inline-flex items-center gap-2 self-start rounded-lg border-3 border-ink bg-buttercup px-4 py-2 font-semibold shadow-comic-sm"
      >
        <Download className="size-4" aria-hidden /> Download CV (PDF)
      </a>
    </div>
  )
}

export default function Workspace() {
  const { id } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const [showOriginal, setShowOriginal] = useState(false)
  const [fresh, setFresh] = useState(false)
  const [method, setMethod] = useState<'email' | 'form' | null>(null)
  const appKey = ['application', id] as const

  const job = useQuery({ queryKey: ['job', id], queryFn: () => api<JobDetail>(`/api/jobs/${id}`) })
  const application = useQuery({
    queryKey: appKey,
    queryFn: async () => {
      try {
        return await api<Application>(`/api/jobs/${id}/application`)
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null
        throw err
      }
    },
  })

  const [preferences, setPreferences] = useState('')
  const tailor = useMutation({
    mutationFn: (wanted: string = '') =>
      api<Application>(`/api/jobs/${id}/tailor`, { method: 'POST', body: json({ preferences: wanted }) }),
    onSuccess: (app) => {
      qc.setQueryData(appKey, app)
      setFresh(true)
      window.setTimeout(() => setFresh(false), 700)
      void qc.invalidateQueries({ queryKey: ['job', id] })
      void qc.invalidateQueries({ queryKey: ['jobs'] })
    },
    onError: (err) => toast(err.message, 'error'),
  })
  const answer = useMutation({
    mutationFn: ({ claim, action }: { claim: Claim; action: ClaimAction }) =>
      api<Application>(`/api/claims/${claim.id}/decision`, { method: 'POST', body: json(action) }),
    onSuccess: (app) => qc.setQueryData(appKey, app),
    onError: (err) => toast(err.message, 'error'),
  })
  const approve = useMutation({
    mutationFn: () => api<Application>(`/api/jobs/${id}/application/approve`, { method: 'POST' }),
    onSuccess: (app) => {
      qc.setQueryData(appKey, app)
      toast('CV approved. Download it below.')
    },
    onError: (err) => toast(err.message, 'error'),
  })

  // First visit: tailor straight away rather than showing an empty page.
  const started = useRef(false)
  useEffect(() => {
    if (application.data === null && !started.current && !tailor.isPending) {
      started.current = true
      tailor.mutate('')
    }
  }, [application.data, tailor])

  if (job.error) return <ErrorNote>{job.error.message}</ErrorNote>
  if (application.error) return <ErrorNote>{application.error.message}</ErrorNote>
  const app = application.data
  if (!job.data || !app) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-32" />
        <p className="text-center font-display text-2xl tracking-wide">
          {tailor.isPending ? 'The Tailor is writing, the Verifier is checking…' : 'Loading…'}
        </p>
        {tailor.error && (
          <Button className="mx-auto" onClick={() => tailor.mutate('')}>
            Try again
          </Button>
        )}
        <div className="grid gap-4 lg:grid-cols-3">
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      </div>
    )
  }

  const j = job.data
  const open = app.claims.filter((c) => c.bucket === 'needs_you')
  const blocked = app.claims.filter((c) => c.bucket === 'blocked')
  const approved = app.status !== 'draft'
  // Email when the posting asks for it (or the email was already drafted), else the form.
  const route = method ?? (app.email || (j.apply_method === 'email' && !app.form) ? 'email' : 'form')
  const sentenceRenderer = (claims: Claim[]) => (block: CvBlock, index: number) => (
    <BlockText block={block} claims={claims.filter((c) => c.block_index === index)} fresh={fresh} />
  )

  return (
    <div className="flex flex-col gap-6">
      <Link to={`/jobs/${j.id}`} className="inline-flex w-fit items-center gap-1 text-sm font-semibold hover:underline">
        <ArrowLeft className="size-4" aria-hidden /> Back to the job
      </Link>

      <section className="panel hearts flex flex-wrap items-center gap-5 p-6">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-xs font-medium uppercase tracking-widest">Apply Workspace</p>
          <h1 className="mt-1 text-3xl font-extrabold leading-tight">{j.title}</h1>
          <p className="text-lg font-semibold">{j.company}</p>
        </div>
        <AtsGauge before={app.ats.before} after={app.ats.after} />
        <div className="flex flex-wrap gap-2">
          <Counter label="Checked by the squad" value={app.checked_automatically} />
          <Counter label="Questions asked" value={app.questions_asked} />
          <Counter label="Unverified shipped" value={0} />
        </div>
      </section>

      {app.note && (
        <p className="rounded-lg border-3 border-ink bg-signal px-4 py-2 text-sm font-semibold">{app.note}</p>
      )}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.3fr)_minmax(0,1fr)]">
        <Panel className="min-w-0">
          <PanelTitle kicker="The job">What they want</PanelTitle>
          <div className="max-h-[40rem] overflow-y-auto whitespace-pre-line break-words text-sm leading-relaxed">
            <Highlighted text={j.description || 'No description.'} keywords={j.keywords} />
          </div>
        </Panel>

        <Panel className="min-w-0">
          <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
            <PanelTitle kicker="Tailor + Verifier">Your tailored CV</PanelTitle>
            <Button
              variant="ghost"
              className="px-2 py-1 text-xs"
              aria-pressed={showOriginal}
              onClick={() => setShowOriginal((v) => !v)}
            >
              {showOriginal ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
              {showOriginal
                ? 'Hide your words'
                : `Compare with your words (${app.blocks.filter((b) => b.kind === 'bullet' && b.rewritten).length} changed)`}
            </Button>
          </div>
          <p className="mb-3 flex flex-wrap gap-2 text-xs">
            {(['verified', 'inferred', 'needs_you'] as const).map((bucket) => {
              const { label, icon: Icon, style } = BADGES[bucket]
              return (
                <span key={bucket} className={cx('inline-flex items-center gap-1 rounded-full border-2 border-ink px-2 py-0.5 font-semibold', style)}>
                  <Icon className="size-3.5" aria-hidden /> {label}
                </span>
              )
            })}
          </p>
          <TailoredCv app={app} showOriginal={showOriginal} fresh={fresh} />
        </Panel>

        <div className="flex flex-col gap-6">
          <Panel>
            <PanelTitle kicker="Gate 2: verify">
              {open.length ? `Needs you (${open.length})` : 'All claims checked'}
            </PanelTitle>
            {open.length === 0 ? (
              <p className="flex items-center gap-2 text-sm font-semibold">
                <ShieldCheck className="size-5" aria-hidden /> Every claim has a source or was removed.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                <p className="text-xs">
                  Confirm it's true, edit the sentence, or remove it. Keys: Y, E, R.
                </p>
                {open.map((claim, i) => (
                  <NeedsYouCard
                    key={claim.id}
                    claim={claim}
                    label={documentLabel(app, claim)}
                    block={documentBlocks(app, claim)[claim.block_index]}
                    busy={answer.isPending}
                    autoFocus={i === 0 && !answer.isPending}
                    onAnswer={(action) => answer.mutate({ claim, action })}
                  />
                ))}
              </div>
            )}
            {blocked.length > 0 && (
              <p className="mt-3 text-xs">
                <span className="font-semibold">Removed automatically</span> (you marked these false before):{' '}
                {blocked.map((c) => c.text).join(', ')}
              </p>
            )}
          </Panel>

          <Panel>
            <PanelTitle kicker="Approve">Your CV</PanelTitle>
            {approved ? (
              <CvDownload jobId={j.id} />
            ) : (
              <div className="flex flex-col gap-2">
                <Button onClick={() => approve.mutate()} disabled={open.length > 0 || approve.isPending}>
                  <ShieldCheck className="size-4" aria-hidden /> Approve this CV
                </Button>
                {open.length > 0 && <p className="text-xs">Answer every Needs-you claim first.</p>}
                <form
                  className="mt-2 flex flex-col gap-2 border-t-2 border-ink/20 pt-3"
                  onSubmit={(event) => {
                    event.preventDefault()
                    tailor.mutate(preferences.trim())
                  }}
                >
                  <label htmlFor="preferences" className="text-sm font-semibold">
                    Revise with your instructions
                  </label>
                  <textarea
                    id="preferences"
                    rows={3}
                    value={preferences}
                    onChange={(e) => setPreferences(e.target.value)}
                    placeholder="For example: lead with the RAG work, keep bullets shorter, don't mention Flutter."
                    className="rounded-lg border-3 border-ink bg-white px-3 py-2 text-sm"
                  />
                  <p className="text-xs">The Tailor follows these only within your facts; every line is checked again.</p>
                  <Button type="submit" variant="secondary" className="text-sm" disabled={tailor.isPending}>
                    {tailor.isPending ? 'Revising…' : preferences.trim() ? 'Revise the CV' : 'Tailor again'}
                  </Button>
                </form>
              </div>
            )}
          </Panel>

          {j.missing_skills.length > 0 && (
            <Panel>
              <PanelTitle kicker="Skill gaps">Not in your ledger yet</PanelTitle>
              <p className="text-sm">
                Power Hunt never adds these to your CV. Click one and the Coach turns it into a small project you can
                prove on GitHub.
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {j.missing_skills.map((skill) => (
                  <SkillGap key={skill} skill={skill} jobId={j.id} />
                ))}
              </div>
            </Panel>
          )}
        </div>
      </div>

      {approved && (
        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="How to apply">
            {(['email', 'form'] as const).map((choice) => (
              <button
                key={choice}
                type="button"
                role="tab"
                aria-selected={route === choice}
                onClick={() => setMethod(choice)}
                className={cx(
                  'rounded-full border-3 border-ink px-4 py-1.5 text-sm font-semibold',
                  route === choice ? 'bg-ink text-card' : 'bg-card',
                )}
              >
                {choice === 'email' ? 'Apply by email' : 'Apply on their form'}
              </button>
            ))}
            <span className="text-xs">
              The posting suggests {j.apply_method === 'email' ? 'email' : j.apply_method === 'form' ? 'their form' : 'either'}.
            </span>
          </div>
          {route === 'email' ? (
            <EmailPanel app={app} jobId={j.id} renderBlock={sentenceRenderer} />
          ) : (
            <FormPanel app={app} jobId={j.id} formUrl={j.apply_url ?? j.url} renderBlock={sentenceRenderer} />
          )}
        </section>
      )}
    </div>
  )
}
