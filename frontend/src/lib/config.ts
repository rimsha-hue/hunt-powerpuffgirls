import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export interface Option {
  value: string
  label: string
}

/** Limits and choices published by the server, so the UI never hardcodes its own copy. */
export interface PublicConfig {
  max_upload_mb: number
  google_sign_in: boolean
  retention_unverified_days: number
  retention_email_log_days: number
  /** Empty when the server has no support address set. */
  support_email: string
  work_authorization: Option[]
  notice_period: Option[]
  salary_period: Option[]
}

export function usePublicConfig() {
  return useQuery({
    queryKey: ['public-config'],
    queryFn: () => api<PublicConfig>('/api/meta/config'),
    staleTime: Infinity,
  })
}
