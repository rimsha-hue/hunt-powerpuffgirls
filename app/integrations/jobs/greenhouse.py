"""One posting from a Greenhouse job board, for "add a job by its link"."""

import html

import httpx

from app.domain.jobs import JobPosting
from app.integrations.forms import GREENHOUSE_API, greenhouse_ids
from app.integrations.jobs.base import get_json, parse_iso, posting
from app.integrations.webtext import html_to_text


class NotGreenhouseError(ValueError):
    """Safe to show to the user."""


async def greenhouse_posting(http: httpx.AsyncClient, url: str) -> JobPosting:
    """Raises NotGreenhouseError for other links, JobSourceError when Greenhouse fails."""
    ids = greenhouse_ids(url.strip())
    if ids is None:
        raise NotGreenhouseError(
            "Only Greenhouse job links can be added for now (boards.greenhouse.io or "
            "job-boards.greenhouse.io)."
        )
    board, job_id = ids
    data = await get_json(http, f"{GREENHOUSE_API}/{board}/jobs/{job_id}", source="Greenhouse")
    if not isinstance(data, dict):
        raise NotGreenhouseError("Greenhouse returned something we couldn't read.")
    location = str((data.get("location") or {}).get("name") or "")
    # The board's own job page always carries the form, even when absolute_url is the company's site.
    board_url = f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
    found = posting(
        source="greenhouse",
        external_id=f"{board}:{job_id}",
        title=data.get("title"),
        company=data.get("company_name") or board.replace("-", " ").title(),
        location=location,
        remote="remote" in location.lower(),
        url=data.get("absolute_url") or board_url,
        # Greenhouse sends the description as escaped HTML.
        description=html_to_text(html.unescape(str(data.get("content") or ""))),
        posted_at=parse_iso(data.get("first_published") or data.get("updated_at")),
        apply_url=board_url,
    )
    if found is None:
        raise NotGreenhouseError("That Greenhouse posting has no title or link.")
    return found
