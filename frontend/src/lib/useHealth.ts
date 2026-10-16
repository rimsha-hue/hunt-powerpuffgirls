import { useEffect, useState } from 'react'

export type CheckState = 'ok' | 'down' | 'not_configured' | 'not_started'

export interface HealthReport {
  status: 'ok' | 'degraded'
  version: string
  database: CheckState
  llm: CheckState
  scheduler: CheckState
}

export type HealthResult =
  | { kind: 'loading' }
  | { kind: 'ready'; report: HealthReport }
  | { kind: 'unreachable' }

const TIMEOUT_MS = 5000

export function useHealth(): HealthResult {
  const [result, setResult] = useState<HealthResult>({ kind: 'loading' })

  useEffect(() => {
    let mounted = true
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS)

    // /health answers 503 with a full report when degraded, so read the body either way.
    fetch('/health', { signal: controller.signal, headers: { Accept: 'application/json' } })
      .then(async (res) => {
        const report = (await res.json()) as HealthReport
        if (mounted) setResult({ kind: 'ready', report })
      })
      .catch(() => {
        if (mounted) setResult({ kind: 'unreachable' })
      })
      .finally(() => window.clearTimeout(timer))

    return () => {
      mounted = false
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [])

  return result
}
