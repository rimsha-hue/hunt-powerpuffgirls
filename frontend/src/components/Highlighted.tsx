import type { ReactNode } from 'react'
import type { JobKeyword } from '../lib/api'
import { cx } from '../lib/cx'

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** The description as plain text, with the job's keywords marked. Never rendered as HTML. */
export function Highlighted({ text, keywords }: { text: string; keywords: JobKeyword[] }) {
  const terms = [...new Set(keywords.map((k) => k.term))].sort((a, b) => b.length - a.length)
  if (terms.length === 0) return <>{text}</>
  const found = new Map(keywords.map((k) => [k.term.toLowerCase(), k.found]))
  const pattern = new RegExp(`(?<![\\w+#])(${terms.map(escapeRegExp).join('|')})(?![\\w+#])`, 'gi')
  const parts: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(pattern)) {
    const start = match.index
    parts.push(text.slice(last, start))
    const isFound = found.get(match[0].toLowerCase())
    parts.push(
      <mark
        key={start}
        className={cx('rounded px-0.5 text-ink', isFound ? 'bg-buttercup' : 'bg-blush underline decoration-dotted')}
        title={isFound ? 'In your CV' : 'Not in your CV yet'}
      >
        {match[0]}
      </mark>,
    )
    last = start + match[0].length
  }
  parts.push(text.slice(last))
  return <>{parts}</>
}

