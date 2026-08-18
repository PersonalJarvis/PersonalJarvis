"""BrainManager streaming correctness nets."""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.protocols import BrainMessage


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


@pytest.mark.asyncio
async def test_generate_stream_buffers_contextual_action_turn_until_final() -> None:
    manager = _bare_manager()
    manager._history = [
        BrainMessage(role="user", content="What is in my private Wiki?"),
    ]

    async def fake_generate(user_text: str, **kwargs: Any) -> str:
        consumer = kwargs["text_consumer"]
        consumer("One moment, ")
        consumer("I'll check and get back to you.")
        return "I did not start that action."

    manager.generate = fake_generate  # type: ignore[method-assign]

    assert await _collect_stream(manager, "What does it say?") == [
        "I did not start that action."
    ]


# ---------------------------------------------------------------------------
# Tool-use leak guard (audit OF-10).
#
# The guard used to fire when the ACCUMULATED buffer merely STARTED with "[" or
# "{", and the flag was sticky for the rest of the turn — every later chunk was
# dropped in silence. An answer opening with a markdown link or a JSON example
# lost the ENTIRE streamed reply. The shape test may now only HOLD the chunks;
# the verdict is taken once, on the finished buffer, with the real parser.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_releases_answer_that_only_opens_like_json() -> None:
    manager = _bare_manager()

    async def fake_generate(user_text: str, **kwargs: Any) -> str:
        consumer = kwargs["text_consumer"]
        consumer("[Doku](https://example.com)")
        consumer(" covers it. ")
        consumer("The rest follows.")
        return "[Doku](https://example.com) covers it. The rest follows."

    manager.generate = fake_generate  # type: ignore[method-assign]

    chunks = await _collect_stream(manager)
    assert "".join(chunks) == (
        "[Doku](https://example.com) covers it. The rest follows."
    )


@pytest.mark.asyncio
async def test_stream_releases_answer_opening_with_a_json_example() -> None:
    manager = _bare_manager()

    async def fake_generate(user_text: str, **kwargs: Any) -> str:
        consumer = kwargs["text_consumer"]
        consumer('{"name": "x"}')
        consumer(" is a valid configuration entry.")
        return '{"name": "x"} is a valid configuration entry.'

    manager.generate = fake_generate  # type: ignore[method-assign]

    assert "".join(await _collect_stream(manager)) == (
        '{"name": "x"} is a valid configuration entry.'
    )


@pytest.mark.asyncio
async def test_stream_still_withholds_a_real_leaked_tool_use() -> None:
    manager = _bare_manager()

    async def fake_generate(user_text: str, **kwargs: Any) -> str:
        consumer = kwargs["text_consumer"]
        consumer('[{"type":"tool_use",')
        consumer('"name":"open_app",')
        consumer('"input":{"app":"notepad"}}]')
        return "Notepad is open."

    manager.generate = fake_generate  # type: ignore[method-assign]

    # The raw envelope never reaches TTS; generate()'s spoken result does.
    assert await _collect_stream(manager) == ["Notepad is open."]


@pytest.mark.asyncio
async def test_stream_withholds_a_bare_structured_blob() -> None:
    """A whole-document blob is not speech even when it is not a tool call."""
    manager = _bare_manager()

    async def fake_generate(user_text: str, **kwargs: Any) -> str:
        consumer = kwargs["text_consumer"]
        consumer('{"query": "x", ')
        consumer('"results": []}')
        return "I found nothing for that."

    manager.generate = fake_generate  # type: ignore[method-assign]

    assert await _collect_stream(manager) == ["I found nothing for that."]


@pytest.mark.asyncio
async def test_stream_held_prose_still_yields_to_the_evidence_gate() -> None:
    """Holding must not smuggle an unverified answer past the evidence gate."""
    manager = _bare_manager()

    async def fake_generate(user_text: str, **kwargs: Any) -> str:
        manager._evidence_required_tool = "cli_gcloud"
        consumer = kwargs["text_consumer"]
        consumer("[see the invoice](https://example.com)")
        consumer(" — 42 euros.")
        return "honest fallback"

    manager.generate = fake_generate  # type: ignore[method-assign]

    assert await _collect_stream(manager) == ["honest fallback"]
