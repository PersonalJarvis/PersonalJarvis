"""The app's side of the PTY host: ``RemoteSession`` and ``RemotePtyManager``.

No host process and no pseudo-terminal: a tiny asyncio server runs in the test
and plays the host's half of the wire protocol, so every frame the client sends
and every event it reacts to is visible here.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from jarvis.terminal.pty_host_client import (
    HostConnectionError,
    HostedInfo,
    RemotePtyManager,
    RemoteSession,
)
from jarvis.terminal.pty_manager import MAX_PENDING_CHARS, UNKNOWN_EXIT_CODE

WAIT_S = 5.0


async def _until(check: Callable[[], bool], limit_s: float = WAIT_S) -> None:
    """Poll ``check`` until it holds, failing loudly after ``limit_s``."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_s
    while not check():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


class Recorder:
    """Output/exit callbacks that remember what they were handed, in order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def output(self, _tid: str, text: str) -> None:
        self.events.append(("o", text))

    async def closed(self, _tid: str, code: int) -> None:
        self.events.append(("exit", code))

    @property
    def text(self) -> str:
        return "".join(value for kind, value in self.events if kind == "o")

    @property
    def exits(self) -> list[int]:
        return [value for kind, value in self.events if kind == "exit"]


# ------------------------------------------------------------ RemoteSession
async def test_output_that_arrives_during_a_send_leaves_as_one_chunk() -> None:
    gate = asyncio.Event()
    received: list[str] = []

    async def _output(_tid: str, text: str) -> None:
        received.append(text)
        if len(received) == 1:
            await gate.wait()

    async def _closed(_tid: str, _code: int) -> None:
        return None

    session = RemoteSession("t1", 1, _output, _closed)
    pump = asyncio.create_task(session.run())
    session.offer("a")
    await _until(lambda: received == ["a"])
    # The first send is still in flight: these three must coalesce.
    session.offer("b")
    session.offer("c")
    session.offer("d")
    gate.set()
    await _until(lambda: len(received) == 2)
    assert received == ["a", "bcd"]
    session.finish(0)
    await asyncio.wait_for(pump, WAIT_S)


async def test_the_exit_is_reported_only_after_the_last_output_is_flushed() -> None:
    rec = Recorder()
    session = RemoteSession("t1", 1, rec.output, rec.closed)
    session.offer("last words")
    session.finish(3)
    # Offered after the exit: dropped, never delivered after on_closed.
    session.offer("ghost")
    await asyncio.wait_for(session.run(), WAIT_S)
    assert rec.events == [("o", "last words"), ("exit", 3)]


async def test_a_backlog_over_the_cap_drops_the_oldest_chunks() -> None:
    rec = Recorder()
    session = RemoteSession("t1", 1, rec.output, rec.closed)
    old = "x" * (MAX_PENDING_CHARS // 2 + 1)
    new = "y" * (MAX_PENDING_CHARS // 2 + 1)
    session.offer(old)
    session.offer(new)
    session.offer("tail")
    session.finish(0)
    await asyncio.wait_for(session.run(), WAIT_S)
    assert rec.text == new + "tail"
    assert rec.exits == [0]


async def test_a_failing_output_callback_does_not_stop_the_exit() -> None:
    closed: list[int] = []

    async def _boom(_tid: str, _text: str) -> None:
        raise RuntimeError("viewer gone")

    async def _closed(_tid: str, code: int) -> None:
        closed.append(code)

    session = RemoteSession("t1", 1, _boom, _closed)
    session.offer("x")
    session.finish(7)
    await asyncio.wait_for(session.run(), WAIT_S)
    assert closed == [7]


# ---------------------------------------------------- in-test host server
class FakeHost:
    """Plays the host's side of the wire protocol for one client."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.writer: asyncio.StreamWriter | None = None
        self.eof = asyncio.Event()
        self.server: asyncio.base_events.Server | None = None
        self.answer_requests = True
        self._next = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._on_connect, "127.0.0.1", 0)
        return int(self.server.sockets[0].getsockname()[1])

    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                frame = json.loads(line.decode("utf-8"))
                self.frames.append(frame)
                await self._answer(frame)
        except (ConnectionError, OSError):
            pass
        finally:
            self.eof.set()

    async def _answer(self, frame: dict[str, Any]) -> None:
        rid = frame.get("rid")
        if rid is None or not self.answer_requests:
            return
        op = frame.get("op")
        if op == "spawn":
            self._next += 1
            reply = {"ok": True, "id": f"host-{self._next}", "pid": 900 + self._next}
        elif op == "adopt":
            if frame["id"] == "dead":
                reply = {"ok": True, "alive": False, "code": 5}
            else:
                reply = {
                    "ok": True,
                    "alive": True,
                    "replay": "screen so far",
                    "truncated": True,
                    "cols": 100,
                    "rows": 40,
                }
        else:
            reply = {"ok": True}
        reply["rid"] = rid
        await self.send(reply)

    async def send(self, frame: dict[str, Any]) -> None:
        assert self.writer is not None
        self.writer.write((json.dumps(frame) + "\n").encode("utf-8"))
        await self.writer.drain()

    def ops(self, op: str) -> list[dict[str, Any]]:
        return [f for f in self.frames if f.get("op") == op]

    async def drop(self) -> None:
        assert self.writer is not None
        self.writer.close()

    async def stop(self) -> None:
        if self.writer is not None:
            self.writer.close()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


