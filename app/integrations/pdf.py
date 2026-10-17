"""PDFs: the CV in one of a few single-column templates (standard headings, no tables or images,
so applicant tracking systems read it cleanly), the cover letter and the Coach's proposals.
fpdf2 is pure Python, so it renders the same in Docker, CI and on Windows (WeasyPrint needs GTK
system libraries). Every CV is read back with pdfplumber and checked before it is offered."""

import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fpdf import FPDF

from app.domain.coach import ProjectIdea
from app.domain.cv import Block
from app.domain.cv_format import (
    CV_TEMPLATES,
    CvTemplate,
    FormatCheck,
    format_checks,
    passes,
    split_dates,
)

FONT_DIR = Path(__file__).resolve().parents[1] / "templates" / "fonts"
FONT = "PublicSans"
INK = (27, 21, 48)
RULE = (180, 175, 195)


SERIF = "Tinos"  # metric-compatible with Times New Roman (OFL; see templates/fonts)


@dataclass(frozen=True)
class CvStyle:
    """One template's look, in millimetres and points. All are one column with text headings,
    the layout applicant tracking systems parse best."""

    family: str
    margin: float
    centered_header: bool
    name_size: float
    contact_size: float
    heading_size: float
    body_size: float
    line: float
    heading_gap: float
    accent: tuple[int, int, int]  # the name and headings
    rule: bool  # a full-width line under each heading
    bar: bool  # an accent bar before each heading
    italic_dates: bool


# classic: the Harvard career-office layout (serif, centered name, ruled headings).
# compact: the widely used "Jake's resume" layout (sans, tight, dates flush right).
# modern: a sans layout with a colour accent on the name and headings.
STYLES: dict[CvTemplate, CvStyle] = {
    "classic": CvStyle(SERIF, 19, True, 20, 10, 11.5, 10.5, 5, 3.5, INK, True, False, True),
    "compact": CvStyle(FONT, 14, True, 19, 9, 10.5, 9.5, 4.4, 2.5, INK, True, False, False),
    "modern": CvStyle(FONT, 17, False, 22, 9.5, 11, 10, 4.9, 4, (159, 18, 87), False, True, False),
}
# "Languages: Python, SQL": a short label before a colon, as skills lines are written.
LABEL_RE = re.compile(r"^[A-Za-z][\w /&+.-]{0,28}:\s*\S")
# Sections listed one item per line, each with its date on the right.
LISTED_SECTIONS = frozenset({"certifications", "awards", "publications", "education"})
BULLET_INDENT = 3  # mm before the bullet mark
BULLET_WIDTH = 4  # mm for the mark itself


class _Pdf(FPDF):
    def __init__(self, margin: float = 18) -> None:
        super().__init__(format="A4")
        self.set_margins(margin, 15, margin)
        self.set_auto_page_break(auto=True, margin=15)
        self.add_font(FONT, "", str(FONT_DIR / "PublicSans-Regular.ttf"))
        self.add_font(FONT, "B", str(FONT_DIR / "PublicSans-Bold.ttf"))
        self.add_font(SERIF, "", str(FONT_DIR / "Tinos-Regular.ttf"))
        self.add_font(SERIF, "B", str(FONT_DIR / "Tinos-Bold.ttf"))
        self.add_font(SERIF, "I", str(FONT_DIR / "Tinos-Italic.ttf"))
        self.set_text_color(*INK)


