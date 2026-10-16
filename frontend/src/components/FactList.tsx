import { motion } from 'motion/react'
import { groupFacts, type Evidence } from '../lib/api'
import { useCalm } from '../lib/calm'
import { SourceBadge, TypeTag } from './ui'

const MAX_TOTAL_STAGGER_S = 0.8

/**
 * Facts render immediately; the stagger only decorates their arrival and is capped so the
 * whole list lands within 800 ms, however many facts there are.
 */
export function FactList({ facts }: { facts: Evidence[] }) {
  const calm = useCalm()
  const groups = groupFacts(facts)
  const step = groups.length ? Math.min(0.06, MAX_TOTAL_STAGGER_S / groups.length) : 0

  return (
    <ul className="flex flex-col gap-2">
      {groups.map((group, index) => (
        <motion.li
          key={group.key}
          initial={calm ? false : { opacity: 0, x: -12 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: index * step, duration: 0.18 }}
          className="flex flex-wrap items-center gap-2 rounded-lg border-2 border-ink bg-white px-3 py-2"
        >
          <TypeTag type={group.type} />
          <span className="min-w-0 flex-1 text-sm font-semibold break-words">
            {group.claim_text}
            {group.context && <span className="block font-mono text-[11px] font-medium text-ink/70">in {group.context}</span>}
          </span>
          {group.sources.map((source) => (
            <SourceBadge key={source.id} fact={source} />
          ))}
        </motion.li>
      ))}
    </ul>
  )
}
