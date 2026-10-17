import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, Trash2 } from 'lucide-react'
import { useDeferredValue, useState } from 'react'
import { Link } from 'react-router'
import { ErrorNote, FilterChips, Skeleton, SourceBadge, TypeTag } from '../components/ui'
import { api, groupFacts, type Evidence, type FactGroup, type FactSource, type FactType } from '../lib/api'
import { useToast } from '../lib/toast'

const TYPES: Array<FactType | 'all'> = [
  'all',
  'skill',
  'metric',
  'role',
  'employer',
  'degree',
  'school',
  'certification',
  'project',
  'publication',
  'award',
  'other',
]
const SOURCES: Array<{ value: FactSource | 'all'; label: string }> = [
  { value: 'all', label: 'All sources' },
  { value: 'resume', label: 'Resume' },
  { value: 'github', label: 'GitHub' },
  { value: 'portfolio', label: 'Portfolio' },
  { value: 'user_confirmed', label: 'Confirmed by you' },
]

const dateFormat = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' })

/** The "All" view, one table per kind of fact. Entry-bound facts are grouped under their entry
 * (the job, school or project they belong to), so a role sits next to its employer. */
const SECTIONS: Array<{ id: string; title: string; types: FactType[]; byEntry?: boolean; chips?: boolean }> = [
  { id: 'experience', title: 'Experience', types: ['role', 'employer'], byEntry: true },
  { id: 'education', title: 'Education', types: ['degree', 'school'], byEntry: true },
  { id: 'projects', title: 'Projects', types: ['project'] },
  { id: 'results', title: 'Results', types: ['metric'], byEntry: true },
  { id: 'skills', title: 'Skills', types: ['skill'], chips: true },
  { id: 'certifications', title: 'Certifications', types: ['certification'] },
  { id: 'publications', title: 'Publications', types: ['publication'] },
  { id: 'awards', title: 'Awards', types: ['award'] },
  { id: 'personal', title: 'Personal', types: ['personal'] },
  { id: 'other', title: 'Other', types: ['other'] },
]

function byEntry(groups: FactGroup[]): Array<[string | null, FactGroup[]]> {
  const entries = new Map<string | null, FactGroup[]>()
  for (const g of groups) entries.set(g.context, [...(entries.get(g.context) ?? []), g])
  // Facts with no entry last.
  return [...entries.entries()].sort(([a], [b]) => (a === null ? 1 : b === null ? -1 : 0))
}

function FactRow({ group, onDelete }: { group: FactGroup; onDelete: (group: FactGroup) => void }) {
  return (
    <tr className="flex flex-wrap items-center gap-2 border-b-2 border-ink/15 px-4 py-3 last:border-0 sm:table-row sm:p-0">
      <td className="sm:w-32 sm:px-4 sm:py-3">
        <TypeTag type={group.type} />
      </td>
      <td className="w-full break-words sm:w-auto sm:px-4 sm:py-3">
        <span className="font-semibold">{group.claim_text}</span>
        {group.confirmed_by_user && (
          <span className="ml-2 font-mono text-[11px] font-medium uppercase">· confirmed by you</span>
        )}
      </td>
      <td className="sm:px-4 sm:py-3">
        <span className="flex flex-wrap gap-1.5">
          {group.sources.map((s) => (
            <SourceBadge key={s.id} fact={s} />
          ))}
        </span>
      </td>
      <td className="whitespace-nowrap font-mono text-xs sm:px-4 sm:py-3">{dateFormat.format(new Date(group.created_at))}</td>
      <td className="ml-auto sm:px-4 sm:py-3 sm:text-right">
        <button
          type="button"
          onClick={() => onDelete(group)}
          className="inline-flex items-center gap-1 rounded-lg border-2 border-transparent px-2 py-1 text-xs font-semibold hover:border-ink hover:bg-blossom"
          aria-label={`Delete fact: ${group.claim_text}`}
        >
          <Trash2 className="size-4" aria-hidden />
          <span className="sm:sr-only">Delete</span>
        </button>
      </td>
    </tr>
  )
}

function FactTable({
  groups,
  grouped,
  onDelete,
}: {
  groups: FactGroup[]
  grouped: boolean
  onDelete: (group: FactGroup) => void
}) {
  return (
    <div className="panel overflow-hidden bg-card">
      <table className="w-full text-left text-sm">
        {grouped ? (
          byEntry(groups).map(([entry, items]) => (
            <tbody key={entry ?? 'none'}>
              <tr>
                <th colSpan={5} className="border-b-2 border-ink bg-buttercup/60 px-4 py-1.5 text-left text-xs font-bold">
                  {entry ?? 'Not tied to one entry'}
                </th>
              </tr>
              {items.map((g) => (
                <FactRow key={g.key} group={g} onDelete={onDelete} />
              ))}
            </tbody>
          ))
        ) : (
          <tbody>
            {groups.map((g) => (
              <FactRow key={g.key} group={g} onDelete={onDelete} />
            ))}
          </tbody>
        )}
      </table>
    </div>
  )
}

function SkillChips({ groups, onDelete }: { groups: FactGroup[]; onDelete: (group: FactGroup) => void }) {
  return (
    <ul className="panel flex flex-wrap gap-2 bg-card p-4">
      {groups.map((g) => (
        <li
          key={g.key}
          className="inline-flex items-center gap-1.5 rounded-full border-2 border-ink bg-bubbles/50 py-0.5 pl-3 pr-1 text-sm font-semibold"
        >
          {g.claim_text}
          <span className="flex gap-1">
            {g.sources.map((s) => (
              <SourceBadge key={s.id} fact={s} />
            ))}
          </span>
          <button
            type="button"
            onClick={() => onDelete(g)}
            className="rounded-full p-1 hover:bg-blossom"
            aria-label={`Delete skill: ${g.claim_text}`}
          >
            <Trash2 className="size-3.5" aria-hidden />
          </button>
        </li>
      ))}
    </ul>
  )
}