class Responder:
    """Stands in for ``TerminalQueryResponder``: the client reads its appearance."""

    appearance = "light"

    def feed(self, text: str) -> str:
        return ""


@pytest.fixture
async def host() -> AsyncIterator[FakeHost]:
    fake = FakeHost()
    await fake.start()
    yield fake
    await fake.stop()


async def _client(host: FakeHost, hosted: list[HostedInfo] | None = None) -> RemotePtyManager:
    assert host.server is not None
    port = int(host.server.sockets[0].getsockname()[1])
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    manager = RemotePtyManager(reader, writer, hosted or [], host_pid=4321)
    await _until(lambda: host.writer is not None)
    return manager


async def _cleanup(manager: RemotePtyManager) -> None:
    manager.detach()
    for session in list(manager._sessions.values()):  # noqa: SLF001 - test teardown
        if session._pump is not None:  # noqa: SLF001
            session._pump.cancel()  # noqa: SLF001
    await asyncio.sleep(0)


def _hosted(terminal_id: str, **meta: Any) -> HostedInfo:
    return HostedInfo(
        terminal_id=terminal_id, pid=77, meta=dict(meta), cols=80, rows=24, started_at=1.0
    )


# --------------------------------------------------------- RemotePtyManager
async def test_spawn_sends_argv_meta_and_the_viewers_appearance(host: FakeHost) -> None:
    manager = await _client(host)
    rec = Recorder()
    try:
        session = await manager.spawn(
            ("agent", "--flag"),
            "agentic-ide:t1",
            "/work",
            120,
            30,
            rec.output,
            rec.closed,
            env={"A": "1"},
            on_probe=Responder().feed,
            meta={"history_id": "h1", "name": "T1"},
        )
        assert session.terminal_id == "host-1"
        assert session.pid == 901
        spawn = host.ops("spawn")[0]
        assert spawn["argv"] == ["agent", "--flag"]
        assert spawn["cwd"] == "/work"
        assert (spawn["cols"], spawn["rows"]) == (120, 30)
        assert spawn["env"] == {"A": "1"}
        assert spawn["appearance"] == "light"
        assert spawn["meta"] == {"history_id": "h1", "name": "T1"}
        assert manager.persistent is True
        assert manager.connected is True
        assert manager.has("host-1")
    finally:
        await _cleanup(manager)


async def test_output_and_exit_events_reach_the_right_terminal(host: FakeHost) -> None:
    manager = await _client(host)
    one, two = Recorder(), Recorder()
    try:
        await manager.spawn(("a",), "s", None, 80, 24, one.output, one.closed)
        await manager.spawn(("b",), "s", None, 80, 24, two.output, two.closed)
        await host.send({"ev": "o", "id": "host-1", "d": "hello "})
        await host.send({"ev": "o", "id": "host-2", "d": "other"})
        await host.send({"ev": "o", "id": "host-1", "d": "world"})
        # An event for a terminal this client does not hold is ignored.
        await host.send({"ev": "o", "id": "stranger", "d": "nope"})
        await host.send({"ev": "exit", "id": "host-1", "code": 0})
        await _until(lambda: one.exits == [0])
        assert one.text == "hello world"
        await _until(lambda: two.text == "other")
        assert two.exits == []
        # The ended terminal is forgotten; the other is still routed.
        await _until(lambda: not manager.has("host-1"))
        assert manager.has("host-2")
    finally:
        await _cleanup(manager)


