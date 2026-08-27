"""The model, the effort and the permission stance a pane actually runs on.

A picker that changes nothing is worse than no picker: it tells the person
their pane is on Opus while it answers on whatever the CLI defaults to. So
these drive the registry end to end and read the argv the PTY was handed —
not the intent, the result.

The second half is the trap the feature creates: the picks live on the pane,
and a pane comes BACK from a restart by being launched again. A restore that
forgot them would quietly move a conversation onto another model, which is the
kind of change nobody can see happening.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return Registry(pty_manager=fake_pty)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _attach(registry: Registry, name: str) -> None:
    await registry.attach(name, 80, 24, _noop, _noop_exit)


def _argv(fake_pty: FakePtyManager, index: int = 0) -> tuple[str, ...]:
    return tuple(fake_pty.spawns[index]["argv"])


async def test_the_picks_reach_the_command_line(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The whole point: pick Opus and the pane is started on Opus."""
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await registry.add_terminal(
        agent="claude",
        model="claude-opus-5",
        effort="high",
        permission_mode="acceptEdits",
    )
    await _attach(registry, registry.session.terminals[-1].name)
    argv = _argv(fake_pty)
    assert "--model" in argv and argv[argv.index("--model") + 1] == "claude-opus-5"
    assert "--effort" in argv and argv[argv.index("--effort") + 1] == "high"
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


async def test_a_pane_with_no_picks_is_launched_exactly_as_before(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Nobody who never opens the picker may notice this feature exists."""
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await _attach(registry, "T1")
    argv = _argv(fake_pty)
    assert "--model" not in argv
    assert "--effort" not in argv
    assert "--permission-mode" not in argv


async def test_the_wizard_opens_each_pane_on_its_own_picks(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await registry.start(
        str(tmp_path),
        [
            {"agent": "claude", "model": "claude-opus-5"},
            {"agent": "claude", "model": "claude-sonnet-5"},
        ],
    )
    panes = registry.session.terminals
    assert [p.model for p in panes] == ["claude-opus-5", "claude-sonnet-5"]


async def test_a_pick_this_cli_cannot_express_costs_only_itself(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A stale pick from a client must not be a pane that refuses to start."""
    await registry.start(str(tmp_path), [{"agent": "codex"}])
    pane = await registry.add_terminal(agent="codex", model="claude-opus-5")
    assert pane.model == ""
    await _attach(registry, pane.name)
    assert "claude-opus-5" not in _argv(fake_pty)


async def test_a_split_does_not_inherit_the_anchors_model(
    registry: Registry, tmp_path: Path
) -> None:
    """Splitting means "another one of these CLI", not "of these settings"."""
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    first = await registry.add_terminal(agent="claude", model="claude-opus-5")
    second = await registry.add_terminal(anchor=first.name)
    assert second.agent == "claude"
    assert second.model == ""


async def test_the_picks_survive_a_restart(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A restored pane is a RE-LAUNCH: without this it drops to the defaults.

    Resuming hands the CLI a conversation, never the model or the stance it
    ran under — so the snapshot has to carry them or a pane that comes back
    silently answers on something else.
    """
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    pane = await registry.add_terminal(
        agent="claude", model="claude-opus-5", permission_mode="plan"
    )
    snapshot = pane.to_snapshot()
    assert snapshot.model == "claude-opus-5"
    assert snapshot.permission_mode == "plan"
    from jarvis.agentic_ide.resume_store import SnapshotTerminal

    restored = SnapshotTerminal.from_dict(snapshot.to_dict())
    assert restored is not None
    assert restored.model == "claude-opus-5"
    assert restored.permission_mode == "plan"


async def test_the_workspace_says_what_a_pane_runs_on(
    registry: Registry, tmp_path: Path
) -> None:
    """A list of panes has to tell an Opus session from a Sonnet one.

    Without this the picks would be visible only on a command line nobody can
    read, and every surface that names what a session runs on would be back to
    guessing — which is where the pill saying "Default" over an Opus pane came
    from in the first place.
    """
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    pane = await registry.add_terminal(
        agent="claude", model="claude-opus-5", effort="max", permission_mode="plan"
    )
    row = pane.to_dict()
    assert row["model"] == "claude-opus-5"
    assert row["effort"] == "max"
    assert row["permission_mode"] == "plan"
    # A pane nobody picked for reports the CLI's own default, as it always did.
    assert registry.session.terminals[0].to_dict()["model"] == ""


async def test_an_older_snapshot_reopens_on_the_cli_default(tmp_path: Path) -> None:
    """A snapshot written before the picks existed still restores."""
    from jarvis.agentic_ide.resume_store import SnapshotTerminal

    restored = SnapshotTerminal.from_dict({"name": "T1", "agent": "claude"})
    assert restored is not None
    assert (restored.model, restored.effort, restored.permission_mode) == ("", "", "")
