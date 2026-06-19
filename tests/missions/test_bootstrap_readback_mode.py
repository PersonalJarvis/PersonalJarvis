"""Tests for the mission voice-readback mode resolver.

#6 (2026-05-27 hardening audit) double-voice-readback-listener-and-announcer-
both-active: ``bootstrap_missions`` started the MissionVoiceListener (gated on
``tts_speak_fn``) and the MissionAnnouncer (gated on ``speech_bus``)
independently, with no mutual-exclusion guard. A caller that supplied BOTH
would get every MissionApproved/MissionFailed spoken twice (the announcer's own
docstring warns: "gleichzeitig beide aktivieren = Doppel-Ansage"). The resolver
makes the choice explicit and exclusive: the announcer wins whenever a
speech_bus is available; the listener is the fallback for direct-TTS callers.
"""
from __future__ import annotations

from jarvis.missions.init import _resolve_readback_mode


def _fn(*_a: object, **_k: object) -> None:  # stand-in TTS callback
    return None


def test_both_provided_prefers_announcer() -> None:
    assert (
        _resolve_readback_mode(tts_speak_fn=_fn, speech_bus=object())
        == "announcer"
    )


def test_only_tts_uses_listener() -> None:
    assert (
        _resolve_readback_mode(tts_speak_fn=_fn, speech_bus=None) == "listener"
    )


def test_only_speech_bus_uses_announcer() -> None:
    assert (
        _resolve_readback_mode(tts_speak_fn=None, speech_bus=object())
        == "announcer"
    )


def test_neither_is_none() -> None:
    assert _resolve_readback_mode(tts_speak_fn=None, speech_bus=None) == "none"
