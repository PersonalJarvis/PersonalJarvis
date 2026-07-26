"""Which subscription a pane actually runs on.

The switch is only worth anything if the pane's environment really carries it,
so these drive the registry end to end and read the environment the PTY was
handed — not the intent, the result.

The other half is the trap this feature could create: a user switches the
default while agents are running. A pane that silently followed would resume a
conversation on a plan whose history has never seen it, and the agent would come
back amnesiac with no explanation. Pinning the account at pane CREATION is what
prevents that, and it is pinned down here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis import agent_accounts
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


def _env_of(fake_pty: FakePtyManager, index: int = 0) -> dict[str, str] | None:
    return fake_pty.spawns[index]["env"]


# ------------------------------------------------------------------- pinning


async def test_a_pane_is_pinned_to_the_active_account_when_it_is_created(
    registry: Registry, tmp_path: Path
) -> None:
    second = agent_accounts.create_account("claude", "Second seat")
    agent_accounts.set_active("claude", second.id)
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    assert registry.session.terminals[0].account == second.id


async def test_a_pane_may_be_opened_on_an_account_that_is_not_the_active_one(
    registry: Registry, tmp_path: Path
) -> None:
    """This is what makes two subscriptions usable AT THE SAME TIME."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(
        str(tmp_path),
        [{"agent": "claude"}, {"agent": "claude", "account": second.id}],
    )
    panes = registry.session.terminals
    assert panes[0].account == agent_accounts.builtin_id("claude")
    assert panes[1].account == second.id


async def test_switching_the_default_does_not_move_a_running_pane(
    registry: Registry, tmp_path: Path
) -> None:
    """The whole reason the account is pinned at creation rather than read live."""
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    pinned = registry.session.terminals[0].account
    second = agent_accounts.create_account("claude", "Second seat")
    agent_accounts.set_active("claude", second.id)
    assert registry.session.terminals[0].account == pinned


async def test_a_new_pane_after_the_switch_gets_the_new_account(
    registry: Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    second = agent_accounts.create_account("claude", "Second seat")
    agent_accounts.set_active("claude", second.id)
    added = await registry.add_terminal(agent="claude", anchor=None)
    # No anchor was named, so the last pane is the anchor and its account is
    # inherited — the switch reaches the panes that DON'T inherit one.
    assert added.account == registry.session.terminals[0].account


async def test_a_split_stays_on_the_account_its_anchor_runs_on(
    registry: Registry, tmp_path: Path
) -> None:
    """Splitting must not quietly move the work onto a different bill."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    anchor = registry.session.terminals[0]
    split = await registry.add_terminal(anchor=anchor.name, direction="down")
    assert split.account == second.id


async def test_a_split_onto_a_different_cli_does_not_inherit_the_account(
    registry: Registry, tmp_path: Path
) -> None:
    """A Claude account id means nothing to Codex."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    anchor = registry.session.terminals[0]
    split = await registry.add_terminal(anchor=anchor.name, agent="codex")
    assert split.account == agent_accounts.builtin_id("codex")


async def test_an_unknown_account_falls_back_instead_of_failing_the_pane(
    registry: Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "account": "claude:ghost"}])
    assert registry.session.terminals[0].account == agent_accounts.builtin_id("claude")


# --------------------------------------------------------------- the spawn


async def test_a_pane_on_an_added_account_spawns_with_that_config_dir(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(str(tmp_path), [{"agent": "claude", "account": second.id}])
    await _attach(registry, registry.session.terminals[0].name)
    env = _env_of(fake_pty)
    assert env is not None
    assert env["CLAUDE_CONFIG_DIR"] == str(second.config_dir)


async def test_a_pane_on_the_builtin_account_spawns_exactly_as_it_always_did(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """No environment is passed at all — plain inheritance, byte for byte.

    This is the promise to everyone who never opens the switcher: adding the
    feature changed nothing about how their panes start.
    """
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    await _attach(registry, registry.session.terminals[0].name)
    assert _env_of(fake_pty) is None


async def test_the_spawn_environment_keeps_PATH(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A replaced environment would leave the agent binary unresolvable."""
    second = agent_accounts.create_account("codex", "Second plan")
    await registry.start(str(tmp_path), [{"agent": "codex", "account": second.id}])
    await _attach(registry, registry.session.terminals[0].name)
    env = _env_of(fake_pty)
    assert env is not None
    assert env.get("PATH")
    assert env["CODEX_HOME"] == str(second.config_dir)


async def test_two_panes_can_run_two_different_subscriptions_at_once(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The point of holding two plans, expressed as one assertion."""
    second = agent_accounts.create_account("claude", "Second seat")
    await registry.start(
        str(tmp_path),
        [{"agent": "claude"}, {"agent": "claude", "account": second.id}],
    )
    for term in registry.session.terminals:
        await _attach(registry, term.name)
    first_env, second_env = _env_of(fake_pty, 0), _env_of(fake_pty, 1)
    # The default pane inherits untouched; the second is pinned to its own seat.
    assert first_env is None
    assert second_env is not None
    assert second_env["CLAUDE_CONFIG_DIR"] == str(second.config_dir)


# ------------------------------------------------------------------- resume


async def test_a_pane_looks_for_its_conversation_in_its_own_account(
    registry: Registry, tmp_path: Path
) -> None:
    """The silent-amnesia bug: the right handle, searched in the wrong folder."""
    from jarvis.agentic_ide.agent_sessions import ResumeHandle, has_conversation

    second = agent_accounts.create_account("claude", "Second seat")
    handle = ResumeHandle(
        kind="claude_session", id="11111111-2222-3333-4444-555555555555", captured_at=0.0
    )
    projects = second.config_dir / "projects" / "some-repo"
    projects.mkdir(parents=True, exist_ok=True)
    (projects / f"{handle.id}.jsonl").write_text("{}\n", encoding="utf-8")

    # Found when asked about the account that holds it...
    assert has_conversation("claude", handle, second.config_dir) is True
    # ...and honestly absent from the default account, which never saw it.
    assert has_conversation("claude", handle, tmp_path / "elsewhere") is False


async def test_a_resumed_workspace_comes_back_on_the_same_accounts(
    registry: Registry, tmp_path: Path
) -> None:
    from jarvis.agentic_ide import resume_store

    second = agent_accounts.create_account("codex", "Second plan")
    snapshot = resume_store.Snapshot(
        session_id="s1",
        folder=str(tmp_path),
        saved_at=0.0,
        terminals=[
            resume_store.SnapshotTerminal(
                key="alex", name="Alex", agent="codex", account=second.id
            )
        ],
    )
    restored = resume_store.Snapshot.from_dict(snapshot.to_dict())
    assert restored is not None
    assert restored.terminals[0].account == second.id


async def test_an_older_snapshot_without_an_account_still_reopens() -> None:
    """A build that predates the switcher must keep resuming."""
    from jarvis.agentic_ide import resume_store

    restored = resume_store.SnapshotTerminal.from_dict(
        {"key": "alex", "name": "Alex", "agent": "claude", "column": 0, "slot": 0}
    )
    assert restored is not None
    assert restored.account is None
