"""BrainManager streaming correctness nets."""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.manager import BrainManager


def _bare_manager() -> BrainManager:
    m = BrainManager.__new__(BrainManager)
    m._evidence_required_tool = ""
    return m


async def _collect_stream(manager: BrainManager, text: str = "hello") -> list[str]:
    return [chunk async for chunk in manager.generate_stream(text)]


@pytest.mark.asyncio
async def test_generate_stream_yields_chunks_without_evidence_gate() -> None:
    manager = _bare_manager()

    async def fake_generate(user_text: str, **kwargs: Any) -> str:
        consumer = kwargs["text_consumer"]
        consumer("hello ")
        consumer("world")
        return "hello world"

    manager.generate = fake_generate  # type: ignore[method-assign]

    assert await _collect_stream(manager) == ["hello ", "world"]


@pytest.mark.asyncio
async def test_generate_stream_buffers_evidence_gated_chunks_until_final() -> None:
    manager = _bare_manager()

    async def fake_generate(user_text: str, **kwargs: Any) -> str:
        manager._evidence_required_tool = "cli_twilio"
        consumer = kwargs["text_consumer"]
        consumer("unverified ")
        consumer("answer")
        return "honest fallback"

    manager.generate = fake_generate  # type: ignore[method-assign]

    assert await _collect_stream(manager) == ["honest fallback"]
