import {
  BadgeCheck,
  Link2,
  type LucideIcon,
  Mail,
  MousePointerClick,
  PenLine,
  Scissors,
  Search,
  Send,
  ShieldCheck,
  Sprout,
  Stamp,
  Target,
  CircleHelp,
  Zap,
} from 'lucide-react'
import { type CheckState, useHealth } from './useHealth'

interface Agent {
  name: string
  job: string
  icons: LucideIcon[]
}

const SQUAD: Agent[] = [
  { name: 'Scout', job: 'Finds and dedupes jobs', icons: [Search] },
  { name: 'Matcher', job: 'Scores fit and ATS', icons: [Target] },
  { name: 'Tailor', job: 'Rewrites your CV, never invents', icons: [Scissors, PenLine] },
  { name: 'Verifier', job: 'Proves every claim', icons: [ShieldCheck] },
  { name: 'Coach', job: 'Turns gaps into projects', icons: [Sprout] },
  { name: 'Courier', job: 'Sends only after you approve', icons: [Mail] },
]

const GATES: { step: number; title: string; body: string; icon: LucideIcon }[] = [
  { step: 1, title: 'Choose', body: 'You pick the job. A career call, not a matching problem.', icon: MousePointerClick },
  { step: 2, title: 'Verify', body: 'You answer every claim the squad could not prove.', icon: BadgeCheck },
  { step: 3, title: 'Approve', body: 'Nothing goes out under your name without your OK.', icon: Stamp },
  { step: 4, title: 'Submit', body: 'You press submit on the employer’s own site.', icon: Send },
]

export default function App() {
  return (
    <div className="min-h-screen">
      <Header />
      <main className="mx-auto flex max-w-6xl flex-col gap-10 px-4 pb-16 sm:px-6">
        <Hero />
        <Squad />
        <Gates />
      </main>
      <footer className="border-t-3 border-ink bg-ink px-4 py-5 text-center font-mono text-xs text-cream">
        Power Hunt · built for NASCON ’26 Agentika 2.0 · Human + AI: Better Together
      </footer>
    </div>
  )
}

function Header() {
  return (
    <header className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-5 sm:px-6">
      <a href="/" className="flex items-center gap-2" aria-label="Power Hunt home">
        <span className="grid size-10 place-items-center rounded-lg border-3 border-ink bg-signal shadow-comic-sm">
          <Zap className="size-5 fill-coral text-ink" strokeWidth={2.5} aria-hidden />
        </span>
        <span className="font-display text-3xl tracking-wide">Power Hunt</span>
      </a>
      <SystemStatus />
    </header>
  )
}

function SystemStatus() {
  const health = useHealth()

  if (health.kind === 'loading') {
    return <span className="h-8 w-40 animate-pulse rounded-full border-3 border-ink bg-cream" aria-label="Checking system status" />
  }

  const ok = health.kind === 'ready' && health.report.status === 'ok'
  const label = ok ? 'All systems go' : health.kind === 'unreachable' ? 'Backend unreachable' : 'Degraded'

  return (
    <div className="group relative">
      <span
        className={`inline-flex items-center gap-2 rounded-full border-3 border-ink px-3 py-1 font-mono text-xs font-medium ${ok ? 'bg-signal' : 'bg-coral'}`}
        role="status"
        tabIndex={0}
      >
        <span className={`size-2.5 rounded-full border-2 border-ink ${ok ? 'bg-verified' : 'bg-ink'}`} aria-hidden />
        {label}
      </span>
      {health.kind === 'ready' && (
        <div className="invisible absolute right-0 top-full z-10 mt-3 w-56 rounded-xl border-3 border-ink bg-cream p-3 font-mono text-xs shadow-comic-sm group-focus-within:visible group-hover:visible">
          <span className="absolute -top-2 right-6 size-3.5 rotate-45 border-l-3 border-t-3 border-ink bg-cream" aria-hidden />
          <dl className="grid grid-cols-2 gap-y-1">
            <StatusRow name="database" state={health.report.database} />
            <StatusRow name="llm" state={health.report.llm} />
            <StatusRow name="scheduler" state={health.report.scheduler} />
            <dt>version</dt>
            <dd className="text-right">{health.report.version}</dd>
          </dl>
        </div>
      )}
    </div>
  )
}

