"""Readable text from fetched web pages (HTML) and Jina Reader output (Markdown)."""

import re

from bs4 import BeautifulSoup

_DROP_TAGS = ("script", "style", "noscript", "svg", "template", "iframe", "head")
_BLOCK_TAGS = (
    "p",
    "div",
    "section",
    "article",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "br",
    "tr",
)


def _tidy(text: str) -> str:
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(_BLOCK_TAGS):
        # List items keep a bullet so the line splitter knows they describe the entry above.
        tag.insert_before("\n• " if tag.name == "li" else "\n")
    return _tidy(soup.get_text(" "))


def markdown_to_text(markdown: str) -> str:
    # Jina Reader prefixes metadata ("Title:", "URL Source:", "Markdown Content:").
    body = markdown.split("Markdown Content:", 1)[-1]
    # YAML front matter (title, meta tags) is page metadata, not the person's words.
    body = re.sub(r"\A\s*---\s*\n.*?\n---\s*\n", "", body, flags=re.S)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)  # images
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)  # links keep their text
    body = re.sub(r"^\s{0,3}#{1,6}\s+", "", body, flags=re.M)  # headings
    body = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "• ", body, flags=re.M)  # list items keep a bullet
    body = re.sub(r"[*_`]{1,3}", "", body)
    body = re.sub(r"^\s*(?:-{3,}|={3,})\s*$", "", body, flags=re.M)
    return _tidy(body)
