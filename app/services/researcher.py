"""Researcher: facts about a company, each a sentence from a fetched source with its URL.

Search results (Tavily) are cached per company for a week. When search is unavailable or finds
nothing usable, the job description itself is the source. If neither has a fact, the user is
asked for one sentence about why they want this company (spec section 9).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CompanyFact as CompanyFactRow
from app.domain.research import CACHE_DAYS, CompanyFact, company_key, facts_from_text, select_facts
from app.integrations.research import ResearchError, TavilyClient

logger = structlog.get_logger()


@dataclass
class Research:
    facts: list[CompanyFact]
    note: str | None


async def company_facts(
    db: AsyncSession,
    *,
    company: str,
    job_description: str,
    job_url: str,
    tavily: TavilyClient | None,
    now: datetime,
) -> Research:
    key = company_key(company)
    fresh = now - timedelta(days=CACHE_DAYS)
    cached = list(
        (
            await db.execute(
                select(CompanyFactRow)
                .where(CompanyFactRow.company_key == key, CompanyFactRow.retrieved_at >= fresh)
                .order_by(CompanyFactRow.id)
            )
        ).scalars()
    )
    if cached:
        return Research([CompanyFact(r.fact, r.source_url) for r in cached], None)

    note: str | None = None
    found: list[CompanyFact] = []
    if tavily is not None:
        try:
            for result in await tavily.search(company):
                found += facts_from_text(result.content, company, result.url)
        except ResearchError as exc:
            note = f"{exc} Company facts come from the job description."
            logger.warning("research_failed", company_key=key)
    facts = select_facts(found, company)
    if facts:
        await db.execute(delete(CompanyFactRow).where(CompanyFactRow.company_key == key))
        db.add_all(
            CompanyFactRow(company_key=key, fact=f.fact, source_url=f.source_url, retrieved_at=now)
            for f in facts
        )
        return Research(facts, note)

    # Not cached: these describe one posting, not the company in general.
    from_posting = select_facts(facts_from_text(job_description, company, job_url), company)
    if not from_posting:
        note = "No company facts were found. Add one sentence about why you want to work here."
    return Research(from_posting, note)


def numbered(facts: Sequence[CompanyFact]) -> str:
    return "\n".join(f"[F{i}] {f.fact}" for i, f in enumerate(facts, start=1))
