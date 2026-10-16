import { X } from 'lucide-react'
import { useEffect, useId, useState, type KeyboardEvent } from 'react'
import { cx } from '../lib/cx'

export interface Chip {
  value: string
  label: string
}

interface Props {
  label: string
  chips: Chip[]
  onChange: (chips: Chip[]) => void
  /** Suggestions for the typed text; called after a short pause, cancelled if typing continues. */
  suggest: (query: string, signal: AbortSignal) => Promise<Chip[]>
  /** true: typed text can become a chip (the server still validates it). false: pick from the list. */
  freeText: boolean
  placeholder?: string
  hint?: string
  error?: string | undefined
  max?: number
}

const MIN_QUERY = 2
const DEBOUNCE_MS = 150

/** Accessible combobox that collects chips, with server-backed suggestions. */
export function ChipInput({ label, chips, onChange, suggest, freeText, placeholder, hint, error, max = 10 }: Props) {
  const id = useId()
  const listId = `${id}-list`
  const [draft, setDraft] = useState('')
  const [options, setOptions] = useState<Chip[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [notice, setNotice] = useState<string | null>(null)
  const query = draft.trim()

  useEffect(() => {
    if (query.length < MIN_QUERY) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      suggest(query, controller.signal)
        .then((found) => {
          setOptions(found)
          setActive(found.length ? 0 : -1)
        })
        .catch(() => {
          if (!controller.signal.aborted) setOptions([])
        })
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [query, suggest])

  const visible = open && query.length >= MIN_QUERY ? options : []
  const full = chips.length >= max

  function add(chip: Chip) {
    if (!chips.some((c) => c.value.toLowerCase() === chip.value.toLowerCase())) onChange([...chips, chip])
    setDraft('')
    setOptions([])
    setOpen(false)
    setNotice(null)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
      if (visible.length) {
        const step = event.key === 'ArrowDown' ? 1 : -1
        setActive((i) => (i + step + visible.length) % visible.length)
      }
    } else if (event.key === 'Enter' || (event.key === ',' && freeText)) {
      event.preventDefault()
      const highlighted = visible[active]
      if (highlighted) add(highlighted)
      else if (freeText && query) add({ value: query, label: query })
      else if (query) setNotice('Pick one of the suggestions.')
    } else if (event.key === 'Escape') {
      setOpen(false)
    } else if (event.key === 'Backspace' && !draft && chips.length) {
      onChange(chips.slice(0, -1))
    }
  }

  const describedBy = [error && `${id}-error`, (hint || notice) && `${id}-hint`].filter(Boolean).join(' ')

  return (
    <div className="relative">
      <label htmlFor={id} className="mb-1 block text-sm font-semibold">
        {label}
      </label>
      <div
        className={cx(
          'flex flex-wrap items-center gap-2 rounded-lg border-3 bg-white p-2',
          error ? 'border-blossom' : 'border-ink',
        )}
      >
        {chips.map((chip) => (
          <span key={chip.value} className="inline-flex items-center gap-1 rounded-full border-2 border-ink bg-bubbles py-0.5 pl-2.5 pr-1 text-sm font-semibold">
            {chip.label}
            <button
              type="button"
              onClick={() => onChange(chips.filter((c) => c.value !== chip.value))}
              className="grid size-5 place-items-center rounded-full hover:bg-blossom"
              aria-label={`Remove ${chip.label}`}
            >
              <X className="size-3.5" aria-hidden />
            </button>
          </span>
        ))}
        <input
          id={id}
          role="combobox"
          aria-expanded={visible.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={visible[active] ? `${listId}-${active}` : undefined}
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy || undefined}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            setOpen(true)
            setNotice(null)
          }}
          onKeyDown={onKeyDown}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          placeholder={full ? `Up to ${max}` : placeholder}
          disabled={full}
          maxLength={100}
          autoComplete="off"
          className="min-w-40 flex-1 bg-transparent px-1 py-1 outline-none"
        />
      </div>
      {visible.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-30 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border-3 border-ink bg-white py-1 shadow-comic-sm"
        >
          {visible.map((option, index) => (
            <li
              key={option.value}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              // mousedown, not click: it fires before the input's blur closes the list
              onMouseDown={(e) => {
                e.preventDefault()
                add(option)
              }}
              onMouseEnter={() => setActive(index)}
              className={cx('cursor-pointer px-3 py-1.5 text-sm font-semibold', index === active && 'bg-blush')}
            >
              {option.label}
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p id={`${id}-error`} role="alert" className="mt-1 text-sm font-semibold text-ink">
          <span className="mr-1 rounded bg-blossom px-1.5 font-mono text-xs">!</span>
          {error}
        </p>
      )}
      {(notice || hint) && !error && (
        <p id={`${id}-hint`} className="mt-1 text-xs">
          {notice ?? hint}
        </p>
      )}
    </div>
  )
}
