"""Exercise automatic readback with native CLI records, not display-turn fakes."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import agent_transcript, followthrough, task_state
from jarvis.core.delegation import ResultInbox


def _stamp(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat()


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(followthrough, "time", SimpleNamespace(time=lambda: 100.0))
    path = tmp_path / "session.jsonl"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(agent_transcript, "_codex_file", lambda *args: path)
    monkeypatch.setattr(agent_transcript, "_claude_file", lambda *args: path)
    task_state.reset()
    term = SimpleNamespace(
        name="Coder", agent="codex", account=None, resume=SimpleNamespace(id="recorded"),
        process_generation=1, last_submit_at=100.0, last_prompt="Fix startup latency",
        submitted=True, delegation_result=None, last_output_at=103.0,
        reading=lambda: SimpleNamespace(activity="waiting"),
    )

    def write(rows):
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    yield term, write
    task_state.reset()


def _message(role, text, at):
    return {
        "timestamp": _stamp(at), "type": "response_item",
        "payload": {"type": "message", "role": role, "content": [
            {"type": "input_text" if role == "user" else "output_text", "text": text},
        ]},
    }


def _codex_rows(prompt, *, complete=True):
    rows = [
        _message(
            "user", "# AGENTS.md instructions for /project\n<INSTRUCTIONS>Rules</INSTRUCTIONS>",
            100,
        ),
        _message("user", prompt, 101),
        _message("assistant", "Investigating; no result yet.", 102),
        _message(
            "assistant", "Fixed startup latency. Offline tests passed; live audio unverified.", 103,
        ),
    ]
    if complete:
        rows.append({"timestamp": _stamp(104), "type": "event_msg",
                     "payload": {"type": "task_complete"}})
    return rows


async def _arm(term):
    pending = await followthrough.prepare(term, term.last_prompt, term.last_prompt, {"lang": "en"})
    followthrough.submitted(term, pending)
    return term.delegation_result


@pytest.mark.asyncio
@pytest.mark.parametrize("long_prompt", [False, True])
async def test_codex_instructions_do_not_hide_final_report(recorded, long_prompt):
    term, write = recorded
    if long_prompt:
        term.last_prompt += " Preserve cancellation and privacy." * 160
    pending = await _arm(term)
    write(_codex_rows(term.last_prompt))
    inbox = ResultInbox()
    assert await followthrough.publish_result("completed", term, pending, inbox.add)
    batch = inbox.take()
    assert "Fixed startup latency" in batch.report
    assert "live audio unverified" in batch.report
    assert "Investigating; no result yet." not in batch.report
    assert term.delegation_result is None
    assert not await followthrough.publish_result("completed", term, pending, inbox.add)
    assert inbox.take() is None


@pytest.mark.asyncio
async def test_delayed_report_is_retried_without_empty_announcement(recorded, monkeypatch):
    term, write = recorded
    pending = await _arm(term)
    write(_codex_rows(term.last_prompt))
    events = []
    # Lifecycle already sees task_complete while the report read lags behind.
    snapshot = followthrough._snapshot
    with monkeypatch.context() as lagging:
        lagging.setattr(followthrough, "_snapshot", lambda _term: [])
        assert not await followthrough.publish_result("completed", term, pending, events.append)
    assert not events
    assert term.delegation_result is pending
    assert snapshot(term)
    registry = SimpleNamespace(sessions=[SimpleNamespace(terminals=[term])])
    await followthrough.poll_ready(registry, events.append, now=130.0)
    assert len(events) == 1
    assert "Fixed startup latency" in events[0].report
    assert term.delegation_result is None


@pytest.mark.asyncio
async def test_native_claude_report_reaches_the_original_chat(recorded):
    term, write = recorded
    term.agent = "claude"
    pending = await followthrough.prepare(
        term, term.last_prompt, term.last_prompt, {"lang": "en", "reply_session_id": "origin"},
    )
    followthrough.submitted(term, pending)
    write([
        {"type": "user", "timestamp": _stamp(101),
         "message": {"role": "user", "content": term.last_prompt}},
        {"type": "assistant", "timestamp": _stamp(102), "message": {
            "role": "assistant", "content": [{"type": "text", "text": "Fixed; tests passed."}],
            "stop_reason": "end_turn",
        }},
    ])
    events = []
    assert await followthrough.publish_result(
        "completed", term, term.delegation_result, events.append,
    )
    assert events[0].session_id == "origin"
    assert "Fixed; tests passed." in json.loads(events[0].report)["report"]


@pytest.mark.asyncio
async def test_another_submission_cannot_supply_the_missing_report(recorded):
    term, write = recorded
    pending = await _arm(term)
    rows = _codex_rows(term.last_prompt)
    rows.extend(_codex_rows("An unrelated task"))
    write(rows)
    events = []
    assert not await followthrough.publish_result("completed", term, pending, events.append)
    assert not events
    assert term.delegation_result is pending


@pytest.mark.asyncio
async def test_prompt_recorded_before_input_receipt_still_matches(recorded):
    term, write = recorded
    pending = await followthrough.prepare(term, term.last_prompt, term.last_prompt, {"lang": "en"})
    write(_codex_rows(term.last_prompt))
    term.last_submit_at = 102.0  # The prompt was recorded at 101, before receipt confirmation.
    followthrough.submitted(term, pending)
    events = []
    assert await followthrough.publish_result(
        "completed", term, term.delegation_result, events.append,
    )
    assert "Fixed startup latency" in events[0].report


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", [None, "turn_aborted"])
async def test_unfinished_or_interrupted_output_never_becomes_a_report(recorded, ending):
    term, write = recorded
    pending = await _arm(term)
    rows = _codex_rows(term.last_prompt, complete=False)
    if ending:
        rows.append({"timestamp": _stamp(104), "type": "event_msg", "payload": {"type": ending}})
    write(rows)
    events = []
    assert not await followthrough.publish_result("completed", term, pending, events.append)
    assert not events
    assert term.delegation_result is pending
