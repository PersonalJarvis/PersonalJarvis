"""A failing turn planner must never take the live voice call down.

Live incident 2026-07-25 15:35: ``plan_turn`` was called with a keyword the
installed planner did not accept yet, so every committed turn raised
``TypeError``. The exception escaped ``_plan_turn`` into the transport pump,
which treats any exception as a dead provider socket — it rebuilt the session
three times, hit the rebuild ceiling and gave up. Four spoken turns produced
no answer and no audible reply at all.

Turn planning only chooses a route between the native realtime model and the
orchestrator, and it has a safe default. A planner that fails must degrade to
that default, never terminate the call.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession

_REPLY = "Mostly through the Suez Canal."


class _PlainBrain:
    """A brain without its own planner, so the local planner is exercised."""

    conversation_language = "en"

    async def __call__(self, text: str) -> str:
        return "delegated"


class _AnsweringWire:
    """Gemini-shaped wire: it answers on its own after a committed turn."""

    session_id = "planner-crash-wire"
    supports_tool_updates = False
    creates_responses_automatically = True
    isolates_response_generations = True

    def __init__(self) -> None:
        self.text_inputs: list[str] = []
        self.opened = 0
        self.closed = asyncio.Event()

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="Do ships to New York take the Suez Canal?",
            is_final=True,
            item_id="suez-turn",
        )
        yield RealtimeEvent(type="output_transcript_delta", text=_REPLY)
        yield RealtimeEvent(type="turn_complete")
        await self.closed.wait()

    async def send_audio(self, _chunk: Any) -> None:
        return None

    async def update_session(self, **_kwargs: Any) -> None:
        return None

    async def request_response(self, **_kwargs: Any) -> None:
        return None

    async def send_text(self, text: str) -> None:
        self.text_inputs.append(text)

    async def truncate(self, _audio_end_ms: int) -> None:
        return None

    async def interrupt(self) -> None:
        return None

    async def send_tool_result(self, *_args: Any) -> None:
        return None

    async def close(self) -> None:
        self.closed.set()


class _AnsweringProvider:
    name = "planner-crash"
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self) -> None:
        self.session = _AnsweringWire()

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, _config: Any) -> _AnsweringWire:
        self.session.opened += 1
        return self.session


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


@pytest.mark.asyncio
async def test_planner_exception_still_delivers_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising planner degrades to the safe route; the reply still arrives."""
    import jarvis.realtime.session as session_module

    def _explode(*_args: Any, **_kwargs: Any):
        raise TypeError(
            "plan_turn() got an unexpected keyword argument 'skill_index'"
        )

    monkeypatch.setattr(session_module, "plan_turn", _explode)

    provider = _AnsweringProvider()
    messages: list[dict[str, Any]] = []
    answered = asyncio.Event()

    def _capture(message: dict[str, Any]):
        messages.append(message)
        if message.get("type") == "transcript" and message.get(
            "role"
        ) == "assistant":
            answered.set()
        return asyncio.sleep(0)

    session = RealtimeVoiceSession(
        session_id="planner-crash",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=_capture,
        provider=provider,
        config=_config(),
        bus=None,
        browser_sample_rate=16_000,
        surface="desktop",
        brain=_PlainBrain(),
    )

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        await asyncio.wait_for(answered.wait(), timeout=2.0)
    except TimeoutError:
        pass
    finally:
        await session.end(reason="test")

    assert answered.is_set(), (
        "a failing turn planner silenced the call: the provider's answer "
        f"never reached the surface (messages={messages})"
    )
    assert not [
        message for message in messages if message.get("type") == "provider_error"
    ], "a planner failure must not be reported as a dead transport"
    assert provider.session.opened == 1, (
        "a planner failure must not trigger a transport rebuild "
        f"(opened={provider.session.opened})"
    )
