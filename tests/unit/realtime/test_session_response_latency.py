"""``first_final_to_first_audio_ms``: the honest answer-latency metric.

``first_audio_ms`` measures from session start, so it also counts the time
the user spent listening to the greeting and speaking their own utterance —
a healthy codex call read 8 311 ms while the wait after the final was 923 ms.
These tests pin the new metric's contract: it runs from the FIRST user final
to the first AUDIBLE provider frame after it, silence never closes it, audio
before any final never starts it, and 0 stays the "never measured" sentinel.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from jarvis.core.bus import EventBus
from jarvis.core.events import RealtimeSessionPostmortem
from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession

TIMEOUT_S = 5.0
RATE = 24_000
AUDIBLE_PCM = b"\x01\x02" * 160  # int16 peak 513, above the silence floor
SILENT_PCM = b"\x01\x00" * 160  # int16 peak 1, embedded silence


class _ScriptedWire:
    session_id = "response-latency-wire"
    supports_tool_updates = True
    creates_responses_automatically = False
    isolates_response_generations = False

    def __init__(self, events: list[RealtimeEvent]) -> None:
        self._events = events

    async def receive(self):  # noqa: ANN201 - async generator, protocol shape
        for event in self._events:
            yield event
            await asyncio.sleep(0)

    async def send_audio(self, chunk: Any) -> None:
        del chunk

    async def update_session(self, **kwargs: Any) -> None:
        del kwargs

    async def request_response(self, **kwargs: Any) -> None:
        del kwargs

    async def send_text(self, text: str) -> None:
        del text

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms

    async def interrupt(self, **kwargs: Any) -> None:
        del kwargs

    async def send_tool_result(self, *args: Any) -> None:
        del args

    async def close(self) -> None:
        return None


class _Provider:
    name = "scripted"
    supports_realtime = True
    input_sample_rate = RATE
    output_sample_rate = RATE

    def __init__(self, events: list[RealtimeEvent]) -> None:
        self._events = events

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> _ScriptedWire:
        del config
        return _ScriptedWire(self._events)


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


async def _run_call(
    events: list[RealtimeEvent],
) -> RealtimeSessionPostmortem:
    bus = EventBus()
    seen: list[RealtimeSessionPostmortem] = []

    async def _collect(event: RealtimeSessionPostmortem) -> None:
        seen.append(event)

    bus.subscribe(RealtimeSessionPostmortem, _collect)
    session = RealtimeVoiceSession(
        session_id="response-latency",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        providers=[_Provider(events)],
        config=_config(),
        bus=bus,
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=RATE,
    )
    await asyncio.wait_for(
        session.handle_control({"type": "audio_start", "sample_rate": RATE}),
        TIMEOUT_S,
    )
    await asyncio.wait_for(session.wait_finished(), TIMEOUT_S)
    await asyncio.wait_for(session.end(reason="test"), TIMEOUT_S)
    assert len(seen) == 1
    return seen[0]


async def test_metric_runs_from_first_final_to_first_audible_frame() -> None:
    postmortem = await _run_call(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Hello how are you",
                is_final=True,
                item_id="turn-1",
            ),
            RealtimeEvent(type="output_transcript_delta", text="Hello there."),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=AUDIBLE_PCM, sample_rate=RATE, timestamp_ns=0
                ),
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    assert postmortem.first_final_to_first_audio_ms >= 1, (
        "a captured value is floored to 1 ms so it can never read as the "
        "0 'never measured' sentinel"
    )
    assert (
        postmortem.first_final_to_first_audio_ms <= postmortem.first_audio_ms
        or postmortem.first_audio_ms == 0
    ), "the wait after the final can never exceed the wait from session start"


async def test_audio_before_any_final_never_starts_the_measurement() -> None:
    postmortem = await _run_call(
        [
            RealtimeEvent(type="output_transcript_delta", text="Hi there."),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=AUDIBLE_PCM, sample_rate=RATE, timestamp_ns=0
                ),
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    assert postmortem.first_final_to_first_audio_ms == 0


async def test_embedded_silence_never_closes_the_measurement() -> None:
    postmortem = await _run_call(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Hello how are you",
                is_final=True,
                item_id="turn-1",
            ),
            RealtimeEvent(type="output_transcript_delta", text="Hello there."),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(
                    pcm=SILENT_PCM, sample_rate=RATE, timestamp_ns=0
                ),
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    assert postmortem.first_final_to_first_audio_ms == 0, (
        "silent PCM is forwarded but is not an audible answer"
    )
