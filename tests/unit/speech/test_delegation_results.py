"""Delegation completion never occupies the user's conversational floor."""

import asyncio
import json
from dataclasses import replace

import pytest

from jarvis.core.delegation import ResultInbox, result_announcement
from jarvis.speech.pipeline import TurnTakingState
from tests.unit.speech.test_realtime_announcement_bridge import _pipeline


def result(name="Scout", status="done"):
    return result_announcement(
        source="society.lead", request_id=name, name=name,
        request=f"Research {name}", status=status, report=f"Findings from {name}", language="en",
    )


@pytest.fixture(autouse=True)
def quick_batch(monkeypatch):
    monkeypatch.setattr("jarvis.core.delegation.BATCH_WINDOW_S", 0.005)


@pytest.mark.asyncio
async def test_simultaneous_results_make_one_natural_live_request_with_both_tasks():
    pipe, tts, _, live = _pipeline(accepted=True)
    first, second = result(), result("Coder", "blocked")
    await asyncio.gather(pipe._on_announcement(first), pipe._on_announcement(second))
    assert not live.calls  # Producers return without waiting for speech or a model.
    await asyncio.sleep(0.03)
    assert len(live.calls) == 1
    assert "Research Scout" in live.calls[0]["report"]
    assert "Research Coder" in live.calls[0]["report"]
    assert "blocked" in live.calls[0]["report"]
    assert not tts.calls
    pipe._settle_agent_reply(completed=True)
    # A replayed transport callback must not read the same result twice.
    await pipe._on_announcement(replace(first, timestamp_ns=first.timestamp_ns + 1))
    await asyncio.sleep(0.02)
    assert len(live.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    TurnTakingState.IDLE, TurnTakingState.USER_SPEAKING,
    TurnTakingState.PROCESSING, TurnTakingState.JARVIS_SPEAKING,
])
async def test_result_waits_while_user_keeps_talking_or_another_answer_is_running(state):
    pipe, tts, player, live = _pipeline(accepted=True)
    pipe._turn_state = state
    await pipe._on_announcement(result())
    await asyncio.sleep(0.02)
    assert not live.calls and not tts.calls and not player.plays
    assert live.remembered  # Follow-up context is available even before it is spoken.
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.03)
    assert len(live.calls) == 1


@pytest.mark.asyncio
async def test_user_begins_speaking_during_coalescing_window():
    pipe, _, _, live = _pipeline(accepted=True)
    await pipe._on_announcement(result())
    pipe._turn_state = TurnTakingState.USER_SPEAKING
    await asyncio.sleep(0.03)
    assert not live.calls
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.03)
    assert len(live.calls) == 1


@pytest.mark.asyncio
async def test_muted_or_hung_up_result_waits_for_next_call():
    pipe, tts, _, live = _pipeline(accepted=True)
    pipe._hangup_event.set()
    pipe._muted = True
    await pipe._on_announcement(result())
    await asyncio.sleep(0.03)
    assert not live.calls and not tts.calls
    pipe._hangup_event.clear()
    pipe._muted = False
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.03)
    assert len(live.calls) == 1


@pytest.mark.asyncio
async def test_arrivals_during_readback_wait_until_audio_finishes():
    pipe, _, _, live = _pipeline(accepted=True)
    await pipe._on_announcement(result())
    await asyncio.sleep(0.03)
    await pipe._on_announcement(result("Coder"))
    await asyncio.sleep(0.03)
    assert len(live.calls) == 1
    pipe._settle_agent_reply(completed=True)
    await pipe._set_turn_state(TurnTakingState.LISTENING)
    await asyncio.sleep(0.03)
    assert len(live.calls) == 2


def test_batch_has_bounded_fair_context_and_preserves_overflow():
    inbox = ResultInbox()
    for i in range(5):
        inbox.add(replace(result(str(i)), report=f"task-{i} " + "x" * 8000 + f" verdict-{i}"))
    batch = inbox.take()
    assert len(batch.report) < 6000
    for i in range(4):
        assert f"task-{i}" in batch.report and f"verdict-{i}" in batch.report
    assert "task-4" in inbox.take().report
    assert inbox.take() is None


def test_report_is_data_and_stopped_is_not_claimed_success():
    event = result("Coder", "completed")
    assert "is done" not in event.text
    data = json.loads(event.report)
    assert data["request"] == "Research Coder"
    assert data["status"] == "completed"
