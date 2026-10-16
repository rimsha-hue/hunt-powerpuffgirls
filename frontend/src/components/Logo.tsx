import { cx } from '../lib/cx'

/** The mark: a pink heart with a lightning bolt and three trio flight streaks. Original artwork. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true" focusable="false">
      <g fill="none" strokeLinecap="round" strokeWidth="5">
        <path d="M4 50 L14 44" stroke="var(--color-blossom)" />
        <path d="M6 58 L17 51" stroke="var(--color-bubbles)" />
        <path d="M14 62 L23 55" stroke="var(--color-buttercup)" />
      </g>
      <path
        d="M36 58 C 18 46, 8 36, 8 24 A 13 13 0 0 1 36 16 A 13 13 0 0 1 62 24 C 62 36, 54 46, 36 58 Z"
        fill="var(--color-blossom)"
        stroke="var(--color-ink)"
        strokeWidth="4"
        strokeLinejoin="round"
      />
      <path d="M17 22 A 7 7 0 0 1 24 16" fill="none" stroke="#fff" strokeWidth="3.5" strokeLinecap="round" />
      <path
        d="M39 19 L27 37 H35 L32 50 L46 30 H38 Z"
        fill="var(--color-signal)"
        stroke="var(--color-ink)"
        strokeWidth="3"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function Logo({ size = 'md' }: { size?: 'md' | 'lg' }) {
  return (
    <span className="flex items-center gap-2">
      <LogoMark className={size === 'lg' ? 'size-14' : 'size-10'} />
      <span className={cx('wordmark leading-none', size === 'lg' ? 'text-5xl' : 'text-3xl')}>Power Hunt</span>
    </span>
  )
}
