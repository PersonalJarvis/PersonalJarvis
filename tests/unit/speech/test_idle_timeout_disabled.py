"""A conversation session must stay active until a MANUAL hangup when the idle
timeout is disabled.

User mandate 2026-06-30: in conversation mode (``single_turn_mode = false``) the
only time-based auto-ender is the idle/silence timeout in ``_active_session``
(default 30 s). The user wants the session to stay active indefinitely and end
ONLY on an explicit hangup ("auflegen" / the hangup hotkey). Setting
``[trigger] session_idle_timeout_s = 0`` (any value <= 0) disables the idle
hangup: ``SpeechPipeline(idle_timeout_s=0)`` then never returns
``HANGUP_IDLE_TIMEOUT`` on its own — it waits forever for the next utterance or a
manual hangup.

These guards pin that contract; pre-fix the session hung up on the first idle
window.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import AudioChunk
from jarvis.sessions.constants import HANGUP_HOTKEY, HANGUP_IDLE_TIMEOUT
from jarvis.speech import pipeline as pipeline_mod
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


class _FakeMic:
    async def __aenter__(self) -> "_FakeMic":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    def stream(self):
        async def gen():
            await asyncio.Event().wait()  # pragma: no cover — never yields
            yield b""
        return gen()


class _FakeVAD:
    def utterances(self, stream):
        async def gen():
            await asyncio.Event().wait()  # silence: never an utterance
            yield b""  # pragma: no cover
        return gen()


def _pipeline(idle_timeout_s: float) -> SpeechPipeline:
    return SpeechPipeline(
        tts=FakeTTS(),
        bus=EventBus(),
        enable_whisper_wake=False,
        idle_timeout_s=idle_timeout_s,
    )


# ---------------------------------------------------------------------------
# Config schema default + constructor flag
# ---------------------------------------------------------------------------


def test_trigger_config_session_idle_timeout_default() -> None:
    # Downloaders keep the safe 30 s auto-hangup; only an explicit 0 disables it.
    from jarvis.core.config import TriggerConfig

    assert TriggerConfig().session_idle_timeout_s == 30.0


@pytest.mark.parametrize(
    "value,enabled",
    [(30.0, True), (0.0, False), (-1.0, False)],
)
def test_idle_hangup_enabled_flag(value: float, enabled: bool) -> None:
    pipe = _pipeline(value)
    assert pipe._idle_hangup_enabled is enabled


# ---------------------------------------------------------------------------
# Behavioural: the real bug repro (drives the actual ``_active_session`` loop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_session_never_idle_hangs_up_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the idle timeout disabled, a silent session must NOT hang up — it
    stays open until a manual hangup. Pre-fix (``timeout=0``) the idle branch
    fired immediately and returned ``HANGUP_IDLE_TIMEOUT``."""
    pipe = _pipeline(0)
    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda device=None: _FakeMic(),
    )
    pipe._vad = _FakeVAD()

    session = asyncio.create_task(pipe._active_session())
    # Many "idle windows" worth of silence — the session must still be alive.
    await asyncio.sleep(0.25)
    assert not session.done(), (
        "the session hung up by itself — with the idle timeout disabled it must "
        "stay active until a manual hangup"
    )

    # A manual hangup (hotkey / 'auflegen' setting the event) ends it cleanly.
    pipe._hangup_event.set()
    reason = await asyncio.wait_for(session, timeout=2.0)
    assert reason == HANGUP_HOTKEY


@pytest.mark.asyncio
async def test_active_session_still_idle_hangs_up_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard the other way: a positive timeout still auto-hangs-up so
    the default behaviour (and every downloader) is unchanged."""
    pipe = _pipeline(0.05)  # tiny positive window → fast test
    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda device=None: _FakeMic(),
    )
    pipe._vad = _FakeVAD()

    reason = await asyncio.wait_for(pipe._active_session(), timeout=2.0)
    assert reason == HANGUP_IDLE_TIMEOUT
