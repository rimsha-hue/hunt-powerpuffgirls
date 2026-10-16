import { useReducedMotion } from 'motion/react'
import { useMe } from './session'

/** True when animations should be off: OS "reduce motion" or the user's Calm mode. */
export function useCalm(): boolean {
  const reduced = useReducedMotion()
  const { data: me } = useMe()
  return Boolean(reduced) || Boolean(me?.calm_mode)
}
