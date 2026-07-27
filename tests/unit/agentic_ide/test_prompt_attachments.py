"""Dropped files reaching the composed prompt.

A described screenshot that does not make it into the brief has cost the user
the drop AND the model call that described it, while looking like it worked. So
these pin the one thing that matters: the contents travel on BOTH layers — the
model-written brief and the deterministic one — because the model that describes
a picture and the model that writes prose are separate providers, and having one
without the other is an ordinary install, not an edge case.
"""
from __future__ import annotations

from jarvis.agentic_ide import prompt_blueprint as blueprint
from jarvis.agentic_ide.drop_analysis import DropAnalysis


def _described(name: str = "shot.png") -> DropAnalysis:
    return DropAnalysis(
        name=name,
        reference=f"@.jarvis/drops/{name}",
        kind="image",
        detail="The submit button overflows its container and the label reads 'Sign inn'.",
        described_by="vision",
    )


def _extracted(name: str = "spec.md") -> DropAnalysis:
    return DropAnalysis(
        name=name,
        reference=f'"{name}"',
        kind="text",
        detail="The endpoint must return 202 on success.",
        described_by="extraction",
    )


def _undescribed(name: str = "photo.png") -> DropAnalysis:
    return DropAnalysis(
        name=name,
        reference=f"@{name}",
        kind="image",
        note="No provider that can see images is reachable, so this one was attached.",
    )


class TestWriterBlock:
    """What the writing model is shown."""

    def test_the_description_and_the_reference_both_reach_the_writer(self) -> None:
        block = blueprint.attachment_block([_described()])

        assert "submit button overflows" in block
        assert "@.jarvis/drops/shot.png" in block

    def test_an_undescribed_file_shows_its_reason_not_a_blank(self) -> None:
        block = blueprint.attachment_block([_undescribed()])

        assert "attached" in block
        assert "None" not in block

    def test_no_attachments_produces_no_block(self) -> None:
        assert blueprint.attachment_block([]) == ""

    def test_the_user_block_carries_the_attachment_and_its_rules(self) -> None:
        text = blueprint.user_block(
            utterance="fix this",
            instruction="fix this",
            terminal_name="Mika",
            agent_display="Claude Code",
            profile_lines=[],
            candidates=[],
            skeletons={},
            house_rules="",
            attachments=[_described()],
        )

        assert "submit button overflows" in text
        # The rules are what stop the writer from producing "the screenshot the
        # user dropped" and calling it a brief.
        assert "VERBATIM" in text

    def test_the_attachment_rules_stay_out_of_a_prompt_with_no_attachment(self) -> None:
        text = blueprint.user_block(
            utterance="run the tests",
            instruction="run the tests",
            terminal_name="Mika",
            agent_display="Claude Code",
            profile_lines=[],
            candidates=[],
            skeletons={},
            house_rules="",
        )

        assert "DROPPED" not in text
        assert "attachment" not in text.lower()


class TestDeterministicLayer:
    """The layer a downloader with no writing model actually gets."""

    def test_the_description_survives_with_no_writing_model(self) -> None:
        text = blueprint.render_fallback("fix the layout", [], [_described()])

        assert "submit button overflows" in text
        assert "shot.png" in text

    def test_extracted_document_text_survives_too(self) -> None:
        text = blueprint.render_fallback("implement it", [], [_extracted()])

        assert "must return 202" in text

    def test_an_undescribed_file_is_still_named(self) -> None:
        text = blueprint.render_fallback("fix the layout", [], [_undescribed()])

        assert "photo.png" in text
        assert "attached" in text

    def test_attachments_and_file_references_coexist(self) -> None:
        text = blueprint.render_fallback("fix it", ["src/Login.tsx"], [_described()])

        assert "submit button overflows" in text
        assert "@src/Login.tsx" in text

    def test_the_prompt_never_ends_on_a_reference(self) -> None:
        # A trailing @path holds the agent's completion popup open and eats the
        # Enter that follows.
        text = blueprint.render_fallback("fix it", ["src/Login.tsx"], [_described()])

        assert not blueprint.ends_on_reference(text)

    def test_no_instruction_but_a_dropped_file_still_produces_a_prompt(self) -> None:
        # Dropping a file and sending without typing is a real gesture; it must
        # not silently produce an empty prompt.
        text = blueprint.render_fallback("", [], [_described()])

        assert "submit button overflows" in text

    def test_nothing_at_all_stays_empty(self) -> None:
        assert blueprint.render_fallback("", [], []) == ""

    def test_the_body_cap_is_respected(self) -> None:
        huge = DropAnalysis(
            name="big.txt",
            reference='"big.txt"',
            kind="text",
            detail="x" * 50_000,
            described_by="extraction",
        )

        text = blueprint.render_fallback("read it", [], [huge])

        assert len(text) <= blueprint.MAX_BODY_CHARS
