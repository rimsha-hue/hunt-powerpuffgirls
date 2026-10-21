import { ArrowLeft } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Logo } from './Logo'

/** A centred card for pages reached from an email link (reset, unsubscribe): no app chrome,
 * because the visitor may not be signed in. */
export function PublicCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-5 sm:px-6">
        <Link to="/" aria-label="Power Hunt home">
          <Logo />
        </Link>
        <Link
          to="/login"
          className="inline-flex items-center gap-1.5 rounded-lg border-3 border-transparent px-3 py-1.5 font-semibold hover:border-ink"
        >
          <ArrowLeft className="size-4" aria-hidden />
          Log in
        </Link>
      </header>
      <main className="mx-auto flex max-w-md flex-col px-4 pb-16 pt-6 sm:px-6">
        <section className="panel bg-card p-6">
          <h1 className="mb-4 font-display text-4xl tracking-wide">{title}</h1>
          {children}
        </section>
      </main>
    </div>
  )
}
