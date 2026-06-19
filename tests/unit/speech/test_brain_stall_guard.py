"""Regression tests for the brain *no-progress* (stall) timeout.

Live bug 2026-06-01: a voice vision question ("Was ist das hier?") triggers a
Gemini tool-use loop (large image upload + context-cache build + function_call
+ tool execution). The whole turn legitimately exceeds the old 25 s TOTAL
wall-clock cap, so ``asyncio.wait_for`` cancelled the in-flight turn mid-work
and spoke "That took too long, say it again" — Jarvis looked lazy while it was
actually still working.

Root cause: a single wall-clock cap cannot tell a genuinely STALLED provider
(no progress, ever) apart from a slow-but-working one (steady tool/token
progress). The fix replaces the total cap with a deadline that *resets on every
progress signal* (``_mark_brain_progress``), with an absolute ceiling as the
pathological-drip-feed backstop.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from jarvis.speech.pipeline import SpeechPipeline


def _make_pipeline(*, stall: float, ceiling: float, poll: float) -> SpeechPipeline:
    """A bare pipeline with only the stall-guard attributes wired.

    Mirrors the ctor-bypass pattern used across tests/unit/speech (the full
    SpeechPipeline ctor needs audio devices we don't have in unit scope).
    """
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._brain_timeout_s = stall
    p._brain_hard_timeout_s = ceiling
    p._brain_stall_poll_s = poll
    p._brain_last_progress = time.monotonic()
    return p


@pytest.mark.asyncio
async def test_stall_guard_times_out_when_brain_makes_no_progress() -> None:
    """A genuinely stalled brain (never calls progress) raises TimeoutError —
    the original liveness guard against a hung provider is preserved."""
    p = _make_pipeline(stall=0.3, ceiling=5.0, poll=0.05)

    async def never_progresses() -> tuple[str, bool]:
        await asyncio.sleep(10.0)
        return ("unreachable", False)

    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(never_progresses())


@pytest.mark.asyncio
async def test_stall_guard_completes_a_slow_but_working_turn() -> None:
    """THE FIX: a turn whose total runtime far exceeds the stall window — but
    whose individual no-progress gaps stay *under* it — runs to completion and
    delivers its result, instead of being guillotined like the old total cap."""
    p = _make_pipeline(stall=0.3, ceiling=10.0, poll=0.05)

    async def slow_but_working() -> tuple[str, bool]:
        # 6 x 0.15 s = 0.9 s total, well past the 0.3 s stall window, but every
        # gap (0.15 s) is below it — exactly the vision+tool-loop profile.
        for _ in range(6):
            await asyncio.sleep(0.15)
            p._mark_brain_progress()
        return ("Das ist dein Editor.", False)

    result = await p._run_brain_with_stall_guard(slow_but_working())

    assert result == ("Das ist dein Editor.", False)


@pytest.mark.asyncio
async def test_stall_guard_enforces_the_absolute_ceiling() -> None:
    """Even a brain that keeps pinging progress forever (pathological drip
    feed) is bounded by the hard ceiling so the session can never wedge."""
    p = _make_pipeline(stall=5.0, ceiling=0.4, poll=0.05)

    async def progresses_forever() -> tuple[str, bool]:
        while True:
            await asyncio.sleep(0.05)
            p._mark_brain_progress()

    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(progresses_forever())


@pytest.mark.asyncio
async def test_stall_guard_does_not_cut_off_active_computer_use() -> None:
    """THE FIX (2026-06-07): a ``computer_use`` loop runs as ONE opaque tool
    call inside the brain turn and legitimately exceeds BOTH the no-progress
    stall window AND the absolute ceiling (a 10-step OBS automation took 30 s+
    live). It reports progress only via ObservationCaptured/ActionPlanned bus
    events, NOT text chunks. While those events keep arriving, the ceiling is
    suspended so the desktop task runs to completion instead of being
    guillotined mid-work and spoken 'Das hat zu lange gedauert'."""
    # ceiling 0.4 s is SHORTER than the 1.0 s turn — the old absolute-ceiling
    # code raised TimeoutError here even though the loop kept stepping.
    p = _make_pipeline(stall=0.3, ceiling=0.4, poll=0.05)
    p._long_tool_last_activity = 0.0

    async def computer_use_working() -> tuple[str, bool]:
        # A CU step event arrives every 0.1 s (live: every ~2-3 s, far inside
        # the 30 s window). Each marks brain progress AND long-tool activity,
        # exactly as the _on_agent_progress bus handler does.
        for _ in range(10):
            await asyncio.sleep(0.1)
            p._mark_brain_progress()
            p._long_tool_last_activity = time.monotonic()
        return ("OBS Studio ist offen.", False)

    result = await p._run_brain_with_stall_guard(computer_use_working())

    assert result == ("OBS Studio ist offen.", False)


@pytest.mark.asyncio
async def test_stall_guard_still_aborts_a_wedged_computer_use() -> None:
    """Boundary: suspending the ceiling for an ACTIVE desktop loop must not
    also disable the no-progress liveness guard. A computer_use loop that
    genuinely wedges (a COM hang — events STOP arriving) must still abort after
    the stall window, so the voice session can never freeze."""
    p = _make_pipeline(stall=0.3, ceiling=10.0, poll=0.05)
    p._long_tool_last_activity = 0.0

    async def computer_use_wedges() -> tuple[str, bool]:
        for _ in range(3):  # a few healthy steps...
            await asyncio.sleep(0.1)
            p._mark_brain_progress()
            p._long_tool_last_activity = time.monotonic()
        await asyncio.sleep(10.0)  # ...then wedged: no more progress events
        return ("unreachable", False)

    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(computer_use_wedges())


@pytest.mark.asyncio
async def test_agent_progress_handler_resets_both_deadlines() -> None:
    """The computer_use/vision bus-event handler resets the no-progress
    deadline (``_brain_last_progress``) AND records long-tool activity
    (``_long_tool_last_activity``), so a stepping desktop loop holds off BOTH
    the stall and the ceiling. Bus handlers must be ``async`` (a sync handler
    is silently dropped by the bus, live lesson 2026-06-02)."""
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._brain_last_progress = 0.0
    p._long_tool_last_activity = 0.0

    before = time.monotonic()
    await p._on_agent_progress(object())

    assert p._brain_last_progress >= before
    assert p._long_tool_last_activity >= before


@pytest.mark.asyncio
async def test_stall_guard_propagates_a_brain_error() -> None:
    """A brain coroutine that raises surfaces its exception unchanged — the
    caller's ``except Exception`` branch must keep handling provider failures."""
    p = _make_pipeline(stall=1.0, ceiling=5.0, poll=0.05)

    async def boom() -> tuple[str, bool]:
        raise RuntimeError("provider exploded")

    with pytest.raises(RuntimeError, match="provider exploded"):
        await p._run_brain_with_stall_guard(boom())


@pytest.mark.asyncio
async def test_stall_guard_cancels_the_brain_task_on_timeout() -> None:
    """On a stall the in-flight brain coroutine must actually be cancelled —
    no orphaned task keeps running (and possibly speaking) after we gave up."""
    p = _make_pipeline(stall=0.2, ceiling=5.0, poll=0.05)
    cancelled = asyncio.Event()

    async def runs_until_cancelled() -> tuple[str, bool]:
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ("unreachable", False)

    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(runs_until_cancelled())

    assert cancelled.is_set()


class _ActivePlayer:
    """A player that is continuously writing audio sub-blocks.

    Mirrors a live TTS playback: ``AudioPlayer._write_samples`` bumps
    ``last_write_ns = time.monotonic_ns()`` after every ~60 ms sub-block, so a
    read always reports "just wrote". Stands in for the real player on the bare
    ``__new__`` pipeline.

    Modelled as a read-only ``@property`` deliberately: the stall guard only
    ever READS ``last_write_ns``. The real ``AudioPlayer.last_write_ns`` is a
    plain int attribute that ``play_chunks`` also writes (resets to 0); this
    stub never needs a setter because the guard does not write it. See
    ``_IdlePlayer`` for the plain-attribute, frozen-value variant.
    """

    @property
    def last_write_ns(self) -> int:
        return time.monotonic_ns()


class _IdlePlayer:
    """A player whose last audio sub-block was written 'just now' and then stops.

    Plain int attribute (exactly like the real ``AudioPlayer.last_write_ns``),
    frozen at construction: it reports a fresh timestamp at guard start but
    never advances, modelling 'playback just ended, no more frames'. After the
    stall window elapses the value goes stale and the guards must re-engage.
    """

    def __init__(self) -> None:
        self.last_write_ns = time.monotonic_ns()


class _WedgedPlayer:
    """A player whose output device wedged: ``last_write_ns`` is frozen in the
    past and never advances (no audio sub-block has been written for a while)."""

    def __init__(self, age_s: float) -> None:
        self._frozen = time.monotonic_ns() - int(age_s * 1_000_000_000)

    @property
    def last_write_ns(self) -> int:
        return self._frozen


@pytest.mark.asyncio
async def test_stall_guard_does_not_cut_off_active_tts_playback() -> None:
    """THE FIX (2026-06-19): after the brain's LAST token, ``_brain_streaming``
    stays inside ``_await_playback`` reading a long answer aloud, and nothing
    bumps ``_brain_last_progress`` during that tail. Keying the stall purely on
    brain-token progress guillotined the still-playing tail of a long answer
    mid-sentence (live 'Wegzugsteuer' 16:27: last token ~37 s, playback ran to
    ~64 s, abort exactly 30 s after the last token). While the player keeps
    writing audio (``last_write_ns`` advances within the stall window) the turn
    is working, not wedged — the no-progress stall must NOT fire."""
    p = _make_pipeline(stall=0.3, ceiling=10.0, poll=0.05)
    p._player = _ActivePlayer()

    async def long_answer_read_aloud() -> tuple[str, bool]:
        await asyncio.sleep(0.1)
        p._mark_brain_progress()  # the brain's last token arrives early...
        # ...then _brain_streaming sits in _await_playback reading the long
        # answer aloud well past the stall window, with NO further brain
        # progress. The player keeps writing the whole time.
        await asyncio.sleep(0.8)
        return ("Die lange Wegzugsteuer-Antwort.", False)

    result = await p._run_brain_with_stall_guard(long_answer_read_aloud())

    assert result == ("Die lange Wegzugsteuer-Antwort.", False)


@pytest.mark.asyncio
async def test_active_tts_playback_also_suspends_the_absolute_ceiling() -> None:
    """A very long answer's playback can exceed even the absolute ceiling — in
    the live Wegzugsteuer turn the no-progress stall and the 90 s ceiling came
    due almost simultaneously. Active playback must suspend BOTH guards (exactly
    as an active computer_use loop suspends the ceiling), otherwise fixing only
    the no-progress stall would let the ceiling behead the tail one second
    later."""
    p = _make_pipeline(stall=5.0, ceiling=0.4, poll=0.05)
    p._player = _ActivePlayer()

    async def long_answer_read_aloud() -> tuple[str, bool]:
        # 0.8 s total > the 0.4 s ceiling, but the player is writing throughout.
        await asyncio.sleep(0.8)
        return ("Eine sehr lange Antwort.", False)

    result = await p._run_brain_with_stall_guard(long_answer_read_aloud())

    assert result == ("Eine sehr lange Antwort.", False)


@pytest.mark.asyncio
async def test_stall_guard_still_aborts_when_playback_is_wedged() -> None:
    """Boundary: suspending the stall for ACTIVE playback must not disable the
    liveness guard. A wedged output device (``last_write_ns`` frozen) with no
    brain progress must still abort after the stall window, so the voice session
    can never freeze. The dedicated device-wedge watchdog in ``_await_playback``
    owns the fast path; this is defense in depth."""
    p = _make_pipeline(stall=0.3, ceiling=10.0, poll=0.05)
    # Frozen twice the stall window in the past → never counts as active.
    p._player = _WedgedPlayer(age_s=0.6)

    async def wedged_after_one_token() -> tuple[str, bool]:
        await asyncio.sleep(0.1)
        p._mark_brain_progress()  # last brain token...
        await asyncio.sleep(10.0)  # ...then the device wedges: no writes
        return ("unreachable", False)

    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(wedged_after_one_token())


@pytest.mark.asyncio
async def test_ceiling_re_engages_after_playback_ends() -> None:
    """Symmetric to the suspension tests: playback suspending the ceiling is not
    a permanent disable. Once playback ENDS (last_write_ns goes stale past the
    stall window) the absolute ceiling must re-engage and abort a turn that then
    refuses to return — otherwise an idle-after-playback hang could never die.

    The brain keeps pinging progress so the no-progress ``stalled`` check never
    fires; the ONLY guard that can stop this turn is the ceiling."""
    p = _make_pipeline(stall=0.2, ceiling=0.5, poll=0.02)
    p._player = _IdlePlayer()  # fresh at guard start, then frozen (playback ended)

    async def progresses_but_never_returns() -> tuple[str, bool]:
        # last_write_ns goes stale after stall=0.2 s; the ceiling must then fire
        # at 0.5 s despite continuous brain progress.
        for _ in range(100):
            await asyncio.sleep(0.02)
            p._mark_brain_progress()
        return ("unreachable", False)

    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(progresses_but_never_returns())
