"""Barge-in while a delegated action runs — the "thinking" phase.

The reported bug: speaking while Jarvis was working on something did nothing.
The session deferred the provider's speech edge (``_pending_delegate_needs_
endpoint_protection``), the confirm path never cancelled anything, and a short
utterance was folded into the running order by ``_continues_executing_order``
and answered with a canned progress line. The action kept running and its
result was still delivered.

These tests pin the four behaviours that fix it, and the four that must NOT
change with it.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.realtime.protocol import RealtimeEvent
from tests.unit.realtime.test_session import (
    FakeBrain,
    FakeProvider,
    FakeSession,
    _session,
)

ORDER_TEXT = "Write this to my wiki."


def _scripted_provider(script):
    """A provider whose session yields ``script`` — a list or a coroutine fn."""

    class _ScriptedSession(FakeSession):
        async def receive(self):
            async for event in script():
                yield event

    class _ScriptedProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _ScriptedSession([])
            return self.session

    return _ScriptedProvider([])


def _spoken(jsons):
    """Surface-TTS lines the orchestrator spoke itself."""
    return [
        str(message.get("text") or "")
        for message in jsons
        if message.get("type") == "error_spoken"
    ]


async def _run(provider, brain, jsons):
    sess = _session(provider, brain=brain, jsons=jsons)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    return sess


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop_phrase",
    ["Stop.", "Warte mal.", "Nein.", "Forget it.", "Olvídalo."],  # i18n-allow
)
async def test_a_stop_during_a_running_action_cancels_it(stop_phrase):
    """The action is abandoned — not detached, not merely un-spoken."""
    gate = asyncio.Event()
    dispatched = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatched.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(replies=("Wiki updated.",), gate=gate)

    async def script():
        yield RealtimeEvent(type="input_transcript", text=ORDER_TEXT, is_final=True)
        await dispatched.wait()
        yield RealtimeEvent(type="speech_started")
        yield RealtimeEvent(type="input_transcript", text=stop_phrase, is_final=True)

    jsons = []
    sess = await _run(_scripted_provider(script), brain, jsons)
    gate.set()
    await asyncio.sleep(0.1)

    assert brain.cancelled, "the delegated action kept running after a stop"
    assert not sess._late_delegate_results, (
        "the cancelled action still queued its result as a follow-up"
    )
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_b_the_stop_is_confirmed_out_loud():
    """A silent cancellation is indistinguishable from being ignored."""
    gate = asyncio.Event()
    dispatched = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatched.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(replies=("Wiki updated.",), gate=gate)

    async def script():
        yield RealtimeEvent(type="input_transcript", text=ORDER_TEXT, is_final=True)
        await dispatched.wait()
        yield RealtimeEvent(type="speech_started")
        yield RealtimeEvent(type="input_transcript", text="Stop.", is_final=True)

    jsons = []
    sess = await _run(_scripted_provider(script), brain, jsons)
    gate.set()
    await asyncio.sleep(0.1)

    lines = _spoken(jsons)
    assert lines, "the user got no confirmation that the action stopped"
    # ...and it is a cancellation, never the "still working on it" line the
    # old continuation guard produced for exactly this utterance.
    assert not any(
        "still" in line.lower() or "moment" in line.lower() for line in lines
    ), f"a stop was answered with a progress line: {lines}"
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_c_a_redirect_cancels_the_old_order_and_keeps_the_new_one():
    """"Wait, I meant Rome" must abandon Paris AND book Rome."""
    gate = asyncio.Event()
    dispatched = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatched.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(replies=("Booked.",), gate=gate)

    async def script():
        yield RealtimeEvent(
            type="input_transcript", text="Book me a flight to Paris.", is_final=True
        )
        await dispatched.wait()
        yield RealtimeEvent(type="speech_started")
        yield RealtimeEvent(
            type="input_transcript", text="Wait, I meant Rome.", is_final=True
        )

    jsons = []
    provider = _scripted_provider(script)
    sess = await _run(provider, brain, jsons)
    gate.set()
    await asyncio.sleep(0.1)

    assert brain.cancelled, "the superseded order was not abandoned"
    # A redirect does NOT take the turn with a cancellation line — the
    # replacement order owns it and is routed normally.
    assert not any(
        "stopped" in line.lower() or "cancel" in line.lower()
        for line in _spoken(jsons)
    )
    # ...and the replacement is actually ANSWERED. This is the half of the
    # goal the cancellation alone does not deliver: interrupting has to leave
    # Jarvis holding the new context, not merely silent.
    assert provider.session.response_requests >= 1, (
        "the replacement order was cancelled into silence"
    )
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_d_a_continuation_fragment_still_does_not_cancel():
    """The 2026-08-12 guard must survive: a split sentence is not a stop."""
    gate = asyncio.Event()
    dispatched = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatched.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(replies=("Done.",), gate=gate)

    async def script():
        yield RealtimeEvent(
            type="input_transcript",
            text="Brief the pane on the skill system.",
            is_final=True,
        )
        await dispatched.wait()
        yield RealtimeEvent(type="speech_started")
        yield RealtimeEvent(
            type="input_transcript",
            text="you know, recognize the skills",
            is_final=True,
        )

    jsons = []
    sess = await _run(_scripted_provider(script), brain, jsons)
    gate.set()
    await asyncio.sleep(0.1)

    assert not brain.cancelled, (
        "an ordinary continuation fragment cancelled the running order"
    )
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_e_a_bare_no_answering_a_clarify_question_is_not_a_stop():
    """An open delegate question owns the next short answer.

    Two independent guards keep it that way, and this pins the second one.
    The first is structural: a delegate that asked a question has COMPLETED,
    so nothing is running and the cancellation path is never reached. The
    second covers the overlap — action A still running while action B's
    clarify question is open — where only ``_answers_open_delegate_question``
    can tell the answer from a stop. That latch is recomputed at every turn
    boundary (``session.py`` ~6565) and is false while A is unfinished, so it
    is asserted directly rather than through a latch the flow would clear.
    """
    gate = asyncio.Event()
    dispatched = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatched.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(replies=("Which one?",), gate=gate)

    async def script():
        yield RealtimeEvent(type="input_transcript", text=ORDER_TEXT, is_final=True)
        await dispatched.wait()
        yield RealtimeEvent(type="speech_started")
        yield RealtimeEvent(type="input_transcript", text="No.", is_final=True)

    jsons = []
    sess = _session(_scripted_provider(script), brain=brain, jsons=jsons)
    sess._answers_open_delegate_question = lambda: True
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    gate.set()
    await asyncio.sleep(0.1)

    assert not brain.cancelled, "a clarify ANSWER was treated as a stop"
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_g_a_hesitation_mid_sentence_is_not_a_stop():
    """The microphone outranks the words while the user is still talking.

    "Warte" is also just a word people say while thinking. The provider
    commits on ITS VAD, so a filler pause can surface as a final transcript
    in the middle of one spoken sentence — cancelling there would abandon the
    order the user is still in the middle of giving.
    """
    gate = asyncio.Event()
    dispatched = asyncio.Event()

    class _SignallingBrain(FakeBrain):
        async def generate(self, text, **kwargs):
            dispatched.set()
            return await super().generate(text, **kwargs)

    brain = _SignallingBrain(replies=("Done.",), gate=gate)

    async def script():
        yield RealtimeEvent(type="input_transcript", text=ORDER_TEXT, is_final=True)
        await dispatched.wait()
        yield RealtimeEvent(type="speech_started")
        yield RealtimeEvent(type="input_transcript", text="Warte.", is_final=True)  # i18n-allow

    jsons = []
    sess = _session(_scripted_provider(script), brain=brain, jsons=jsons)
    # The mic still carries the user's voice: mid-utterance, not a barge-in.
    sess._user_is_speaking = lambda: True
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    gate.set()
    await asyncio.sleep(0.1)

    assert not brain.cancelled, (
        "a hesitation inside one sentence cancelled the running order"
    )
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_f_a_stop_with_nothing_running_cancels_nothing():
    """No action in flight — the stop is ordinary speech for the provider."""

    async def script():
        yield RealtimeEvent(type="input_transcript", text="Stop.", is_final=True)

    brain = FakeBrain(replies=("ok",))
    jsons = []
    sess = await _run(_scripted_provider(script), brain, jsons)

    assert not brain.cancelled
    # Nothing was cancelled, so the orchestrator never claimed the turn.
    assert not any("stopped" in line.lower() for line in _spoken(jsons))
    await sess.end(reason="test")
