"""ADR-0034 in the realtime engine: the user keeps talking while the tool model works.

Four guarantees, each pinned by one test shape:

* a parked (late) result never expires — it is spoken at the next rest however
  long the conversation went on;
* rest is judged per delivery, not per session — a background order that is
  still computing does not hold a ready result hostage;
* a provider function call still open on the wire is answered with the interim
  payload the moment the user opens a new turn (blocking transports can then
  answer that turn), and cancelled orders close their calls too;
* "how far are you?" / "what came out of it?" are owned by the orchestrator:
  a grounded progress line, or the parked result itself.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import (
    _DELEGATE_BRIDGE_TEXTS,
    _PENDING_TOOL_CALL_INTERIM_RESULT,
    _delegate_result_prompt,
    _DelegateTurnState,
)
from jarvis.voice.instant_ack import ToolActivity, progress_pool
from jarvis.voice.parked_results import ParkedResult
from tests.unit.realtime.test_session import (
    FakeBrain,
    FakeProvider,
    FakeSession,
    _session,
)


def _surface_lines(jsons):
    return [m["text"] for m in jsons if m.get("type") == "error_spoken"]


def _all_status_lines() -> set[str]:
    pool = {text for texts in _DELEGATE_BRIDGE_TEXTS.values() for text in texts}
    for activity in ToolActivity:
        for language in ("de", "en", "es"):
            pool.update(progress_pool(activity, language))
    return pool


# --- no expiry -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_parked_result_is_spoken_however_long_it_waited():
    """A result parked an hour ago is still owed; the flush waits for rest, not a clock."""
    provider = FakeProvider([])
    sess = _session(provider, brain=FakeBrain())
    fake = FakeSession([])
    sess._session = fake
    sess._late_delegate_results.append(
        ParkedResult(
            text="Berlin: 21 degrees and sunny.",
            language="en",
            success=True,
            delivery_id="late-1",
            request_text="what's the weather in Berlin",
            queued_at=time.monotonic() - 3600.0,
        )
    )
    # Not at rest yet: the user is talking.
    sess._user_speech_active = True
    flush = asyncio.create_task(sess._flush_late_delegate_results())
    await asyncio.sleep(0.4)
    assert fake.text_inputs == []
    assert len(sess._late_delegate_results) == 1
    # The floor clears → the parked result is injected, tied to its request.
    sess._user_speech_active = False
    await asyncio.wait_for(flush, timeout=3.0)
    assert sess._late_delegate_results == []
    assert len(fake.text_inputs) == 1
    injected = fake.text_inputs[0]
    assert "<trusted_action_result>\nBerlin: 21 degrees and sunny.\n" in injected
    assert "what's the weather in Berlin" in injected
    await sess.end(reason="test")


def test_late_result_prompt_ties_back_to_the_request_text():
    prompt = _delegate_result_prompt(
        "Done.",
        language="en",
        success=True,
        late=True,
        request_text="send the travel plan to Anna",
    )
    assert "earlier request" in prompt
    assert '"send the travel plan to Anna"' in prompt
    plain = _delegate_result_prompt("Done.", language="en", success=True)
    assert "earlier request" not in plain


# --- rest is per delivery --------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_ignores_a_running_order_but_not_a_delivery_in_flight():
    provider = FakeProvider([])
    sess = _session(provider, brain=FakeBrain())
    sess._session = FakeSession([])
    assert sess._session_is_at_rest()

    gate = asyncio.Event()

    async def _never_done():
        await gate.wait()

    task = asyncio.create_task(_never_done())
    try:
        state = _DelegateTurnState(user_text="write the plan to my wiki")
        sess._delegate_turns["turn-a"] = state
        sess._track_delegate_task("turn-a", task)
        # Still computing, nothing handed to the provider: a follow-up may go.
        assert sess._session_is_at_rest()
        # A trusted reply is with the provider and its readback has not landed:
        # a follow-up now would race it (BUG-143 class) — hold.
        state.delivery_started = True
        assert not sess._session_is_at_rest()
        state.delivery_completed = True
        assert sess._session_is_at_rest()
    finally:
        gate.set()
        await task
    await sess.end(reason="test")


# --- the wire is freed for the new turn -----------------------------------------


@pytest.mark.asyncio
async def test_new_turn_answers_the_pending_provider_call_with_the_interim_payload():
    """The user talks into a slow provider-requested action: the open call is closed."""
    gate = asyncio.Event()
    brain = FakeBrain(replies=("The travel plan is in your wiki.",), gate=gate)
    jsons = []
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Write the travel plan to my wiki.",
                is_final=True,
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="call-1",
                tool_name="jarvis_action",
                tool_args={"request": "Write the travel plan to my wiki."},
            ),
            RealtimeEvent(type="interrupted"),
            RealtimeEvent(
                type="input_transcript",
                text="By the way, what is the capital of Portugal?",
                is_final=True,
            ),
        ]
    )
    sess = _session(provider, brain=brain, jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.05)

    interim = [r for r in provider.session.tool_results if r[0] == "call-1"]
    assert len(interim) == 1
    assert interim[0][2]["status"] == "in_progress"
    assert interim[0][2] == _PENDING_TOOL_CALL_INTERIM_RESULT
    # The new turn itself was NOT dispatched to the tool model and NOT answered
    # with a canned line: it stays native.
    assert all("Portugal" not in call[0] for call in brain.calls)
    assert _surface_lines(jsons) == []
    state = next(iter(sess._delegate_states_by_turn.values()))
    assert state.interim_tool_reply_sent
    assert state.pending_tool_calls == []

    # The order finishes later: its result is parked, never sent as a late tool
    # result against the answered call id.
    gate.set()
    await asyncio.sleep(0.1)
    assert [r for r in provider.session.tool_results if r[0] == "call-1"] == interim
    parked = sess._late_delegate_results
    injected = "".join(provider.session.text_inputs)
    assert (
        parked and parked[0].request_text == "Write the travel plan to my wiki."
    ) or "The travel plan is in your wiki." in injected
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_new_turn_leaves_the_call_open_when_the_switch_is_off():
    gate = asyncio.Event()
    brain = FakeBrain(gate=gate)
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript", text="Write the travel plan to my wiki.", is_final=True
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="call-1",
                tool_name="jarvis_action",
                tool_args={"request": "Write the travel plan to my wiki."},
            ),
            RealtimeEvent(type="interrupted"),
            RealtimeEvent(
                type="input_transcript",
                text="By the way, what is the capital of Portugal?",
                is_final=True,
            ),
        ]
    )
    sess = _session(provider, brain=brain)
    sess._config.voice.realtime_unblock_pending_tool_calls = False

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.05)

    assert provider.session.tool_results == []
    gate.set()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_stop_word_closes_the_pending_provider_call_on_the_wire():
    gate = asyncio.Event()
    brain = FakeBrain(gate=gate)
    jsons = []
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript", text="Write the travel plan to my wiki.", is_final=True
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="call-1",
                tool_name="jarvis_action",
                tool_args={"request": "Write the travel plan to my wiki."},
            ),
            RealtimeEvent(type="interrupted"),
            RealtimeEvent(type="input_transcript", text="Stop.", is_final=True),
        ]
    )
    sess = _session(provider, brain=brain, jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.05)

    answers = [r for r in provider.session.tool_results if r[0] == "call-1"]
    assert len(answers) == 1
    assert answers[0][2]["success"] is False
    assert "Cancelled" in answers[0][2]["error"]
    assert brain.cancelled
    await sess.end(reason="test")


# --- what the user says into the wait -----------------------------------------


@pytest.mark.asyncio
async def test_progress_question_gets_one_grounded_line_from_the_running_tool():
    gate = asyncio.Event()  # the order outlives the test
    brain = FakeBrain(gate=gate)
    jsons = []
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript", text="Write the travel plan to my wiki.", is_final=True
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="call-1",
                tool_name="jarvis_action",
                tool_args={"request": "Write the travel plan to my wiki."},
            ),
            RealtimeEvent(type="interrupted"),
            RealtimeEvent(type="input_transcript", text="How far are you?", is_final=True),
        ]
    )
    sess = _session(provider, brain=brain, jsons=jsons)
    sess._running_tool_name = "web_search"

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.05)

    spoken = _surface_lines(jsons)
    assert len(spoken) == 1
    assert spoken[0] in progress_pool(ToolActivity.SEARCH, "en")
    # The probe never became a brain turn.
    assert all("far" not in call[0] for call in brain.calls)
    gate.set()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_progress_question_falls_back_to_the_status_pool_without_a_known_tool():
    gate = asyncio.Event()
    brain = FakeBrain(gate=gate)
    jsons = []
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript", text="Write the travel plan to my wiki.", is_final=True
            ),
            RealtimeEvent(
                type="tool_call",
                call_id="call-1",
                tool_name="jarvis_action",
                tool_args={"request": "Write the travel plan to my wiki."},
            ),
            RealtimeEvent(type="interrupted"),
            RealtimeEvent(type="input_transcript", text="Are you done yet?", is_final=True),
        ]
    )
    sess = _session(provider, brain=brain, jsons=jsons)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.05)

    spoken = _surface_lines(jsons)
    assert len(spoken) == 1
    assert spoken[0] in _all_status_lines()
    gate.set()
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_result_request_delivers_the_parked_result_as_this_turns_answer():
    """No order running, one result parked: "what did you find" gets it now."""
    jsons = []
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="What did you find?", is_final=True),
        ]
    )
    sess = _session(provider, brain=FakeBrain(), jsons=jsons)
    sess._late_delegate_results.append(
        ParkedResult(
            text="Rome: 21 degrees, clear sky.",
            language="en",
            success=True,
            delivery_id="late-rome",
            request_text="look up the weather in Rome",
            queued_at=time.monotonic() - 90.0,
        )
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.05)

    injected = "".join(provider.session.text_inputs)
    assert "<trusted_action_result>\nRome: 21 degrees, clear sky.\n" in injected
    assert "look up the weather in Rome" in injected
    assert sess._late_delegate_results == []
    # No canned "still working" line in front of a ready result.
    assert _surface_lines(jsons) == []
    await sess.end(reason="test")


@pytest.mark.asyncio
async def test_unrelated_new_turn_stays_native_while_a_result_is_parked():
    jsons = []
    provider = FakeProvider(
        [
            RealtimeEvent(
                type="input_transcript",
                text="Tell me a fun fact about octopuses.",
                is_final=True,
            ),
        ]
    )
    sess = _session(provider, brain=FakeBrain(), jsons=jsons)
    sess._late_delegate_results.append(
        ParkedResult(
            text="Rome: 21 degrees.",
            language="en",
            success=True,
            delivery_id="late-rome",
            request_text="look up the weather in Rome",
        )
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await asyncio.sleep(0.05)

    # The turn belongs to the provider: one ordinary response request, no
    # surface line, and the parked result was not forced into this turn.
    assert provider.session.response_requests == 1
    assert _surface_lines(jsons) == []
    assert not any("<trusted_action_result>" in t for t in provider.session.text_inputs)
    await sess.end(reason="test")
