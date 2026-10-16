import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, Check, FileUp, Link2, Mail, Paperclip, Pencil, Send, Undo2 } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { type ChangeEvent, type FormEvent, type ReactNode, useState } from 'react'
import { type Application, type AttachmentItem, type Claim, type CvBlock, api, json } from '../lib/api'
import { useCalm } from '../lib/calm'
import { cx } from '../lib/cx'
import { useToast } from '../lib/toast'
import { Button, ErrorNote, Panel, PanelTitle } from './ui'

const dateFormat = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

/** Prose blocks back to paragraphs, each sentence rendered by `renderBlock` (claims and badges). */
export function Prose({
  blocks,
  renderBlock,
}: {
  blocks: CvBlock[]
  renderBlock: (block: CvBlock, index: number) => ReactNode
}) {
  const paragraphs = new Map<number, Array<[CvBlock, number]>>()
  blocks.forEach((block, index) => {
    if (block.text) paragraphs.set(block.entry, [...(paragraphs.get(block.entry) ?? []), [block, index]])
  })
  return (
    <div className="flex flex-col gap-3 text-sm leading-relaxed">
      {[...paragraphs.entries()].map(([entry, sentences]) => (
        <p key={entry}>
          {sentences.map(([block, index], i) => (
            <span key={index}>
              {i > 0 && ' '}
              {renderBlock(block, index)}
            </span>
          ))}
        </p>
      ))}
    </div>
  )
}

const proseText = (blocks: CvBlock[]): string => {
  const paragraphs = new Map<number, string[]>()
  for (const block of blocks) if (block.text) paragraphs.set(block.entry, [...(paragraphs.get(block.entry) ?? []), block.text])
  return [...paragraphs.values()].map((p) => p.join(' ')).join('\n\n')
}

function AttachmentRow({
  item,
  jobId,
  disabled,
  onChange,
}: {
  item: AttachmentItem
  jobId: number
  disabled: boolean
  onChange: (app: Application) => void
}) {
  const toast = useToast()
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return api<Application>(`/api/jobs/${jobId}/application/files/${item.kind}`, { method: 'POST', body: form })
    },
    onSuccess: onChange,
    onError: (err) => toast(err.message, 'error'),
  })
  const waive = useMutation({
    mutationFn: (waived: boolean) =>
      api<Application>(`/api/jobs/${jobId}/application/attachments/${item.kind}/waive`, {
        method: 'POST',
        body: json({ waived }),
      }),
    onSuccess: onChange,
    onError: (err) => toast(err.message, 'error'),
  })
  const pick = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) upload.mutate(file)
    event.target.value = ''
  }
  const ready = item.status !== 'missing'
  const Icon = item.status === 'link' ? Link2 : item.status === 'waived' ? Ban : ready ? Check : Paperclip
  return (
    <li
      className={cx(
        'flex flex-wrap items-center gap-2 rounded-lg border-3 border-ink px-3 py-2 text-sm',
        ready ? 'bg-white' : 'bg-blush',
      )}
    >
      <Icon className="size-4 shrink-0" aria-hidden />
      <span className="font-semibold">{item.label}</span>
      <span className="min-w-0 flex-1 truncate text-xs">
        {item.status === 'missing' ? 'You need to add this' : item.status === 'waived' ? 'Sending without it' : item.detail}
      </span>
      {!disabled && (item.status === 'missing' || item.status === 'uploaded') && (
        <label className="inline-flex cursor-pointer items-center gap-1 rounded-md border-2 border-ink bg-card px-2 py-0.5 text-xs font-semibold">
          <FileUp className="size-3.5" aria-hidden />
          {item.status === 'uploaded' ? 'Replace' : 'Upload'}
          <input type="file" accept=".pdf,.docx" className="sr-only" onChange={pick} disabled={upload.isPending} />
        </label>
      )}
      {!disabled && item.status === 'missing' && (
        <button type="button" className="text-xs font-semibold underline" onClick={() => waive.mutate(true)}>
          Send without it
        </button>
      )}
      {!disabled && item.status === 'waived' && (
        <button type="button" className="inline-flex items-center gap-1 text-xs font-semibold underline" onClick={() => waive.mutate(false)}>
          <Undo2 className="size-3.5" aria-hidden /> Undo
        </button>
      )}
    </li>
  )
}

