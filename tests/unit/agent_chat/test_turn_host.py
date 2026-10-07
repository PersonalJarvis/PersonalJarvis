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


@pytest.mark.parametrize("built_in", ["event-loop", "route-worker-thread"])
async def test_a_restarted_app_carries_on_the_thread_turn_the_host_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app_host_mode: Any, built_in: str
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

    if built_in == "event-loop":
        svc = AgentChatService(AgentChatStore(db))
    else:
        # The thread list is a plain ``def`` route: the framework builds the
        # service in a worker thread, where no event loop is running.
        import anyio.to_thread

        svc = await anyio.to_thread.run_sync(lambda: AgentChatService(AgentChatStore(db)))
    # Held as running until the host was asked: no second CLI on the conversation.
    assert svc.is_running(kept.session_id) and svc.is_running(lost.session_id)
    await svc.wait_reattached()
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


# Asks for a Bash approval on the control protocol, then reports the answer.
_ASKING_CHILD = (
    "import json, sys\n"
    "ask = {'type': 'control_request', 'request_id': 'r1', 'request': {\n"
    "    'subtype': 'can_use_tool', 'tool_name': 'Bash',\n"
    "    'input': {'command': 'ls'}, 'tool_use_id': 'tu1'}}\n"
    "print(json.dumps(ask), flush=True)\n"
    "for raw in sys.stdin:\n"
    "    obj = json.loads(raw) if raw.strip().startswith('{') else {}\n"
    "    if obj.get('type') != 'control_response':\n"
    "        continue\n"
    "    said = obj['response']['response']['behavior']\n"
    "    text = {'type': 'text', 'text': 'approval: ' + said}\n"
    "    msg = {'type': 'assistant', 'message': {'id': 'm1', 'content': [text]}}\n"
    "    print(json.dumps(msg), flush=True)\n"
    "    end = {'type': 'result', 'subtype': 'success', 'is_error': False, 'usage': {}}\n"
    "    print(json.dumps(end), flush=True)\n"
    "    break\n"
)


async def test_an_approval_the_old_app_never_answered_opens_again_after_a_restart(
    tmp_path: Path,
) -> None:
    host, task, port = await _start_host(tmp_path)
    store = AgentChatStore(":memory:")
    try:
        session = store.create_session(
            provider="claude-api", model="m", effort="medium", cwd=str(tmp_path), surface="agent"
        )
        meta = {
            "session_id": session.session_id,
            "turn_id": "t5",
            "runner": "claude-cli",
            "shape": "claude",
            "keep_stdin": True,
            "control": True,
            "vendor_session": None,
            "discover": False,
            "codex_home": "",
            "cwd": str(tmp_path),
            "started_at": time.time(),
        }
        first = await _client(port)
        cli = await first.spawn(
            [sys.executable, "-c", _ASKING_CHILD],
            cwd=str(tmp_path),
            env=None,
            stdin='{"type": "control_request", "request": {"subtype": "initialize"}}\n',
            keep_stdin=True,
            meta=meta,
        )
        # The old app saw the request and opened its card — then went away.
        assert b"control_request" in await cli.stdout.readline()
        first.detach()
        await asyncio.sleep(0.2)

        second = await _client(port)
        again = await second.attach(cli.host_id)
        assert again is not None
        asked: list[tuple[str, str]] = []
        events: list[dict[str, Any]] = []

        async def emit(event: dict[str, Any]) -> None:
            events.append(event)

        async def approve(call_id: str, name: str, _args: Any, _summary: str) -> str:
            asked.append((call_id, name))
            return "allow"

        handle = runner_api.TurnHandle(
            session=session,
            turn_id="t5",
            emit=emit,
            request_approval=approve,
            cancel=asyncio.Event(),
        )
        await asyncio.wait_for(resume_hosted_cli_turn(handle, again), timeout=30)

        assert asked == [("tu1", "Bash")]
        texts = [e["payload"]["text"] for e in events if e["kind"] == "assistant_text"]
        assert texts == ["approval: allow"]
        assert events[-1]["kind"] == "turn_finished"
        assert events[-1]["payload"]["status"] == "done"
        second.detach()
    finally:
        store.close()
        await _stop(host, task)


