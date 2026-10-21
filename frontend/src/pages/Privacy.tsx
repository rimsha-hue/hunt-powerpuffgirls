import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { PublicCard } from '../components/PublicCard'
import { usePublicConfig } from '../lib/config'

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-5">
      <h2 className="mb-1 text-lg font-bold">{title}</h2>
      <div className="flex flex-col gap-2 text-sm">{children}</div>
    </section>
  )
}

/** What Power Hunt stores, why, who processes it and for how long. The retention periods and the
 * contact address come from the server, so this page can't drift from what the service does. */
export default function Privacy() {
  const config = usePublicConfig().data
  return (
    <PublicCard title="Privacy notice" wide>
      <Section title="What we store">
        <p>
          Your account (email, name, password hash or Google sign-in), your profile and job preferences, your resume as
          numbered lines and the facts drawn from it (your evidence ledger), the GitHub and portfolio facts you link,
          the jobs found for you, your tailored CVs, emails and form answers, the claims checked in them and your
          answers, your growth plans, and an activity log of actions and decisions.
        </p>
        <p>
          We keep a log of the emails we send you or for you, with their kind and time only, never their content.
          Uploaded resume files aren't kept, only the text lines taken from them.
        </p>
      </Section>
      <Section title="Why">
        <p>
          To find jobs that fit you, tailor your applications and prove every claim they make about you. Nothing is
          sent in your name without your approval, and we don't sell or share your data for advertising.
        </p>
      </Section>
      <Section title="Who processes it for us">
        <ul className="list-disc pl-5">
          <li>Neon hosts the database.</li>
          <li>
            Groq and Google (Gemini) process resume text, job descriptions and drafts to extract facts and write
            text. Untrusted text is sent as data, and every reply is checked by our own code.
          </li>
          <li>JSearch (RapidAPI), Remotive and Arbeitnow receive the roles and places you search for.</li>
          <li>Tavily receives company names for company research.</li>
          <li>GitHub is read for the public repositories of the username you give.</li>
          <li>Jina Reader may fetch your public portfolio page.</li>
          <li>Gmail sends our emails. Google handles Google sign-in if you use it.</li>
        </ul>
      </Section>
      <Section title="How long we keep it">
        <p>
          Your data stays until you delete it. Accounts that never confirm their email are deleted after{' '}
          {config?.retention_unverified_days ?? 'a few'} days without a sign-in. The email log is kept for{' '}
          {config?.retention_email_log_days ?? 'a few'} days. Expired sign-in sessions and used links are removed
          automatically.
        </p>
      </Section>
      <Section title="Your rights">
        <p>
          From your <Link to="/profile" className="font-semibold underline">profile</Link> you can download a copy of
          everything stored about you, turn job digest emails off, and delete your account with all its data at once.
          Emails already sent to employers can't be recalled.
        </p>
        {config?.support_email && (
          <p>
            Questions about your data:{' '}
            <a href={`mailto:${config.support_email}`} className="font-semibold underline">
              {config.support_email}
            </a>
          </p>
        )}
      </Section>
      <p className="text-xs">
        Power Hunt is a student project. This notice describes how the service works; it isn't legal advice.
      </p>
    </PublicCard>
  )
}
