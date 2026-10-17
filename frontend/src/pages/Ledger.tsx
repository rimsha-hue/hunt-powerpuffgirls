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
      ) : (
        <div className="panel overflow-hidden bg-card">
          <table className="w-full text-left text-sm">
            <thead className="hidden border-b-3 border-ink bg-violet text-card sm:table-header-group">
              <tr>
                <th className="px-4 py-2 font-mono text-xs font-medium uppercase">Type</th>
                <th className="px-4 py-2 font-mono text-xs font-medium uppercase">Fact</th>
                <th className="px-4 py-2 font-mono text-xs font-medium uppercase">Sources</th>
                <th className="px-4 py-2 font-mono text-xs font-medium uppercase">Added</th>
                <th className="px-4 py-2">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => (
                <tr
                  key={group.key}
                  className="flex flex-wrap items-center gap-2 border-b-2 border-ink/15 px-4 py-3 last:border-0 sm:table-row sm:p-0"
                >
                  <td className="sm:px-4 sm:py-3">
                    <TypeTag type={group.type} />
                  </td>
                  <td className="w-full break-words sm:w-auto sm:px-4 sm:py-3">
                    <span className="font-semibold">{group.claim_text}</span>
                    {group.confirmed_by_user && (
                      <span className="ml-2 font-mono text-[11px] font-medium uppercase">· confirmed by you</span>
                    )}
                    {group.context && (
                      <span className="mt-0.5 block font-mono text-[11px] font-medium text-ink/70">in {group.context}</span>
                    )}
                  </td>
                  <td className="sm:px-4 sm:py-3">
                    <span className="flex flex-wrap gap-1.5">
                      {group.sources.map((s) => (
                        <SourceBadge key={s.id} fact={s} />
                      ))}
                    </span>
                  </td>
                  <td className="whitespace-nowrap font-mono text-xs sm:px-4 sm:py-3">
                    {dateFormat.format(new Date(group.created_at))}
                  </td>
                  <td className="ml-auto sm:px-4 sm:py-3 sm:text-right">
                    <button
                      type="button"
                      onClick={() => remove.mutate(group)}
                      className="inline-flex items-center gap-1 rounded-lg border-2 border-transparent px-2 py-1 text-xs font-semibold hover:border-ink hover:bg-blossom"
                      aria-label={`Delete fact: ${group.claim_text}`}
                    >
                      <Trash2 className="size-4" aria-hidden />
                      <span className="sm:sr-only">Delete</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