async def test_write_resize_appearance_and_close_become_frames(host: FakeHost) -> None:
    manager = await _client(host)
    rec = Recorder()
    try:
        await manager.spawn(("a",), "s", None, 80, 24, rec.output, rec.closed)
        assert manager.write("host-1", "ls\r") is True
        assert manager.resize("host-1", 132, 50) is True
        manager.set_appearance("host-1", "dark")
        # Unknown ids never reach the wire.
        assert manager.write("nope", "x") is False
        assert manager.resize("nope", 1, 1) is False
        assert manager.close("nope") is False
        assert manager.close("host-1") is True
        await _until(lambda: bool(host.ops("close")))
        assert host.ops("write") == [{"op": "write", "id": "host-1", "d": "ls\r"}]
        assert host.ops("resize") == [{"op": "resize", "id": "host-1", "cols": 132, "rows": 50}]
        assert host.ops("appearance") == [
            {"op": "appearance", "id": "host-1", "appearance": "dark"}
        ]
        assert host.ops("close") == [{"op": "close", "id": "host-1"}]
        # Closing is not an exit: the exit still comes from the host.
        assert rec.exits == []
        await host.send({"ev": "exit", "id": "host-1", "code": 1})
        await _until(lambda: rec.exits == [1])
    finally:
        await _cleanup(manager)


async def test_adopt_returns_the_replay_and_streams_from_then_on(host: FakeHost) -> None:
    manager = await _client(host, [_hosted("h-live", history_id="x"), _hosted("dead")])
    rec = Recorder()
    try:
        assert {info.terminal_id for info in manager.hosted()} == {"h-live", "dead"}
        result = await manager.adopt("h-live", rec.output, rec.closed)
        assert result.alive is True
        assert result.replay == "screen so far"
        assert result.truncated is True
        assert (result.cols, result.rows) == (100, 40)
        assert manager.has("h-live")
        assert manager._sessions["h-live"].pid == 77  # noqa: SLF001
        assert [info.terminal_id for info in manager.hosted()] == ["dead"]
        await host.send({"ev": "o", "id": "h-live", "d": "live"})
        await _until(lambda: rec.text == "live")

        gone = await manager.adopt("dead", rec.output, rec.closed)
        assert gone.alive is False
        assert gone.exit_code == 5
        assert not manager.has("dead")
        assert manager.hosted() == []
    finally:
        await _cleanup(manager)


async def test_kill_hosted_closes_an_unclaimed_terminal(host: FakeHost) -> None:
    manager = await _client(host, [_hosted("orphan")])
    try:
        assert manager.kill_hosted("orphan") is True
        assert manager.hosted() == []
        await _until(lambda: bool(host.ops("close")))
        assert host.ops("close") == [{"op": "close", "id": "orphan"}]
    finally:
        await _cleanup(manager)


async def test_losing_the_host_ends_every_terminal_with_unknown_code(host: FakeHost) -> None:
    manager = await _client(host, [_hosted("never-adopted")])
    one, two = Recorder(), Recorder()
    try:
        await manager.spawn(("a",), "s", None, 80, 24, one.output, one.closed)
        await manager.spawn(("b",), "s", None, 80, 24, two.output, two.closed)
        await host.send({"ev": "o", "id": "host-1", "d": "before"})
        await _until(lambda: one.text == "before")
        # A request in flight when the host goes away fails instead of hanging.
        host.answer_requests = False
        pending = asyncio.create_task(manager.adopt("never-adopted", one.output, one.closed))
        await _until(lambda: bool(host.ops("adopt")))
        await host.drop()
        await _until(lambda: one.exits == [UNKNOWN_EXIT_CODE])
        await _until(lambda: two.exits == [UNKNOWN_EXIT_CODE])
        with pytest.raises(HostConnectionError):
            await asyncio.wait_for(pending, WAIT_S)
        assert manager.connected is False
        assert manager.hosted() == []
        assert not manager.has("host-1")
        assert manager.write("host-2", "x") is False
        with pytest.raises(HostConnectionError):
            await manager.spawn(("c",), "s", None, 80, 24, one.output, one.closed)
    finally:
        await _cleanup(manager)


