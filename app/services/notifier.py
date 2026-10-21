"""Notifier: one digest email listing the new jobs at or above the user's match threshold, each
with a signed Apply link. A job is announced once; nothing is marked sent if sending fails."""

from dataclasses import dataclass
from datetime import datetime
from html import escape

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Notification, User
from app.integrations.email import EmailError, EmailProvider, OutgoingEmail
from app.services import apply_links, audit, mailer, unsubscribe

logger = structlog.get_logger()

MAX_DIGEST_JOBS = 10
METHOD_LABELS = {
    "email": "Apply by email",
    "form": "Apply on their site",
    "unknown": "Method unclear",
}

# Colours from the app palette; every pair is checked for 4.5:1 contrast in index.css.
_INK = "#1b1530"
_PAGE = "#ffe4f0"
_BLOSSOM = "#ff4f9a"  # a fill with ink text (5.6:1), never text itself


@dataclass
class DigestResult:
    sent: int = 0
    note: str | None = None


def _card(job: Job, url: str) -> str:
    place = "Remote" if job.remote and not job.location else job.location
    return f"""
<tr><td style="padding:0 0 16px">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="border:3px solid {_INK};border-radius:12px;background:#ffffff">
    <tr><td style="padding:16px">
      <p style="margin:0;font:700 18px/1.3 Arial,sans-serif;color:{_INK}">{escape(job.title)}</p>
      <p style="margin:4px 0 12px;font:14px/1.4 Arial,sans-serif;color:{_INK}">
        {escape(job.company)}{" &middot; " + escape(place) if place else ""}</p>
      <p style="margin:0 0 14px;font:13px/1.4 'Courier New',monospace;color:{_INK}">
        Match <b>{job.match_score}</b> &nbsp;|&nbsp; ATS <b>{job.ats_before}</b>
        &nbsp;|&nbsp; {escape(METHOD_LABELS.get(job.apply_method, job.apply_method))}</p>
      <a href="{escape(url, quote=True)}"
         style="display:inline-block;padding:10px 18px;border:3px solid {_INK};border-radius:10px;
                background:{_BLOSSOM};color:{_INK};font:700 15px Arial,sans-serif;
                text-decoration:none">Apply now</a>
    </td></tr>
  </table>
</td></tr>"""


def render_digest(
    jobs: list[Job], links: dict[int, str], hours: int, unsubscribe_url: str
) -> tuple[str, str, str]:
    """(subject, plain text, HTML). Job text is escaped; nothing from a posting is raw HTML."""
    count = len(jobs)
    subject = f"Power Hunt: {count} new job{'s' if count != 1 else ''} for you"
    lines = [f"{count} new job{'s' if count != 1 else ''} match what you're looking for.", ""]
    for job in jobs:
        lines += [
            f"{job.title}, {job.company}",
            f"Match {job.match_score} | ATS {job.ats_before} | "
            f"{METHOD_LABELS.get(job.apply_method, job.apply_method)}",
            f"Apply now: {links[job.id]}",
            "",
        ]
    lines.append(f"Each Apply link works once and expires in {hours} hours.")
    lines.append(f"Stop job digest emails: {unsubscribe_url}")
    cards = "".join(_card(job, links[job.id]) for job in jobs)
    html = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:{_PAGE}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:auto">
  <tr><td style="padding:0 0 16px">
    <p style="margin:0;font:700 26px Arial,sans-serif;color:{_INK}">Power Hunt</p>
    <p style="margin:6px 0 0;font:15px/1.4 Arial,sans-serif;color:{_INK}">{escape(lines[0])}</p>
  </td></tr>
  {cards}
  <tr><td style="font:12px/1.4 Arial,sans-serif;color:{_INK}">
    Each Apply link works once and expires in {hours} hours. Nothing is sent to an employer
    until you approve it.<br>
    <a href="{escape(unsubscribe_url, quote=True)}" style="color:{_INK}">Stop job digest emails</a>
  </td></tr>
</table></body></html>"""
    return subject, "\n".join(lines), html


async def send_digest(
    db: AsyncSession,
    user: User,
    *,
    email: EmailProvider,
    base_url: str,
    link_hours: int,
    secret: str,
    unsubscribe_days: int,
    daily_limit: int,
    now: datetime,
) -> DigestResult:
    result = await db.execute(
        select(Job)
        .where(
            Job.user_id == user.id,
            Job.status == "new",
            Job.notified_at.is_(None),
            Job.match_score >= user.profile.match_threshold,
        )
        .order_by(Job.match_score.desc(), Job.id)
        .limit(MAX_DIGEST_JOBS)
    )
    jobs = list(result.scalars())
    if not jobs:
        return DigestResult()
    if not email.configured:
        return DigestResult(note="Email isn't set up, so no digest was sent.")
    if not user.email_verified:
        # The jobs stay unnotified, so they are in the first digest after verification.
        return DigestResult(note="Verify your email to get digests. The link is in your inbox.")
    if not user.profile.digest_emails:
        return DigestResult(note="Digest emails are off. Turn them on in your profile.")
    try:
        await mailer.check_limit(db, user.id, limit=daily_limit, now=now)
    except mailer.SendLimitError as exc:
        return DigestResult(note=str(exc))

    issued = {
        job.id: apply_links.issue(db, user_id=user.id, job_id=job.id, hours=link_hours, now=now)
        for job in jobs
    }
    await db.flush()
    links = {
        job_id: apply_links.link(base_url, job_id, token)
        for job_id, (token, _row) in issued.items()
    }
    stop = unsubscribe.token(user.id, secret, unsubscribe_days)
    subject, text, html = render_digest(
        jobs, links, link_hours, unsubscribe.page_link(base_url, stop)
    )
    try:
        message_id = await email.send(
            OutgoingEmail(
                to=user.email,
                subject=subject,
                text=text,
                html=html,
                headers=unsubscribe.headers(base_url, stop),
            )
        )
    except EmailError as exc:
        # Links that never went out must not stay valid; the jobs stay un-notified for next time.
        for _token, row in issued.values():
            await db.delete(row)
        logger.warning("digest_failed", user_id=user.id)
        return DigestResult(note=str(exc))

    for job in jobs:
        job.notified_at = now
    mailer.record(db, user.id, "digest", now)
    db.add(Notification(user_id=user.id, kind="digest", sent_at=now, job_ids=[j.id for j in jobs]))
    audit.record(
        db,
        user_id=user.id,
        actor="notifier",
        action="digest_sent",
        entity="notification",
        jobs=len(jobs),
        message_id=message_id,
    )
    return DigestResult(sent=len(jobs))
