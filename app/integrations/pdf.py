"""PDFs: the CV in one of a few single-column templates (standard headings, no tables or images,
so applicant tracking systems read it cleanly), the cover letter and the Coach's proposals.
fpdf2 is pure Python, so it renders the same in Docker, CI and on Windows (WeasyPrint needs GTK
system libraries). Every CV is read back with pdfplumber and checked before it is offered."""

import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fpdf import FPDF

from app.domain.coach import ProjectIdea
from app.domain.cv import Block
from app.domain.cv_format import CV_TEMPLATES, CvTemplate, FormatCheck, format_checks, passes

FONT_DIR = Path(__file__).resolve().parents[1] / "templates" / "fonts"
FONT = "PublicSans"
INK = (27, 21, 48)
RULE = (180, 175, 195)


@dataclass(frozen=True)
class CvStyle:
    """One template's measurements, in millimetres and points."""

    margin: float
    name_size: float
    contact_size: float
    heading_size: float
    body_size: float
    line: float
    heading_gap: float
    accent: tuple[int, int, int]  # headings and the name
    rule: bool  # a line under each heading


STYLES: dict[CvTemplate, CvStyle] = {
    "classic": CvStyle(18, 18, 9.5, 11, 10, 5, 3, INK, True),
    "compact": CvStyle(14, 16, 9, 10, 9.5, 4.4, 2, INK, True),
    "modern": CvStyle(18, 20, 9.5, 11, 10, 5, 4, (159, 18, 87), False),
}


class _Pdf(FPDF):
    def __init__(self, margin: float = 18) -> None:
        super().__init__(format="A4")
        self.set_margins(margin, 16, margin)
        self.set_auto_page_break(auto=True, margin=16)
        self.add_font(FONT, "", str(FONT_DIR / "PublicSans-Regular.ttf"))
        self.add_font(FONT, "B", str(FONT_DIR / "PublicSans-Bold.ttf"))
        self.set_text_color(*INK)


def render_cv(blocks: Sequence[Block], *, title: str, template: CvTemplate = "classic") -> bytes:
    style = STYLES[template]
    pdf = _Pdf(style.margin)
    pdf.set_title(title)
    pdf.set_creator("Power Hunt")
    pdf.add_page()
    width = pdf.epw

    def write(text: str, size: float, *, bold: bool = False, height: float | None = None) -> None:
        pdf.set_font(FONT, "B" if bold else "", size)
        pdf.multi_cell(width, height or style.line, text, new_x="LMARGIN", new_y="NEXT", align="L")

    header = [b for b in blocks if b.section == "header" and b.text]
    for i, block in enumerate(header):
        if i == 0:
            pdf.set_text_color(*style.accent)
            write(block.text, style.name_size, bold=True, height=style.name_size * 0.45)
            pdf.set_text_color(*INK)
        else:
            write(block.text, style.contact_size)

    for block in blocks:
        if block.section == "header" or not block.text:
            continue
        if block.kind == "heading":
            pdf.ln(style.heading_gap)
            pdf.set_text_color(*style.accent)
            write(block.text.upper(), style.heading_size, bold=True, height=style.line + 1)
            pdf.set_text_color(*INK)
            if style.rule:
                pdf.set_draw_color(*RULE)
                pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
            pdf.ln(1.5)
        elif block.kind == "title":
            pdf.ln(1)
            write(block.text, style.body_size, bold=True)
        elif block.kind == "bullet" and block.section != "summary":
            pdf.set_font(FONT, "", style.body_size)
            pdf.cell(5, style.line, "•")
            pdf.multi_cell(
                width - 5, style.line, block.text, new_x="LMARGIN", new_y="NEXT", align="L"
            )
        else:
            write(block.text, style.body_size)
    return bytes(pdf.output())


def read_back(data: bytes) -> tuple[str, int]:
    """(text, pages) as a text extractor sees the PDF."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages), len(pdf.pages)


@dataclass(frozen=True)
class CheckedCv:
    pdf: bytes
    template: CvTemplate
    checks: list[FormatCheck]


def checked_cv(blocks: Sequence[Block], *, title: str, preferred: CvTemplate) -> CheckedCv:
    """The CV in the preferred template if it passes every format check; otherwise the first
    other template that does (a long CV moves to "compact"); otherwise the preferred one with
    its failed checks, so the user sees what is wrong."""
    first: CheckedCv | None = None
    for template in (preferred, *(t for t in CV_TEMPLATES if t != preferred)):
        pdf = render_cv(blocks, title=title, template=template)
        text, pages = read_back(pdf)
        result = CheckedCv(pdf, template, format_checks(text, pages, blocks))
        if passes(result.checks):
            return result
        first = first or result
    assert first is not None  # CV_TEMPLATES is never empty
    return first


def render_proposal(idea: ProjectIdea, skill: str) -> bytes:
    """A one-page project proposal from the Coach: title, then labelled sections."""
    pdf = _Pdf()
    pdf.set_title(idea.title)
    pdf.set_creator("Power Hunt")
    pdf.add_page()
    width = pdf.epw

    def section(heading: str, lines: Sequence[str], *, bullet: str = "") -> None:
        pdf.ln(2)
        pdf.set_font(FONT, "B", 11)
        pdf.multi_cell(width, 6, heading.upper(), new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.set_draw_color(*RULE)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
        pdf.ln(1.5)
        pdf.set_font(FONT, "", 10.5)
        for i, line in enumerate(lines, start=1):
            mark = f"{i}." if bullet == "number" else bullet
            if mark:
                pdf.cell(7, 5.5, mark)
                pdf.multi_cell(width - 7, 5.5, line, new_x="LMARGIN", new_y="NEXT", align="L")
            else:
                pdf.multi_cell(width, 5.5, line, new_x="LMARGIN", new_y="NEXT", align="L")

    pdf.set_font(FONT, "B", 18)
    pdf.multi_cell(width, 8, idea.title, new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.set_font(FONT, "", 9.5)
    pdf.multi_cell(
        width,
        5,
        f"A project that proves {skill}. About {idea.hours} hours.",
        new_x="LMARGIN",
        new_y="NEXT",
        align="L",
    )
    section("What it is", [idea.pitch])
    section("The problem", [idea.problem])
    section("First version", idea.scope, bullet="•")
    section("Stack", [", ".join(idea.stack)])
    section("Milestones", idea.milestones, bullet="number")
    section(f"How it proves {skill}", [idea.proof])
    return bytes(pdf.output())


def render_letter(*, sender: str, contact: str, body: str, title: str) -> bytes:
    """Cover letter: the sender's name and contact line, then the letter's paragraphs."""
    pdf = _Pdf()
    pdf.set_title(title)
    pdf.set_creator("Power Hunt")
    pdf.add_page()
    width = pdf.epw
    pdf.set_font(FONT, "B", 16)
    pdf.multi_cell(width, 8, sender, new_x="LMARGIN", new_y="NEXT", align="L")
    if contact:
        pdf.set_font(FONT, "", 9.5)
        pdf.multi_cell(width, 5, contact, new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.ln(6)
    pdf.set_font(FONT, "", 10.5)
    for paragraph in body.split("\n\n"):
        pdf.multi_cell(width, 5.5, paragraph.strip(), new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.ln(3)
    return bytes(pdf.output())
