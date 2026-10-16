import io
import zipfile

import pytest

from app.domain.resume import extract_contact, split_resume
from app.domain.uploads import UploadRejectedError, sniff_resume

MB = 1024 * 1024


class TestSplitResume:
    def test_numbers_lines_and_tracks_sections(self) -> None:
        lines = split_resume("Jane Doe\n\nEXPERIENCE\n• Built things\n\nSkills:\nPython\n")
        assert [(l.line_no, l.section, l.text) for l in lines] == [  # noqa: E741
            (1, "header", "Jane Doe"),
            (2, "experience", "EXPERIENCE"),
            (3, "experience", "Built things"),
            (4, "skills", "Skills:"),
            (5, "skills", "Python"),
        ]

    def test_long_lines_are_capped(self) -> None:
        (line,) = split_resume("x" * 2000)
        assert len(line.text) == 800

    def test_wrapped_bullets_become_one_line(self) -> None:
        text = (
            "PROJECTS\n"
            "MediScan: AI Medicine Scanner [link]\n"
            "• Integrated Gemini with a pharmacist persona for English and Urdu summaries; 94%\n"
            "JSON parse success rate, 100% persona compliance.\n"
            "• Second bullet\n"
        )
        texts = [line.text for line in split_resume(text)]
        assert texts == [
            "PROJECTS",
            "MediScan: AI Medicine Scanner [link]",
            "Integrated Gemini with a pharmacist persona for English and Urdu summaries; 94% "
            "JSON parse success rate, 100% persona compliance.",
            "Second bullet",
        ]

    def test_hyphenation_versus_real_hyphens(self) -> None:
        text = (
            "EXPERIENCE\n"
            "• Hardened hybrid retrieval, session mem‐\n"
            "ory, and a retrieval‐\n"
            "augmented pipeline with Artificial Intel‐\n"
            "ligence and Business Intelligence.\n"
        )
        (_, bullet) = split_resume(text)
        assert "session memory" in bullet.text
        assert "retrieval-augmented" in bullet.text
        assert "Artificial Intelligence" in bullet.text

    def test_header_lines_never_merge(self) -> None:
        lines = split_resume("Jane Doe\njane@example.com | github.com/jane\nEXPERIENCE\nEngineer")
        assert [line.text for line in lines[:2]] == [
            "Jane Doe",
            "jane@example.com | github.com/jane",
        ]

    def test_skills_without_a_label_continue_the_previous_line(self) -> None:
        lines = split_resume("SKILLS\nML / AI: PyTorch, Keras\nHugging Face, NLP\nDev Tools: Git\n")
        assert [line.text for line in lines] == [
            "SKILLS",
            "ML / AI: PyTorch, Keras Hugging Face, NLP",
            "Dev Tools: Git",
        ]

    def test_bullets_are_flagged(self) -> None:
        lines = split_resume("EXPERIENCE\nAI Intern\n• Built a thing\n")
        assert [line.bulleted for line in lines] == [False, False, True]


class TestContact:
    def test_extracts_contact_details_from_header(self) -> None:
        lines = split_resume(
            "AYESHA KHAN\nayesha@example.com | +92 300 1234567 | linkedin.com/in/ayesha-k\n"
            "github.com/ayeshakdev | ayesha-portfolio.lovable.app\nEXPERIENCE\nEngineer"
        )
        contact = extract_contact(lines)
        assert contact.name == "Ayesha Khan"
        assert contact.email == "ayesha@example.com"
        assert contact.phone == "+92 300 1234567"
        assert contact.linkedin == "linkedin.com/in/ayesha-k"
        assert contact.github_username == "ayeshakdev"
        assert contact.portfolio_url == "https://ayesha-portfolio.lovable.app"


def _docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


class TestSniff:
    def test_accepts_pdf_by_magic_bytes(self) -> None:
        assert sniff_resume(b"%PDF-1.7\n...", 5 * MB) == "pdf"

    def test_accepts_docx(self) -> None:
        assert sniff_resume(_docx_bytes(), 5 * MB) == "docx"

    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (b"", "empty"),
            (b"<html><script>alert(1)</script>", "Only PDF and DOCX"),
            (b"MZ\x90\x00 fake exe", "Only PDF and DOCX"),
            (b"PK\x03\x04 not really a zip", "not a valid DOCX"),
        ],
    )
    def test_rejects_other_content(self, data: bytes, message: str) -> None:
        with pytest.raises(UploadRejectedError, match=message):
            sniff_resume(data, 5 * MB)

    def test_rejects_zip_that_is_not_word(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("evil.sh", "rm -rf /")
        with pytest.raises(UploadRejectedError, match="not a Word document"):
            sniff_resume(buf.getvalue(), 5 * MB)

    def test_rejects_oversized_files(self) -> None:
        with pytest.raises(UploadRejectedError, match="larger than 5 MB"):
            sniff_resume(b"%PDF-" + b"0" * (5 * MB), 5 * MB)
