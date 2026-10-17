import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, ClipboardList, ExternalLink, Pencil, Search, Sparkles } from 'lucide-react'
import { type FormEvent, type ReactNode, useState } from 'react'
import { Link } from 'react-router'
import { type Application, type Claim, type CvBlock, type FormAnswer, type QuestionType, api, json } from '../lib/api'
import { cx } from '../lib/cx'
import { useToast } from '../lib/toast'
import { Prose } from './EmailPanel'
import { Button, ErrorNote, Panel, PanelTitle } from './ui'

const TYPE_LABELS: Record<QuestionType, string> = {
  factual_profile: 'From your profile',
  factual_ledger: 'From your ledger',
  descriptive: 'Written answer',
  analytical: 'Analytical',
  file: 'File',
  consent: 'Your agreement',
  voluntary: 'Voluntary',
}
const STATUS: Record<FormAnswer['status'], { label: string; style: string }> = {
  needs_you: { label: 'Needs you', style: 'bg-blossom' },
  ready: { label: 'Ready to check', style: 'bg-signal' },
  approved: { label: 'Approved', style: 'bg-buttercup' },
}
const WRITTEN = new Set<QuestionType>(['descriptive', 'analytical'])

function AnswerCard({
  answer,
  jobId,
  locked,
  claims,
  renderBlock,
  onChange,
}: {
  answer: FormAnswer
  jobId: number
  locked: boolean
  claims: Claim[]
  renderBlock: (claims: Claim[]) => (block: CvBlock, index: number) => ReactNode
  onChange: (app: Application) => void
}) {
  const toast = useToast()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(answer.answer)
  const [remember, setRemember] = useState(answer.type !== 'consent')
  const base = `/api/jobs/${jobId}/application/answers/${answer.index}`
  const save = useMutation({
    mutationFn: (text: string) =>
      api<Application>(base, { method: 'PUT', body: json({ answer: text, remember: remember && answer.type !== 'consent' }) }),
    onSuccess: (app) => {
      onChange(app)
      setEditing(false)
    },
    onError: (err) => toast(err.message, 'error'),
  })
  const approve = useMutation({
    mutationFn: () => api<Application>(`${base}/approve`, { method: 'POST' }),
    onSuccess: onChange,
    onError: (err) => toast(err.message, 'error'),
  })
  const status = STATUS[answer.status]
  const written = WRITTEN.has(answer.type)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    save.mutate(draft)
  }

  return (
    <li className="rounded-xl border-3 border-ink bg-white p-4">
      <div className="flex flex-wrap items-start gap-2">
        <p className="min-w-0 flex-1 font-bold">
          {answer.label}
          {answer.required && <span aria-label="required"> *</span>}
        </p>
        <span className="rounded-full border-2 border-ink bg-card px-2 py-0.5 font-mono text-[11px] uppercase">
          {TYPE_LABELS[answer.type]}
        </span>
        <span className={cx('rounded-full border-2 border-ink px-2 py-0.5 font-mono text-[11px] font-medium uppercase', status.style)}>
          {status.label}
        </span>
      </div>
      {answer.description && <p className="mt-1 line-clamp-3 text-xs">{answer.description}</p>}

      {editing ? (
        <form onSubmit={submit} className="mt-3 flex flex-col gap-2">
          {answer.options.length > 0 ? (
            <select value={draft} onChange={(e) => setDraft(e.target.value)} className="rounded-lg border-3 border-ink px-2 py-1 text-sm">
              <option value="">Choose…</option>
              {answer.options.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          ) : written ? (
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={8} maxLength={4000} className="rounded-lg border-3 border-ink p-2 text-sm" />
          ) : (
            <input value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={4000} className="rounded-lg border-3 border-ink px-2 py-1 text-sm" />
          )}
          {!written && answer.type !== 'consent' && answer.type !== 'file' && (
            <label className="flex items-center gap-2 text-xs font-semibold">
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
              Remember this answer for future forms
            </label>
          )}
          <div className="flex gap-2">
            <Button type="submit" className="px-3 py-1 text-sm" disabled={save.isPending}>
              {written ? 'Save and check again' : 'Save'}
            </Button>
            <Button variant="ghost" className="px-3 py-1 text-sm" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="mt-3 text-sm">
          {answer.answer ? (
            written && answer.blocks.length ? (
              <Prose blocks={answer.blocks} renderBlock={renderBlock(claims)} />
            ) : (
              <p className="whitespace-pre-line font-semibold">{answer.answer}</p>
            )
          ) : (
            <p className="italic">No answer yet.</p>
          )}
        </div>
      )}

      {answer.company_fact && (
        <p className="mt-2 rounded-lg border-2 border-ink bg-card px-2 py-1 text-xs">
          <span className="font-semibold">Company fact used: </span>
          {answer.company_fact.fact}{' '}
          <a href={answer.company_fact.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
            source <ExternalLink className="size-3" aria-hidden />
          </a>
        </p>
      )}
      {answer.source && (
        <p className="mt-1 text-xs">
          <span className="font-semibold">Source: </span>
          {answer.source.label}
          {answer.source.detail && ` (${answer.source.detail})`}
        </p>
      )}
      {answer.note && <p className="mt-1 text-xs font-semibold">{answer.note}</p>}

      {!locked && !editing && (
        <div className="mt-3 flex flex-wrap gap-2">
          {answer.status !== 'approved' && (
            <Button className="px-3 py-1 text-xs" onClick={() => approve.mutate()} disabled={approve.isPending || answer.status === 'needs_you'}>
              <Check className="size-3.5" aria-hidden /> Approve
            </Button>
          )}
          <Button
            variant="secondary"
            className="px-3 py-1 text-xs"
            onClick={() => {
              setDraft(answer.answer)
              setEditing(true)
            }}
          >
            <Pencil className="size-3.5" aria-hidden /> {answer.answer ? 'Edit' : 'Answer'}
          </Button>
        </div>
      )}
    </li>
  )
}

/** Flow D: find the form's questions, draft typed answers, check and approve each one. */
export function FormPanel({
  app,
  jobId,
  formUrl,
  renderBlock,
}: {
  app: Application
  jobId: number
  /** The posting's apply page, until the form itself has been read. */
  formUrl: string | null
  renderBlock: (claims: Claim[]) => (block: CvBlock, index: number) => ReactNode
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const key = ['application', String(jobId)]
  const set = (next: Application) => qc.setQueryData(key, next)
  const [pasting, setPasting] = useState(false)
  const [pasted, setPasted] = useState('')
  const base = `/api/jobs/${jobId}/application`

  const discover = useMutation({
    mutationFn: () => api<Application>(`${base}/questions/discover`, { method: 'POST' }),
    onSuccess: set,
    onError: (err) => {
      toast(err.message, 'error')
      setPasting(true)
    },
  })
  const paste = useMutation({
    mutationFn: () => api<Application>(`${base}/questions/paste`, { method: 'POST', body: json({ text: pasted }) }),
    onSuccess: (next) => {
      set(next)
      setPasting(false)
    },
    onError: (err) => toast(err.message, 'error'),
  })
  const draft = useMutation({
    mutationFn: () => api<Application>(`${base}/answers/draft`, { method: 'POST' }),
    onSuccess: set,
    onError: (err) => toast(err.message, 'error'),
  })

  const form = app.form
  const locked = app.status === 'submitted' || app.status === 'sent'
  const unanswered = form ? form.answers.every((a) => !a.answer) : true
  const link = form?.url ?? formUrl

  return (
    <Panel>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <PanelTitle kicker="Flow D: apply on their form">Form answers</PanelTitle>
        {link && (
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded-lg border-3 border-ink bg-card px-3 py-1.5 text-sm font-semibold shadow-comic-sm"
          >
            Open their form <ExternalLink className="size-4" aria-hidden />
          </a>
        )}
      </div>
      {app.status === 'sent' && (
        <p className="mb-3 rounded-lg border-2 border-ink bg-signal px-3 py-2 text-sm font-semibold">
          You already applied to this job by email, so the form answers are closed.
        </p>
      )}
      {!form ? (
        <p className="mb-3 text-sm">
          Power Hunt reads the employer's form (Greenhouse, Lever or the apply page), answers each question by its type,
          and checks every claim. You approve each answer and submit on their site yourself.
        </p>
      ) : (
        <p className="mb-3 text-sm">
          {form.answers.length} questions from{' '}
          <span className="font-semibold">{form.source === 'pasted' ? 'your paste' : form.source}</span>.
        </p>
      )}

      {!locked && (
        <div className="mb-4 flex flex-wrap gap-2">
          <Button variant={form ? 'secondary' : 'primary'} onClick={() => discover.mutate()} disabled={discover.isPending}>
            <Search className="size-4" aria-hidden />
            {discover.isPending ? 'Reading the form…' : form ? 'Read the form again' : 'Find the questions'}
          </Button>
          <Button variant="ghost" onClick={() => setPasting((v) => !v)}>
            <ClipboardList className="size-4" aria-hidden /> Paste questions
          </Button>
          {form && (
            <Button onClick={() => draft.mutate()} disabled={draft.isPending}>
              <Sparkles className="size-4" aria-hidden />
              {draft.isPending ? 'Answering…' : unanswered ? 'Draft the answers' : 'Draft open answers again'}
            </Button>
          )}
        </div>
      )}

      {pasting && !locked && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            paste.mutate()
          }}
          className="mb-4 flex flex-col gap-2"
        >
          <textarea
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            rows={6}
            maxLength={8000}
            placeholder={'One question per line. End a line with * if it is required.'}
            className="rounded-lg border-3 border-ink p-2 text-sm"
          />
          <Button type="submit" className="w-fit" disabled={paste.isPending || pasted.trim().length < 3}>
            Use these questions
          </Button>
        </form>
      )}

      {form && (
        <>
          <ul className="flex flex-col gap-3">
            {form.answers.map((answer) => (
              <AnswerCard
                key={`${answer.index}-${answer.label}`}
                answer={answer}
                jobId={jobId}
                locked={locked}
                claims={app.claims.filter((c) => c.document === `q:${answer.index}`)}
                renderBlock={renderBlock}
                onChange={set}
              />
            ))}
          </ul>
          <div className="mt-4 flex flex-col gap-2">
            {form.blockers.length > 0 && !locked && <ErrorNote>Before the Apply Pack: {form.blockers.join(' ')}</ErrorNote>}
            <Link
              to={`/jobs/${jobId}/pack`}
              className="inline-flex w-fit items-center gap-2 rounded-lg border-3 border-ink bg-signal px-4 py-2 font-semibold shadow-comic-sm"
            >
              <ClipboardList className="size-4" aria-hidden /> Open the Apply Pack
            </Link>
          </div>
        </>
      )}
    </Panel>
  )
}
