"""The shared native/web Jarvis-presence drop routing."""

from __future__ import annotations

import pytest

from jarvis.agentic_ide import prompt_attachments
from jarvis.agentic_ide.prompt_attachments import StagedPromptAttachments
from jarvis.brain.drop_context import DroppedItem
from jarvis.ui.drop_intake import capture_presence_drop


class _Brain:
    def __init__(self) -> None:
        self.dropped: list[tuple[str, tuple]] = []

    def add_dropped_context(self, text: str, images=()) -> None:
        self.dropped.append((text, tuple(images)))


@pytest.mark.asyncio
async def test_visible_ide_target_owns_file_without_duplicate_global_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _stage(_items):
        return StagedPromptAttachments(terminal="Mika", batch_id="batch-1", files=("shot.png",))

    monkeypatch.setattr(prompt_attachments, "stage_items_for_prompt_target", _stage)
    brain = _Brain()

    result = await capture_presence_drop(
        brain=brain,
        items=[DroppedItem(name="shot.png", mime="image/png", data=b"png")],
    )

    assert result.captured is True
    assert result.terminal == "Mika"
    assert result.batch_id == "batch-1"
    assert brain.dropped == []


@pytest.mark.asyncio
async def test_drop_falls_back_to_assistant_context_without_ide_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _stage(_items):
        return None

    monkeypatch.setattr(prompt_attachments, "stage_items_for_prompt_target", _stage)
    brain = _Brain()

    result = await capture_presence_drop(
        brain=brain,
        items=[DroppedItem(name="notes.txt", mime="text/plain", data=b"hello")],
    )

    assert result.captured is True
    assert result.terminal == ""
    assert "notes.txt" in brain.dropped[0][0]
