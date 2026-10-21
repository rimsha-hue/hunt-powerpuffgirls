import { Link } from 'react-router'
import { PublicCard } from '../components/PublicCard'

export default function Terms() {
  return (
    <PublicCard title="Terms of use" wide>
      <ol className="flex list-decimal flex-col gap-3 pl-5 text-sm">
        <li>
          Power Hunt helps you find jobs and prepare applications. You decide what is sent: nothing goes out in your
          name until you approve it, and you submit employers' forms yourself.
        </li>
        <li>
          Everything you upload or confirm must be true and yours. Power Hunt checks the claims it writes against your
          evidence, but you are responsible for what you send to employers.
        </li>
        <li>
          Don't use the service to send spam, to apply for someone else, or to attack or overload it. Sending is
          limited per account, and accounts that break these terms may be suspended.
        </li>
        <li>
          Job postings come from public sources and may be out of date or wrong. Scores are estimates, not promises of
          an interview.
        </li>
        <li>
          The service is provided as it is, without guarantees, and may change or stop. Keep your own copy of anything
          important; you can download your data from your profile.
        </li>
        <li>
          How we handle your data is described in the{' '}
          <Link to="/privacy" className="font-semibold underline">
            privacy notice
          </Link>
          .
        </li>
      </ol>
      <p className="mt-5 text-xs">Power Hunt is a student project. These terms aren't legal advice.</p>
    </PublicCard>
  )
}
