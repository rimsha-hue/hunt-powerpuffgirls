"""CV PDF from the tailored CV blocks: plain text in one column, standard headings, no tables or
images, so applicant tracking systems read it cleanly. fpdf2 is pure Python, so it renders the
same in Docker, CI and on Windows (WeasyPrint needs GTK system libraries)."""

from collections.abc import Sequence
from pathlib import Path

from fpdf import FPDF

from app.domain.cv import Block

FONT_DIR = Path(__file__).resolve().parents[1] / "templates" / "fonts"
FONT = "PublicSans"
INK = (27, 21, 48)
RULE = (180, 175, 195)


class _CvPdf(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4")
        self.set_margins(18, 16, 18)
        self.set_auto_page_break(auto=True, margin=16)
        self.add_font(FONT, "", str(FONT_DIR / "PublicSans-Regular.ttf"))
        self.add_font(FONT, "B", str(FONT_DIR / "PublicSans-Bold.ttf"))
        self.set_text_color(*INK)


def render_cv(blocks: Sequence[Block], *, title: str) -> bytes:
    pdf = _CvPdf()
    pdf.set_title(title)
    pdf.set_creator("Power Hunt")
    pdf.add_page()
    width = pdf.epw

    header = [b for b in blocks if b.section == "header" and b.text]
    for i, block in enumerate(header):
        if i == 0:
            pdf.set_font(FONT, "B", 18)
            pdf.multi_cell(width, 8, block.text, new_x="LMARGIN", new_y="NEXT", align="L")
        else:
            pdf.set_font(FONT, "", 9.5)
            pdf.multi_cell(width, 5, block.text, new_x="LMARGIN", new_y="NEXT", align="L")

    for block in blocks:
        if block.section == "header" or not block.text:
            continue
        if block.kind == "heading":
            pdf.ln(3)
            pdf.set_font(FONT, "B", 11)
            pdf.multi_cell(width, 6, block.text.upper(), new_x="LMARGIN", new_y="NEXT", align="L")
            pdf.set_draw_color(*RULE)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
            pdf.ln(1.5)
        elif block.kind == "title":
            pdf.ln(1)
            pdf.set_font(FONT, "B", 10)
            pdf.multi_cell(width, 5, block.text, new_x="LMARGIN", new_y="NEXT", align="L")
        elif block.kind == "bullet" and block.section != "summary":
            pdf.set_font(FONT, "", 10)
            pdf.cell(5, 5, "•")
            pdf.multi_cell(width - 5, 5, block.text, new_x="LMARGIN", new_y="NEXT", align="L")
        else:
            pdf.set_font(FONT, "", 10)
            pdf.multi_cell(width, 5, block.text, new_x="LMARGIN", new_y="NEXT", align="L")
    return bytes(pdf.output())
