"""Two-column PDFs are read column by column, and invisible text never becomes evidence."""

from app.integrations.resume_text import extract_text, find_gutter, is_visible


def _pdf(runs: list[tuple[float, float, float, str, float]]) -> bytes:
    """A one-page PDF from (x, y, size, text, gray) runs; gray 1.0 is white."""
    ops = []
    for x, y, size, text, gray in runs:
        safe = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(f"BT {gray} g /F1 {size} Tf {x} {y} Td ({safe}) Tj ET")
    stream = "\n".join(ops).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
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


def _two_column_resume() -> bytes:
    runs = [(50.0, 750.0, 16.0, "Jane Doe  jane@example.com  github.com/janedoe  janedoe.dev", 0.0)]
    left = [
        "EDUCATION",
        "BS Data Science",
        "SKILLS",
        "Python, PyTorch",
        "LANGUAGES",
        "English (Professional)",
    ]
    right = [
        "EXPERIENCE",
        "AI Intern",
        "Built a RAG assistant for 1,200 agents",
        "PROJECTS",
        "MediScan app",
        "94% JSON parse success",
    ]
    for i in range(14):
        y = 680.0 - i * 30
        runs.append((50.0, y, 10.0, left[i % len(left)], 0.0))
        runs.append((260.0, y, 10.0, right[i % len(right)], 0.0))
    runs.append((50.0, 120.0, 0.1, "kubernetesgolangphdharvard", 1.0))  # hidden keyword stuffing
    runs.append((260.0, 100.0, 10.0, "WhiteTextClaimsAPhD", 1.0))  # white text at normal size
    return _pdf(runs)


class TestTwoColumnPdf:
    def test_columns_are_read_separately(self) -> None:
        text = extract_text(_two_column_resume(), "pdf")
        lines = text.splitlines()
        assert lines[0].startswith("Jane Doe")
        # Each column's lines stay intact instead of being interleaved across the page.
        assert "BS Data Science" in lines
        assert "Built a RAG assistant for 1,200 agents" in lines
        assert lines.index("EDUCATION") < lines.index("EXPERIENCE")
        assert not any("BS Data Science" in line and "AI Intern" in line for line in lines)

    def test_invisible_text_is_dropped(self) -> None:
        text = extract_text(_two_column_resume(), "pdf")
        assert "kubernetes" not in text
        assert "WhiteText" not in text


class TestGutter:
    def _word(self, x0: float, x1: float, top: float) -> dict[str, float]:
        return {"x0": x0, "x1": x1, "top": top, "bottom": top + 10}

    def test_single_column_with_right_aligned_dates_is_not_split(self) -> None:
        words = []
        for i in range(30):
            top = 200 + i * 15
            words += [
                self._word(50, 120, top),
                self._word(125, 400, top),
                self._word(520, 560, top),
            ]
        assert find_gutter(words, 612, 792) is None

    def test_header_crossing_the_gutter_is_allowed(self) -> None:
        words = [self._word(50, 500, 40)]  # full-width contact line
        for i in range(30):
            top = 200 + i * 15
            words += [self._word(50, 190, top), self._word(230, 560, top)]
        gutter, header_bottom = find_gutter(words, 612, 792)  # type: ignore[misc]
        assert 190 < gutter < 230
        assert header_bottom > 40


def test_visibility_rules() -> None:
    char = {"object_type": "char", "size": 10.0, "non_stroking_color": (0.0,)}
    assert is_visible(char)
    assert not is_visible({**char, "size": 0.1})
    assert not is_visible({**char, "non_stroking_color": (1.0,)})
    assert not is_visible({**char, "non_stroking_color": (1.0, 1.0, 1.0)})
    assert not is_visible({**char, "non_stroking_color": (0.0, 0.0, 0.0, 0.0)})  # CMYK white
    assert is_visible({"object_type": "rect"})
