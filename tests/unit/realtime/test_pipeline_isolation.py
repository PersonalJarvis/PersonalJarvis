"""Regression guards for realtime ownership and provider error isolation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession


class _EventSession:
    session_id = "provider-session"
    supports_tool_updates = True
    creates_responses_automatically = False
    isolates_response_generations = True

    def __init__(self, event: RealtimeEvent, *, stay_open: bool) -> None:
        self._event = event
        self._stay_open = stay_open
        self.processed = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def send_audio(self, _chunk: Any) -> None:
        return None

    async def receive(self):
        yield self._event
        self.processed.set()
        if self._stay_open:
            await self.release.wait()

    async def update_session(self, **_kwargs: Any) -> None:
        return None

    async def request_response(self, **_kwargs: Any) -> None:
        return None

    async def send_text(self, _text: str) -> None:
        return None

    async def truncate(self, _audio_end_ms: int) -> None:
        return None

    async def interrupt(self) -> None:
        return None

    async def send_tool_result(
        self, _call_id: str, _name: str, _result: dict[str, Any]
    ) -> None:
        return None

    async def close(self) -> None:
        self.closed = True
        self.release.set()


class _Provider:
    name = "realtime-only"
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self, event: RealtimeEvent, *, stay_open: bool) -> None:
        self.session = _EventSession(event, stay_open=stay_open)

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, _config: Any) -> _EventSession:
        return self.session


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="direct"),
        latency=SimpleNamespace(enabled=False),
    )


def _wrapper(provider: _Provider, messages: list[dict[str, Any]]) -> RealtimeVoiceSession:
    async def _send_json(message: dict[str, Any]) -> None:
        messages.append(message)

    async def _send_binary(_data: bytes) -> None:
        return None

    return RealtimeVoiceSession(
        session_id="pipeline-isolation",
        send_binary=_send_binary,
        send_json=_send_json,
        provider=provider,
        config=_config(),
        bus=None,
        brain=None,
    )


@pytest.mark.asyncio
async def test_recoverable_provider_error_keeps_realtime_session_healthy() -> None:
    messages: list[dict[str, Any]] = []
    provider = _Provider(
        RealtimeEvent(
            type="error",
            error="conversation_already_has_active_response",
            recoverable=True,
        ),
        stay_open=True,
    )
    session = _wrapper(provider, messages)

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(provider.session.processed.wait(), timeout=1.0)

    assert session.failed is False
    assert session.is_active is True
    assert any(message["type"] == "provider_warning" for message in messages)
    assert not any(message["type"] == "provider_error" for message in messages)
    await session.end(reason="test")


@pytest.mark.asyncio
async def test_terminal_provider_error_keeps_voice_until_teardown() -> None:
    messages: list[dict[str, Any]] = []
    provider = _Provider(
        RealtimeEvent(type="error", error="fatal provider error"),
        stay_open=False,
    )
    session = _wrapper(provider, messages)

    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(session.wait_finished(), timeout=1.0)

    assert session.failed is True
    assert any(message["type"] == "provider_error" for message in messages)
    await session.end(reason="test")
    assert session.is_active is False
