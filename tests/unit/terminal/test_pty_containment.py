"""A closed pane takes the processes its agent started with it.

The defect these pin down: terminating a PTY child terminates that child and
nothing else. A coding CLI's MCP servers are separate processes that keep
running when their client disappears — they wait on a stdin nobody will ever
write to again — so every closed pane used to leave its whole server fleet
resident, and the machine's idle load climbed with each workspace opened.
Measured on a real install with the app already shut down: 102 ``node``, 59
``cmd`` and 89 ``conhost`` processes, all of them descendants of long-closed
panes.

So a session owns a kill-on-close container and everything it spawns lands in
it (``jarvis.core.process_tree``). What matters is the CONTRACT, pinned here
against a fake container so the tests run on every OS:

* a spawned child is put in a container before it can fork,
* closing the pane reaps it,
* an agent that exits BY ITSELF is reaped too — quitting a CLI orphans exactly
  the same servers as killing it,
* reaping happens once, not twice,
* and a host that cannot contain anything still gets a working terminal.
"""

from __future__ import annotations

import asyncio

import pytest

import jarvis.terminal.pty_manager as pty_mod
from jarvis.terminal.pty_manager import PtyManager
from tests.fakes.fake_pty_backend import FakePtyBackend


class FakeTree:
    """A ``ProcessTree`` that records what it was asked to hold and reap."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.assigned: list[int] = []
        self.closes = 0

    def assign(self, pid: int) -> None:
        self.assigned.append(pid)

    def close(self) -> None:
        self.closes += 1


class RefusingTree(FakeTree):
    """A container on a host where assignment does not work."""

    def assign(self, pid: int) -> None:
        raise OSError("no containment on this host")


@pytest.fixture
def trees(monkeypatch: pytest.MonkeyPatch) -> list[FakeTree]:
    made: list[FakeTree] = []

    def _make(name: str) -> FakeTree:
        tree = FakeTree(name)
        made.append(tree)
        return tree

    monkeypatch.setattr(pty_mod, "make_process_tree", _make)
    return made


async def _spawn(manager: PtyManager, backend: FakePtyBackend):
    async def on_output(_tid: str, _text: str) -> None:
        return None

    async def on_closed(_tid: str, _code: int) -> None:
        return None

    return await manager.spawn(
        shell_argv=("sh",),
        shell_id="pane-a",
        cwd=None,
        cols=80,
        rows=24,
        on_output=on_output,
        on_closed=on_closed,
    )


async def test_a_spawned_child_is_contained(
    monkeypatch: pytest.MonkeyPatch, trees: list[FakeTree]
) -> None:
    """The child is in a container, holding its real pid, before it runs."""
    backend = FakePtyBackend(pid=1234)
    monkeypatch.setattr(pty_mod, "make_pty_backend", lambda: backend)
    manager = PtyManager()

    session = await _spawn(manager, backend)

    assert len(trees) == 1
    assert trees[0].assigned == [1234]
    assert session.tree is trees[0]
    manager.close(session.terminal_id)


async def test_closing_a_pane_reaps_its_tree(
    monkeypatch: pytest.MonkeyPatch, trees: list[FakeTree]
) -> None:
    """This is the leak: the descendants have to go when the pane does."""
    backend = FakePtyBackend(pid=1234)
    monkeypatch.setattr(pty_mod, "make_pty_backend", lambda: backend)
    manager = PtyManager()
    session = await _spawn(manager, backend)

    assert trees[0].closes == 0
    manager.close(session.terminal_id)

    assert trees[0].closes == 1
    # Reaped once, not once per teardown path — the pump reaps too, and both
    # running would signal a process group whose id may since have been reused.
    await asyncio.sleep(0.05)
    assert trees[0].closes == 1


async def test_an_agent_that_quits_by_itself_is_reaped_too(
    monkeypatch: pytest.MonkeyPatch, trees: list[FakeTree]
) -> None:
    """Quitting a CLI orphans the same servers as killing it does.

    Nothing calls ``close`` in this case — the child simply ended — so the reap
    has to come from the path that notices the exit.
    """
    backend = FakePtyBackend(pid=1234, exitstatus=0)
    monkeypatch.setattr(pty_mod, "make_pty_backend", lambda: backend)
    manager = PtyManager()

    exited = asyncio.Event()

    async def on_output(_tid: str, _text: str) -> None:
        return None

    async def on_closed(_tid: str, _code: int) -> None:
        exited.set()

    session = await manager.spawn(
        shell_argv=("sh",),
        shell_id="pane-a",
        cwd=None,
        cols=80,
        rows=24,
        on_output=on_output,
        on_closed=on_closed,
    )
    # The child ends on its own — no ``manager.close``, nothing terminated from
    # this side. The handle simply stops being alive, which is all the reader
    # loop ever learns about an agent that quit.
    handle = backend.last_handle
    assert handle is not None
    handle.terminate(force=False)

    await asyncio.wait_for(exited.wait(), timeout=5.0)
    await asyncio.sleep(0.05)
    assert trees[0].closes == 1
    assert session.tree is None


async def test_a_host_without_containment_still_opens_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Containment is a safeguard. Its absence must not cost the user a pane."""
    backend = FakePtyBackend(pid=1234)
    monkeypatch.setattr(pty_mod, "make_pty_backend", lambda: backend)
    monkeypatch.setattr(pty_mod, "make_process_tree", RefusingTree)
    manager = PtyManager()

    session = await _spawn(manager, backend)

    assert manager.has(session.terminal_id)
    manager.close(session.terminal_id)
