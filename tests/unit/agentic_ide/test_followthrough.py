"""Only a fresh answer to the tracked prompt can be read back as its report."""

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import followthrough as ft
from jarvis.agentic_ide.agent_transcript import Turn
from jarvis.core.events import AnnouncementRequested, DelegationResultReady


@pytest.fixture
def pane(monkeypatch):
    turns = [Turn("user", "Old task"), Turn("assistant", "Old answer")]
    term = SimpleNamespace(
        name="T2", process_generation=1, last_submit_at=100.0, last_prompt="Fix the bug",
        submitted=True, delegation_result=None, exit_code=None,
        last_output_at=100.0, reading=lambda: SimpleNamespace(activity="waiting"),
        transcript=SimpleNamespace(tail=lambda n: ["Which repository should I change?"]),
    )
    monkeypatch.setattr(ft, "_turns", lambda t: list(turns))
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
    turns.extend([Turn("user", "Fix the bug"), Turn("assistant", "Fixed login; tests passed.")])
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
        turns[:] = [Turn("user", "Fix the bug"), Turn("assistant", "Old answer")]
    pending = await arm(pane)
    if stale == "unrelated":
        turns.extend([Turn("user", "Other task"), Turn("assistant", "Unrelated answer")])
    events = []
    await ft.publish_result("completed", term, pending, events.append)
    assert "Old answer" not in events[0].report and "Unrelated answer" not in events[0].report
    assert "unverified" in events[0].report


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
async def test_question_does_not_consume_later_completion(pane):
    pending = await arm(pane)
    term, turns = pane
    events = []
    await ft.publish_result("needs_input", term, pending, events.append)
    assert "Which repository" in events[0].report
    assert term.delegation_result is pending
    turns.extend([Turn("user", "Fix the bug"), Turn("assistant", "Resolved.")])
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
    turns.extend([Turn("user", "Fix the bug"), Turn("assistant", "Fixed immediately.")])
    await ft.poll_ready(registry, events.append, now=141.0)
    assert len(events) == 1 and "Fixed immediately" in events[0].report