def test_a_frozen_build_starts_the_turn_host_from_its_own_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core import frozen
    from jarvis.terminal import pty_host_client
    from jarvis.ui import relauncher

    started: list[list[str]] = []
    monkeypatch.setattr(frozen, "is_frozen", lambda: True)
    monkeypatch.setattr(
        pty_host_client,
        "_start_host_windows",
        lambda argv, *_rest: started.append(list(argv)) or True,
    )
    monkeypatch.setattr(
        relauncher, "spawn_detached", lambda argv, **_kw: started.append(list(argv))
    )
    assert pty_host_client._start_host(
        tmp_path / "state.json",
        "tok",
        module=turn_host_client.MODULE,
        token_env=turn_host.TOKEN_ENV,
        log_path=tmp_path / "host.log",
        extra_args=("--spool", str(tmp_path / "spool")),
        frozen_flag=turn_host.FROZEN_FLAG,
    )
    [argv] = started
    assert argv[0] == sys.executable
    assert argv[1:3] == ["--turn-host", "--state"]
    assert "-m" not in argv


def test_the_app_executable_routes_the_turn_host_flag_without_booting_the_app(
    tmp_path: Path,
) -> None:
    import subprocess

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    env = {k: v for k, v in __import__("os").environ.items() if k != turn_host.TOKEN_ENV}
    done = subprocess.run(  # noqa: S603 - our own interpreter, fixed arguments
        [
            sys.executable,
            "-m",
            "jarvis",
            "--turn-host",
            "--state",
            str(tmp_path / "s.json"),
            "--spool",
            str(tmp_path / "spool"),
        ],
        capture_output=True,
        env=env,
        timeout=60,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    # The host's own refusal (no token) — the app itself never started.
    assert done.returncode == 2
    assert b"no token" in done.stderr


async def test_the_app_start_builds_the_chat_service_only_when_the_host_holds_turns(
    monkeypatch: pytest.MonkeyPatch, app_host_mode: Any
) -> None:
    from types import SimpleNamespace

    from jarvis.ui.web import agent_chat_routes

    monkeypatch.setattr(agent_chat_routes, "_REATTACH_DELAY_S", 0.0)
    built: list[str] = []
    holds = {"value": False}
    monkeypatch.setattr(turn_host_client, "may_hold_turns", lambda: holds["value"])

    def factory() -> str:
        built.append("svc")
        return "svc"

    state = SimpleNamespace(agent_chat=None, agent_chat_factory=factory)
    task = agent_chat_routes.schedule_turn_reattach(state)
    assert task is not None
    await task
    assert built == []  # nothing to carry on: the chat stays unbuilt (AP-26)

    holds["value"] = True
    task = agent_chat_routes.schedule_turn_reattach(state)
    assert task is not None
    await task
    assert built == ["svc"] and state.agent_chat == "svc"


def test_the_app_start_does_nothing_outside_a_real_app_process() -> None:
    from types import SimpleNamespace

    from jarvis.ui.web import agent_chat_routes

    host_mode.reset()
    assert agent_chat_routes.schedule_turn_reattach(SimpleNamespace()) is None


# Prints its result line, then keeps reading stdin until it is closed.
_RESULT_THEN_WAIT = (
    "import json, sys\n"
    "print(json.dumps({'type': 'result', 'subtype': 'success'}), flush=True)\n"
    "sys.stdin.read()\n"
)


async def test_the_host_closes_stdin_after_the_result_when_no_app_is_attached(
    tmp_path: Path,
) -> None:
    host, task, port = await _start_host(tmp_path)
    try:
        client = await _client(port)
        cli = await client.spawn(
            [sys.executable, "-c", _RESULT_THEN_WAIT],
            cwd=str(tmp_path),
            env=None,
            stdin="prompt\n",
            keep_stdin=True,
            meta={"turn_id": "t6"},
            close_stdin_on="result",
        )
        client.detach()  # the app is gone before the CLI even answered
        for _ in range(200):
            turn = host._turns.get(cli.host_id)
            if turn is not None and turn.exit_code is not None:
                break
            await asyncio.sleep(0.05)
        assert host._turns[cli.host_id].exit_code == 0
    finally:
        await _stop(host, task)


def test_concurrent_first_requests_build_only_one_chat_service() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    from jarvis.ui.web.agent_chat_routes import _service_from_state

    entrants = Barrier(3)
    built = []

    def factory():
        service = object()
        built.append(service)
        time.sleep(0.1)  # SQLite initialization releases the GIL in production.
        return service

    state = SimpleNamespace(agent_chat=None, agent_chat_factory=factory)

    def first_request():
        entrants.wait(timeout=3)
        return _service_from_state(state)

    with ThreadPoolExecutor(max_workers=3) as pool:
        services = list(pool.map(lambda _: first_request(), range(3)))
    assert len(built) == 1
    assert all(svc is state.agent_chat for svc in services)


async def test_reattaching_twice_hands_over_the_old_reader_without_killing_the_cli(tmp_path):
    host, task, port = await _start_host(tmp_path)
    client = await _client(port)
    try:
        spawned = await client.spawn(
            [sys.executable, "-c", _CHILD],
            cwd=str(tmp_path),
            env=None,
            stdin="hi\n",
            keep_stdin=True,
            meta={"turn_id": "duplicate", "keep_stdin": True},
        )
        client.detach()
        client = await _client(port)
        first = await client.attach(spawned.host_id)
        assert first is not None
        assert await first.stdout.readline() == b"got hi\n"
        second = await client.attach(first.host_id)
        assert second is not None
        assert first.handed_over and first.detached
        assert await asyncio.wait_for(first.wait(), 1) == turn_host_client.HOST_LOST_CODE
        first.kill()
        first.release()  # a stale owner must not release the current reader
        assert client.live() == [second]
        assert second.stdin is not None
        second.stdin.write(b"go\n")
        assert [await second.stdout.readline() for _ in range(4)] == [
            b"got hi\n",
            b"two\n",
            b"three\n",
            b"",
        ]
        assert await asyncio.wait_for(second.wait(), 3) == 3
        second.release()
    finally:
        client.detach()
        await _stop(host, task)


async def test_duplicate_recovery_publishes_one_success_and_no_late_timeout(tmp_path):
    from jarvis.agent_chat.turn_host_client import TurnHandedOver

    host, task, port = await _start_host(tmp_path)
    client = await _client(port)
    store = AgentChatStore(":memory:")
    session = store.create_session(
        provider="claude-api", model="fake", effort="", cwd=str(tmp_path)
    )
    events = []
    readers = []

    async def emit(event):
        events.append(event)

    async def approve(*_args):
        pytest.fail("No approval expected")

    try:
        source = "import sys, json\nsys.stdin.readline()\nsys.stdin.readline()\n" + "\n".join(
            f"print({json.dumps(line)!r}, flush=True)" for line in _CLAUDE_LINES
        )
        spawned = await client.spawn(
            [sys.executable, "-u", "-c", source],
            cwd=str(tmp_path),
            env=None,
            stdin="prompt\n",
            keep_stdin=True,
            meta={
                "turn_id": "duplicate",
                "runner": "claude-cli",
                "shape": "claude",
                "keep_stdin": True,
                "started_at": time.time(),
            },
        )
        client.detach()
        client = await _client(port)
        for _ in range(3):
            proc = await client.attach(spawned.host_id)
            assert proc is not None
            handle = runner_api.TurnHandle(session, "duplicate", emit, approve, asyncio.Event())
            readers.append(asyncio.create_task(resume_hosted_cli_turn(handle, proc)))
            await asyncio.sleep(0)
        for reader in readers[:-1]:
            with pytest.raises(TurnHandedOver):
                await asyncio.wait_for(reader, 2)
        assert proc.stdin is not None
        proc.stdin.write(b"go\n")
        await asyncio.wait_for(readers[-1], 3)
        finishes = [e["payload"] for e in events if e["kind"] == "turn_finished"]
        assert len(finishes) == 1
        assert finishes[0]["status"] == "done"
        assert finishes[0]["usage"]["output_tokens"] == 5
        assert proc.returncode == 0
    finally:
        for reader in readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        client.detach()
        await _stop(host, task)
        store.close()
