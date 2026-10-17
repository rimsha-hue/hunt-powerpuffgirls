import { motion } from 'motion/react'
import { contextNote, groupFacts, uniqueSources, type Evidence } from '../lib/api'
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
          className="flex flex-col gap-1.5 rounded-lg border-2 border-ink bg-white px-3 py-2"
        >
          <div className="flex items-start gap-2">
            <TypeTag type={group.type} />
            <span className="min-w-0 flex-1 text-sm font-semibold [overflow-wrap:break-word]">
              {group.claim_text}
              {contextNote(group) && (
                <span className="block text-xs font-medium text-ink/70">in {contextNote(group)}</span>
              )}
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {uniqueSources(group).map((source) => (
              <SourceBadge key={source.id} fact={source} />
            ))}
          </div>
        </motion.li>
      ))}
    </ul>
  )
}
