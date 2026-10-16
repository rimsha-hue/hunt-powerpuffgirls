import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, FolderGit2, Sparkles, Target, UploadCloud } from 'lucide-react'
import { useState, type ChangeEvent, type DragEvent } from 'react'
import { Link } from 'react-router'
import { ChipInput } from '../components/ChipInput'
import { FactList } from '../components/FactList'
import { Button, ErrorNote, Field, Panel, PanelTitle, Skeleton } from '../components/ui'
import { cx } from '../lib/cx'
import { useToast } from '../lib/toast'
import {
  api,
  json,
  type Evidence,
  type GitHubIngestion,
  type Profile,
  type ResumeIngestion,
} from '../lib/api'
import { meKey } from '../lib/session'

const MAX_MB = 5

function useProfile() {
  return useQuery({ queryKey: ['profile'], queryFn: () => api<Profile>('/api/profile') })
}

function StepNumber({ n, done }: { n: number; done: boolean }) {
  return (
    <span
      className={cx(
        'grid size-9 shrink-0 place-items-center rounded-full border-3 border-ink font-mono text-sm font-medium',
        done ? 'bg-verified text-cream' : 'bg-signal',
      )}
      aria-label={done ? `Step ${n}, done` : `Step ${n}`}
    >
      {done ? '✓' : n}
    </span>
  )
}

function ResumeStep({ onFacts, done }: { onFacts: (r: ResumeIngestion) => void; done: boolean }) {
  const [dragging, setDragging] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const qc = useQueryClient()

  const upload = useMutation({
    mutationFn: (file: File | null) => {
      if (!file) return api<ResumeIngestion>('/api/resume/sample', { method: 'POST' })
      const body = new FormData()
      body.append('file', file)
      return api<ResumeIngestion>('/api/resume', { method: 'POST', body })
    },
    onSuccess: (result) => {
      onFacts(result)
      void qc.invalidateQueries({ queryKey: meKey })
      void qc.invalidateQueries({ queryKey: ['profile'] })
      void qc.invalidateQueries({ queryKey: ['ledger'] })
    },
  })

  function pick(file: File | undefined) {
    setLocalError(null)
    if (!file) return
    if (!/\.(pdf|docx)$/i.test(file.name)) return setLocalError('Choose a PDF or DOCX file.')
    if (file.size > MAX_MB * 1024 * 1024) return setLocalError(`The file is larger than ${MAX_MB} MB.`)
    upload.mutate(file)
  }

  return (
    <Panel>
      <div className="mb-4 flex items-center gap-3">
        <StepNumber n={1} done={done} />
        <PanelTitle kicker="Step 1">Your resume</PanelTitle>
      </div>
      <label
        onDragOver={(e: DragEvent) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e: DragEvent) => {
          e.preventDefault()
          setDragging(false)
          pick(e.dataTransfer.files[0])
        }}
        className={cx(
          'flex cursor-pointer flex-col items-center gap-2 rounded-xl border-3 border-dashed border-ink p-6 text-center',
          dragging ? 'bg-signal' : 'bg-white hover:bg-signal/40',
        )}
      >
        <UploadCloud className="size-8" aria-hidden />
        <span className="font-semibold">Drop your resume here, or click to choose</span>
        <span className="font-mono text-xs">PDF or DOCX · up to {MAX_MB} MB</span>
        <input
          type="file"
          accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          className="sr-only"
          onChange={(e: ChangeEvent<HTMLInputElement>) => {
            pick(e.target.files?.[0])
            e.target.value = ''
          }}
          disabled={upload.isPending}
        />
      </label>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={() => upload.mutate(null)} disabled={upload.isPending}>
          <Sparkles className="size-4" aria-hidden />
          Try with sample data
        </Button>
        {upload.isPending && <span className="font-mono text-sm">Reading and proving facts…</span>}
      </div>
      {(localError ?? upload.error?.message) && (
        <div className="mt-3">
          <ErrorNote>{localError ?? upload.error?.message}</ErrorNote>
        </div>
      )}
    </Panel>
  )
}

function GitHubStep({ profile, onFacts }: { profile: Profile; onFacts: (facts: Evidence[]) => void }) {
  const [username, setUsername] = useState(profile.github_username ?? '')
  const [portfolio, setPortfolio] = useState(profile.portfolio_url ?? '')
  const qc = useQueryClient()
  const toast = useToast()

  const sync = useMutation({
    mutationFn: async () => {
      await api<Profile>('/api/profile', {
        method: 'PUT',
        body: json({ github_username: username.trim() || null, portfolio_url: portfolio.trim() || null }),
      })
      if (!username.trim()) return null
      return api<GitHubIngestion>('/api/github/sync', { method: 'POST' })
    },
    onSuccess: (result) => {
      void qc.invalidateQueries({ queryKey: ['profile'] })
      void qc.invalidateQueries({ queryKey: ['ledger'] })
      if (result) {
        onFacts(result.facts)
        toast(`Scanned ${result.repos_scanned} repos: ${result.facts.length} new facts from GitHub.`)
      } else {
        toast('Saved.')
      }
    },
  })

  return (
    <Panel>
      <div className="mb-4 flex items-center gap-3">
        <StepNumber n={2} done={Boolean(profile.github_username)} />
        <PanelTitle kicker="Step 2">GitHub and portfolio</PanelTitle>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="GitHub username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="octocat"
          hint="Public repos only. Languages and topics become evidence."
          maxLength={60}
        />
        <Field
          label="Portfolio URL"
          type="url"
          value={portfolio}
          onChange={(e) => setPortfolio(e.target.value)}
          placeholder="https://"
          maxLength={500}
        />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button onClick={() => sync.mutate()} disabled={sync.isPending}>
          <FolderGit2 className="size-4" aria-hidden />
          {sync.isPending ? 'Reading repos…' : username.trim() ? 'Save and scan repos' : 'Save'}
        </Button>
      </div>
      {sync.error && (
        <div className="mt-3">
          <ErrorNote>{sync.error.message}</ErrorNote>
        </div>
      )}
    </Panel>
  )
}