export default function Ledger() {
  const [q, setQ] = useState('')
  const [type, setType] = useState<FactType | 'all'>('all')
  const [source, setSource] = useState<FactSource | 'all'>('all')
  const query = useDeferredValue(q.trim())
  const qc = useQueryClient()
  const toast = useToast()
  const key = ['ledger', query, type, source] as const

  const ledger = useQuery({
    queryKey: key,
    queryFn: () => {
      const params = new URLSearchParams()
      if (query) params.set('q', query)
      if (type !== 'all') params.set('type', type)
      if (source !== 'all') params.set('source', source)
      return api<Evidence[]>(`/api/ledger?${params.toString()}`)
    },
    placeholderData: keepPreviousData,
  })

  const remove = useMutation({
    // A fact may be proven by several sources; removing it removes every copy.
    mutationFn: (group: FactGroup) =>
      Promise.all(group.sources.map((s) => api<void>(`/api/ledger/${s.id}`, { method: 'DELETE' }))),
    // Optimistic: the row disappears at once and comes back if the server refuses.
    onMutate: async (group) => {
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData<Evidence[]>(key)
      const ids = new Set(group.sources.map((s) => s.id))
      qc.setQueryData<Evidence[]>(key, (rows) => rows?.filter((r) => !ids.has(r.id)))
      return { previous }
    },
    onError: (err, _group, context) => {
      qc.setQueryData(key, context?.previous)
      toast(err.message, 'error')
    },
    onSuccess: () => toast('Fact removed. The squad will ask you before using it again.'),
    onSettled: () => void qc.invalidateQueries({ queryKey: ['ledger'] }),
  })

  const rows = ledger.data ?? []
  const groups = groupFacts(rows)
  const filtered = query || type !== 'all' || source !== 'all'

  return (
    <div className="flex flex-col gap-6">
      <section className="panel hearts flex flex-wrap items-end justify-between gap-4 p-6">
        <div>
          <h1 className="font-display text-5xl tracking-wide">Evidence ledger</h1>
          <p className="mt-1 max-w-2xl font-semibold">
            Every fact the squad may say about you, and where it came from. Hover a badge to see the exact line or
            link. Delete anything you don't want used.
          </p>
        </div>
        <span className="rounded-lg border-3 border-ink bg-card px-3 py-1.5 font-mono text-sm font-medium">
          {ledger.isPending ? '…' : `${groups.length} facts · ${rows.length} proofs`}
        </span>
      </section>

      <div className="flex flex-col gap-3">
        <label className="relative">
          <span className="sr-only">Search facts</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2" aria-hidden />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search: python, 94%, MediScan, NUST…"
            maxLength={100}
            className="w-full rounded-lg border-3 border-ink bg-white py-2 pl-9 pr-3"
          />
        </label>
        <FilterChips
          label="Filter by type"
          options={TYPES.map((t) => ({ value: t, label: t }))}
          value={type}
          onChange={setType}
        />
        <FilterChips label="Filter by source" options={SOURCES} value={source} onChange={setSource} />
      </div>

      {ledger.error && <ErrorNote>{ledger.error.message}</ErrorNote>}

      {ledger.isPending ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-14" />
          ))}
        </div>
      ) : groups.length === 0 ? (
        <div className="panel bg-card p-8 text-center">
          <p className="font-display text-3xl tracking-wide">{filtered ? 'No matches' : 'No evidence yet'}</p>
          {type === 'role' || type === 'employer' ? (
            <p className="mt-2 font-semibold">
              Roles and employers are read by the AI step. Add a Groq key to turn it on, then re-upload your resume.
            </p>
          ) : (
            !filtered && (
              <p className="mt-2 font-semibold">
                <Link to="/onboarding" className="underline">
                  Upload a resume or try the sample data
                </Link>{' '}
                to build your ledger.
              </p>
            )
          )}
        </div>
      ) : type === 'all' ? (
        <>
          <nav aria-label="Sections" className="flex flex-wrap gap-2">
            {SECTIONS.map((s) => {
              const count = groups.filter((g) => s.types.includes(g.type)).length
              return count ? (
                <a
                  key={s.id}
                  href={`#ledger-${s.id}`}
                  className="rounded-lg border-3 border-ink bg-card px-3 py-1 text-sm font-semibold shadow-comic-sm"
                >
                  {s.title} <span className="font-mono text-xs">{count}</span>
                </a>
              ) : null
            })}
          </nav>
          {SECTIONS.map((s) => {
            const inSection = groups.filter((g) => s.types.includes(g.type))
            if (!inSection.length) return null
            return (
              <section key={s.id} id={`ledger-${s.id}`} className="flex scroll-mt-6 flex-col gap-2">
                <h2 className="font-display text-3xl tracking-wide">{s.title}</h2>
                {s.chips ? (
                  <SkillChips groups={inSection} onDelete={(g) => remove.mutate(g)} />
                ) : (
                  <FactTable groups={inSection} grouped={Boolean(s.byEntry)} onDelete={(g) => remove.mutate(g)} />
                )}
              </section>
            )
          })}
        </>
      ) : (
        <FactTable
          groups={groups}
          grouped={SECTIONS.some((s) => s.byEntry && s.types.includes(type))}
          onDelete={(g) => remove.mutate(g)}
        />
      )}
    </div>
  )
}
