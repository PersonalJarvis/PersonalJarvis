"""Unit tests for SpeechPipeline.request_voice_session (Chats manager, Slice 4).

The "Speak in this conversation" entry point: arm a wake-style session from
the /api/chats/.../speak route, optionally seeding the brain with prior turns.
Built via ``__new__`` + attribute injection (the established pipeline-unit-test
pattern) so we don't drag in stt/tts/audio.
"""
from __future__ import annotations

import asyncio
import threading
import time

from jarvis.speech.pipeline import PipelineState, SpeechPipeline, TurnTakingState


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
    p._runtime_loop = None
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


def test_session_is_active_while_start_subscribers_are_still_running() -> None:
    """The close X must remain a hangup during the startup state gap.

    PipelineState becomes ACTIVE before VoiceSessionStarted is dispatched, but
    the turn-state stays IDLE until all start subscribers return. This is the
    exact interval in which the live bar previously routed X clicks back into
    request_voice_session(), which then rejected them as "pipeline not idle".
    """
    p = _pipe(state=PipelineState.ACTIVE)
    p._turn_state = TurnTakingState.IDLE

    assert p.is_session_active() is True


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


async def test_taskbar_thread_wakes_owner_loop_immediately() -> None:
    """A Tk-thread request must wake the pipeline loop without another timer.

    ``asyncio.Event.set()`` is not thread-safe. Calling it directly from the
    Jarvis Bar's Tk thread only flips the flag; the selector can remain asleep
    until unrelated I/O or a timer happens to wake it, which created the
    variable 100-300 ms click-to-listen delay. The public request method must
    marshal the edge through the pipeline loop's thread-safe scheduler.
    """
    p = _pipe()
    owner = asyncio.get_running_loop()
    p._runtime_loop = owner
    ready = threading.Event()
    release = threading.Event()

    def _click_from_taskbar() -> None:
        ready.set()
        release.wait(timeout=0.5)
        assert p.request_voice_session() is True

    thread = threading.Thread(target=_click_from_taskbar, daemon=True)
    thread.start()
    assert ready.wait(timeout=0.1)
    waiter = asyncio.create_task(p._call_event.wait())
    await asyncio.sleep(0)  # register the waiter before the Tk-thread edge

    began = time.perf_counter()
    release.set()
    await asyncio.wait_for(waiter, timeout=0.5)
    elapsed = time.perf_counter() - began
    thread.join(timeout=0.1)

    assert elapsed < 0.2, (
        "taskbar request did not wake the owner loop promptly; it likely set "
        "asyncio.Event directly from the Tk thread"
    )
