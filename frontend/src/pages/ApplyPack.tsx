import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, Copy, Download, ExternalLink, Send } from 'lucide-react'
import { motion } from 'motion/react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { Button, ErrorNote, Panel, PanelTitle, Skeleton } from '../components/ui'
import { type Application, type JobDetail, api } from '../lib/api'
import { useCalm } from '../lib/calm'
import { useToast } from '../lib/toast'

const dateFormat = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const toast = useToast()
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard.writeText(text).then(
          () => {
            setCopied(true)
            window.setTimeout(() => setCopied(false), 1200)
          },
          () => toast('Copy failed. Select the text and copy it instead.', 'error'),
        )
      }}
      className="inline-flex shrink-0 items-center gap-1 rounded-md border-2 border-ink bg-card px-2 py-0.5 text-xs font-semibold"
      aria-label="Copy answer"
    >
      {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}

/** Spec Flow D step 5: every field with its approved answer, the files, and the employer's form. */
export default function ApplyPack() {
  const { id } = useParams()
  const qc = useQueryClient()
  const toast = useToast()
  const calm = useCalm()
  const key = ['application', id]
  const job = useQuery({ queryKey: ['job', id], queryFn: () => api<JobDetail>(`/api/jobs/${id}`) })
  const application = useQuery({ queryKey: key, queryFn: () => api<Application>(`/api/jobs/${id}/application`) })
  const submitted = useMutation({
    mutationFn: () => api<Application>(`/api/jobs/${id}/application/submitted`, { method: 'POST' }),
    onSuccess: (app) => {
      qc.setQueryData(key, app)
      void qc.invalidateQueries({ queryKey: ['jobs'] })
      void qc.invalidateQueries({ queryKey: ['job', id] })
      toast('Marked as applied. A follow-up reminder is set for 7 days from now.')
    },
    onError: (err) => toast(err.message, 'error'),
  })

  if (job.error || application.error) return <ErrorNote>{(job.error ?? application.error)?.message}</ErrorNote>
  if (!job.data || !application.data) return <Skeleton className="h-96" />
  const j = job.data
  const app = application.data
  const form = app.form
  if (!form) {
    return (
      <Panel>
        <PanelTitle kicker="Apply Pack">Nothing here yet</PanelTitle>
        <p className="text-sm">Find the form's questions in the workspace first.</p>
        <Link to={`/jobs/${j.id}/workspace`} className="mt-3 inline-block font-semibold underline">
          Open the workspace
        </Link>
      </Panel>
    )
  }
  const formUrl = form.url ?? j.apply_url ?? j.url
  const ready = form.blockers.length === 0
  const done = form.submitted_at !== null

  return (
    <div className="flex flex-col gap-6">
      <Link to={`/jobs/${j.id}/workspace`} className="inline-flex w-fit items-center gap-1 text-sm font-semibold hover:underline">
        <ArrowLeft className="size-4" aria-hidden /> Back to the workspace
      </Link>

      <section className="panel hearts flex flex-wrap items-center gap-4 p-6">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-xs font-medium uppercase tracking-widest">Apply Pack</p>
          <h1 className="mt-1 text-3xl font-extrabold leading-tight">{j.title}</h1>
          <p className="text-lg font-semibold">{j.company}</p>
          <p className="mt-2 max-w-2xl text-sm">
            Copy each answer into the employer's form, attach your files, and submit there. Power Hunt never submits a
            form for you.
          </p>
        </div>
        <a
          href={formUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg border-3 border-ink bg-signal px-4 py-2 font-semibold shadow-comic-sm"
        >
          Open application form <ExternalLink className="size-4" aria-hidden />
        </a>
      </section>

      {!ready && !done && <ErrorNote>Not final yet: {form.blockers.join(' ')}</ErrorNote>}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <Panel>
          <PanelTitle kicker="Fields">Your answers</PanelTitle>
          <ol className="flex flex-col gap-3">
            {form.answers.map((answer) => (
              <li key={answer.index} className="rounded-xl border-3 border-ink bg-white p-3">
                <div className="flex items-start gap-2">
                  <p className="min-w-0 flex-1 text-sm font-bold">
                    {answer.label}
                    {answer.required && ' *'}
                  </p>
                  {answer.answer && answer.type !== 'file' && <CopyButton text={answer.answer} />}
                </div>
                <p className="mt-1 whitespace-pre-line text-sm">{answer.answer || <span className="italic">Left blank</span>}</p>
                {answer.company_fact && (
                  <p className="mt-1 text-xs">
                    Company fact:{' '}
                    <a href={answer.company_fact.url} target="_blank" rel="noopener noreferrer" className="underline">
                      source
                    </a>
                  </p>
                )}
                {answer.status !== 'approved' && <p className="mt-1 font-mono text-[11px] uppercase">Not approved yet</p>}
              </li>
            ))}
          </ol>
        </Panel>

        <div className="flex flex-col gap-6">
          <Panel>
            <PanelTitle kicker="Files">Attach these</PanelTitle>
            <div className="flex flex-col gap-2">
              <a href={`/api/jobs/${j.id}/application/cv.pdf`} className="inline-flex items-center gap-2 font-semibold underline">
                <Download className="size-4" aria-hidden /> Tailored CV (PDF)
              </a>
              {app.email?.cover_letter.length ? (
                <a href={`/api/jobs/${j.id}/application/cover-letter.pdf`} className="inline-flex items-center gap-2 font-semibold underline">
                  <Download className="size-4" aria-hidden /> Cover letter (PDF)
                </a>
              ) : null}
            </div>
          </Panel>

          <Panel>
            <PanelTitle kicker="Gate 4: you submit">Done on their site?</PanelTitle>
            {done ? (
              <motion.div
                initial={calm ? false : { rotateY: 90 }}
                animate={{ rotateY: 0 }}
                transition={{ duration: 0.3 }}
                className="rounded-xl border-3 border-ink bg-buttercup p-4"
              >
                <p className="font-display text-3xl tracking-wide">Applied!</p>
                <p className="text-sm font-semibold">
                  Follow up on {dateFormat.format(new Date(form.follow_up_due ?? ''))} if you haven't heard back.
                </p>
              </motion.div>
            ) : (
              <>
                <Button onClick={() => submitted.mutate()} disabled={!ready || submitted.isPending} className="w-full">
                  <Send className="size-4" aria-hidden /> I submitted it
                </Button>
                <p className="mt-2 text-xs">Press this after you submit the form. The job moves to Applied.</p>
              </>
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}
