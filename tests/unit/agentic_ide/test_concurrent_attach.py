"""One pane connected to twice at once still runs exactly ONE agent.

The defect these pin down (measured 2026-07-28). A pane is routinely attached
to more than once in the same instant: the panes of a restored workspace all
reconnect while the workspace is still opening, are answered "not yet", and
retry — and a retry that overlaps the attempt it replaces is two sockets asking
for the same pane. The spawn path awaits three times between asking "is a
process already running?" and recording the one it starts (a cold-start slot,
the account's filesystem work, the spawn itself), so the second attempt walked
through that gap and started a SECOND coding CLI for one call-sign.

What that cost, in the order the user notices it:

* **Black panes.** The newer spawn takes the viewer slot and clears the replay
  buffer, so the viewer that is actually on screen ends up attached to nothing.
  Its agent's transcript keeps filling in the backend while the pane shows an
  empty rectangle — and a coding CLI paints its interface once and then goes
  quiet, so nothing ever arrives to correct it.
* **A workspace that takes forever to open.** Fifteen CLIs booting through a
  gate built for nine.
* **Orphaned agents.** ``term.pty_id`` only ever points at the last one; the
  others run on, burning a subscription, with nothing left holding their ids.

The contract: concurrent attaches to ONE pane serialize and the later ones
re-join, exactly as the class docstring has always promised; attaches to
DIFFERENT panes stay concurrent, or opening a full workspace would queue every
cold start behind the slowest one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.agentic_ide import session as ide
from tests.fakes.fake_pty_manager import FakePtyManager


class SlowSpawnPool(FakePtyManager):
    """A pty pool whose spawn HOLDS, which is what makes the race reachable.

    A spawn that returns before the next caller is scheduled can never overlap
    with anything, so a fake that answers instantly would report this bug as
    fixed on the day it was written.
    """

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.peak = 0
        self.hold_s = 0.05

    async def spawn(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.hold_s:
                await asyncio.sleep(self.hold_s)
            return await super().spawn(*args, **kwargs)
        finally:
            self.in_flight -= 1


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> SlowSpawnPool:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    monkeypatch.setattr(ide, "COLD_START_SETTLE_S", 0.0)
    return SlowSpawnPool()


async def _sink(_text: str) -> None:
    return None


async def _gone(_code: int) -> None:
    return None


async def test_two_sockets_for_one_pane_start_one_agent(
    pool: SlowSpawnPool, tmp_path: Path
) -> None:
    """The whole bug in one assertion: one call-sign, one process."""
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]

    await asyncio.gather(
        registry.attach(term.key, 80, 24, _sink, _gone, workspace_id=session.id),
        registry.attach(term.key, 80, 24, _sink, _gone, workspace_id=session.id),
    )

    assert len(pool.spawns) == 1, (
        f"{len(pool.spawns)} agents were started for one pane — "
        "the second attach must re-join, not spawn"
    )
    assert pool.has(term.pty_id or ""), "the pane points at a live process"


async def test_a_burst_of_reconnects_starts_one_agent(pool: SlowSpawnPool, tmp_path: Path) -> None:
    """The shape the live incident had: a pane knocked at five times over."""
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]

    await asyncio.gather(
        *(
            registry.attach(term.key, 80, 24, _sink, _gone, workspace_id=session.id)
            for _ in range(5)
        )
    )

    assert len(pool.spawns) == 1, f"{len(pool.spawns)} agents for one pane"


async def test_the_later_socket_is_told_it_re_joined(pool: SlowSpawnPool, tmp_path: Path) -> None:
    """A pane that re-joined must not be reported as a fresh conversation.

    The three states look identical on screen and only differ when it matters,
    so a second socket that silently claimed a new conversation would tell the
    user their history is gone while the agent still holds all of it.
    """
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]

    results = await asyncio.gather(
        registry.attach(term.key, 80, 24, _sink, _gone, workspace_id=session.id),
        registry.attach(term.key, 80, 24, _sink, _gone, workspace_id=session.id),
    )

    assert results[0] is results[1], "both sockets hold the one pane"
    assert term.reattached is True, "the later socket re-joined a running agent"


async def test_the_pane_that_is_on_screen_still_receives_the_agent(
    pool: SlowSpawnPool, tmp_path: Path
) -> None:
    """The symptom, end to end: what the agent prints must reach the viewer.

    The viewer that attached LAST is the one holding the pane, and a screen the
    backend can describe while the browser shows an empty rectangle is precisely
    what a duplicate spawn produced.
    """
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    term = session.terminals[0]
    seen: list[str] = []

    async def watching(text: str) -> None:
        seen.append(text)

    await asyncio.gather(
        registry.attach(term.key, 80, 24, _sink, _gone, workspace_id=session.id),
        registry.attach(term.key, 80, 24, watching, _gone, workspace_id=session.id),
    )
    # Whichever of the two took the slot, the pane has ONE process and the
    # viewer it points at is the one being written to.
    if term.viewer_output is not watching:
        pytest.skip("the other socket took the slot; the ordering is not pinned here")
    await pool.emit(term.pty_id, "Claude Code v2.1.220\r\n")

    assert "".join(seen).endswith("Claude Code v2.1.220\r\n")
    assert term.transcript.lines(), "and the backend recorded the same screen"


async def test_different_panes_still_start_concurrently(
    pool: SlowSpawnPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock is per pane, not registry-wide.

    Serializing every attach would turn opening a workspace of a dozen agents
    into a dozen cold starts end to end — the exact cost `COLD_START_LIMIT`
    exists to shape rather than remove.
    """
    monkeypatch.setattr(ide, "COLD_START_LIMIT", 4)
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(4)])

    await asyncio.gather(
        *(
            registry.attach(term.key, 80, 24, _sink, _gone, workspace_id=session.id)
            for term in session.terminals
        )
    )

    assert len(pool.spawns) == 4, "every pane gets its own agent"
    assert pool.peak > 1, "different panes must still start at the same time"
