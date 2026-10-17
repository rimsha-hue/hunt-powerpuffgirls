"""Finding an application form's questions (spec Flow D, step 1), in order: the Greenhouse job
board API, the Lever apply page, any other apply page through the SSRF-safe fetcher, and as the
last resort questions the user pastes."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from app.domain.questions import MAX_QUESTIONS, FieldType, Question, clean_question
from app.integrations.fetch import FetchError, SafeFetcher
from app.integrations.jobs.base import JobSourceError, get_json
from app.integrations.webtext import html_to_text

FormSource = Literal["greenhouse", "lever", "page", "pasted"]

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards"
_GREENHOUSE_URL = re.compile(
    r"^https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?P<board>[\w-]+)/jobs/(?P<id>\d+)"
)
_LEVER_URL = re.compile(
    r"^https?://jobs\.(?:eu\.)?lever\.co/(?P<company>[\w.-]+)/(?P<id>[0-9a-f-]{36})"
)
_GREENHOUSE_TYPES: dict[str, FieldType] = {
    "input_text": "text",
    "textarea": "textarea",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multiselect",
    "input_file": "file",
}
_SKIP_INPUTS = {"hidden", "submit", "button", "reset", "image", "search", "password"}


class FormDiscoveryError(Exception):
    """Safe to show to the user."""


@dataclass(frozen=True)
class FoundForm:
    source: FormSource
    url: str
    questions: list[Question]


def greenhouse_ids(url: str) -> tuple[str, str] | None:
    """(board, job id) from a Greenhouse job URL, including the embed form's query string."""
    match = _GREENHOUSE_URL.match(url)
    if match:
        return match.group("board"), match.group("id")
    parts = urlsplit(url)
    if parts.netloc.endswith("greenhouse.io") and "embed" in parts.path:
        query = parse_qs(parts.query)
        board, token = query.get("for", [""])[0], query.get("token", [""])[0]
        if re.fullmatch(r"[\w-]+", board) and token.isdigit():
            return board, token
    return None


def lever_ids(url: str) -> tuple[str, str] | None:
    """(company, posting id) from a Lever job URL."""
    match = _LEVER_URL.match(url)
    return (match.group("company"), match.group("id")) if match else None


def _text(html: str | None) -> str:
    return html_to_text(html or "").replace("\n", " ").replace("• ", "") if html else ""


def parse_greenhouse(data: dict[str, Any]) -> list[Question]:
    items = list(data.get("questions") or [])
    items += list(data.get("location_questions") or [])
    for group in data.get("compliance") or []:
        items += list(group.get("questions") or [])
    items += list(data.get("demographic_questions") or [])  # some boards list these separately
    out: list[Question] = []
    for item in items:
        fields = [f for f in item.get("fields") or [] if f.get("type") != "input_hidden"]
        if not fields:
            continue
        field = next((f for f in fields if f.get("type") != "textarea"), fields[0])
        kind = _GREENHOUSE_TYPES.get(str(field.get("type")), "text")
        question = clean_question(
            str(item.get("label") or ""),
            kind,
            required=bool(item.get("required")),
            description=_text(item.get("description")),
            options=[str(v.get("label") or "") for v in field.get("values") or []],
            name=str(field.get("name") or ""),
        )
        if question:
            out.append(question)
    return out[:MAX_QUESTIONS]


def _label_for(soup: BeautifulSoup, element: Tag) -> str:
    element_id = element.get("id")
    if isinstance(element_id, str) and element_id:
        label = soup.find("label", attrs={"for": element_id})
        if isinstance(label, Tag):
            return label.get_text(" ", strip=True)
    parent = element.find_parent("label")
    if isinstance(parent, Tag):
        return parent.get_text(" ", strip=True)
    for attribute in ("aria-label", "placeholder", "title"):
        value = element.get(attribute)
        if isinstance(value, str) and value.strip():
            return value
    name = element.get("name")
    return (
        name.replace("_", " ").replace("[", " ").replace("]", " ") if isinstance(name, str) else ""
    )


