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
