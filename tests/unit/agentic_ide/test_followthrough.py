"""Only a fresh answer to the tracked prompt can be read back as its report."""

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import followthrough as ft
from jarvis.core.events import AnnouncementRequested, DelegationResultReady


def _finished(prompt, report, *, at=101):
    return [
        {"kind": "user_message", "ts_ms": at * 1000, "payload": {"text": prompt}},
        {"kind": "assistant_text", "ts_ms": (at + 1) * 1000,
         "payload": {"text": report, "turn_id": "current"}},
        {"kind": "turn_finished", "ts_ms": (at + 2) * 1000,
         "payload": {"status": "done", "turn_id": "current"}},
    ]


@pytest.fixture
def pane(monkeypatch):
    monkeypatch.setattr(ft, "time", SimpleNamespace(time=lambda: 100.0))
    turns = [*_finished("Old task", "Old answer", at=90)]
    term = SimpleNamespace(
        name="T2", process_generation=1, last_submit_at=100.0, last_prompt="Fix the bug",
        submitted=True, delegation_result=None, exit_code=None,
        last_output_at=100.0, reading=lambda: SimpleNamespace(activity="waiting"),
        transcript=SimpleNamespace(tail=lambda n: ["Which repository should I change?"]),
    )
    monkeypatch.setattr(ft, "_events", lambda t: list(turns))
    from jarvis.agentic_ide import task_state

    async def completed(_term):
        return task_state.Evidence("completed")

    monkeypatch.setattr(task_state, "probe", completed)
    return term, turns


async def arm(pane, **origin):
    term, _ = pane
    pending = await ft.prepare(term, "Fix the bug", "Fix the bug", {"lang": "en", **origin})
    ft.submitted(term, pending)
    return term.delegation_result


@pytest.mark.asyncio
async def test_fresh_correlated_answer_reaches_voice_once(pane):
    pending = await arm(pane)
    term, turns = pane
    turns.extend(_finished("Fix the bug", "Fixed login; tests passed."))
    events = []
    assert await ft.publish_result("completed", term, pending, events.append)
    assert not await ft.publish_result("completed", term, pending, events.append)
    assert len(events) == 1 and isinstance(events[0], AnnouncementRequested)
    assert "Fixed login" in events[0].report
    assert "Fix the bug" in events[0].report


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", ["old", "same_prompt", "unrelated"])
async def test_stale_transcript_never_becomes_the_current_answer(pane, stale):
    term, turns = pane
    if stale == "same_prompt":
        turns[:] = _finished("Fix the bug", "Old answer", at=90)
    pending = await arm(pane)
    if stale == "unrelated":
        turns.extend(_finished("Other task", "Unrelated answer"))
    events = []
    assert not await ft.publish_result("completed", term, pending, events.append)
    assert not events
    assert term.delegation_result is pending


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["manual_prompt", "restarted", "replaced"])
async def test_later_prompt_or_process_cannot_claim_previous_result(pane, change):
    pending = await arm(pane)
    term, _ = pane
    if change == "manual_prompt":
        term.last_submit_at += 1
    elif change == "restarted":
        term.process_generation += 1
    else:
        term.delegation_result = None
    events = []
    assert not await ft.publish_result("completed", term, pending, events.append)
    assert not events


@pytest.mark.asyncio
async def test_question_does_not_consume_later_completion(pane, monkeypatch):
    pending = await arm(pane)
    term, turns = pane
    events = []
    from jarvis.agentic_ide import activity

    monkeypatch.setattr(activity, "shows_question", lambda _term: True)
    await ft.publish_result("needs_input", term, pending, events.append)
    assert "Which repository" in events[0].report
    assert term.delegation_result is pending
    turns.extend(_finished("Fix the bug", "Resolved."))
    await ft.publish_result("completed", term, pending, events.append)
    assert len(events) == 2 and "Resolved" in events[1].report


@pytest.mark.asyncio
async def test_text_origin_is_returned_only_to_its_original_chat(pane):
    pending = await arm(pane, reply_session_id="original-chat")
    events = []
    await ft.publish_result("exited", pane[0], pending, events.append)
    assert isinstance(events[0], DelegationResultReady)
    assert events[0].session_id == "original-chat"
    assert events[0].status == "exited"


@pytest.mark.asyncio
async def test_refused_submission_has_no_pending_job(pane):
    pane[0].submitted = False
    assert await arm(pane) is None


@pytest.mark.asyncio
async def test_fast_job_finishing_between_sweeps_still_returns_its_fresh_answer(pane):
    await arm(pane)
    term, turns = pane
    registry = SimpleNamespace(sessions=[SimpleNamespace(terminals=[term])])
    events = []
    await ft.poll_ready(registry, events.append, now=130.0)
    assert not events  # An idle terminal and an old answer are not completion evidence.
    turns.extend(_finished("Fix the bug", "Fixed immediately."))
    await ft.poll_ready(registry, events.append, now=141.0)
    assert len(events) == 1 and "Fixed immediately" in events[0].report


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["working", "asking", "unknown", "stopped"])
async def test_silence_and_interim_text_do_not_announce_completion(pane, monkeypatch, state):
    from jarvis.agentic_ide import task_state

    pending = await arm(pane)
    term, turns = pane
    turns.extend(_finished("Fix the bug", "Checking the tests now."))

    async def unfinished(_term):
        return task_state.Evidence(state)

    monkeypatch.setattr(task_state, "probe", unfinished)
    events = []
    assert not await ft.publish_result("completed", term, pending, events.append)
    assert not events
    assert term.delegation_result is pending


@pytest.mark.asyncio
async def test_auto_resume_during_report_read_does_not_publish_stale_completion(pane, monkeypatch):
    from jarvis.agentic_ide import task_state

    pending = await arm(pane)
    calls = []

    async def resuming(_term):
        calls.append(None)
        return task_state.Evidence("completed" if len(calls) == 1 else "working")

    monkeypatch.setattr(task_state, "probe", resuming)
    events = []
    assert not await ft.publish_result("completed", pane[0], pending, events.append)
    assert not events


@pytest.mark.asyncio
async def test_confirmed_stop_has_distinct_wording_and_no_completion_claim(pane, monkeypatch):
    from jarvis.agentic_ide import task_state

    pending = await arm(pane)

    async def stopped(_term):
        return task_state.Evidence("stopped")

    monkeypatch.setattr(task_state, "probe", stopped)
    events = []
    assert await ft.publish_result("stopped", pane[0], pending, events.append)
    assert "was interrupted" in events[0].text
    assert "no completion" in events[0].report
    assert pane[0].delegation_result is None


@pytest.mark.asyncio
async def test_submission_change_during_final_probe_cannot_consume_result(pane, monkeypatch):
    from jarvis.agentic_ide import task_state

    pending = await arm(pane)
    term, turns = pane
    turns.extend(_finished("Fix the bug", "Fixed."))
    calls = []

    async def changing(_term):
        calls.append(None)
        if len(calls) == 2:
            term.last_submit_at += 1
        return task_state.Evidence("completed")

    monkeypatch.setattr(task_state, "probe", changing)
    events = []
    assert not await ft.publish_result("completed", term, pending, events.append)
    assert not events


@pytest.mark.asyncio
async def test_late_old_record_for_identical_prompt_is_not_fresh_work(pane):
    pending = await arm(pane)
    term, turns = pane
    turns.extend(_finished("Fix the bug", "An older result.", at=90))
    events = []
    assert not await ft.publish_result("completed", term, pending, events.append)
    assert not events
    assert term.delegation_result is pending
