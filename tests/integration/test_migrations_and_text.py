import sqlite3
from pathlib import Path

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


def test_metric_keys_are_recomputed_by_head_noun(tmp_path: Path) -> None:
    """0009: stored metrics get the new unit rule; rows that become equal are merged."""
    path = tmp_path / "metrics.db"
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = f"sqlite+aiosqlite:///{path.as_posix()}"
    command.upgrade(cfg, "0008")
    now = "2026-10-17 00:00:00"
    rows = [
        ("used by 1,200 support agents", "1200 support", "resume"),
        ("2M records daily", "2000000 record", "resume"),
        ("2M daily records", "2000000 daily", "resume"),  # becomes a duplicate of the row above
        ("1,200 support agents", "1200 support", "user_confirmed"),
    ]
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (1, 'a@b.c', '', '', ?)",
            (now,),
        )
        db.executemany(
            "INSERT INTO evidence (user_id, claim_text, type, normalized_value, source, source_ref,"
            " confirmed_by, confidence, created_at) VALUES (1, ?, 'metric', ?, ?, 'x', 'system', 1, ?)",
            [(*row, now) for row in rows],
        )
    command.upgrade(cfg, "0009")
    with sqlite3.connect(path) as db:
        stored = db.execute("SELECT normalized_value, source FROM evidence ORDER BY id").fetchall()
    assert stored == [
        ("1200 agent", "resume"),
        ("2000000 record/day", "resume"),
        ("1200 agent", "user_confirmed"),
    ]


def test_resume_facts_get_their_entries(tmp_path: Path) -> None:
    """0011: stored roles and employers are linked to their entry from the stored resume lines,
    and the same role can then be stored once per entry."""
    path = tmp_path / "entries.db"
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = f"sqlite+aiosqlite:///{path.as_posix()}"
    command.upgrade(cfg, "0010")
    now = "2026-10-17 00:00:00"
    lines = ["EXPERIENCE", "ML Engineer", "Nimbus Labs, 2024 - Present", "SKILLS", "Python"]
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (1, 'a@b.c', '', '', ?)",
            (now,),
        )
        db.executemany(
            "INSERT INTO resume_lines (id, user_id, resume_version, section, line_no, text, bulleted)"
            " VALUES (?, 1, 1, ?, ?, ?, 0)",
            [
                (10 + no, "skills" if no >= 4 else "experience", no, text)
                for no, text in enumerate(lines, start=1)
            ],
        )
        db.executemany(
            "INSERT INTO evidence (user_id, claim_text, type, normalized_value, source, source_ref,"
            " confirmed_by, confidence, created_at) VALUES (1, ?, ?, ?, 'resume', ?, 'system', 1, ?)",
            [
                ("ML Engineer", "role", "ml engineer", "12", now),
                ("Nimbus Labs", "employer", "nimbus labs", "13", now),
                ("Python", "skill", "python", "15", now),
            ],
        )
    command.upgrade(cfg, "0011")
    with sqlite3.connect(path) as db:
        stored = dict(db.execute("SELECT type, context FROM evidence").fetchall())
        db.execute(
            "INSERT INTO evidence (user_id, claim_text, type, normalized_value, source, source_ref,"
            " context, confirmed_by, confidence, created_at)"
            " VALUES (1, 'ML Engineer', 'role', 'ml engineer', 'resume', '20', 'ML Engineer · Acme',"
            " 'system', 1, ?)",
            (now,),
        )
    assert stored == {
        "role": "ML Engineer · Nimbus Labs",
        "employer": "ML Engineer · Nimbus Labs",
        "skill": None,
    }
