"""The turn host — thread CLIs that outlive the app, and carrying their turns on."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from jarvis.agent_chat import runner_api, turn_host, turn_host_client
from jarvis.agent_chat.runner_cli import resume_hosted_cli_turn
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.agentic_ide import host_mode
from jarvis.terminal.pty_host_client import _handshake

TOKEN = "test-token"  # noqa: S105 - a fixture value

# Prints what it got on stdin, then a second line, waits for "go", prints a
# third line and exits 3 — enough to tell replayed, live and written lines apart.
_CHILD = (
    "import sys\n"
    "print('got ' + sys.stdin.readline().strip(), flush=True)\n"
    "print('two', flush=True)\n"
    "sys.stdin.readline()\n"
    "print('three', flush=True)\n"
    "sys.exit(3)\n"
)


async def _start_host(
    tmp_path: Path, *, idle_exit_s: float = 60.0
) -> tuple[turn_host.TurnHost, asyncio.Task[None], int]:
    host = turn_host.TurnHost(TOKEN, tmp_path / "spool", idle_exit_s=idle_exit_s)
    state = tmp_path / "state.json"
    task = asyncio.create_task(host.serve(state))
    for _ in range(200):
        if state.exists():
            break
        await asyncio.sleep(0.02)
    return host, task, int(json.loads(state.read_text(encoding="utf-8"))["port"])


async def _client(port: int) -> turn_host_client.TurnHostClient:
    found = await _handshake(
        port,
        TOKEN,
        3.0,
        proto=turn_host.PROTOCOL_VERSION,
        limit=turn_host.MAX_FRAME_BYTES,
    )
    assert found is not None
    return turn_host_client._client_from(*found)


async def _stop(host: turn_host.TurnHost, task: asyncio.Task[None]) -> None:
    host._stop.set()
    await asyncio.wait_for(task, timeout=10)


async def test_a_cli_keeps_running_when_the_app_detaches_and_replays_to_the_next(
    tmp_path: Path,
) -> None:
    host, task, port = await _start_host(tmp_path)
    try:
        first = await _client(port)
        cli = await first.spawn(
            [sys.executable, "-c", _CHILD],
            cwd=str(tmp_path),
            env=None,
            stdin="hello\n",
            keep_stdin=True,
            meta={"turn_id": "t1", "keep_stdin": True},
        )
        assert await cli.stdout.readline() == b"got hello\n"
        assert await cli.stdout.readline() == b"two\n"  # acknowledges line 1
        await asyncio.sleep(0.2)
        # The app quits: nothing ends, the CLI waits for its "go".
        first.detach()
        await asyncio.sleep(0.2)

        second = await _client(port)
        assert [t["meta"]["turn_id"] for t in second.turns.values()] == ["t1"]
        again = await second.attach(cli.host_id)
        assert again is not None
        assert await again.stdout.readline() == b"got hello\n"
        assert again.stdout.replaying  # handled before the restart: state only
        assert await again.stdout.readline() == b"two\n"
        assert not again.stdout.replaying  # never acknowledged: handled again
        assert again.stdin is not None
        again.stdin.write(b"go\n")
        await again.stdin.drain()
        assert await again.stdout.readline() == b"three\n"
        assert await again.stdout.readline() == b""
        assert await asyncio.wait_for(again.wait(), timeout=10) == 3
        again.release()
        await asyncio.sleep(0.2)
        assert host._turns == {}
        second.detach()
    finally:
        await _stop(host, task)


async def test_a_second_app_process_takes_the_turn_over_without_ending_it(
    tmp_path: Path,
) -> None:
    host, task, port = await _start_host(tmp_path)
    try:
        first = await _client(port)
        cli = await first.spawn(
            [sys.executable, "-c", _CHILD],
            cwd=str(tmp_path),
            env=None,
            stdin="hi\n",
            keep_stdin=True,
            meta={"turn_id": "t4", "keep_stdin": True},
        )
        assert await cli.stdout.readline() == b"got hi\n"

        second = await _client(port)
        await asyncio.wait_for(cli.wait(), timeout=5)
        assert cli.handed_over and cli.detached  # not an ending: no kill, no outcome
        again = await second.attach(cli.host_id)
        assert again is not None
        assert again.stdin is not None
        again.stdin.write(b"go\n")
        lines = [await again.stdout.readline() for _ in range(4)]
        assert lines == [b"got hi\n", b"two\n", b"three\n", b""]
        assert await asyncio.wait_for(again.wait(), timeout=10) == 3
        second.detach()
    finally:
        await _stop(host, task)


async def test_a_turn_that_ends_with_no_app_attached_is_spooled_for_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(turn_host, "_IDLE_POLL_S", 0.05)
    host, task, port = await _start_host(tmp_path, idle_exit_s=0.2)
    client = await _client(port)
    await client.spawn(
        [sys.executable, "-c", "print('a', flush=True)"],
        cwd=str(tmp_path),
        env=None,
        stdin=None,
        keep_stdin=False,
        meta={"turn_id": "t2"},
    )
    client.detach()
    await asyncio.wait_for(task, timeout=15)  # idle: spools and exits on its own
    del host

    monkeypatch.setattr(turn_host_client, "spool_dir", lambda: tmp_path / "spool")
    [record] = turn_host_client.read_spool()
    assert record["meta"] == {"turn_id": "t2"}
    cli = turn_host_client.spooled_cli(record)
    assert await cli.stdout.readline() == b"a\n"
    assert not cli.stdout.replaying
    assert await cli.stdout.readline() == b""
    assert await cli.wait() == 0
    cli.release()
    assert turn_host_client.read_spool() == []


_CLAUDE_LINES: list[dict[str, Any]] = [
    {"type": "system", "subtype": "init", "session_id": "sess-1"},
    {"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "hello"}]}},
    {
        "type": "assistant",
        "message": {
            "id": "m1",
            "content": [
                {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}
            ],
        },
    },
    {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "a b", "is_error": False}
            ]
        },
    },
    {"type": "assistant", "message": {"id": "m2", "content": [{"type": "text", "text": "done"}]}},
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "total_cost_usd": 0.01,
        "usage": {"output_tokens": 5},
    },
]


def _spool_record(tmp_path: Path, *, session_id: str, turn_id: str, acked: int) -> dict[str, Any]:
    path = tmp_path / "spool" / f"host-{turn_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": f"host-{turn_id}",
        "pid": 1,
        "meta": {
            "session_id": session_id,
            "turn_id": turn_id,
            "runner": "claude-cli",
            "shape": "claude",
            "keep_stdin": True,
            "control": True,
            "vendor_session": "sess-1",
            "discover": False,
            "codex_home": "",
            "cwd": str(tmp_path),
            "started_at": time.time() - 5,
        },
        "alive": False,
        "code": 0,
        "acked": acked,
        "stdout": [json.dumps(line) for line in _CLAUDE_LINES],
        "stderr": [],
        "truncated": False,
    }
    path.write_text(json.dumps(record), encoding="utf-8")
    return {**record, "spool_path": str(path)}


async def test_a_carried_on_turn_emits_only_what_the_old_app_never_saw(tmp_path: Path) -> None:
    store = AgentChatStore(":memory:")
    try:
        session = store.create_session(
            provider="claude-api", model="m", effort="medium", cwd=str(tmp_path), surface="agent"
        )
        events: list[dict[str, Any]] = []

        async def emit(event: dict[str, Any]) -> None:
            events.append(event)

        async def no_approval(*_args: Any) -> str:
            pytest.fail("a replayed turn must not ask again")

        handle = runner_api.TurnHandle(
            session=session,
            turn_id="t3",
            emit=emit,
            request_approval=no_approval,
            cancel=asyncio.Event(),
        )
        # The old app handled the init line, "hello" and the tool call.
        record = _spool_record(tmp_path, session_id=session.session_id, turn_id="t3", acked=3)
        vendor = await resume_hosted_cli_turn(handle, turn_host_client.spooled_cli(record))

        kinds = [e["kind"] for e in events]
        texts = [e["payload"]["text"] for e in events if e["kind"] == "assistant_text"]
        assert texts == ["done"]
        assert "tool_call" not in kinds
        assert "tool_result" in kinds
        assert kinds[-1] == "turn_finished"
        assert events[-1]["payload"]["status"] == "done"
        assert events[-1]["payload"]["duration_ms"] >= 5000
        assert vendor == "sess-1"
        assert not await asyncio.to_thread(Path(record["spool_path"]).exists)
    finally:
        store.close()


@pytest.fixture
def app_host_mode() -> Any:
    host_mode.enable()
    yield
    host_mode.reset()


async def test_a_restarted_app_carries_on_the_thread_turn_the_host_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_host_mode: Any
) -> None:
    monkeypatch.setattr(turn_host_client, "spool_dir", lambda: tmp_path / "spool")
    monkeypatch.setattr(turn_host_client, "_host_alive", lambda state: False)

    async def no_live_host(*, start: bool = True) -> None:
        return None

    monkeypatch.setattr(turn_host_client, "get_client", no_live_host)
    db = tmp_path / "agent_chat.db"
    store = AgentChatStore(db)
    kept = store.create_session(
        provider="claude-api", model="m", effort="medium", cwd=str(tmp_path), surface="agent"
    )
    lost = store.create_session(
        provider="claude-api", model="m", effort="medium", cwd=str(tmp_path), surface="agent"
    )
    for sid in (kept.session_id, lost.session_id):
        store.append_event(
            sid, {"kind": "turn_started", "ts_ms": 1_000, "payload": {"turn_id": f"t-{sid}"}}
        )
    _spool_record(tmp_path, session_id=kept.session_id, turn_id=f"t-{kept.session_id}", acked=0)

    svc = AgentChatService(AgentChatStore(db))
    # Held as running until the host was asked: no second CLI on the conversation.
    assert svc.is_running(kept.session_id) and svc.is_running(lost.session_id)
    await svc._reattach_task
    run = svc._running.get(kept.session_id)
    if run is not None and run.task is not None:
        await asyncio.wait_for(run.task, timeout=10)

    finished = store.list_events(kept.session_id)[-1]
    assert finished["kind"] == "turn_finished"
    assert finished["payload"]["status"] == "done"
    texts = [
        e["payload"]["text"]
        for e in store.list_events(kept.session_id)
        if e["kind"] == "assistant_text"
    ]
    assert texts == ["hello", "done"]
    assert store.get_session(kept.session_id).vendor_session == "sess-1"  # type: ignore[union-attr]

    sealed = store.list_events(lost.session_id)[-1]
    assert sealed["kind"] == "turn_finished"
    assert sealed["payload"]["status"] == "error"
    assert not svc.is_running(kept.session_id) and not svc.is_running(lost.session_id)
    assert store.open_turns() == []


def test_tests_and_scripts_never_reach_the_users_turn_host() -> None:
    host_mode.reset()
    assert not turn_host_client.host_available()
    assert not turn_host_client.may_hold_turns()
