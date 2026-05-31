"""Unit tests for SpeechPipeline.request_voice_session (Chats manager, Slice 4).

The "Speak in this conversation" entry point: arm a wake-style session from
the /api/chats/.../speak route, optionally seeding the brain with prior turns.
Built via ``__new__`` + attribute injection (the established pipeline-unit-test
pattern) so we don't drag in stt/tts/audio.
"""
from __future__ import annotations

import asyncio

from jarvis.speech.pipeline import PipelineState, SpeechPipeline


class _FakeBrain:
    def __init__(self, raises: bool = False) -> None:
        self.seeded: list[tuple[str, str]] | None = None
        self._raises = raises

    def seed_history(self, turns) -> None:
        if self._raises:
            raise RuntimeError("boom")
        self.seeded = list(turns)


def _pipe(*, state=PipelineState.IDLE, gate=True, ptt=False, brain=None):
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._ptt_mode = ptt
    p._state = state
    p._call_event = asyncio.Event()
    p._activation_gate = (lambda: gate)
    p._muted = False
    p._last_wake_keyword = ""
    p._brain = brain
    return p


def test_arms_when_idle() -> None:
    p = _pipe()
    assert p.request_voice_session() is True
    assert p._call_event.is_set()


def test_seeds_brain_on_arm() -> None:
    brain = _FakeBrain()
    p = _pipe(brain=brain)
    msgs = [("user", "hi"), ("assistant", "hello")]
    assert p.request_voice_session(seed_messages=msgs) is True
    assert brain.seeded == msgs


def test_noop_when_not_idle_and_does_not_seed() -> None:
    brain = _FakeBrain()
    p = _pipe(state=PipelineState.ACTIVE, brain=brain)
    assert p.request_voice_session(seed_messages=[("user", "x")]) is False
    assert not p._call_event.is_set()
    assert brain.seeded is None


def test_noop_when_ptt_active() -> None:
    p = _pipe(ptt=True)
    assert p.request_voice_session() is False
    assert not p._call_event.is_set()


def test_noop_when_activation_not_allowed_and_does_not_seed() -> None:
    brain = _FakeBrain()
    p = _pipe(gate=False, brain=brain)
    assert p.request_voice_session(seed_messages=[("user", "x")]) is False
    assert not p._call_event.is_set()
    assert brain.seeded is None


def test_seed_failure_still_arms() -> None:
    brain = _FakeBrain(raises=True)
    p = _pipe(brain=brain)
    assert p.request_voice_session(seed_messages=[("user", "x")]) is True
    assert p._call_event.is_set()


def test_arms_without_seed_messages() -> None:
    brain = _FakeBrain()
    p = _pipe(brain=brain)
    assert p.request_voice_session() is True
    assert p._call_event.is_set()
    assert brain.seeded is None  # seed_history never called
