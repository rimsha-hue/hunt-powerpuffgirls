import { CircleHelp, Globe, Mail } from 'lucide-react'
import type { ApplyMethod } from '../lib/api'
import { cx } from '../lib/cx'

const METHOD: Record<ApplyMethod, { label: string; icon: typeof Mail; style: string }> = {
  email: { label: 'Email', icon: Mail, style: 'bg-bubbles' },
  form: { label: 'Form', icon: Globe, style: 'bg-buttercup' },
  unknown: { label: 'Unclear', icon: CircleHelp, style: 'bg-white' },
}

/** How to apply, as an icon and a word (never colour alone). */
export function MethodBadge({ method }: { method: ApplyMethod }) {
  const { label, icon: Icon, style } = METHOD[method]
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded-full border-2 border-ink px-2 py-0.5 font-mono text-xs font-medium',
        style,
      )}
    >
      <Icon className="size-3.5" strokeWidth={2.5} aria-hidden />
      {label}
    </span>
  )
}

/** Ink on each fill is at least 9:1 (see the palette notes in index.css). */
function scoreFill(value: number): string {
  if (value >= 70) return 'bg-buttercup'
  if (value >= 50) return 'bg-signal'
  return 'bg-blush'
}

export function ScoreChip({ label, value, size = 'md' }: { label: string; value: number; size?: 'md' | 'lg' }) {
  return (
    <div
      className={cx(
        'grid shrink-0 place-items-center rounded-xl border-3 border-ink text-center shadow-comic-sm',
        scoreFill(value),
        size === 'lg' ? 'size-24' : 'size-16',
      )}
      aria-label={`${label} ${value} out of 100`}
    >
      <span className={cx('font-mono font-bold leading-none', size === 'lg' ? 'text-4xl' : 'text-2xl')}>{value}</span>
      <span className="font-mono text-[10px] font-medium uppercase leading-none">{label}</span>
    </div>
  )
}
