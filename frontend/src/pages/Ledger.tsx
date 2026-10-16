import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, Trash2 } from 'lucide-react'
import { useDeferredValue, useState } from 'react'
import { Link } from 'react-router'
import { ErrorNote, Skeleton, SourceBadge, TypeTag } from '../components/ui'
import { api, type Evidence, type FactType } from '../lib/api'
import { cx } from '../lib/cx'
import { useToast } from '../lib/toast'

const TYPES: Array<FactType | 'all'> = ['all', 'skill', 'metric', 'role', 'employer', 'degree', 'certification', 'project']

const dateFormat = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

export default function Ledger() {
  const [q, setQ] = useState('')
  const [type, setType] = useState<FactType | 'all'>('all')
  const query = useDeferredValue(q.trim())
  const qc = useQueryClient()
  const toast = useToast()
  const key = ['ledger', query, type] as const

  const ledger = useQuery({
    queryKey: key,
    queryFn: () => {
      const params = new URLSearchParams()
      if (query) params.set('q', query)
      if (type !== 'all') params.set('type', type)
      return api<Evidence[]>(`/api/ledger?${params.toString()}`)
    },
    placeholderData: keepPreviousData,
  })

  const remove = useMutation({
    mutationFn: (id: number) => api<void>(`/api/ledger/${id}`, { method: 'DELETE' }),
    // Optimistic: the row disappears at once and comes back if the server refuses.
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData<Evidence[]>(key)
      qc.setQueryData<Evidence[]>(key, (rows) => rows?.filter((r) => r.id !== id))
      return { previous }
    },
    onError: (err, _id, context) => {
      qc.setQueryData(key, context?.previous)
      toast(err.message, 'error')
    },
    onSuccess: () => toast('Fact removed. The squad will ask you before using it again.'),
    onSettled: () => void qc.invalidateQueries({ queryKey: ['ledger'] }),
  })

  const rows = ledger.data ?? []

  return (
    <div className="flex flex-col gap-6">
      <section className="panel halftone flex flex-wrap items-end justify-between gap-4 bg-signal p-6">
        <div>
          <h1 className="font-display text-5xl tracking-wide">Evidence ledger</h1>
          <p className="mt-1 max-w-2xl font-semibold">
            Every fact the squad may say about you, and where it came from. Delete anything you don't want used.
          </p>
        </div>
        <span className="rounded-lg border-3 border-ink bg-cream px-3 py-1.5 font-mono text-sm font-medium">
          {ledger.isPending ? '…' : rows.length} facts
        </span>
      </section>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <label className="relative flex-1">
          <span className="sr-only">Search facts</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2" aria-hidden />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search: python, 40%, NUST…"
            maxLength={100}
            className="w-full rounded-lg border-3 border-ink bg-white py-2 pl-9 pr-3"
          />
        </label>
        <div className="flex gap-2 overflow-x-auto pb-1" role="radiogroup" aria-label="Filter by type">
          {TYPES.map((t) => (
            <button
              key={t}
              type="button"
              role="radio"
              aria-checked={type === t}
              onClick={() => setType(t)}
              className={cx(
                'shrink-0 rounded-full border-2 border-ink px-3 py-1 font-mono text-xs font-medium uppercase',
                type === t ? 'bg-ink text-cream' : 'bg-white',
              )}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {ledger.error && <ErrorNote>{ledger.error.message}</ErrorNote>}

      {ledger.isPending ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-14" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="panel bg-cream p-8 text-center">
          <p className="font-display text-3xl tracking-wide">{query || type !== 'all' ? 'No matches' : 'No evidence yet'}</p>
          {!query && type === 'all' && (
            <p className="mt-2 font-semibold">
              <Link to="/onboarding" className="underline">
                Upload a resume or try the sample data
              </Link>{' '}
              to build your ledger.
            </p>
          )}
        </div>
      ) : (
        <div className="panel overflow-hidden bg-cream">
          <table className="w-full text-left text-sm">
            <thead className="hidden border-b-3 border-ink bg-violet text-cream sm:table-header-group">
              <tr>
                <th className="px-4 py-2 font-mono text-xs font-medium uppercase">Type</th>
                <th className="px-4 py-2 font-mono text-xs font-medium uppercase">Fact</th>
                <th className="px-4 py-2 font-mono text-xs font-medium uppercase">Source</th>
                <th className="px-4 py-2 font-mono text-xs font-medium uppercase">Added</th>
                <th className="px-4 py-2">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((fact) => (
                <tr key={fact.id} className="flex flex-wrap items-center gap-2 border-b-2 border-ink/15 px-4 py-3 last:border-0 sm:table-row sm:p-0">
                  <td className="sm:px-4 sm:py-3">
                    <TypeTag type={fact.type} />
                  </td>
                  <td className="w-full font-semibold break-words sm:w-auto sm:px-4 sm:py-3">
                    {fact.claim_text}
                    {fact.confirmed_by === 'user' && (
                      <span className="ml-2 font-mono text-[11px] font-medium uppercase">· confirmed by you</span>
                    )}
                  </td>
                  <td className="sm:px-4 sm:py-3">
                    <SourceBadge fact={fact} />
                  </td>
                  <td className="font-mono text-xs sm:px-4 sm:py-3">{dateFormat.format(new Date(fact.created_at))}</td>
                  <td className="ml-auto sm:px-4 sm:py-3 sm:text-right">
                    <button
                      type="button"
                      onClick={() => remove.mutate(fact.id)}
                      className="inline-flex items-center gap-1 rounded-lg border-2 border-transparent px-2 py-1 text-xs font-semibold hover:border-ink hover:bg-coral"
                      aria-label={`Delete fact: ${fact.claim_text}`}
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