def render_cv(blocks: Sequence[Block], *, title: str, template: CvTemplate = "classic") -> bytes:
    style = STYLES[template]
    pdf = _Pdf(style.margin)
    pdf.set_title(title)
    pdf.set_creator("Power Hunt")
    pdf.add_page()
    width = pdf.epw
    family = style.family

    def write(text: str, size: float, *, bold: bool = False, align: str = "L") -> None:
        pdf.set_font(family, "B" if bold else "", size)
        pdf.multi_cell(width, size * 0.5, text, new_x="LMARGIN", new_y="NEXT", align=align)

    # Name, then the contact lines joined on one line: "email | phone | linkedin".
    header = [b.text for b in blocks if b.section == "header" and b.text]
    align = "C" if style.centered_header else "L"
    if header:
        pdf.set_text_color(*style.accent)
        write(header[0], style.name_size, bold=True, align=align)
        pdf.set_text_color(*INK)
        if len(header) > 1:
            pdf.ln(0.5)
            write("  |  ".join(header[1:]), style.contact_size, align=align)

    def heading(text: str) -> None:
        pdf.ln(style.heading_gap)
        y = pdf.get_y()
        indent = 0.0
        if style.bar:
            pdf.set_fill_color(*style.accent)
            pdf.rect(pdf.l_margin, y + 0.8, 1.6, style.heading_size * 0.38, style="F")
            indent = 3.5
        pdf.set_text_color(*style.accent)
        pdf.set_font(family, "B", style.heading_size)
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(
            width - indent, style.heading_size * 0.5, text.upper(), new_x="LMARGIN", new_y="NEXT"
        )
        pdf.set_text_color(*INK)
        if style.rule:
            pdf.set_draw_color(*RULE)
            pdf.set_line_width(0.3)
            pdf.line(pdf.l_margin, pdf.get_y() + 0.4, pdf.l_margin + width, pdf.get_y() + 0.4)
        pdf.ln(1.8)

    def entry_title(text: str, *, bold: bool = True) -> None:
        """Role or organisation (bold for entries, plain for list items such as a certificate),
        its dates flush right on the same line."""
        name, dates = split_dates(text)
        pdf.ln(0.8 if bold else 0.2)
        y = pdf.get_y()
        date_width = 0.0
        if dates:
            pdf.set_font(family, "I" if style.italic_dates else "", style.body_size)
            date_width = pdf.get_string_width(dates) + 1
            pdf.set_xy(pdf.l_margin, y)
            pdf.cell(width, style.line, dates, align="R")
        pdf.set_xy(pdf.l_margin, y)
        pdf.set_font(family, "B" if bold else "", style.body_size)
        pdf.multi_cell(
            width - date_width - 3, style.line, name, new_x="LMARGIN", new_y="NEXT", align="L"
        )

    def labelled(label: str, rest: str) -> None:
        """ "Languages: Python, SQL" with the label in bold and the list wrapping beside it."""
        pdf.set_font(family, "B", style.body_size)
        label_width = pdf.get_string_width(label) + 1.5
        pdf.cell(label_width, style.line, label)
        pdf.set_font(family, "", style.body_size)
        pdf.multi_cell(width - label_width, style.line, rest.strip(), new_x="LMARGIN", new_y="NEXT")

    def bullet(text: str) -> None:
        pdf.set_font(family, "", style.body_size)
        pdf.set_x(pdf.l_margin + BULLET_INDENT)
        pdf.cell(BULLET_WIDTH, style.line, "•")
        pdf.multi_cell(
            width - BULLET_INDENT - BULLET_WIDTH,
            style.line,
            text,
            new_x="LMARGIN",
            new_y="NEXT",
            align="L",
        )

    for block in blocks:
        if block.section == "header" or not block.text:
            continue
        if block.kind == "heading":
            heading(block.text)
        elif block.kind == "title":
            entry_title(block.text)
        elif block.kind == "bullet" and block.section != "summary":
            bullet(block.text)
        elif LABEL_RE.match(block.text):
            label, rest = block.text.split(":", 1)
            labelled(f"{label}:", rest)
        elif block.section in LISTED_SECTIONS:
            entry_title(block.text, bold=False)
        else:
            pdf.set_font(family, "", style.body_size)
            pdf.multi_cell(width, style.line, block.text, new_x="LMARGIN", new_y="NEXT", align="L")
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
