"""Opening a workspace starts its agents in waves, not all at once.

The defect these pin down: every pane of a workspace mounts together, connects
together and therefore used to launch its coding CLI in the same instant. A
CLI's start is the expensive part of a pane — it loads its plugins and hooks
and then starts one process per configured MCP server, most of them through
``npx``, which resolves a package before it runs one. On a real install that is
eleven servers at roughly two and a half processes each, so eight panes meant
over two hundred process starts inside a second: every core pinned, the machine
unresponsive, and the app too starved to draw the panes it was starting.

The same work still happens — only its shape changes. What must hold:

* no more than :data:`COLD_START_LIMIT` agents are STARTING at any moment,
* a pane re-joining an agent that never stopped is never made to wait behind
  one that is booting (switching workspaces has to stay instant),
* a spawn that FAILS gives its slot back immediately rather than making the
  panes behind it wait out a settle window for nothing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.agentic_ide import fleet_actions
from jarvis.agentic_ide import session as ide
from tests.fakes.fake_pty_manager import FakePtyManager


class CountingPtyManager(FakePtyManager):
    """A pty pool that reports how many spawns overlap in time.

    ``spawn`` holds for as long as the test tells it to, which is what makes
    "how many were in flight at once" observable at all — a spawn that returns
    instantly can never overlap with anything.
    """

    def __init__(self) -> None:
        # The base is a dataclass; its generated ``__init__`` builds the
        # recording fields, and the counters below are this subclass's own.
        super().__init__()
        self.in_flight = 0
        self.peak = 0
        self.hold_s = 0.0

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
def pool(monkeypatch: pytest.MonkeyPatch) -> CountingPtyManager:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return CountingPtyManager()


async def _attach_all(registry: ide.Registry, session) -> None:  # noqa: ANN001
    async def sink(_text: str) -> None:
        return None

    async def gone(_code: int) -> None:
        return None

    await asyncio.gather(
        *(
            registry.attach(term.key, 80, 24, sink, gone, workspace_id=session.id)
            for term in session.terminals
        )
    )


async def test_a_full_workspace_starts_in_waves(
    pool: CountingPtyManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole grid connecting at once must not become one starting storm."""
    monkeypatch.setattr(ide, "COLD_START_LIMIT", 3)
    monkeypatch.setattr(ide, "COLD_START_SETTLE_S", 0.0)
    pool.hold_s = 0.05
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"} for _ in range(12)]
    )

    await _attach_all(registry, session)

    assert len(pool.spawns) == 12, "every pane still gets its agent"
    assert pool.peak <= 3, f"{pool.peak} agents were starting at once"


async def test_codex_shared_store_starts_are_serial_until_the_input_line(
    pool: CountingPtyManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process existing does not release Codex's account-scoped boot slot."""
    monkeypatch.setattr(ide, "COLD_START_SETTLE_S", 0.0)
    monkeypatch.setattr(fleet_actions, "READY_POLL_S", 0.01)
    monkeypatch.setattr(fleet_actions, "READY_TIMEOUT_S", 1.0)
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(
        str(tmp_path),
        [
            {"agent": "codex", "name": "Cody"},
            {"agent": "codex", "name": "Cole"},
        ],
    )
    monkeypatch.setattr(ide, "has_conversation", lambda *_args, **_kwargs: True)
    for index, term in enumerate(session.terminals):
        term.resume = ide.ResumeHandle(
            kind="codex_rollout", id=f"resume-{index}", captured_at=0.0
        )

    mounting = asyncio.create_task(_attach_all(registry, session))
    for _ in range(50):
        if len(pool.spawns) == 1:
            break
        await asyncio.sleep(0.01)
    assert len(pool.spawns) == 1
    first = next(term for term in session.terminals if term.status == "live")
    await pool.emit(
        first.pty_id,
        "\x1b[2J\x1b[H\u203a Ask Codex anything\x1b[1;3H\x1b[?25h",
    )

    for _ in range(50):
        if len(pool.spawns) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(pool.spawns) == 2, "the next pane starts as soon as the first is writable"
    second = next(term for term in session.terminals if term is not first)
    await pool.emit(
        second.pty_id,
        "\x1b[2J\x1b[H\u203a Ask Codex anything\x1b[1;3H\x1b[?25h",
    )
    await asyncio.wait_for(mounting, timeout=1.0)


async def test_rejoining_a_running_agent_never_waits_for_a_starting_one(
    pool: CountingPtyManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switching workspaces re-joins agents; that path must not be gated.

    With the gate full of slow starts, a pane whose agent is already alive has
    to come back immediately — it is not starting anything.
    """
    monkeypatch.setattr(ide, "COLD_START_LIMIT", 1)
    monkeypatch.setattr(ide, "COLD_START_SETTLE_S", 0.0)
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}]
    )
    first, second = session.terminals

    async def sink(_text: str) -> None:
        return None

    async def gone(_code: int) -> None:
        return None

    # Both agents are up and running.
    await registry.attach(first.key, 80, 24, sink, gone, workspace_id=session.id)
    await registry.attach(second.key, 80, 24, sink, gone, workspace_id=session.id)
    spawned = len(pool.spawns)

    # Now make every START slow, and re-join one of the running agents.
    pool.hold_s = 5.0
    await asyncio.wait_for(
        registry.attach(first.key, 80, 24, sink, gone, workspace_id=session.id),
        timeout=2.0,
    )
    assert len(pool.spawns) == spawned, "a re-join must not start anything"


async def test_a_failed_start_hands_its_slot_back_at_once(
    pool: CountingPtyManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken pane must not hold up the ones queued behind it.

    The settle window exists to keep a HEALTHY agent's boot from overlapping
    with the next one. Nothing is booting after a failure, so waiting it out
    would delay every remaining pane for a pane that does not exist.
    """
    monkeypatch.setattr(ide, "COLD_START_LIMIT", 1)
    monkeypatch.setattr(ide, "COLD_START_SETTLE_S", 30.0)
    pool.spawn_error = "no pseudo-terminal on this host"
    registry = ide.Registry(pty_manager=pool)
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}]
    )

    async def sink(_text: str) -> None:
        return None

    async def gone(_code: int) -> None:
        return None

    for term in session.terminals:
        with pytest.raises(ide.SessionError):
            await asyncio.wait_for(
                registry.attach(term.key, 80, 24, sink, gone, workspace_id=session.id),
                timeout=2.0,
            )
