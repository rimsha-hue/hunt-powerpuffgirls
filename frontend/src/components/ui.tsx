import {
  Award,
  FileText,
  FolderGit2,
  Globe,
  type LucideIcon,
  UserCheck,
} from 'lucide-react'
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import { useId } from 'react'
import type { Evidence, FactSource, FactType } from '../lib/api'
import { cx } from '../lib/cx'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-coral text-ink shadow-comic-sm hover:-translate-x-0.5 hover:-translate-y-0.5',
  secondary: 'bg-cream text-ink shadow-comic-sm hover:-translate-x-0.5 hover:-translate-y-0.5',
  ghost: 'border-transparent bg-transparent text-ink hover:border-ink',
  danger: 'bg-cream text-ink hover:bg-coral',
}

export function Button({
  variant = 'primary',
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      {...props}
      className={cx(
        'inline-flex items-center justify-center gap-2 rounded-lg border-3 border-ink px-4 py-2 font-semibold transition-transform disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-x-0 disabled:hover:translate-y-0',
        VARIANTS[variant],
        className,
      )}
    >
      {children}
    </button>
  )
}

export function Panel({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cx('panel bg-cream p-5', className)}>{children}</section>
}

export function PanelTitle({ children, kicker }: { children: ReactNode; kicker?: string }) {
  return (
    <header className="mb-4">
      {kicker && <p className="font-mono text-xs font-medium uppercase tracking-widest">{kicker}</p>}
      <h2 className="font-display text-3xl tracking-wide">{children}</h2>
    </header>
  )
}

export function Field({
  label,
  hint,
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  const id = useId()
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block text-sm font-semibold">
        {label}
      </label>
      <input
        id={id}
        {...props}
        className="w-full rounded-lg border-3 border-ink bg-white px-3 py-2 text-ink placeholder:text-ink/50"
      />
      {hint && <p className="mt-1 text-xs">{hint}</p>}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('animate-pulse rounded-lg border-3 border-ink/20 bg-ink/10', className)} aria-hidden />
}

const SOURCE_ICONS: Record<FactSource, LucideIcon> = {
  resume: FileText,
  github: FolderGit2,
  certificate: Award,
  portfolio: Globe,
  user_confirmed: UserCheck,
}

/** Source badge whose speech-bubble tooltip shows exactly where the fact came from. */
export function SourceBadge({ fact }: { fact: Evidence }) {
  const Icon = SOURCE_ICONS[fact.source]
  return (
    <span className="group relative inline-flex">
      <span
        tabIndex={0}
        className="inline-flex items-center gap-1 rounded-full border-2 border-ink bg-white px-2 py-0.5 font-mono text-xs font-medium"
      >
        <Icon className="size-3.5" strokeWidth={2.5} aria-hidden />
        {fact.source_label}
      </span>
      <span
        role="tooltip"
        className="invisible absolute bottom-full left-0 z-20 mb-3 w-72 rounded-xl border-3 border-ink bg-white p-3 text-xs leading-snug text-ink shadow-comic-sm group-focus-within:visible group-hover:visible"
      >
        <span className="mb-1 block font-mono font-medium uppercase tracking-wider">Source</span>
        {fact.source_url ? (
          <a href={fact.source_url} target="_blank" rel="noopener noreferrer" className="break-all underline">
            {fact.source_url}
          </a>
        ) : (
          <span className="block">{fact.source_detail ?? fact.source_ref}</span>
        )}
        <span className="absolute -bottom-2 left-5 size-3.5 rotate-45 border-b-3 border-r-3 border-ink bg-white" aria-hidden />
      </span>
    </span>
  )
}

const TYPE_STYLES: Record<FactType, string> = {
  skill: 'bg-signal',
  metric: 'bg-coral',
  employer: 'bg-violet text-cream',
  role: 'bg-violet text-cream',
  degree: 'bg-white',
  certification: 'bg-white',
  project: 'bg-cream',
  personal: 'bg-cream',
}

export function TypeTag({ type }: { type: FactType }) {
  return (
    <span className={cx('rounded-md border-2 border-ink px-1.5 py-0.5 font-mono text-[11px] font-medium uppercase', TYPE_STYLES[type])}>
      {type}
    </span>
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-lg border-3 border-ink bg-coral px-3 py-2 text-sm font-semibold">
      {children}
    </p>
  )
}