function TargetsStep({ profile }: { profile: Profile }) {
  const [roles, setRoles] = useState(profile.target_roles)
  const [locations, setLocations] = useState(profile.target_locations)
  const [threshold, setThreshold] = useState(profile.match_threshold)
  const qc = useQueryClient()
  const toast = useToast()

  const save = useMutation({
    mutationFn: () =>
      api<Profile>('/api/profile', {
        method: 'PUT',
        body: json({ target_roles: roles, target_locations: locations, match_threshold: threshold }),
      }),
    onSuccess: (updated) => {
      qc.setQueryData(['profile'], updated)
      toast('Targets saved. The Scout will hunt for these.')
    },
  })

  return (
    <Panel>
      <div className="mb-4 flex items-center gap-3">
        <StepNumber n={3} done={profile.target_roles.length > 0} />
        <PanelTitle kicker="Step 3">What you're hunting for</PanelTitle>
      </div>
      <div className="flex flex-col gap-4">
        <ChipInput label="Target roles" values={roles} onChange={setRoles} placeholder="e.g. AI Engineer, then Enter" />
        <ChipInput label="Locations" values={locations} onChange={setLocations} placeholder="e.g. Remote, Lahore" />
        <div>
          <label htmlFor="threshold" className="mb-1 flex items-center justify-between text-sm font-semibold">
            Match threshold for the email digest
            <span className="rounded-md border-2 border-ink bg-signal px-2 font-mono">{threshold}</span>
          </label>
          <input
            id="threshold"
            type="range"
            min={0}
            max={100}
            step={5}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            className="w-full accent-coral"
          />
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-3">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          <Target className="size-4" aria-hidden />
          {save.isPending ? 'Saving…' : 'Save targets'}
        </Button>
      </div>
      {save.error && (
        <div className="mt-3">
          <ErrorNote>{save.error.message}</ErrorNote>
        </div>
      )}
    </Panel>
  )
}

export default function Onboarding() {
  const profile = useProfile()
  const toast = useToast()
  // Facts from this session's actions; until there are any, show what the ledger already holds.
  const [fresh, setFresh] = useState<Evidence[] | null>(null)
  const [summary, setSummary] = useState<ResumeIngestion | null>(null)

  const existing = useQuery({
    queryKey: ['ledger', 'onboarding'],
    queryFn: () => api<Evidence[]>('/api/ledger'),
    enabled: profile.data !== undefined && profile.data.resume_version > 0,
  })
  const live = fresh ?? existing.data ?? []

  function onResume(result: ResumeIngestion) {
    setSummary(result)
    setFresh(result.facts)
    const rejected = result.rejected_by_code ? `, ${result.rejected_by_code} AI suggestions rejected by code` : ''
    toast(`${result.facts.length} facts proven from ${result.line_count} resume lines${rejected}.`)
  }

  if (profile.isPending) {
    return (
      <div className="grid gap-6 lg:grid-cols-[1fr_minmax(0,26rem)]">
        <div className="flex flex-col gap-6">
          <Skeleton className="h-56" />
          <Skeleton className="h-44" />
        </div>
        <Skeleton className="h-96" />
      </div>
    )
  }
  if (profile.error) return <ErrorNote>{profile.error.message}</ErrorNote>

  const data = profile.data
  return (
    <div className="flex flex-col gap-6">
      <section className="panel halftone bg-signal p-6">
        <h1 className="font-display text-5xl tracking-wide">Suit up</h1>
        <p className="mt-2 max-w-2xl font-semibold">
          Your squad only writes what it can prove. Give it your resume and GitHub, and watch the evidence ledger
          build itself, fact by fact, each with its source.
        </p>
      </section>

      <div className="grid gap-6 lg:grid-cols-[1fr_minmax(0,26rem)]">
        <div className="flex flex-col gap-6">
          <ResumeStep onFacts={onResume} done={data.resume_version > 0} />
          <GitHubStep profile={data} onFacts={(facts) => setFresh([...facts, ...live])} />
          <TargetsStep key={data.resume_version} profile={data} />
          <Link
            to="/ledger"
            className="inline-flex items-center justify-center gap-2 self-start rounded-lg border-3 border-ink bg-violet px-5 py-2.5 font-semibold text-cream shadow-comic-sm"
          >
            Open my evidence ledger <ArrowRight className="size-4" aria-hidden />
          </Link>
        </div>

        <Panel className="h-fit lg:sticky lg:top-6">
          <PanelTitle kicker="Live">Evidence ledger</PanelTitle>
          {summary?.llm_note && (
            <p className="mb-3 rounded-lg border-2 border-ink bg-white px-3 py-2 text-xs font-semibold">{summary.llm_note}</p>
          )}
          {live.length === 0 ? (
            <p className="rounded-lg border-2 border-dashed border-ink p-4 text-sm font-semibold">
              Nothing yet. Upload a resume or press <em>Try with sample data</em>. Every fact will show its source:
              hover a badge to see the exact resume line or repo.
            </p>
          ) : (
            <div className="max-h-[60vh] overflow-y-auto pr-1">
              <p className="mb-2 font-mono text-xs font-medium">{live.length} facts, all sourced</p>
              <FactList facts={live} />
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}
