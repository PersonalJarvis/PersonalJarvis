"""Only a fresh answer to the tracked prompt can be read back as its report."""

import json
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import agent_transcript
from jarvis.agentic_ide import followthrough as ft
from jarvis.agentic_ide.agent_transcript import Turn
from jarvis.core.events import AnnouncementRequested, DelegationResultReady


@pytest.fixture
def pane(monkeypatch):
    turns = [Turn("user", "Old task"), Turn("assistant", "Old answer")]
    term = SimpleNamespace(
        name="T2", agent="claude", computer_id="", process_generation=1,
        last_submit_at=100.0, last_prompt="Fix the bug",
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
    # Held first: the answer may still be on its way into the transcript.
    assert not await ft.publish_result("completed", term, pending, events.append, now=500.0)
    assert not events
    await ft.publish_result(
        "completed", term, pending, events.append, now=500.0 + ft.REPORT_GRACE_S,
    )
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


def _recorded(*records: str) -> str:
    """The newest user turn exactly as the transcript reader builds it."""
    turns: list = []
    for raw in records:
        agent_transcript._append(turns, "user", raw)
    return turns[-1].text


def test_codex_first_prompt_behind_its_preamble_is_the_prompt():
    # A real Codex rollout opens with these user records before the first prompt.
    recorded = _recorded(
        "# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>\nBe brief.\n</INSTRUCTIONS>",
        "<environment_context>\n  <cwd>/repo</cwd>\n</environment_context>",
        "Fix the bug",
    )
    assert recorded != "Fix the bug"
    assert ft.answers_prompt(recorded, "Fix the bug")


def test_tag_shaped_code_in_a_prompt_still_matches():
    prompt = "Wrap the <div> in a <section> and make List<String> immutable."
    assert ft.answers_prompt(_recorded(prompt), prompt)


def test_long_prompt_matches_by_the_tail_the_reader_kept():
    prompt = "\n".join(f"Step {i}: change module_{i}.py and run its tests." for i in range(300))
    recorded = _recorded(prompt)
    assert len(recorded) < len(prompt)
    assert ft.answers_prompt(recorded, prompt)
    assert not ft.answers_prompt(recorded, prompt + "\nStep 300: also touch the docs.")


@pytest.mark.parametrize(
    ("recorded", "prompt"),
    [("Fix the bugs", "Fix the bug"), ("Prefix the bug", "fix the bug"), ("", "Fix the bug")],
)
def test_a_different_prompt_is_not_the_tracked_one(recorded, prompt):
    assert not ft.answers_prompt(recorded, prompt)


def test_codex_rollout_read_through_the_real_reader_matches(tmp_path):
    session_id = "0199aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
    folder = tmp_path / "sessions" / "2026" / "10" / "02"
    folder.mkdir(parents=True)

    def message(role: str, text: str) -> str:
        payload = {"type": "message", "role": role, "content": [
            {"type": "input_text" if role == "user" else "output_text", "text": text},
        ]}
        return json.dumps({"type": "response_item", "payload": payload})

    rows = [
        message("user", "# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>\nx\n</INSTRUCTIONS>"),
        message("user", "<environment_context>\n  <cwd>/repo</cwd>\n</environment_context>"),
        message("user", "Rename <Button /> to <Action />"),
        message("assistant", "Renamed it in 3 files."),
    ]
    (folder / f"rollout-2026-10-02T09-00-00-{session_id}.jsonl").write_text(
        "\n".join(rows) + "\n", encoding="utf-8",
    )
    turns = agent_transcript.read("codex", session_id, home=tmp_path)
    assert ft.answers_prompt(turns[0].text, "Rename <Button /> to <Action />")


@pytest.mark.asyncio
async def test_first_codex_prompt_reports_its_real_answer(pane):
    pending = await arm(pane)
    term, turns = pane
    turns.extend([
        Turn("user", "# AGENTS.md instructions for /repo\n\nFix the bug"),
        Turn("assistant", "Fixed the null check."),
    ])
    events = []
    assert await ft.publish_result("completed", term, pending, events.append, now=200.0)
    assert "Fixed the null check" in events[0].report
    assert "fresh assistant message" in events[0].report


@pytest.mark.asyncio
async def test_stop_waits_for_an_answer_that_is_not_recorded_yet(pane):
    pending = await arm(pane)
    term, turns = pane
    events = []
    # The pane went quiet before its transcript was found or flushed.
    assert not await ft.publish_result("completed", term, pending, events.append, now=200.0)
    assert not await ft.publish_result("completed", term, pending, events.append, now=210.0)
    assert not events and term.delegation_result is pending
    turns.extend([Turn("user", "Fix the bug"), Turn("assistant", "Fixed; 12 tests pass.")])
    assert await ft.publish_result("completed", term, pending, events.append, now=212.0)
    assert len(events) == 1 and "12 tests pass" in events[0].report
    assert term.delegation_result is None


@pytest.mark.parametrize("where", ["remote", "unreadable_cli"])
@pytest.mark.asyncio
async def test_stop_without_any_transcript_is_reported_at_once(pane, where):
    pending = await arm(pane)
    term, _ = pane
    if where == "remote":
        term.computer_id = "box-1"
    else:
        term.agent = "aider"
    events = []
    assert await ft.publish_result("completed", term, pending, events.append, now=200.0)
    assert "unverified" in events[0].report


@pytest.mark.asyncio
async def test_a_new_submission_restarts_the_report_grace(pane):
    pending = await arm(pane)
    term, _ = pane
    await ft.publish_result("completed", term, pending, lambda _e: None, now=200.0)
    assert term.delegation_stopped_at == 200.0
    ft.submitted(term, pending)
    assert term.delegation_stopped_at == 0.0


@pytest.mark.asyncio
async def test_a_refused_later_delivery_keeps_the_earlier_job(pane):
    pending = await arm(pane)
    term, _ = pane
    later = await ft.prepare(term, "Also update the docs", "", {"lang": "en"})
    term.submitted = False  # The text sat in the input box; nothing was handed over.
    ft.submitted(term, later)
    assert term.delegation_result is pending and ft.current(term, pending)


@pytest.mark.asyncio
async def test_a_dialog_answer_without_a_followed_job_tracks_the_task_on_record(pane):
    term, turns = pane
    turns.append(Turn("user", "Refactor the parser"))
    answer = await ft.prepare(term, "1", "1", {"lang": "en"}, answering=True)
    term.last_prompt = "1"
    ft.submitted(term, answer)
    tracked = term.delegation_result
    assert tracked.prompt == "Refactor the parser" and tracked.keeps_prompt
    turns.append(Turn("assistant", "Parser refactored."))
    events = []
    assert await ft.publish_result("completed", term, tracked, events.append, now=200.0)
    assert "Parser refactored" in events[0].report


def test_report_grace_outlasts_the_conversation_lookup():
    from jarvis.agentic_ide.session import CONVERSATION_DELAYS_S

    assert ft.REPORT_GRACE_S > sum(CONVERSATION_DELAYS_S)
