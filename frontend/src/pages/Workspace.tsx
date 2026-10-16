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
import { Highlighted } from '../components/Highlighted'
import { Button, ErrorNote, Panel, PanelTitle, Skeleton } from '../components/ui'
import {
  api,
  ApiError,
  json,
  type Application,
  type Claim,
  type ClaimAction,
  type ClaimBucket,
  type CvBlock,
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

function BlockText({ block, claims, fresh }: { block: CvBlock; claims: Claim[]; fresh: boolean }) {
  const shown = claims
    .filter((c) => isShown(c.bucket) && c.span_end > c.span_start)
    .sort((a, b) => a.span_start - b.span_start)
  const parts: ReactNode[] = []
  let cursor = 0
  for (const claim of shown) {
    if (claim.span_start < cursor || claim.span_end > block.text.length) continue
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
                {block.text ? (
                  <BlockText block={block} claims={claims} fresh={fresh} />
                ) : (
                  <span className="italic">Removed.</span>
                )}
                {showOriginal && block.rewritten && (
                  <p className="mt-0.5 text-xs text-ink/70">
                    <span className="font-mono uppercase">Original: </span>
                    {block.original}
                  </p>
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

const DOCUMENT_LABELS: Record<Claim['document'], string> = {
  cv: 'CV',
  email: 'Email',
  cover_letter: 'Cover letter',
}

function documentBlocks(app: Application, claim: Claim): CvBlock[] {
  if (claim.document === 'email') return app.email?.blocks ?? []
  if (claim.document === 'cover_letter') return app.email?.cover_letter ?? []
  return app.blocks
}

function NeedsYouCard({
  claim,
  block,
  busy,
  onAnswer,
  autoFocus,
}: {
  claim: Claim
  block: CvBlock | undefined
  busy: boolean
  onAnswer: (action: ClaimAction) => void
  autoFocus: boolean
}) {
  const [mode, setMode] = useState<'idle' | 'confirm' | 'edit'>('idle')
  const [proof, setProof] = useState('')
  const [wording, setWording] = useState(claim.text)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (autoFocus) ref.current?.focus()
  }, [autoFocus])

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget || busy) return
    const key = event.key.toLowerCase()
    if (key === 'y') setMode('confirm')
    else if (key === 'e') setMode('edit')
    else if (key === 'r') onAnswer({ action: 'remove' })
    else return
    event.preventDefault()
  }
  function confirm(event: FormEvent) {
    event.preventDefault()
    const value = proof.trim()
    onAnswer(/^https?:\/\//i.test(value) ? { action: 'confirm', url: value } : { action: 'confirm', note: value })
  }
  function edit(event: FormEvent) {
    event.preventDefault()
    onAnswer({ action: 'edit', text: wording })
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
        {DOCUMENT_LABELS[claim.document]} · {claim.type}
      </p>
      <p className="font-bold">{claim.text}</p>
      {block && <p className="mt-1 text-xs text-ink/80">“{block.text}”</p>}
      {mode === 'confirm' && (
        <form onSubmit={confirm} className="mt-2 flex flex-col gap-2">
          <input
            autoFocus
            value={proof}
            onChange={(e) => setProof(e.target.value)}
            maxLength={280}
            placeholder="Link (repo, certificate) or a short note"
            className="rounded-lg border-3 border-ink px-2 py-1 text-sm"
          />
          <div className="flex gap-2">
            <Button type="submit" disabled={busy || !proof.trim()} className="px-3 py-1 text-sm">
              Confirm
            </Button>
            <Button variant="ghost" onClick={() => setMode('idle')} className="px-3 py-1 text-sm">
              Cancel
            </Button>
          </div>
        </form>
      )}
      {mode === 'edit' && (
        <form onSubmit={edit} className="mt-2 flex flex-col gap-2">
          <input
            autoFocus
            value={wording}
            onChange={(e) => setWording(e.target.value)}
            maxLength={300}
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
          <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => setMode('confirm')} disabled={busy}>
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

export default function Workspace() {
  const { id } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const [showOriginal, setShowOriginal] = useState(false)
  const [fresh, setFresh] = useState(false)
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

  const tailor = useMutation({
    mutationFn: () => api<Application>(`/api/jobs/${id}/tailor`, { method: 'POST' }),
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
      tailor.mutate()
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
          <Button className="mx-auto" onClick={() => tailor.mutate()}>
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
            <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setShowOriginal((v) => !v)}>
              {showOriginal ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
              {showOriginal ? 'Hide original' : 'Show original'}
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
                  Confirm with a link or note, edit the wording, or remove it. Keys: Y, E, R. Nothing unproven ships.
                </p>
                {open.map((claim, i) => (
                  <NeedsYouCard
                    key={claim.id}
                    claim={claim}
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
              <a
                href={`/api/jobs/${j.id}/application/cv.pdf`}
                className="inline-flex items-center gap-2 rounded-lg border-3 border-ink bg-buttercup px-4 py-2 font-semibold shadow-comic-sm"
              >
                <Download className="size-4" aria-hidden /> Download CV (PDF)
              </a>
            ) : (
              <div className="flex flex-col gap-2">
                <Button onClick={() => approve.mutate()} disabled={open.length > 0 || approve.isPending}>
                  <ShieldCheck className="size-4" aria-hidden /> Approve this CV
                </Button>
                {open.length > 0 && <p className="text-xs">Answer every Needs-you claim first.</p>}
                <Button variant="ghost" className="text-sm" onClick={() => tailor.mutate()} disabled={tailor.isPending}>
                  Tailor again
                </Button>
              </div>
            )}
          </Panel>

          {j.missing_skills.length > 0 && (
            <Panel>
              <PanelTitle kicker="Skill gaps">Not in your ledger yet</PanelTitle>
              <p className="text-sm">
                Power Hunt never adds these to your CV. The Coach will turn each into a small project you can prove.
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {j.missing_skills.map((skill) => (
                  <span key={skill} className="rounded-full border-2 border-ink bg-white px-2 py-0.5 text-xs font-semibold">
                    {skill}
                  </span>
                ))}
              </div>
            </Panel>
          )}
        </div>
      </div>

      {approved && <EmailPanel app={app} jobId={j.id} renderBlock={sentenceRenderer} />}
    </div>
  )
}
