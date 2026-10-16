import { createContext, useContext } from 'react'

export type ToastTone = 'info' | 'error'
export type PushToast = (text: string, tone?: ToastTone) => void

export const ToastContext = createContext<PushToast>(() => {})

export function useToast(): PushToast {
  return useContext(ToastContext)
}
