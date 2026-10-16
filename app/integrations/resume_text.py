"""Text extraction from PDF and DOCX resumes. CPU-bound, so it runs in a worker thread.

PDFs need care:
- Invisible text (white, or under 4 pt) is dropped. Text a human reader cannot see is not evidence,
  and hidden keyword blocks would otherwise flood the ledger.
- Two-column layouts are detected by finding a vertical gutter that no word crosses below the
  header, and each column is read on its own. Reading straight across interleaves the columns.
- Word gaps are read with a tight tolerance, because many PDFs (LaTeX especially) store no space
  characters and the default tolerance glues words together.
"""

import io
from typing import Any

import anyio
import structlog

from app.domain.uploads import FileKind, UploadRejectedError

logger = structlog.get_logger()

MAX_PDF_PAGES = 10
MIN_VISIBLE_FONT_SIZE = 4.0
X_TOLERANCE = 1.2  # tuned on a tightly justified LaTeX resume; 0.8 to 1.2 gave identical words
HEADER_ZONE = 0.22  # words crossing the gutter in the top 22% of a page are a full-width header
MIN_COLUMN_SHARE = 0.2


def _is_white(color: Any) -> bool:
    if not isinstance(color, (tuple, list)) or not color:
        return False
    try:
        values = [float(v) for v in color]
    except (TypeError, ValueError):
        return False
    if len(values) in (1, 3):  # grayscale or RGB
        return all(v >= 0.97 for v in values)
    if len(values) == 4:  # CMYK
        return all(v <= 0.03 for v in values)
    return False


def is_visible(obj: dict[str, Any]) -> bool:
    if obj.get("object_type") != "char":
        return True
    if float(obj.get("size", 10.0)) < MIN_VISIBLE_FONT_SIZE:
        return False
    return not _is_white(obj.get("non_stroking_color"))


def find_gutter(
    words: list[dict[str, Any]], width: float, height: float
) -> tuple[float, float] | None:
    """(gutter_x, header_bottom) for a two-column page, or None for a single column.

    A gutter is the widest vertical band, between 20% and 80% of the width, that no word crosses
    below the header zone, with a real share of the words on each side.
    """
    if len(words) < 20:
        return None
    header_limit = height * HEADER_ZONE
    body = [w for w in words if w["top"] >= header_limit]
    total_ink = sum(float(w["x1"]) - float(w["x0"]) for w in body)
    clear: list[int] = []
    for x in range(int(width * 0.2), int(width * 0.8)):
        if any(w["x0"] < x < w["x1"] for w in body):
            continue
        # Share of text *width*, not word count: a right-aligned dates column has many words
        # but little ink, while a real column carries a substantial part of the page's text.
        left_ink = sum(float(w["x1"]) - float(w["x0"]) for w in body if w["x1"] <= x)
        if total_ink and min(left_ink, total_ink - left_ink) / total_ink >= MIN_COLUMN_SHARE:
            clear.append(x)
    if not clear:
        return None

    # The widest run of consecutive clear x positions is the gutter; take its middle.
    best_start = run_start = clear[0]
    best_len = run_len = 1
    for prev, x in zip(clear, clear[1:], strict=False):
        if x == prev + 1:
            run_len += 1
        else:
            run_start, run_len = x, 1
        if run_len > best_len:
            best_start, best_len = run_start, run_len
    if best_len < 4:  # under ~4 pt is a word gap, not a gutter
        return None
    gutter = best_start + best_len / 2

    crossing = [w for w in words if w["x0"] < gutter < w["x1"]]
    header_bottom = max((float(w["bottom"]) for w in crossing), default=0.0)
    return gutter, header_bottom + 1 if header_bottom else 0.0


def _page_text(page: Any) -> str:
    visible = page.filter(is_visible)
    words = visible.extract_words(x_tolerance=X_TOLERANCE)
    split = find_gutter(words, float(page.width), float(page.height))
    if split is None:
        return str(visible.extract_text(x_tolerance=X_TOLERANCE) or "")

    gutter, header_bottom = split
    width, height = float(page.width), float(page.height)
    regions: list[tuple[float, float, float, float]] = []
    if header_bottom > 0:
        regions.append((0, 0, width, header_bottom))
    regions += [(0, header_bottom, gutter, height), (gutter, header_bottom, width, height)]
    return "\n".join(
        visible.crop(box).extract_text(x_tolerance=X_TOLERANCE) or "" for box in regions
    )


def _pdf_text(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(_page_text(page) for page in pdf.pages[:MAX_PDF_PAGES])


def _docx_text(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells if cell.text.strip()))
    return "\n".join(parts)


def extract_text(data: bytes, kind: FileKind) -> str:
    try:
        return _pdf_text(data) if kind == "pdf" else _docx_text(data)
    except Exception as exc:
        logger.warning("resume_text_extraction_failed", kind=kind, error_type=type(exc).__name__)
        raise UploadRejectedError(
            "We couldn't read that file. Try exporting it again as PDF or DOCX."
        ) from exc


async def extract_text_async(data: bytes, kind: FileKind) -> str:
    return await anyio.to_thread.run_sync(extract_text, data, kind)