function StatusRow({ name, state }: { name: string; state: CheckState }) {
  return (
    <>
      <dt>{name}</dt>
      <dd className="text-right">{state.replace('_', ' ')}</dd>
    </>
  )
}

function Hero() {
  return (
    <section className="panel halftone relative overflow-hidden bg-signal px-6 py-10 sm:px-10 sm:py-14">
      <p className="mb-4 inline-block -rotate-2 rounded-md border-3 border-ink bg-cream px-3 py-1 font-mono text-xs font-medium uppercase tracking-widest">
        Phase 0 · skeleton online
      </p>
      <h1 className="max-w-3xl font-display text-6xl leading-[0.95] tracking-wide sm:text-8xl">
        No source, <span className="inline-block rotate-1 rounded-lg border-3 border-ink bg-coral px-3 shadow-comic-sm">no claim.</span>
      </h1>
      <p className="mt-6 max-w-2xl text-lg font-semibold leading-relaxed">
        You are the hero. Your squad finds jobs, tailors your CV and drafts every application, then proves each line it
        wrote against your resume, your GitHub or your own word. Anything it cannot prove, it asks you about.
      </p>
      <div className="mt-8 flex flex-wrap gap-3" aria-label="Claim badges">
        <ClaimBadge tone="bg-verified text-cream" icon={BadgeCheck} label="Verified" />
        <ClaimBadge tone="bg-inferred text-cream" icon={Link2} label="Inferred" />
        <ClaimBadge tone="bg-coral text-ink" icon={CircleHelp} label="Needs you" />
      </div>
    </section>
  )
}

function ClaimBadge({ tone, icon: Icon, label }: { tone: string; icon: LucideIcon; label: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border-3 border-ink px-3 py-1 font-mono text-sm font-medium ${tone}`}>
      <Icon className="size-4" strokeWidth={2.5} aria-hidden />
      {label}
    </span>
  )
}

function Squad() {
  return (
    <section aria-labelledby="squad-title">
      <h2 id="squad-title" className="mb-5 font-display text-4xl tracking-wide">
        The squad
      </h2>
      <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {SQUAD.map(({ name, job, icons }) => (
          <li key={name} className="panel lift flex items-center gap-4 bg-cream p-4">
            <span className="flex size-14 shrink-0 items-center justify-center gap-0.5 rounded-xl border-3 border-ink bg-violet text-cream">
              {icons.map((Icon, i) => (
                <Icon key={i} className={icons.length > 1 ? 'size-5' : 'size-7'} strokeWidth={2.25} aria-hidden />
              ))}
            </span>
            <div>
              <p className="font-display text-2xl tracking-wide">{name}</p>
              <p className="text-sm font-semibold">{job}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

function Gates() {
  return (
    <section aria-labelledby="gates-title" className="panel bg-violet p-6 text-cream sm:p-8">
      <h2 id="gates-title" className="font-display text-4xl tracking-wide">
        Four human gates
      </h2>
      <p className="mt-1 font-semibold">The AI does the legwork. You make every call that matters.</p>
      <ol className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        {GATES.map(({ step, title, body, icon: Icon }) => (
          <li key={step} className="panel relative bg-cream p-5 text-ink">
            <span className="absolute -left-3 -top-3 grid size-9 place-items-center rounded-full border-3 border-ink bg-signal font-mono text-sm font-medium">
              {step}
            </span>
            <Icon className="mb-3 size-7" strokeWidth={2.25} aria-hidden />
            <p className="font-display text-2xl tracking-wide">{title}</p>
            <p className="mt-1 text-sm font-semibold leading-snug">{body}</p>
          </li>
        ))}
      </ol>
    </section>
  )
}
