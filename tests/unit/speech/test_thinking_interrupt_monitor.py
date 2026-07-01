"""Unit B: thinking-phase continuation-interrupt monitor in the stall guard."""
from __future__ import annotations

import asyncio
import time

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


def _guard_pipeline(*, enabled=True):
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._continuation_interrupt_enabled = enabled
    p._brain_stall_poll_s = 0.01
    p._brain_timeout_s = 30.0
    p._brain_hard_timeout_s = 90.0
    # Must be time.monotonic() (not 0.0) so the stall guard does not fire
    # immediately (it checks ``time.monotonic() - _brain_last_progress >= stall_s``).
    p._brain_last_progress = time.monotonic()
    p._brain_thinking_heartbeat = 0.0
    p._long_tool_last_activity = 0.0
    p._brain_first_frame_played = False
    return p


@pytest.mark.asyncio
async def test_interrupt_during_thinking_aborts_and_returns_barged(monkeypatch):
    p = _guard_pipeline()

    async def fake_monitor(*, grace_s, respect_input_suppression=False):
        return True

    async def slow_brain():
        await asyncio.sleep(5)
        return ("answer", False)

    monkeypatch.setattr(p, "_barge_monitor", fake_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    response, barged = await p._run_brain_with_stall_guard(
        slow_brain(), interrupt_monitor=True
    )
    assert response == ""
    assert barged is True


@pytest.mark.asyncio
async def test_no_interrupt_returns_brain_result(monkeypatch):
    p = _guard_pipeline()

    async def quiet_monitor(*, grace_s, respect_input_suppression=False):
        await asyncio.sleep(10)
        return False

    async def quick_brain():
        return ("the answer", False)

    monkeypatch.setattr(p, "_barge_monitor", quiet_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    response, barged = await p._run_brain_with_stall_guard(
        quick_brain(), interrupt_monitor=True
    )
    assert response == "the answer"
    assert barged is False


@pytest.mark.asyncio
async def test_monitor_stands_down_after_first_frame(monkeypatch):
    p = _guard_pipeline()

    async def fake_monitor(*, grace_s, respect_input_suppression=False):
        p._brain_first_frame_played = True
        await asyncio.sleep(0.05)
        return True

    async def brain_that_plays():
        await asyncio.sleep(0.1)
        return ("played answer", False)

    monkeypatch.setattr(p, "_barge_monitor", fake_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    response, barged = await p._run_brain_with_stall_guard(
        brain_that_plays(), interrupt_monitor=True
    )
    assert response == "played answer"
    assert barged is False


@pytest.mark.asyncio
async def test_disabled_does_not_start_monitor(monkeypatch):
    p = _guard_pipeline(enabled=False)
    started = {"called": False}

    async def tracking_monitor(*, grace_s, respect_input_suppression=False):
        started["called"] = True
        return True

    async def quick_brain():
        return ("ok", False)

    monkeypatch.setattr(p, "_barge_monitor", tracking_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    response, barged = await p._run_brain_with_stall_guard(
        quick_brain(), interrupt_monitor=True
    )
    assert response == "ok"
    assert barged is False
    assert started["called"] is False


# --- Mute must silence the thinking-interrupt monitor --------------------- #
# Live bug 2026-07-01 ("Was steht alles in meinen E-Mails drin?"): while the
# brain was mid-think (6 Gmail messages already fetched) the user muted via the
# orb double-click. The thinking-interrupt monitor is a SECOND live mic that
# ignored the mute, "heard" audio, and aborted the fully-worked turn — empty
# answer, empty transcript. The muted wake loop could never capture a
# recombination utterance, so the turn died silently. Mute is an input-only
# contract (see _activation_allowed / _speak): the monitor must honour it too.


@pytest.mark.asyncio
async def test_muted_session_does_not_abort_brain_turn(monkeypatch):
    """A mute that flips mid-think must stand the thinking-interrupt monitor down
    instead of aborting: the turn completes and its answer is returned."""
    p = _guard_pipeline()
    p._muted = True

    async def fake_monitor(*, grace_s, respect_input_suppression=False):
        # The monitor's second mic "hears" audio even though voice is muted.
        return True

    async def working_brain():
        await asyncio.sleep(0.05)
        return ("your inbox summary", False)

    monkeypatch.setattr(p, "_barge_monitor", fake_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    response, barged = await asyncio.wait_for(
        p._run_brain_with_stall_guard(working_brain(), interrupt_monitor=True),
        timeout=5.0,
    )
    assert response == "your inbox summary"
    assert barged is False


@pytest.mark.asyncio
async def test_muted_session_does_not_start_thinking_monitor(monkeypatch):
    """When voice is already muted the thinking-interrupt monitor must not even
    open its second mic — mute means 'stop listening to me'."""
    p = _guard_pipeline()
    p._muted = True
    started = {"called": False}

    async def tracking_monitor(*, grace_s, respect_input_suppression=False):
        started["called"] = True
        return True

    async def quick_brain():
        return ("ok", False)

    monkeypatch.setattr(p, "_barge_monitor", tracking_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    response, barged = await p._run_brain_with_stall_guard(
        quick_brain(), interrupt_monitor=True
    )
    assert response == "ok"
    assert barged is False
    assert started["called"] is False


# --- Behavioral tests driving the REAL _brain_streaming path -------------- #
# The four tests above drive _run_brain_with_stall_guard in isolation and flip
# _brain_first_frame_played by hand. These two pin the handoff against the ACTUAL
# _brain_streaming coroutine, where the flag must be set ONLY when the first audio
# chunk reaches the player — never in the synchronous setup before any token is
# generated (the bug the code review caught: a premature set made the monitor
# stand down on poll #1 and the interrupt never fired on the real path).


class _OneChunkTTS:
    name = "one-chunk-tts"
    supports_streaming = True

    async def synthesize(self, text, voice=None, language_code=None):
        yield AudioChunk(
            pcm=text.encode("utf-8"), sample_rate=24_000, timestamp_ns=0, channels=1
        )


class _DrainPlayer:
    def __init__(self):
        self.consumed: list[str] = []

    async def play_chunks(self, chunks):
        async for chunk in chunks:
            self.consumed.append(chunk.pcm.decode("utf-8"))

    def stop(self):
        pass


async def _grace_aware_barge(
    *, grace_s: float = 1.5, respect_input_suppression: bool = False
) -> bool:
    # The stall-guard thinking monitor uses the short 0.3 s grace; the per-playback
    # barge created INSIDE _brain_streaming uses the 1.5 s default. Fire only the
    # thinking monitor (after a delay long enough for a real first chunk to arrive)
    # so these tests isolate it from the playback barge.
    if grace_s < 1.0:
        await asyncio.sleep(0.2)
        return True
    await asyncio.sleep(3600)
    return False


def _streaming_pipeline(brain) -> SpeechPipeline:
    pipeline = SpeechPipeline(
        tts=_OneChunkTTS(), bus=EventBus(), enable_whisper_wake=False
    )
    pipeline._player = _DrainPlayer()  # type: ignore[assignment]
    pipeline._brain = brain  # type: ignore[assignment]
    pipeline._barge_monitor = _grace_aware_barge  # type: ignore[assignment]
    pipeline._brain_stall_poll_s = 0.01
    return pipeline


@pytest.mark.asyncio
async def test_real_streaming_interrupt_fires_before_first_chunk():
    """Interrupt must fire while the brain is still thinking (no token yet),
    proving _brain_first_frame_played is NOT set in the synchronous setup."""

    class _SlowFirstTokenBrain:
        async def generate_stream(self, text, **kwargs):
            await asyncio.sleep(0.5)  # pure thinking, no token emitted yet
            yield "Spaet."

    pipeline = _streaming_pipeline(_SlowFirstTokenBrain())
    response, barged = await asyncio.wait_for(
        pipeline._run_brain_with_stall_guard(
            pipeline._brain_streaming("egal", "de"), interrupt_monitor=True
        ),
        timeout=5.0,
    )
    assert barged is True
    assert response == ""


@pytest.mark.asyncio
async def test_real_streaming_monitor_stands_down_once_audio_plays():
    """Once the first audio chunk reaches the player the thinking monitor stands
    down — a later 'speech' signal must NOT abort the still-streaming answer."""

    class _FirstTokenThenSlowBrain:
        async def generate_stream(self, text, **kwargs):
            yield "Erstens. "  # first token -> first chunk -> flag flips True
            await asyncio.sleep(0.3)  # still streaming after the first frame
            yield "Zweitens."

    pipeline = _streaming_pipeline(_FirstTokenThenSlowBrain())
    response, barged = await asyncio.wait_for(
        pipeline._run_brain_with_stall_guard(
            pipeline._brain_streaming("egal", "de"), interrupt_monitor=True
        ),
        timeout=5.0,
    )
    assert barged is False
    assert "Erstens" in response and "Zweitens" in response


# --- Wedge guards: the stall guard must NEVER block the voice session ------- #
# Live bug 2026-06-19 (session 11:17): a continuation interrupt fired during a
# turn whose brain stream was running an inline "open X" computer_use step. That
# step only stops via its own cancel token (cancel_active_cu), NOT asyncio task
# cancellation — so the interrupt branch's ``await task`` after ``task.cancel()``
# blocked forever. ``_handle_utterance`` never returned, ``_active_session`` was
# stuck before its ``while not _hangup_event.is_set()`` check, and ~40 X presses
# (request_hangup) had nothing to interrupt — the user had to restart the app.


@pytest.mark.asyncio
async def test_interrupt_does_not_wedge_when_brain_ignores_cancel(monkeypatch):
    """A brain turn blocked on an UNCANCELLABLE inline action must not wedge the
    session: the stall guard abandons it after a bounded grace and returns
    PROMPTLY, so the session unwinds (and a later hangup/X has a live loop to act
    on). Asserted by elapsed time — the broad ``except`` around the old
    ``await task`` swallowed wait_for's own cancellation, so a plain wait_for
    'completes' at the timeout and hides the hang; only timing exposes it."""
    p = _guard_pipeline()
    p._brain_cancel_grace_s = 0.05
    release = asyncio.Event()

    async def fake_monitor(*, grace_s, respect_input_suppression=False):
        return True

    async def stubborn_brain():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # Simulate an inline computer_use step that ignores asyncio
            # cancellation (it only stops via its own token). Without a bounded
            # await this keeps ``await task`` blocked forever.
            await release.wait()
        return ("late", False)

    monkeypatch.setattr(p, "_barge_monitor", fake_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    started = time.monotonic()
    response, barged = await asyncio.wait_for(
        p._run_brain_with_stall_guard(stubborn_brain(), interrupt_monitor=True),
        timeout=5.0,
    )
    elapsed = time.monotonic() - started
    assert response == ""
    assert barged is True
    assert elapsed < 1.0, f"stall guard wedged on an uncancellable brain ({elapsed:.2f}s)"
    # Let the abandoned task drain cleanly so the loop closes without warnings.
    release.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_hangup_during_thinking_aborts_turn(monkeypatch):
    """Pressing the bar's X (request_hangup → _hangup_event) while the brain is
    still THINKING must abort the turn at once — the thinking phase honours the
    hangup kill-switch like the TTS phase does. Before the fix the stall guard
    only watched the brain + interrupt monitor, never ``_hangup_event``, so a
    hangup mid-think had no effect until the brain finished on its own."""
    p = _guard_pipeline()
    p._hangup_event = asyncio.Event()

    async def quiet_monitor(*, grace_s, respect_input_suppression=False):
        await asyncio.sleep(3600)
        return False

    async def slow_brain():
        await asyncio.sleep(3600)
        return ("answer", False)

    monkeypatch.setattr(p, "_barge_monitor", quiet_monitor)
    monkeypatch.setattr(p, "_mark_brain_progress", lambda: None)

    async def _press_x():
        await asyncio.sleep(0.05)
        p._hangup_event.set()

    asyncio.create_task(_press_x())
    response, barged = await asyncio.wait_for(
        p._run_brain_with_stall_guard(slow_brain(), interrupt_monitor=True),
        timeout=5.0,
    )
    assert response == ""
    assert barged is True
