"""TaskStore — the Automations additions: the ``paused`` state, the state
CHECK migration, the one-query ``latest_agent_results`` and the last-run
bookkeeping (``finished_at_ns`` / ``last_error``) for recurring tasks."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.tasks.schema import AgentAction, SpeakAction, TaskSpec, TriggerAfterDelay, TriggerEvery
from jarvis.tasks.store import TaskStore

# The `tasks` table exactly as a DB created before `every` AND `paused`
# existed — the oldest shape a downloader can still be carrying.
_LEGACY_TASKS_SQL = """
CREATE TABLE tasks (
    id              TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    spec_json       TEXT NOT NULL,
    state           TEXT NOT NULL CHECK(state IN (
                        'pending','scheduled','running','completed',
                        'failed','cancelled','interrupted')),
    trigger_type    TEXT NOT NULL CHECK(trigger_type IN (
                        'after_delay','at_time','on_event','every')),
    due_at_ns       INTEGER,
    event_selector  TEXT,
    title           TEXT NOT NULL DEFAULT '',
    created_at_ns   INTEGER NOT NULL,
    started_at_ns   INTEGER,
    finished_at_ns  INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    result_json     TEXT
)
"""


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "store.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


def _every() -> TaskSpec:
    return TaskSpec(
        title="recurring",
        trigger=TriggerEvery(interval_seconds=3600),
        action=AgentAction(prompt="p"),
    )


async def test_paused_state_is_accepted(store: TaskStore) -> None:
    tid = await store.insert(_every())
    await store.update_state(tid, "paused")
    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "paused"
    assert task["finished_at_ns"] is None
    # hydrate() only sees `scheduled` — paused tasks stay put.
    assert [r["id"] for r in await store.all_pending_scheduled()] == []


async def test_state_check_migration_rebuilds_legacy_table(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    raw = sqlite3.connect(db)
    raw.executescript(_LEGACY_TASKS_SQL)
    raw.execute(
        "INSERT INTO tasks (id, trace_id, spec_json, state, trigger_type, due_at_ns, "
        "title, created_at_ns) VALUES ('old', 'old', '{}', 'scheduled', 'every', 5, "
        "'legacy', 1)"
    )
    raw.commit()
    raw.close()

    async with TaskStore(db) as store:
        # The legacy row survived the rebuild …
        rows = await store.list()
        assert [r["id"] for r in rows] == ["old"]
        assert rows[0]["due_at_ns"] == 5
        # … and the new state is now accepted.
        await store.update_state("old", "paused")
        assert (await store.get("old"))["state"] == "paused"  # type: ignore[index]
        sql = await store._tasks_table_sql()
        assert sql is not None and "'paused'" in sql

    # Idempotent: a second open does not rebuild again (and does not fail).
    async with TaskStore(db) as store:
        assert (await store.get("old"))["state"] == "paused"  # type: ignore[index]


async def test_recurring_success_marks_finished_and_clears_error(store: TaskStore) -> None:
    tid = await store.insert(_every())
    await store.update_state(tid, "running", increment_attempts=True)
    await store.update_state(tid, "failed", error="boom")
    failed = await store.get(tid)
    assert failed is not None and failed["last_error"] == "boom"

    await store.update_state(tid, "running", increment_attempts=True)
    await store.update_state(tid, "scheduled", result={"duration_ms": 3})
    ok = await store.get(tid)
    assert ok is not None
    assert ok["state"] == "scheduled"
    assert ok["finished_at_ns"] is not None
    assert ok["last_error"] is None


async def test_plain_scheduled_transition_does_not_mark_finished(store: TaskStore) -> None:
    tid = await store.insert(_every())
    await store.update_state(tid, "paused")
    await store.update_state(tid, "scheduled")
    task = await store.get(tid)
    assert task is not None
    assert task["finished_at_ns"] is None


async def test_list_rows_carry_spec_json(store: TaskStore) -> None:
    await store.insert(_every())
    rows = await store.list()
    assert rows and '"interval_seconds"' in rows[0]["spec_json"]


async def test_latest_agent_results_one_per_task_newest_and_truncated(store: TaskStore) -> None:
    a = await store.insert(_every())
    b = await store.insert(_every())
    c = await store.insert(TaskSpec(
        title="speak", trigger=TriggerAfterDelay(delay_seconds=1), action=SpeakAction(text="x"),
    ))
    await store.append_step(a, "log", {"event": "agent_result", "text": "first"})
    await store.append_step(a, "log", {"event": "error", "message": "ignored"})
    await store.append_step(a, "log", {"event": "agent_result", "text": "x" * 1000})
    await store.append_step(b, "action", {"kind": "agent", "prompt": "no result yet"})
    await store.append_step(c, "log", {"event": "tts_done", "chunks": 2})

    results = await store.latest_agent_results([a, b, c], max_chars=400)
    assert set(results) == {a}
    assert results[a] == "x" * 400

    assert await store.latest_agent_results([]) == {}
    everything = await store.latest_agent_results(None, max_chars=2)
    assert everything == {a: "xx"}


# ----------------------------------------------------------------------
# readable_error — what the Runs tab shows as last_error
# ----------------------------------------------------------------------

def test_readable_error_extracts_code_and_message() -> None:
    from jarvis.tasks.runner import readable_error

    class APIStatusError(Exception):
        pass

    exc = APIStatusError(
        "Error code: 402 - {'error': {'message': 'Insufficient credits. Add more using "
        "https://openrouter.ai/settings/credits', 'code': 402, 'metadata': "
        "{'limit_source': 'openrouter_credits'}}}"
    )
    text = readable_error(exc)
    assert text.startswith("APIStatusError: 402 — Insufficient credits. Add more using")
    assert "metadata" not in text


def test_readable_error_plain_and_empty() -> None:
    from jarvis.tasks.runner import readable_error

    plain = readable_error(RuntimeError("Tool 'x' failed:   boom"))
    assert plain == "RuntimeError: Tool 'x' failed: boom"
    assert readable_error(KeyError()) == "KeyError"
    assert len(readable_error(RuntimeError("y" * 5000))) == 400
