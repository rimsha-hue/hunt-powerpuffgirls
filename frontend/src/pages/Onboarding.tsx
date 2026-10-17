import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, FileCheck, FolderGit2, LoaderCircle, Sparkles, Target, UploadCloud } from 'lucide-react'
import { useState, type ChangeEvent, type DragEvent } from 'react'
import { Link } from 'react-router'
import { ChipInput, type Chip } from '../components/ChipInput'
import { FactList } from '../components/FactList'
import { Button, ErrorNote, Field, PageHeader, Panel, PanelTitle, Skeleton } from '../components/ui'
import { cx } from '../lib/cx'
import { useToast } from '../lib/toast'
import {
  api,
  ApiError,
  json,
  type Evidence,
  type Place,
  type GitHubIngestion,
  type PortfolioIngestion,
  type Profile,
  type ResumeIngestion,
} from '../lib/api'
import { usePublicConfig } from '../lib/config'
import { meKey } from '../lib/session'

function useProfile() {
  return useQuery({ queryKey: ['profile'], queryFn: () => api<Profile>('/api/profile') })
}

function StepNumber({ n, done }: { n: number; done: boolean }) {
  return (
    <span
      className={cx(
        'grid size-9 shrink-0 place-items-center rounded-full border-3 border-ink font-mono text-sm font-medium',
        done ? 'bg-verified text-ink' : 'bg-bubbles',
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
  // The file being read, then the one that was read: the drop zone shows which, not its prompt.
  const [chosen, setChosen] = useState<string | null>(null)
  const [uploaded, setUploaded] = useState<{ name: string; facts: number } | null>(null)
  const qc = useQueryClient()
  // The server publishes its limit; it also enforces it, so this check only saves a round trip.
  const maxMb = usePublicConfig().data?.max_upload_mb

  const upload = useMutation({
    mutationFn: (file: File | null) => {
      if (!file) return api<ResumeIngestion>('/api/resume/sample', { method: 'POST' })
      const body = new FormData()
      body.append('file', file)
      return api<ResumeIngestion>('/api/resume', { method: 'POST', body })
    },
    onMutate: (file) => setChosen(file ? file.name : null),
    onSuccess: (result, file) => {
      setUploaded({ name: file ? file.name : 'Sample resume', facts: result.facts.length })
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
    if (maxMb && file.size > maxMb * 1024 * 1024) return setLocalError(`The file is larger than ${maxMb} MB.`)
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
        aria-busy={upload.isPending}
        className={cx(
          'flex flex-col items-center gap-2 rounded-xl border-3 border-dashed border-ink p-6 text-center',
          upload.isPending
            ? 'cursor-wait bg-bubbles/30'
            : uploaded
              ? 'cursor-pointer bg-buttercup/30 hover:bg-buttercup/50'
              : dragging
                ? 'cursor-pointer bg-blush'
                : 'cursor-pointer bg-white hover:bg-blush/60',
        )}
      >
        {upload.isPending ? (
          <>
            <LoaderCircle className="size-8 motion-safe:animate-spin" aria-hidden />
            <span className="font-semibold">Reading {chosen ?? 'the sample resume'}…</span>
            <span className="text-sm">Finding every fact and its source line. This takes a few seconds.</span>
          </>
        ) : uploaded ? (
          <>
            <FileCheck className="size-8" aria-hidden />
            <span className="font-semibold">
              {uploaded.name} · {uploaded.facts} {uploaded.facts === 1 ? 'fact' : 'facts'} proven
            </span>
            <span className="text-sm underline">Upload a new version</span>
          </>
        ) : (
          <>
            <UploadCloud className="size-8" aria-hidden />
            <span className="font-semibold">Drop your resume here, or click to choose</span>
            <span className="font-mono text-xs">PDF or DOCX{maxMb ? ` · up to ${maxMb} MB` : ''}</span>
          </>
        )}
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

  const [problems, setProblems] = useState<string[]>([])

  const sync = useMutation({
    mutationFn: async () => {
      await api<Profile>('/api/profile', {
        method: 'PUT',
        body: json({ github_username: username.trim() || null, portfolio_url: portfolio.trim() || null }),
      })
      // Independent sources: one failing must not hide what the other proved.
      const [github, site] = await Promise.allSettled([
        username.trim() ? api<GitHubIngestion>('/api/github/sync', { method: 'POST' }) : Promise.resolve(null),
        portfolio.trim() ? api<PortfolioIngestion>('/api/portfolio/sync', { method: 'POST' }) : Promise.resolve(null),
      ])
      return { github, site }
    },
    onSuccess: ({ github, site }) => {
      void qc.invalidateQueries({ queryKey: ['profile'] })
      void qc.invalidateQueries({ queryKey: ['ledger'] })
      const found: Evidence[] = []
      const notes: string[] = []
      const errors: string[] = []
      if (github.status === 'fulfilled' && github.value) {
        found.push(...github.value.facts)
        notes.push(`${github.value.facts.length} new from ${github.value.repos_scanned} repos`)
      } else if (github.status === 'rejected') {
        errors.push(`GitHub: ${github.reason instanceof Error ? github.reason.message : 'failed'}`)
      }
      if (site.status === 'fulfilled' && site.value) {
        found.push(...site.value.facts)
        notes.push(`${site.value.facts.length} from your portfolio`)
      } else if (site.status === 'rejected') {
        errors.push(`Portfolio: ${site.reason instanceof Error ? site.reason.message : 'failed'}`)
      }
      setProblems(errors)
      if (found.length) onFacts(found)
      toast(notes.length ? `Facts proven: ${notes.join(', ')}.` : errors.length ? 'Saved, with problems below.' : 'Saved.')
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
          hint="Read safely on the server. Pages built with JavaScript are rendered first."
          maxLength={500}
        />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button onClick={() => sync.mutate()} disabled={sync.isPending}>
          <FolderGit2 className="size-4" aria-hidden />
          {sync.isPending ? 'Reading your work…' : username.trim() || portfolio.trim() ? 'Save and scan' : 'Save'}
        </Button>
      </div>
      {(sync.error || problems.length > 0) && (
        <div className="mt-3 flex flex-col gap-2">
          {sync.error && <ErrorNote>{sync.error.message}</ErrorNote>}
          {problems.map((problem) => (
            <ErrorNote key={problem}>{problem}</ErrorNote>
          ))}
        </div>
      )}
    </Panel>
  )
}

// Stable references, so the chip inputs don't refetch on every render.
const suggestRoles = async (q: string, signal: AbortSignal): Promise<Chip[]> =>
  (await api<string[]>(`/api/meta/roles?q=${encodeURIComponent(q)}`, { signal })).map((title) => ({
    value: title,
    label: title,
  }))
const suggestPlaces = async (q: string, signal: AbortSignal): Promise<Chip[]> =>
  (await api<Place[]>(`/api/meta/locations?q=${encodeURIComponent(q)}`, { signal })).map((place) => ({
    value: place.key,
    label: place.label,
  }))

function TargetsStep({ profile }: { profile: Profile }) {
  const [roles, setRoles] = useState<Chip[]>(profile.target_roles.map((r) => ({ value: r, label: r })))
  const [locations, setLocations] = useState<Chip[]>(
    profile.target_locations.map((p) => ({ value: p.key, label: p.label })),
  )
  const [threshold, setThreshold] = useState(profile.match_threshold)
  const qc = useQueryClient()
  const toast = useToast()

  const save = useMutation({
    mutationFn: () =>
      api<Profile>('/api/profile', {
        method: 'PUT',
        body: json({
          target_roles: roles.map((c) => c.value),
          target_locations: locations.map((c) => c.value),
          match_threshold: threshold,
        }),
      }),
    onSuccess: (updated) => {
      qc.setQueryData(['profile'], updated)
      // Show the server's canonical forms (trimmed titles, resolved place names).
      setRoles(updated.target_roles.map((r) => ({ value: r, label: r })))
      setLocations(updated.target_locations.map((p) => ({ value: p.key, label: p.label })))
      toast('Targets saved. The Scout will hunt for these.')
    },
  })
  const fieldErrors = save.error instanceof ApiError ? save.error.fields : {}
  const generalError = save.error && Object.keys(fieldErrors).length === 0 ? save.error.message : null

  return (
    <Panel>
      <div className="mb-4 flex items-center gap-3">
        <StepNumber n={3} done={profile.target_roles.length > 0} />
        <PanelTitle kicker="Step 3">What you're hunting for</PanelTitle>
      </div>
      <div className="flex flex-col gap-4">
        <ChipInput
          label="Target roles"
          chips={roles}
          onChange={setRoles}
          suggest={suggestRoles}
          freeText
          placeholder="e.g. Data Analyst"
          hint="Pick a suggestion or type a job title and press Enter."
          error={fieldErrors.target_roles}
        />
        <ChipInput
          label="Locations"
          chips={locations}
          onChange={setLocations}
          suggest={suggestPlaces}
          freeText={false}
          placeholder="e.g. Remote, Lahore, Islamabad"
          hint="Cities, countries or Remote. Choose from the suggestions."
          error={fieldErrors.target_locations}
        />
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
            className="w-full accent-blossom"
          />
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-3">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          <Target className="size-4" aria-hidden />
          {save.isPending ? 'Saving…' : 'Save targets'}
        </Button>
      </div>
      {generalError && (
        <div className="mt-3">
          <ErrorNote>{generalError}</ErrorNote>
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
      <PageHeader title="Suit up">
        Your squad only writes what it can prove. Give it your resume and GitHub, and watch the evidence ledger build
        itself, fact by fact, each with its source.
      </PageHeader>

      <div className="grid gap-6 lg:grid-cols-[1fr_minmax(0,26rem)]">
        <div className="flex flex-col gap-6">
          <ResumeStep onFacts={onResume} done={data.resume_version > 0} />
          {/* Remount after a resume upload so links found in the resume header pre-fill the fields.
              Sibling keys must differ: with one shared key React mismatched the two steps on remount
              and left the old GitHub card on screen beside the new one. */}
          <GitHubStep
            key={`github-${data.resume_version}`}
            profile={data}
            onFacts={(facts) => setFresh([...facts, ...live])}
          />
          <TargetsStep key={`targets-${data.resume_version}`} profile={data} />
          <Link
            to="/ledger"
            className="inline-flex items-center justify-center gap-2 self-start rounded-lg border-3 border-ink bg-violet px-5 py-2.5 font-semibold text-card shadow-comic-sm"
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
