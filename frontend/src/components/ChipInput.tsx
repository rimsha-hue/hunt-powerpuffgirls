import { X } from 'lucide-react'
import { useId, useState, type KeyboardEvent } from 'react'

interface Props {
  label: string
  values: string[]
  onChange: (values: string[]) => void
  placeholder?: string
  max?: number
}

/** Type and press Enter (or comma) to add; each chip has a labelled remove button. */
export function ChipInput({ label, values, onChange, placeholder, max = 10 }: Props) {
  const [draft, setDraft] = useState('')
  const id = useId()

  function add() {
    const value = draft.trim().slice(0, 100)
    if (value && values.length < max && !values.some((v) => v.toLowerCase() === value.toLowerCase())) {
      onChange([...values, value])
    }
    setDraft('')
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      add()
    } else if (event.key === 'Backspace' && !draft && values.length) {
      onChange(values.slice(0, -1))
    }
  }

  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-semibold">
        {label}
      </label>
      <div className="flex flex-wrap items-center gap-2 rounded-lg border-3 border-ink bg-white p-2">
        {values.map((value) => (
          <span key={value} className="inline-flex items-center gap-1 rounded-full border-2 border-ink bg-bubbles py-0.5 pl-2.5 pr-1 text-sm font-semibold">
            {value}
            <button
              type="button"
              onClick={() => onChange(values.filter((v) => v !== value))}
              className="grid size-5 place-items-center rounded-full hover:bg-blossom"
              aria-label={`Remove ${value}`}
            >
              <X className="size-3.5" aria-hidden />
            </button>
          </span>
        ))}
        <input
          id={id}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onBlur={add}
          placeholder={values.length >= max ? `Up to ${max}` : placeholder}
          disabled={values.length >= max}
          className="min-w-32 flex-1 bg-transparent px-1 py-1 outline-none"
        />
      </div>
    </div>
  )
}
