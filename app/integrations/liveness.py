"""Asks a posting's own site whether it still takes applications.

Greenhouse and Lever answer through their public job APIs (a closed job is gone: 404); any other
page is read through the SSRF-safe fetcher and judged by its status and its words.
"""

from typing import Literal

import structlog

from app.domain.jobs import GONE_STATUSES, page_says_closed
from app.integrations.fetch import TEXT_TYPES, FetchError, FetchHTTPError, SafeFetcher
from app.integrations.forms import GREENHOUSE_API, greenhouse_ids, lever_ids

logger = structlog.get_logger()

Liveness = Literal["open", "closed", "unknown"]
LEVER_API = "https://api.lever.co/v0/postings"
JSON_TYPES = ("application/json",)


async def check_posting(url: str, fetcher: SafeFetcher) -> Liveness:
    """Open, closed, or unknown when the site can't be read (the job then stays as it was)."""
    greenhouse = greenhouse_ids(url)
    lever = lever_ids(url)
    if greenhouse:
        target = f"{GREENHOUSE_API}/{greenhouse[0]}/jobs/{greenhouse[1]}"
    elif lever:
        target = f"{LEVER_API}/{lever[0]}/{lever[1]}"
    else:
        target = url
    api = target != url
    try:
        page = await fetcher.fetch(target, JSON_TYPES if api else TEXT_TYPES)
    except FetchHTTPError as exc:
        return "closed" if exc.status in GONE_STATUSES else "unknown"
    except FetchError as exc:
        logger.info("liveness_unknown", error_type=type(exc).__name__)
        return "unknown"
    # An ATS API answering at all means the job is listed; a page has to say it is closed.
    return "closed" if not api and page_says_closed(page.text) else "open"
