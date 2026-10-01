"""Remote IDE panes over real SSH against a server that behaves like tmux.

The properties that make "run it on the VPS" worth having: output and input
flow both ways, the agent's session is created once and re-joined afterwards,
a dropped connection re-attaches instead of ending the pane, and only a
session that really ended reports the pane closed.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.computers import identity, remote_terminal
from jarvis.computers.remote_terminal import SshPtyPool, tmux_session_name
from jarvis.computers.service import ComputerService
from tests.fakes.fake_tmux_server import FakeTmuxServer


@pytest.fixture
async def tmux_server():  # noqa: ANN201
    server = FakeTmuxServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def pool(computer_service: ComputerService, tmux_server: FakeTmuxServer):  # noqa: ANN201
    tmux_server.state.authorized.add(identity.public_key_line())
    computer = await computer_service.add_server(
        name="vps", host="127.0.0.1", port=tmux_server.port
    )
    assert computer.health.status == "online"
    remote = SshPtyPool(computer.id, connect=lambda: computer_service.connect(computer.id))
    try:
        yield remote
    finally:
        remote.close_all()


class Screen:
    def __init__(self) -> None:
        self.text = ""
        self.closed: list[int] = []

    async def output(self, _tid: str, chunk: str) -> None:
        self.text += chunk

    async def exit(self, _tid: str, code: int) -> None:
        self.closed.append(code)

    async def wait_for(self, needle: str) -> None:
        for _ in range(250):
            if needle in self.text:
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"{needle!r} never arrived; screen: {self.text!r}")


async def _spawn(pool: SshPtyPool, screen: Screen, identity_id: str = "abc123") -> str:
    spawned = await pool.spawn(
        shell_argv=("claude", "--resume", "id-1"),
        shell_id="agentic-ide:t1",
        cwd="/home/test/jarvis-workspaces/app",
        cols=100,
        rows=30,
        on_output=screen.output,
        on_closed=screen.exit,
        meta={"history_id": identity_id},
    )
    return spawned.terminal_id


async def test_spawn_streams_both_ways_inside_a_named_tmux_session(
    pool: SshPtyPool, tmux_server: FakeTmuxServer
) -> None:
    screen = Screen()
    terminal = await _spawn(pool, screen)
    name = tmux_session_name("abc123")

    await screen.wait_for(f"ready {name}")
    assert pool.write(terminal, "hello")
    await screen.wait_for("echo:hello")
    assert tmux_server.tmux.created == [name]
    assert tmux_server.tmux.sizes[name] == (100, 30)
    command = next(c for c in tmux_server.state.commands if "new-session" in c)
    assert "claude --resume id-1" in command
    assert "jarvis-workspaces/app" in command


async def test_resize_reaches_the_server(pool: SshPtyPool, tmux_server: FakeTmuxServer) -> None:
    screen = Screen()
    terminal = await _spawn(pool, screen)
    await screen.wait_for("ready")

    assert pool.resize(terminal, 140, 40)
    name = tmux_session_name("abc123")
    for _ in range(100):
        if tmux_server.tmux.sizes.get(name) == (140, 40):
            break
        await asyncio.sleep(0.02)
    assert tmux_server.tmux.sizes[name] == (140, 40)


async def test_a_dropped_connection_re_attaches_instead_of_closing(
    pool: SshPtyPool, tmux_server: FakeTmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(remote_terminal, "RECONNECT_DELAYS_S", (0.05, 0.05, 0.05))
    screen = Screen()
    terminal = await _spawn(pool, screen)
    await screen.wait_for("ready")

    tmux_server.drop_connections()
    name = tmux_session_name("abc123")
    for _ in range(200):
        if tmux_server.tmux.attaches:
            break
        await asyncio.sleep(0.02)

    assert tmux_server.tmux.attaches == [name], "the same session was re-joined"
    assert screen.closed == [], "a network drop is not the agent exiting"
    await asyncio.sleep(0.1)
    assert pool.write(terminal, "again")
    await screen.wait_for("echo:again")


async def test_close_kills_the_session_and_a_server_side_end_reports_closed(
    pool: SshPtyPool, tmux_server: FakeTmuxServer
) -> None:
    screen = Screen()
    terminal = await _spawn(pool, screen)
    await screen.wait_for("ready")
    name = tmux_session_name("abc123")

    pool.close(terminal)
    for _ in range(200):
        if name not in tmux_server.tmux.alive:
            break
        await asyncio.sleep(0.02)
    assert name not in tmux_server.tmux.alive
    assert not pool.has(terminal)

    # A second pane whose agent ends on the server by itself.
    other = Screen()
    await _spawn(pool, other, identity_id="zzz")
    await other.wait_for("ready")
    tmux_server.tmux.alive.discard(tmux_session_name("zzz"))
    for screen_proc in tmux_server.tmux.screens.pop(tmux_session_name("zzz"), []):
        screen_proc.exit(0)
    for _ in range(200):
        if other.closed:
            break
        await asyncio.sleep(0.02)
    assert other.closed == [0]


def test_tmux_command_is_create_or_attach_and_quiet() -> None:
    command = remote_terminal.tmux_command(
        "jv-x", ("claude", "--resume", "a b"), "/srv/my app", 80, 24
    )
    assert "new-session -A -s jv-x" in command
    assert "status off" in command
    assert "'/srv/my app'" in command
    assert remote_terminal.tmux_session_name("a/b c") == "jv-a-b-c"


async def test_two_workspaces_same_call_sign_stay_apart(
    pool: SshPtyPool, tmux_server: FakeTmuxServer
) -> None:
    """Call-signs restart at T1 per workspace; the pool must not key on them."""
    first, second = Screen(), Screen()
    one = await _spawn(pool, first, identity_id="ws1-t1")
    two = await _spawn(pool, second, identity_id="ws2-t1")
    await first.wait_for("ready")
    await second.wait_for("ready")

    assert one != two
    assert pool.has(one) and pool.has(two)
    assert pool.write(one, "to-first")
    await first.wait_for("echo:to-first")
    assert "to-first" not in second.text


async def test_several_panes_re_attach_once_each_after_a_drop(
    pool: SshPtyPool, tmux_server: FakeTmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drop must not turn into panes cutting each other's fresh channels."""
    monkeypatch.setattr(remote_terminal, "RECONNECT_DELAYS_S", (0.05, 0.1, 0.1, 0.1))
    screens = [Screen() for _ in range(4)]
    terminals = [
        await _spawn(pool, screen, identity_id=f"pane{index}")
        for index, screen in enumerate(screens)
    ]
    for screen in screens:
        await screen.wait_for("ready")

    tmux_server.drop_connections()
    for _ in range(300):
        if len(tmux_server.tmux.attaches) >= len(screens):
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.4)

    assert sorted(tmux_server.tmux.attaches) == sorted(
        tmux_session_name(f"pane{index}") for index in range(4)
    ), "every pane re-joined exactly once"
    for terminal, screen in zip(terminals, screens, strict=True):
        assert screen.closed == []
        assert pool.write(terminal, "still-here")
        await screen.wait_for("echo:still-here")
