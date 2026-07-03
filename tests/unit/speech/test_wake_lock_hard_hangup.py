"""A user HARD hangup must not deafen the wake to the very next word.

Live bug (data/jarvis_desktop.log 2026-07-02 18:40 / 18:46): closing the
JarvisBar (or muting then hanging up) ended the session and armed the full 3 s
post-hangup wake-lock, so the user's immediate "Hey <wake>" was swallowed and
had to be said twice:

    18:40:44.771  AUFGELEGT — Wake-Lock 3.0s
    18:40:44.857  Wake-Lock aktiv — ignoriere (noch 2.9s)   # the re-wake, eaten

A user hangup stops the player (`stop_player=True`), so there is NO TTS tail to
echo — the long lock is pointless there. It must use the SHORT lock instead. A
farewell hangup (`stop_player=False`) lets the goodbye play, so it keeps the
full speaker-tail guard.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace

from jarvis.core.config import STTConfig
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class _FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None, language_code: str | None = None
    ) -> AsyncIterator:
        if False:  # pragma: no cover
            yield


def _pipe() -> SpeechPipeline:
    # Lightweight, no local Whisper / detectors — we only exercise the hangup
    # → wake-lock decision, which is audio-engine-independent.
    return SpeechPipeline(
        tts=_FakeTTS(),
        bus=None,
        enable_openwakeword=False,
        enable_whisper_wake=False,
        enable_local_whisper=False,
        config=SimpleNamespace(stt=STTConfig(provider="groq-api")),
    )


def test_hard_hangup_uses_the_short_lock() -> None:
    pipe = _pipe()
    pipe._trigger_voice_hangup(stop_player=True)  # JarvisBar / hotkey / "auflegen"
    assert pipe._explicit_hard_hangup is True
    lock = pipe._post_hangup_lock_seconds()
    assert lock == pipe._explicit_hangup_lock_s
    assert lock < 1.0, "a hard hangup must not deafen the wake for ~1s+"
    # one-shot: the flag is consumed so the NEXT (natural) end keeps the guard
    assert pipe._explicit_hard_hangup is False
    assert pipe._post_hangup_lock_seconds() == pipe._post_hangup_lock_s


def test_farewell_hangup_keeps_the_full_speaker_tail_guard() -> None:
    pipe = _pipe()
    pipe._trigger_voice_hangup(stop_player=False)  # let the goodbye play out
    assert pipe._explicit_hard_hangup is False
    assert pipe._post_hangup_lock_seconds() == pipe._post_hangup_lock_s


def test_natural_end_without_a_hangup_keeps_the_full_guard() -> None:
    pipe = _pipe()
    # No hangup call at all (e.g. an idle-timeout / single-turn end).
    assert pipe._post_hangup_lock_seconds() == pipe._post_hangup_lock_s


def test_idle_noop_hangup_does_not_shorten_a_later_natural_end() -> None:
    # A hard hangup while idle sets the flag, but a session that later ends
    # naturally (reset at session start) must still get the full guard. We model
    # the per-session reset that _state_loop does at accept time.
    pipe = _pipe()
    pipe._trigger_voice_hangup(stop_player=True)
    assert pipe._explicit_hard_hangup is True
    pipe._explicit_hard_hangup = False  # what _state_loop does on session start
    assert pipe._post_hangup_lock_seconds() == pipe._post_hangup_lock_s
