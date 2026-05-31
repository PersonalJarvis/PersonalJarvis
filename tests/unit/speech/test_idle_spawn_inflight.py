"""Regression: a background mission in flight must not trip the idle-timeout.

Live repro (jarvis_desktop.log, 2026-05-31 14:31:40 .. 14:32:13):

    14:31:40  user: "Kannst du fuer mich bitte eine HTML-Datei erstellen ..."
    14:31:51  Spawn-ACK suppressed  -> _schedule_spawn_watchdog() armed
    14:32:13  Idle-Timeout -> lege auf.            <-- session hung up
    14:32:20  ClaudeDirectWorker done (still running when the hangup fired)

Root cause: the spawn-in-flight override only lived in
``SpeechPipeline._finish_after_response`` (it keeps the turn open right after
the ACK). ``_active_session``'s idle-timeout branch ignored
``_spawn_watchdog_tasks`` entirely, so once the loop went back to waiting, the
plain 30 s idle timer fired mid-mission. The completion readback that arrives
30-90 s later was then swallowed by the hangup-gate -> "Jarvis hangs up by
itself after a sub-agent task".

The fix makes the idle-timeout honour ``_spawn_watchdog_tasks`` exactly like
``_finish_after_response`` does: while a watchdog is pending, keep the voice
session open and restart the idle window instead of returning
``HANGUP_IDLE_TIMEOUT``.
"""
from __future__ import annotations

import asyncio

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.sessions.constants import HANGUP_IDLE_TIMEOUT
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


class _FakeMic:
    """Async-context mic whose stream never yields a chunk."""

    async def __aenter__(self) -> "_FakeMic":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def stream(self):  # pragma: no cover - never consumed by _FakeVad
        while True:
            await asyncio.sleep(3600)
            yield b""


class _FakeVad:
    """VAD whose ``utterances()`` never produces an endpoint -> every wait
    round times out, which is exactly the "user is silent, waiting for the
    worker" situation the bug is about."""

    def utterances(self, _stream):
        async def _gen():  # pragma: no cover - never advances within the test
            while True:
                await asyncio.sleep(3600)
                yield b""

        return _gen()


def _make_active_session_pipeline(idle_timeout_s: float) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._ptt_mode = False
    pipe._idle_timeout_s = idle_timeout_s
    pipe._input_device = None
    pipe._hangup_event = asyncio.Event()
    pipe._session_end_reason = None
    pipe._carry_pcm = bytearray()
    pipe._carry_started_monotonic = None
    pipe._last_endpoint_reason = None
    pipe._vad = _FakeVad()
    pipe._spawn_watchdog_tasks = []

    async def _noop_state(_state: TurnTakingState) -> None:
        return None

    async def _noop_publish(_event: object) -> None:
        return None

    pipe._set_turn_state = _noop_state  # type: ignore[method-assign]
    pipe._publish_event = _noop_publish  # type: ignore[method-assign]
    return pipe


@pytest.mark.asyncio
async def test_idle_timeout_extended_while_background_mission_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _make_active_session_pipeline(idle_timeout_s=0.02)

    # One pending spawn watchdog == "a background mission is in flight".
    gate = asyncio.Event()
    watchdog = asyncio.create_task(gate.wait())
    pipe._spawn_watchdog_tasks = [watchdog]

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda device=None: _FakeMic()
    )

    task = asyncio.create_task(pipe._active_session())
    try:
        # Let many idle windows elapse. With a mission in flight the session
        # must stay open (pre-fix: it returned HANGUP_IDLE_TIMEOUT after ~20 ms).
        await asyncio.sleep(0.2)
        assert not task.done(), (
            "voice session hung up via idle-timeout while a background "
            "mission was still in flight"
        )

        # Mission completes -> watchdog drained -> idle resumes -> session ends.
        pipe._spawn_watchdog_tasks.clear()
        reason = await asyncio.wait_for(task, timeout=2.0)
        assert reason == HANGUP_IDLE_TIMEOUT
    finally:
        gate.set()
        watchdog.cancel()
        if not task.done():
            task.cancel()


@pytest.mark.asyncio
async def test_idle_timeout_still_hangs_up_without_inflight_mission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard the other direction: with no watchdog pending the plain
    idle-timeout must still hang up (the override must not wedge the session
    open in the normal case)."""
    pipe = _make_active_session_pipeline(idle_timeout_s=0.02)

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda device=None: _FakeMic()
    )

    reason = await asyncio.wait_for(pipe._active_session(), timeout=2.0)
    assert reason == HANGUP_IDLE_TIMEOUT