/** Flow C at the bottom of the workspace: the drafted email, its checks, attachments and Gate 3. */
export function EmailPanel({
  app,
  jobId,
  renderBlock,
}: {
  app: Application
  jobId: number
  renderBlock: (claims: Claim[]) => (block: CvBlock, index: number) => ReactNode
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const calm = useCalm()
  const key = ['application', String(jobId)]
  const set = (next: Application) => qc.setQueryData(key, next)
  const email = app.email
  const [editing, setEditing] = useState<'email' | 'cover_letter' | null>(null)
  const [draft, setDraft] = useState('')
  const [launch, setLaunch] = useState(false)

  const compose = useMutation({
    mutationFn: () => api<Application>(`/api/jobs/${jobId}/application/compose`, { method: 'POST' }),
    onSuccess: set,
    onError: (err) => toast(err.message, 'error'),
  })
  const save = useMutation({
    mutationFn: (body: Record<string, string>) =>
      api<Application>(`/api/jobs/${jobId}/application/email`, { method: 'PUT', body: json(body) }),
    onSuccess: (next) => {
      set(next)
      setEditing(null)
    },
    onError: (err) => toast(err.message, 'error'),
  })
  const send = useMutation({
    mutationFn: () => api<Application>(`/api/jobs/${jobId}/application/send`, { method: 'POST' }),
    onMutate: () => setLaunch(true),
    onSuccess: (next) => {
      set(next)
      void qc.invalidateQueries({ queryKey: ['jobs'] })
      void qc.invalidateQueries({ queryKey: ['job', String(jobId)] })
      toast('Sent. The job is marked Applied, with a follow-up reminder in 7 days.')
    },
    onError: (err) => {
      setLaunch(false)
      toast(err.message, 'error')
    },
  })

  if (!email) {
    return (
      <Panel>
        <PanelTitle kicker="Flow C: apply by email">Your application email</PanelTitle>
        <p className="mb-3 text-sm">
          The Composer writes a short email from your approved CV (and a cover letter if the posting asks for one). The
          Verifier checks every sentence, the same way as your CV.
        </p>
        <Button onClick={() => compose.mutate()} disabled={compose.isPending}>
          <Mail className="size-4" aria-hidden />
          {compose.isPending ? 'Writing…' : 'Draft the email'}
        </Button>
      </Panel>
    )
  }

  const sent = email.sent_at !== null
  const emailClaims = app.claims.filter((c) => c.document === 'email')
  const letterClaims = app.claims.filter((c) => c.document === 'cover_letter')
  const fields = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    save.mutate({ recipient: String(form.get('recipient') ?? ''), subject: String(form.get('subject') ?? '') })
  }

  return (
    <Panel className="relative overflow-hidden">
      <PanelTitle kicker="Flow C, Gate 3: approve and send">Your application email</PanelTitle>
      {email.note && <p className="mb-3 rounded-lg border-3 border-ink bg-signal px-3 py-2 text-sm font-semibold">{email.note}</p>}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <form onSubmit={fields} className="grid gap-2 sm:grid-cols-[5rem_1fr] sm:items-center">
            <label htmlFor="to" className="text-sm font-semibold">To</label>
            <input id="to" name="recipient" type="email" required defaultValue={email.recipient ?? ''} disabled={sent} className="rounded-lg border-3 border-ink px-2 py-1 text-sm" />
            <label htmlFor="subject" className="text-sm font-semibold">Subject</label>
            <input id="subject" name="subject" required maxLength={150} defaultValue={email.subject ?? ''} disabled={sent} className="rounded-lg border-3 border-ink px-2 py-1 text-sm" />
            {!sent && (
              <Button type="submit" variant="secondary" className="w-fit px-3 py-1 text-xs sm:col-start-2" disabled={save.isPending}>
                Save To and Subject
              </Button>
            )}
          </form>

          <div className="rounded-xl border-3 border-ink bg-white p-4">
            {editing === 'email' ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  save.mutate({ body: draft })
                }}
                className="flex flex-col gap-2"
              >
                <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={12} maxLength={4000} className="rounded-lg border-3 border-ink p-2 text-sm" />
                <div className="flex gap-2">
                  <Button type="submit" className="px-3 py-1 text-sm" disabled={save.isPending || !draft.trim()}>
                    Save and check again
                  </Button>
                  <Button variant="ghost" className="px-3 py-1 text-sm" onClick={() => setEditing(null)}>
                    Cancel
                  </Button>
                </div>
              </form>
            ) : (
              <>
                <Prose blocks={email.blocks} renderBlock={renderBlock(emailClaims)} />
                <p className="mt-3 whitespace-pre-line text-sm">Best regards,{'\n'}{email.signature}</p>
                {!sent && (
                  <Button
                    variant="ghost"
                    className="mt-2 px-2 py-1 text-xs"
                    onClick={() => {
                      setDraft(proseText(email.blocks))
                      setEditing('email')
                    }}
                  >
                    <Pencil className="size-3.5" aria-hidden /> Edit the email
                  </Button>
                )}
              </>
            )}
          </div>

          {email.cover_letter.length > 0 && (
            <div className="rounded-xl border-3 border-ink bg-white p-4">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <p className="font-bold">Cover letter</p>
                <a href={`/api/jobs/${jobId}/application/cover-letter.pdf`} target="_blank" rel="noopener noreferrer" className="text-xs font-semibold underline">
                  Preview PDF
                </a>
              </div>
              {editing === 'cover_letter' ? (
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    save.mutate({ cover_letter: draft })
                  }}
                  className="flex flex-col gap-2"
                >
                  <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={14} maxLength={6000} className="rounded-lg border-3 border-ink p-2 text-sm" />
                  <div className="flex gap-2">
                    <Button type="submit" className="px-3 py-1 text-sm" disabled={save.isPending || !draft.trim()}>
                      Save and check again
                    </Button>
                    <Button variant="ghost" className="px-3 py-1 text-sm" onClick={() => setEditing(null)}>
                      Cancel
                    </Button>
                  </div>
                </form>
              ) : (
                <>
                  <Prose blocks={email.cover_letter} renderBlock={renderBlock(letterClaims)} />
                  {!sent && (
                    <Button
                      variant="ghost"
                      className="mt-2 px-2 py-1 text-xs"
                      onClick={() => {
                        setDraft(proseText(email.cover_letter))
                        setEditing('cover_letter')
                      }}
                    >
                      <Pencil className="size-3.5" aria-hidden /> Edit the cover letter
                    </Button>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-4">
          <div>
            <p className="mb-2 font-bold">Attachments</p>
            <ul className="flex flex-col gap-2">
              {email.attachments.map((item) => (
                <AttachmentRow key={item.kind} item={item} jobId={jobId} disabled={sent} onChange={set} />
              ))}
            </ul>
          </div>

          {sent ? (
            <motion.div
              initial={calm ? false : { rotateY: 90 }}
              animate={{ rotateY: 0 }}
              transition={{ duration: 0.3 }}
              className="rounded-xl border-3 border-ink bg-buttercup p-4"
            >
              <p className="font-display text-3xl tracking-wide">Applied!</p>
              <p className="text-sm font-semibold">
                Sent {dateFormat.format(new Date(email.sent_at ?? ''))}. Follow up on{' '}
                {dateFormat.format(new Date(email.follow_up_due ?? ''))} if you haven't heard back.
              </p>
            </motion.div>
          ) : (
            <div className="flex flex-col gap-2">
              {email.blockers.length > 0 && (
                <ErrorNote>
                  Before sending: {email.blockers.join(' ')}
                </ErrorNote>
              )}
              <Button onClick={() => send.mutate()} disabled={email.blockers.length > 0 || send.isPending} className="bg-signal">
                <Send className="size-4" aria-hidden />
                {send.isPending ? 'Sending…' : 'Approve and send'}
              </Button>
              <p className="text-xs">
                It goes out from your mailbox with these attachments. Nothing is sent until you press this.
              </p>
              <Button variant="ghost" className="text-sm" onClick={() => compose.mutate()} disabled={compose.isPending}>
                Write it again
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* Envelope launch: decorative, plays over the panel and never delays the result. */}
      <AnimatePresence>
        {launch && !calm && (
          <motion.div
            className="pointer-events-none absolute bottom-8 left-8 text-ink"
            initial={{ x: 0, y: 0, opacity: 1 }}
            animate={{ x: 600, y: -200, opacity: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.7, ease: 'easeIn' }}
            onAnimationComplete={() => setLaunch(false)}
            aria-hidden
          >
            <Mail className="size-12 fill-blossom" />
          </motion.div>
        )}
      </AnimatePresence>
    </Panel>
  )
}
