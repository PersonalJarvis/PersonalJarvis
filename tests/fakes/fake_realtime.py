"""Reusable in-memory duplex provider fakes for realtime voice tests."""

from __future__ import annotations

import asyncio
from typing import Any


class FakeRealtimeWire:
    """Scripted provider session with no network, audio device, or model."""

    session_id = "fake-realtime-wire"
    creates_responses_automatically = False
    # This fake stands in for the two bundled adapters, both of which isolate
    # cancelled output generations before exposing normalized events.
    isolates_response_generations = True

    def __init__(self, events: list[Any], *, hold_after_events: bool = False) -> None:
        self._events = list(events)
        self._hold_after_events = hold_after_events
        self.events_drained = asyncio.Event()
        self.sent_audio: list[Any] = []
        self.tool_results: list[tuple[str, str, dict[str, Any]]] = []
        self.session_updates: list[dict[str, Any]] = []
        self.response_requests = 0
        self.required_tools: list[str | None] = []
        self.truncated: list[int] = []
        self.interrupts = 0
        self.closed = False

    async def send_audio(self, chunk: Any) -> None:
        self.sent_audio.append(chunk)

    async def receive(self):
        for event in self._events:
            yield event
            await asyncio.sleep(0)
        self.events_drained.set()
        if self._hold_after_events:
            await asyncio.Event().wait()

    async def update_session(
        self,
        *,
        instructions: str | None = None,
        language: str | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> None:
        self.session_updates.append(
            {"instructions": instructions, "language": language, "tools": tools}
        )

    async def request_response(self, *, required_tool: str | None = None) -> None:
        self.response_requests += 1
        self.required_tools.append(required_tool)

    async def truncate(self, audio_end_ms: int) -> None:
        self.truncated.append(audio_end_ms)

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
        self.tool_results.append((call_id, name, result))

    async def close(self) -> None:
        self.closed = True


class FakeRealtimeProvider:
    """Provider wrapper that records the effective session configuration."""

    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(
        self,
        name: str,
        events: list[Any],
        *,
        open_error: Exception | None = None,
        hold_after_events: bool = False,
    ) -> None:
        self.name = name
        self._events = list(events)
        self._open_error = open_error
        self._hold_after_events = hold_after_events
        self.opened_with: Any = None
        self.session: FakeRealtimeWire | None = None

    async def can_open_duplex_session(self) -> bool:
        return self._open_error is None

    async def open_session(self, config: Any) -> FakeRealtimeWire:
        self.opened_with = config
        if self._open_error is not None:
            raise self._open_error
        self.session = FakeRealtimeWire(
            self._events,
            hold_after_events=self._hold_after_events,
        )
        return self.session


class FakeRealtimeToolBridge:
    """Minimal direct-mode bridge; persistence tests do not execute tools."""

    declarations = (
        {
            "name": "open_app",
            "description": "Open an application.",
            "parameters": {"type": "object", "properties": {}},
        },
    )

    def __init__(self) -> None:
        self.languages: list[str] = []
        self.transcripts: list[str] = []
        self.closed = False

    def set_language(self, language: str) -> None:
        self.languages.append(language)

    async def handle_user_transcript(self, text: str) -> None:
        self.transcripts.append(text)

    async def execute(
        self, *, wire_name: str, arguments: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        return wire_name, {"success": True, "output": "done", "error": None}

    async def close(self) -> None:
        self.closed = True


__all__ = [
    "FakeRealtimeProvider",
    "FakeRealtimeToolBridge",
    "FakeRealtimeWire",
]
