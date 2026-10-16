import { motion } from 'motion/react'
import type { Evidence } from '../lib/api'
import { useCalm } from '../lib/calm'
import { SourceBadge, TypeTag } from './ui'

const MAX_TOTAL_STAGGER_S = 0.8

/**
 * Facts render immediately; the stagger only decorates their arrival and is capped so the
 * whole list lands within 800 ms, however many facts there are.
 */
export function FactList({ facts }: { facts: Evidence[] }) {
  const calm = useCalm()
  const step = facts.length ? Math.min(0.06, MAX_TOTAL_STAGGER_S / facts.length) : 0

  return (
    <ul className="flex flex-col gap-2">
      {facts.map((fact, index) => (
        <motion.li
          key={fact.id}
          initial={calm ? false : { opacity: 0, x: -12 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: index * step, duration: 0.18 }}
          className="flex flex-wrap items-center gap-2 rounded-lg border-2 border-ink bg-white px-3 py-2"
        >
          <TypeTag type={fact.type} />
          <span className="min-w-0 flex-1 text-sm font-semibold break-words">{fact.claim_text}</span>
          <SourceBadge fact={fact} />
        </motion.li>
      ))}
    </ul>
  )
}