def parse_form_page(html: str) -> list[Question]:
    """Questions from an HTML form: labelled inputs, textareas and selects; radio and checkbox
    groups become one question with their options."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    out: list[Question] = []
    groups: dict[str, list[Tag]] = {}
    for element in soup.find_all(["input", "textarea", "select"]):
        if not isinstance(element, Tag):
            continue
        kind_attr = str(element.get("type") or "text").lower()
        if element.name == "input" and kind_attr in _SKIP_INPUTS:
            continue
        required = element.has_attr("required") or element.get("aria-required") == "true"
        name = str(element.get("name") or "")
        if element.name == "input" and kind_attr in ("radio", "checkbox") and name:
            groups.setdefault(name, []).append(element)
            continue
        label = _label_for(soup, element)
        if element.name == "select":
            options = [
                o.get_text(" ", strip=True)
                for o in element.find_all("option")
                if o.get("value") not in (None, "")
            ]
            question = clean_question(
                label, "select", required=required, options=options, name=name
            )
        elif element.name == "textarea":
            question = clean_question(label, "textarea", required=required, name=name)
        elif kind_attr == "file":
            question = clean_question(label, "file", required=required, name=name)
        else:
            question = clean_question(label, "text", required=required or "*" in label, name=name)
        if question:
            out.append(question)
    for name, elements in groups.items():
        fieldset = elements[0].find_parent("fieldset")
        legend = fieldset.find("legend") if isinstance(fieldset, Tag) else None
        label = (
            legend.get_text(" ", strip=True) if isinstance(legend, Tag) else name.replace("_", " ")
        )
        options = [_label_for(soup, e) for e in elements]
        multiple = str(elements[0].get("type")).lower() == "checkbox" and len(elements) > 1
        question = clean_question(
            label,
            "multiselect" if multiple else ("checkbox" if len(elements) == 1 else "select"),
            required=any(e.has_attr("required") for e in elements),
            options=options,
            name=name,
        )
        if question:
            out.append(question)
    return out[:MAX_QUESTIONS]


def parse_pasted(text: str) -> list[Question]:
    """One question per non-empty line; a trailing "(required)" or "*" marks it required."""
    out: list[Question] = []
    for line in text.splitlines():
        line = line.strip(" -•\t")
        if len(line) < 3:
            continue
        required = bool(re.search(r"\*\s*$|\(required\)", line, re.I))
        label = re.sub(r"\s*(?:\*|\(required\))\s*$", "", line, flags=re.I)
        # Long or explicitly open questions get a text area; short ones a single line.
        long_answer = len(label.split()) > 6 or label.rstrip().endswith("?")
        question = clean_question(label, "textarea" if long_answer else "text", required=required)
        if question:
            out.append(question)
    return out[:MAX_QUESTIONS]


async def discover(
    urls: Sequence[str], *, http: httpx.AsyncClient, fetcher: SafeFetcher
) -> FoundForm:
    """Questions from the first source that has them, trying every candidate URL in turn."""
    problems: list[str] = []
    for url in urls:
        ids = greenhouse_ids(url)
        if ids:
            board, job_id = ids
            try:
                data = await get_json(
                    http,
                    f"{GREENHOUSE_API}/{board}/jobs/{job_id}",
                    source="Greenhouse",
                    params={"questions": "true"},
                )
            except JobSourceError as exc:
                problems.append(str(exc))
            else:
                questions = parse_greenhouse(data) if isinstance(data, dict) else []
                if questions:
                    return FoundForm("greenhouse", url, questions)
    for url in urls:
        lever = _LEVER_URL.match(url)
        target = (
            f"https://jobs.lever.co/{lever.group('company')}/{lever.group('id')}/apply"
            if lever
            else url
        )
        try:
            page = await fetcher.fetch(target)
        except FetchError as exc:
            problems.append(str(exc))
            continue
        questions = parse_form_page(page.text)
        if questions:
            return FoundForm("lever" if lever else "page", target, questions)
    detail = f" ({problems[0]})" if problems else ""
    raise FormDiscoveryError(
        "We couldn't read this application form" + detail + ". Paste its questions instead."
    )
