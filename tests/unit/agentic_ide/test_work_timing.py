"""The Working badge times a task, not the observer or desktop process."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.activity import observed, record_work_start, stamp
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from jarvis.agentic_ide.notifications import ActivityFeed
from jarvis.agentic_ide.resume_store import SnapshotTerminal
from jarvis.agentic_ide.work_timing import active_start, recover_start
from tests.unit.agentic_ide.test_pty_host_adoption import FakeHostedPool


def _pane() -> ide.Terminal:
    return ide.Terminal(
        key="t1",
        name="T1",
        agent="codex",
        display_name="Codex",
        index=0,
        status="live",
        pty_id="host-1",
        process_generation=1,
    )


def _submit(term: ide.Terminal, at: float) -> None:
    record_work_start(term, at)
    term.last_submit_at = at
    term.submit_generation = term.process_generation


def test_working_clock_survives_detection_gaps_and_queued_input() -> None:
    term = _pane()
    _submit(term, 100.0)
    stamp(term, "working", now=103.0)
    assert observed(term, now=104.0).since == 100.0
    # No repaint for long enough to lose the Working reading, then more work.
    stamp(term, "waiting", now=800.0)
    stamp(term, "working", now=810.0)
    assert observed(term, now=811.0).since == 100.0
    _submit(term, 812.0)  # a queued clarification must not restart the job clock
    assert observed(term, now=813.0).since == 100.0


def test_new_task_resets_but_done_age_remains_the_completion_age() -> None:
    term = _pane()
    _submit(term, 100.0)
    stamp(term, "waiting", now=800.0, since=790.0)
    assert observed(term, now=801.0).since == 790.0
    _submit(term, 802.0)
    assert observed(term, now=803.0).since == 802.0


def test_permission_answer_keeps_the_task_start() -> None:
    term = _pane()
    _submit(term, 100.0)
    stamp(term, "asking", now=800.0)
    _submit(term, 801.0)
    stamp(term, "working", now=802.0)
    assert observed(term, now=803.0).since == 100.0


def test_unknown_adopted_task_never_claims_observer_uptime() -> None:
    term = _pane()
    term.adopted_generation = term.process_generation
    stamp(term, "working", now=1000.0)
    assert observed(term, now=1001.0).since == 0.0


@pytest.mark.parametrize("value", [None, "bad", -1, True, float("nan"), float("inf")])
def test_legacy_or_invalid_snapshot_clock_is_unknown(value: object) -> None:
    restored = SnapshotTerminal.from_dict(
        {"name": "T1", "agent": "codex", "work_started_at": value}
    )
    assert restored is not None
    assert restored.work_started_at == 0.0


def _record(home: Path, agent: str, *, complete: bool = False) -> None:
    if agent == "codex":
        path = home / "sessions" / "2026" / "10" / "01" / "rollout-clock-test.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": "clock-test"}},
            {
                "timestamp": "2026-10-01T18:00:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Fix the login bug"}],
                },
            },
            {
                "timestamp": "2026-10-01T18:00:09Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Checking"}],
                },
            },
        ]
        if complete:
            rows.append(
                {
                    "timestamp": "2026-10-01T18:40:00Z",
                    "type": "event_msg",
                    "payload": {"type": "task_complete"},
                }
            )
    else:
        path = home / "projects" / "example" / "clock-test.jsonl"
        rows = [
            {
                "timestamp": "2026-10-01T18:00:00Z",
                "type": "user",
                "message": {"role": "user", "content": "Fix the login bug"},
            },
            {
                "timestamp": "2026-10-01T18:00:09Z",
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "tool1", "name": "Read", "input": {}}],
                },
            },
            {
                "timestamp": "2026-10-01T18:40:00Z",
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool1", "content": "result"}
                    ],
                },
            },
        ]
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


@pytest.mark.parametrize("agent", ["codex", "claude"])
async def test_adoption_recovers_41_minutes_and_all_readers_agree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
) -> None:
    _record(tmp_path, agent)
    start = 1790877600.0
    now = start + 41 * 60 + 34
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr(ide, "account_home", lambda *_: tmp_path)
    pool = FakeHostedPool()
    pool.add_hosted("host-1", history_id="test", started_at=start - 3600)
    registry = ide.Registry(pty_manager=pool)
    term = _pane()
    term.agent = agent
    term.resume = ResumeHandle(kind=f"{agent}_session", id="clock-test", captured_at=start)
    info = pool.hosted()[0]
    assert await registry._adopt_one(pool, term, info)
    stamp(term, "working", now=now - 12 * 60)
    stamp(term, "working", now=now)
    assert term.to_row()["activity_since"] == start
    assert term.to_dict()["activity_since"] == start
    assert now - term.reading().since == 41 * 60 + 34

    # The websocket diff must carry the same corrected clock as REST.
    class RegistryView:
        sessions = [type("SessionView", (), {"id": "ws", "terminals": [term]})()]

    assert ActivityFeed().changes(RegistryView())[0]["activity_since"] == start


def test_finished_record_does_not_resurrect_an_old_task(tmp_path: Path) -> None:
    _record(tmp_path, "codex", complete=True)
    assert recover_start("codex", "clock-test", tmp_path) == 0.0


def test_latest_task_does_not_inherit_an_older_tasks_time() -> None:
    events = [
        {"kind": "user_message", "ts_ms": 100_000},
        {"kind": "turn_started", "ts_ms": 105_000},
        {"kind": "turn_finished", "ts_ms": 200_000},
        {"kind": "user_message", "ts_ms": 900_000},
        {"kind": "turn_started", "ts_ms": 910_000},
    ]
    assert active_start(events) == 900.0


@pytest.mark.parametrize("same_process", [True, False])
async def test_snapshot_fallback_is_bound_to_the_hosted_process(same_process: bool) -> None:
    original = _pane()
    _submit(original, 100.0)
    saved = SnapshotTerminal.from_dict(original.to_snapshot().to_dict())
    assert saved is not None
    restored = _pane()
    restored.work_started_at = saved.work_started_at
    restored.work_pty_id = saved.work_pty_id
    restored.pty_id = "host-1" if same_process else "replacement"
    await ide.Registry._recover_work_start(restored)
    assert restored.work_started_at == (100.0 if same_process else 0.0)
