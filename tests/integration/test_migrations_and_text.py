from alembic import command
from alembic.config import Config

from app.config import ROOT_DIR
from app.domain.resume import split_resume
from app.integrations.resume_text import extract_text


def _minimal_pdf(lines: list[str]) -> bytes:
    """A valid one-page PDF with Helvetica text, built by hand so tests need no PDF library."""
    text_ops = "\n".join(
        f"BT /F1 11 Tf 50 {780 - i * 16} Td ({line.replace('(', r'\(').replace(')', r'\)')}) Tj ET"
        for i, line in enumerate(lines)
    ).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(text_ops) + text_ops + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def test_pdf_text_extraction_round_trip() -> None:
    pdf = _minimal_pdf(["Ayesha Khan", "EXPERIENCE", "Reduced latency by 30% (FastAPI)"])
    lines = split_resume(extract_text(pdf, "pdf"))
    assert [line.text for line in lines] == [
        "Ayesha Khan",
        "EXPERIENCE",
        "Reduced latency by 30% (FastAPI)",
    ]
    assert lines[2].section == "experience"


def test_migrations_match_the_models(database_url: str) -> None:
    """`alembic check` fails if a model changed without a migration."""
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = database_url
    command.check(cfg)
