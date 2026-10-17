import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, json, setCsrfToken, type Me } from './api'

export const meKey = ['me'] as const

async function fetchMe(): Promise<Me | null> {
  try {
    const me = await api<Me>('/api/auth/me')
    setCsrfToken(me.csrf_token)
    return me
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      setCsrfToken(null)
      return null
    }
    throw err
  }
}

export function useMe() {
  return useQuery({ queryKey: meKey, queryFn: fetchMe, staleTime: 60_000, retry: false })
}

interface Credentials {
  email: string
  password: string
  name?: string
}

export function useAuthMutation(mode: 'login' | 'signup') {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Credentials) =>
      api<Me>(`/api/auth/${mode}`, { method: 'POST', body: json(body) }),
    onSuccess: (me) => {
      setCsrfToken(me.csrf_token)
      // A new session starts from an empty cache: answers fetched before it (a 401 from an expired
      // session, another account's data) must never show under the new one.
      qc.clear()
      qc.setQueryData(meKey, me)
    },
  })
}

export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api<void>('/api/auth/logout', { method: 'POST' }),
    onSettled: () => {
      setCsrfToken(null)
      qc.clear()
      qc.setQueryData(meKey, null)
    },
  })
}
