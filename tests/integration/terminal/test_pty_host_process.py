"""The PTY host, end to end: a real host process, a real child in a real PTY.

The whole promise of the host in one test — an agent keeps running while no
client is attached, and the next client re-joins it with the screen it drew
meanwhile — driven through the public client API only.

The user's own host is never touched: ``LOCALAPPDATA`` and the home directory
point into ``tmp_path``, so the state file this test finds (and the host it
starts) are its own. The host is killed at the end whatever happened.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from jarvis.platform.capabilities import detect_capabilities

pytestmark = pytest.mark.skipif(
    not detect_capabilities().has_pty, reason="no PTY backend on this machine"
)

WAIT_S = 30.0

#: The "agent": prints a marker, then echoes every line it is given.
CHILD_SOURCE = (
    "import sys\n"
    "print('hello-marker', flush=True)\n"
    "while True:\n"
    "    line = sys.stdin.readline()\n"
    "    if not line:\n"
    "        break\n"
    "    print('echo:' + line.strip(), flush=True)\n"
)


async def _until(check: Callable[[], bool], limit_s: float = WAIT_S) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_s
    while not check():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.05)


def _kill(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    with contextlib.suppress(OSError):
        # TerminateProcess on Windows; on POSIX the host's process group
        # containment takes its children with it.
        os.kill(pid, signal.SIGTERM)


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every per-user path at ``tmp_path``, and kill whatever host it held."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    from jarvis.terminal import pty_host_client

    state_path = pty_host_client._state_path()  # noqa: SLF001 - locating the test's own host
    assert tmp_path in state_path.parents, "the test must never reach the real user's host"
    yield state_path
    # Belt and braces: whatever host the state file names is the test's own.
    with contextlib.suppress(OSError, ValueError, AttributeError):
        _kill(int(json.loads(state_path.read_text(encoding="utf-8")).get("pid") or 0))


class Viewer:
    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.exits: list[int] = []

    async def output(self, _tid: str, text: str) -> None:
        self.chunks.append(text)

    async def closed(self, _tid: str, code: int) -> None:
        self.exits.append(code)

    @property
    def text(self) -> str:
        return "".join(self.chunks)


async def test_a_terminal_survives_a_detach_and_is_adopted_with_its_screen(
    isolated_data_dir: Path, tmp_path: Path
) -> None:
    from jarvis.terminal.pty_host_client import connect

    host_pids: list[int] = []
    first = await connect(start=True)
    if first is None:
        pytest.skip("the PTY host cannot run on this install")
    host_pids.append(first.host_pid)
    second = None
    try:
        assert first.connected
        assert first.hosted() == []
        assert await asyncio.to_thread(isolated_data_dir.is_file)

        one = Viewer()
        session = await first.spawn(
            (sys.executable, "-u", "-c", CHILD_SOURCE),
            "test",
            str(tmp_path),
            100,
            30,
            one.output,
            one.closed,
            meta={"history_id": "hist-xyz", "name": "T1"},
        )
        terminal_id = session.terminal_id
        await _until(lambda: "hello-marker" in one.text)
        assert first.write(terminal_id, "first\r")
        await _until(lambda: "echo:first" in one.text)

        # Quitting the app: detach, do not kill.
        first.close_all()
        assert not first.connected
        await asyncio.sleep(0.2)
        assert one.exits == [], "detaching must not report the agent as ended"

        second = await connect(start=False)
        assert second is not None, "the host must still be running"
        assert second.host_pid == first.host_pid
        hosted = second.hosted()
        assert [info.terminal_id for info in hosted] == [terminal_id]
        assert hosted[0].meta == {"history_id": "hist-xyz", "name": "T1"}
        assert (hosted[0].cols, hosted[0].rows) == (100, 30)

        two = Viewer()
        result = await second.adopt(terminal_id, two.output, two.closed)
        assert result.alive
        assert "hello-marker" in result.replay
        assert "echo:first" in result.replay
        assert second.hosted() == []
        assert second.has(terminal_id)

        assert second.write(terminal_id, "second\r")
        await _until(lambda: "echo:second" in two.text)

        assert second.close(terminal_id)
        await _until(lambda: len(two.exits) == 1)
        await _until(lambda: not second.has(terminal_id))
        assert one.exits == []
    finally:
        if second is not None:
            second.close_all()
        first.close_all()
        for pid in host_pids:
            _kill(pid)
