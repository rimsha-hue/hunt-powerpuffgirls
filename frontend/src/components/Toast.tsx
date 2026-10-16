import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useRef, useState, type ReactNode } from 'react'
import { useCalm } from '../lib/calm'
import { ToastContext, type ToastTone } from '../lib/toast'

interface ToastItem {
  id: number
  text: string
  tone: ToastTone
}

/** Speech-bubble toasts, bottom right. They announce outcomes; they never block them. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const next = useRef(1)
  const calm = useCalm()

  const push = useCallback((text: string, tone: ToastTone = 'info') => {
    const id = next.current++
    setItems((current) => [...current.slice(-2), { id, text, tone }])
    window.setTimeout(() => setItems((current) => current.filter((t) => t.id !== id)), 4000)
  }, [])

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-3" aria-live="polite">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div
              key={t.id}
              initial={calm ? false : { opacity: 0, y: 12, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={calm ? { opacity: 0 } : { opacity: 0, y: 8 }}
              transition={{ duration: 0.18 }}
              className={`relative rounded-2xl border-3 border-ink px-4 py-3 text-sm font-semibold shadow-comic-sm ${t.tone === 'error' ? 'bg-coral' : 'bg-white'}`}
              role={t.tone === 'error' ? 'alert' : 'status'}
            >
              {t.text}
              <span
                className={`absolute -bottom-2 right-8 size-3.5 rotate-45 border-b-3 border-r-3 border-ink ${t.tone === 'error' ? 'bg-coral' : 'bg-white'}`}
                aria-hidden
              />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  )
}
