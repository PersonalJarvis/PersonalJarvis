"""An engine handover is not a hangup — surfaces must keep following the call.

Live forensic 2026-07-26 (log 16:15 + 16:56): both realtime providers were out
of credit, so EVERY voice session fell back to the classic pipeline seconds
after the wake word. On its way out the realtime session published
``VoiceSessionEnded(desktop_fallback)`` for a call that was still running under
the SAME ``session_id``. The orb bridge armed its post-hangup suppression latch
and dropped every later LISTENING/THINKING/SPEAKING of that call as a stray
("stray THINKING outside live session suppressed"), so the JarvisBar froze the
instant the handover happened and only came back on the next wake word. The
session recorder closed the row with ``turns=0`` for the same reason.

The desktop pipeline owns the real session bracket: it publishes
``VoiceSessionStarted`` before the engine is chosen and the one real
``VoiceSessionEnded`` in its session-loop teardown. The browser surface has no
such second publisher, so its fallback end must survive.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import VoiceSessionEnded
from jarvis.realtime.session import RealtimeVoiceSession
from jarvis.sessions.constants import (
    HANGUP_DESKTOP_FALLBACK,
    HANGUP_REALTIME_FALLBACK,
    HANGUP_VOICE_PATTERN,
)


class _IdleProvider:
    """Provider that never opens a stream — the handover case under test."""

    name = "fake-realtime"
    input_sample_rate = 16_000

    async def open(self, config):  # pragma: no cover - never reached here
        raise RuntimeError("provider unavailable")


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime"),
        latency=SimpleNamespace(enabled=False),
    )


def _session(*, bus: EventBus, surface: str) -> RealtimeVoiceSession:
    return RealtimeVoiceSession(
        session_id="s-handover",
        send_binary=lambda _b: asyncio.sleep(0),
        send_json=lambda _m: asyncio.sleep(0),
        provider=_IdleProvider(),
        config=_cfg(),
        bus=bus,
        surface=surface,
    )


async def _ends_published(*, surface: str, reason: str) -> list[VoiceSessionEnded]:
    bus = EventBus()
    seen: list[VoiceSessionEnded] = []

    async def _capture(event: VoiceSessionEnded) -> None:
        seen.append(event)

    bus.subscribe(VoiceSessionEnded, _capture)
    session = _session(bus=bus, surface=surface)
    if surface == "browser":
        # The browser gate opens once its own session-started went out.
        session._browser_session_started = True
    await session.end(reason=reason)
    return seen


@pytest.mark.asyncio
async def test_desktop_handover_to_classic_publishes_no_session_end() -> None:
    """The call continues in the classic pipeline — nothing ended."""
    assert await _ends_published(surface="desktop", reason=HANGUP_DESKTOP_FALLBACK) == []


@pytest.mark.asyncio
async def test_real_desktop_hangup_still_publishes_session_end() -> None:
    """A genuine hangup must stay observable (the redundancy that keeps the
    wiki completeness sweep alive when the pipeline misses its teardown)."""
    ends = await _ends_published(surface="desktop", reason=HANGUP_VOICE_PATTERN)
    assert [event.hangup_reason for event in ends] == [HANGUP_VOICE_PATTERN]


@pytest.mark.asyncio
async def test_browser_fallback_still_publishes_session_end() -> None:
    """The browser's classic session publishes no end of its own — suppressing
    this one would leave its recorder row open forever."""
    ends = await _ends_published(surface="browser", reason=HANGUP_REALTIME_FALLBACK)
    assert [event.hangup_reason for event in ends] == [HANGUP_REALTIME_FALLBACK]
