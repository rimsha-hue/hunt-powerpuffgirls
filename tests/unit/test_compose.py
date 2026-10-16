import pytest

from app.domain.compose import (
    AttachmentItem,
    attachments_ready,
    plan_attachments,
    prose_blocks,
    prose_problems,
    prose_text,
    requested_subject,
    sentences,
    subject_for,
    word_count,
)


class TestSubject:
    @pytest.mark.parametrize(
        ("description", "expected"),
        [
            ("Please use the subject line 'ML Engineer - Your Name'.", "ML Engineer - Hero"),
            (
                "Subject: Job Application - [Position] - [Name]",
                "Job Application - ML Engineer - Hero",
            ),
            ('Email us with subject "Application: {Job Title}"', "Application: ML Engineer"),
            ("Apply on our website.", "Application for ML Engineer, Hero"),
        ],
    )
    def test_requested_format_or_default(self, description: str, expected: str) -> None:
        assert subject_for(title="ML Engineer", name="Hero", description=description) == expected

    def test_no_subject_instruction(self) -> None:
        assert requested_subject("We value clear subject matter expertise.") is None


class TestProse:
    TEXT = "Dear team,\n\nI cut cost by 40%. I shipped APIs on Docker!\n\nThanks for reading."

    def test_sentences_keep_paragraphs(self) -> None:
        assert sentences(self.TEXT) == [
            (0, "Dear team,"),
            (1, "I cut cost by 40%."),
            (1, "I shipped APIs on Docker!"),
            (2, "Thanks for reading."),
        ]

    def test_blocks_round_trip_to_text(self) -> None:
        blocks = prose_blocks(self.TEXT, "email", [None, (7, 3, "Cut cost by 40%"), None, None])
        assert blocks[1].line_id == 7 and blocks[1].original == "Cut cost by 40%"
        assert blocks[0].original == "" and blocks[0].kind == "bullet"
        assert prose_text(blocks) == self.TEXT

    def test_length_and_style_rules(self) -> None:
        assert prose_problems("Short note.", 120, 180) == ["is 2 words (keep it to 120-180)"]
        long_enough = " ".join(["word"] * 130)
        assert prose_problems(long_enough, 120, 180) == []
        assert prose_problems(long_enough + " I am passionate.", 120, 180)
        assert word_count("Cut cost 40% in 2024, with Node.js") == 7


class TestAttachments:
    def test_plan(self) -> None:
        items = plan_attachments(
            ["cv", "cover_letter", "portfolio", "transcript"], portfolio_url="https://me.dev"
        )
        assert [(i.kind, i.status) for i in items] == [
            ("cv", "attached"),
            ("cover_letter", "attached"),
            ("portfolio", "link"),
            ("transcript", "missing"),
        ]
        assert not attachments_ready(items)
        assert attachments_ready([*items[:3], AttachmentItem("transcript", "Transcript", "waived")])

    def test_portfolio_without_a_link_is_missing(self) -> None:
        [_, portfolio] = plan_attachments(["portfolio"], portfolio_url=None)
        assert portfolio.status == "missing"

    def test_json_round_trip(self) -> None:
        item = AttachmentItem("transcript", "Transcript", "uploaded", "t.pdf", 4)
        assert AttachmentItem.from_json(item.to_json()) == item
        with pytest.raises(ValueError):
            AttachmentItem.from_json({**item.to_json(), "status": "lost"})