async def test_lost_finishes_sessions_directly(host: FakeHost) -> None:
    manager = await _client(host)
    rec = Recorder()
    try:
        await manager.spawn(("a",), "s", None, 80, 24, rec.output, rec.closed)
        manager._lost(ConnectionResetError("boom"))  # noqa: SLF001 - the unit under test
        await _until(lambda: rec.exits == [UNKNOWN_EXIT_CODE])
        # A second loss report must not finish anybody twice.
        manager._lost(None)  # noqa: SLF001
        await asyncio.sleep(0.05)
        assert rec.exits == [UNKNOWN_EXIT_CODE]
    finally:
        await _cleanup(manager)


async def test_close_all_only_detaches(host: FakeHost) -> None:
    manager = await _client(host)
    rec = Recorder()
    try:
        await manager.spawn(("a",), "s", None, 80, 24, rec.output, rec.closed)
        manager.close_all()
        # The host sees the connection end — and no close for the terminal.
        await asyncio.wait_for(host.eof.wait(), WAIT_S)
        assert host.ops("close") == []
        assert manager.connected is False
        assert manager.write("host-1", "x") is False
        assert manager.close("host-1") is False
        # Detaching is not an exit: nobody is told their agent ended.
        await asyncio.sleep(0.05)
        assert rec.exits == []
        manager.close_all()  # idempotent
    finally:
        await _cleanup(manager)


async def test_swap_sessions_is_refused(host: FakeHost) -> None:
    manager = await _client(host)
    rec = Recorder()
    try:
        await manager.spawn(("a",), "s", None, 80, 24, rec.output, rec.closed)
        await manager.spawn(("b",), "s", None, 80, 24, rec.output, rec.closed)
        assert manager.swap_sessions("host-1", "host-2") is False
        assert manager.swap_sessions("host-1", "host-1") is True
        assert not [f for f in host.frames if f.get("op") not in {"spawn"}]
    finally:
        await _cleanup(manager)


async def test_a_refused_spawn_raises(host: FakeHost) -> None:
    manager = await _client(host)
    rec = Recorder()
    try:
        # The fake answers every spawn; this time the test answers, refusing it.
        host.answer_requests = False
        task = asyncio.create_task(manager.spawn(("a",), "s", None, 80, 24, rec.output, rec.closed))
        await _until(lambda: bool(host.ops("spawn")))
        rid = host.ops("spawn")[0]["rid"]
        await host.send({"rid": rid, "ok": False, "error": "empty argv"})
        with pytest.raises(RuntimeError, match="empty argv"):
            await asyncio.wait_for(task, WAIT_S)
        assert manager.connected is True
    finally:
        await _cleanup(manager)


# ------------------------------------------------ alive vs gone, never guessed
def test_a_state_file_only_counts_for_a_live_pty_host() -> None:
    from jarvis.terminal import pty_host_client as client

    assert client.host_is_alive(None) is False
    # This test process is alive but is not a PTY host (a reused pid).
    assert client.host_is_alive({"pid": os.getpid(), "boot_time": 0}) is False
    assert client.host_is_alive({"pid": 2**22 + 12345}) is False


async def test_a_live_host_that_does_not_answer_raises_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.terminal import pty_host_client as client

    started: list[str] = []
    monkeypatch.setattr(client, "host_available", lambda: True)
    monkeypatch.setattr(
        client,
        "_read_state",
        lambda path: {"pid": 4242, "port": 1, "token": "t", "proto": client.PROTOCOL_VERSION},
    )
    monkeypatch.setattr(client, "host_is_alive", lambda state: True)

    async def no_answer(port: int, token: str, wait_s: float) -> None:
        return None

    monkeypatch.setattr(client, "_handshake", no_answer)
    monkeypatch.setattr(client, "LIVE_HOST_PATIENCE_S", 0.3)
    monkeypatch.setattr(client, "_start_host", lambda path, token: started.append(token) or True)

    with pytest.raises(client.HostUnreachable):
        await client.connect(start=True)
    assert started == [], "a second host must never be started beside a live one"


async def test_a_dead_host_is_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.terminal import pty_host_client as client

    started: list[str] = []
    monkeypatch.setattr(client, "host_available", lambda: True)
    monkeypatch.setattr(
        client,
        "_read_state",
        lambda path: {"pid": 4242, "port": 1, "token": "old", "proto": client.PROTOCOL_VERSION},
    )
    monkeypatch.setattr(client, "host_is_alive", lambda state: False)
    monkeypatch.setattr(client, "_start_host", lambda path, token: started.append(token) or False)

    assert await client.connect(start=True) is None
    assert len(started) == 1
