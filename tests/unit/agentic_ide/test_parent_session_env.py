"""A pane runs as its CLI's OWN session, never as a child of the app's launcher.

The failure these pin down is silent and total, and it only shows up a restart
later. The app is regularly started from inside a coding CLI — a contributor
running it from an agent's terminal, and the in-app restart, which hands the new
process its predecessor's environment and therefore carries one such launch
forward forever. Claude Code finds ``CLAUDE_CODE_CHILD_SESSION`` in a pane's
environment, concludes it is a nested run of itself, and switches its transcript
off: "Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION".

Nothing looks wrong at that point. The pane works, the restore point is written,
and every pane in it holds a session id. The bill arrives on the next restart:
there is no conversation on disk behind any of those ids, so every pane comes
back with a blank history (found 2026-07-28 — a whole morning's work in five
panes, not one transcript written).

So the environment is asserted at the spawn: what a pane must NOT carry, and —
just as important — what it must still get, because a sweep of the whole
``CLAUDE_*`` namespace would take the login with it.
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


async def _spawn_one(registry: Registry, folder: Path, **pane: object) -> dict[str, str] | None:
    await registry.start(str(folder), [{"agent": "claude", **pane}])
    term = registry.session.terminals[0]
    await registry.attach(term.name, 80, 24, _noop, _noop_exit)
    manager = registry._manager()  # noqa: SLF001 - the spawn record is the assertion
    assert isinstance(manager, FakePtyManager)
    return manager.spawns[0]["env"]


async def test_a_pane_does_not_inherit_the_launching_agents_session(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one that costs the conversation: no transcript, so nothing to resume."""
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "a3f1-parent")
    monkeypatch.setenv("CLAUDECODE", "1")

    env = await _spawn_one(registry, tmp_path)

    assert env is not None, "a parent session must force an explicit environment"
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert "CLAUDECODE" not in env


async def test_stripping_keeps_the_login_and_the_path(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``CLAUDE_*`` prefix sweep would open a pane with no credential at all."""
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-not-a-real-token")
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "8192")

    env = await _spawn_one(registry, tmp_path)

    assert env is not None
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-not-a-real-token"  # noqa: S105
    # A setting the user exports for every terminal they open is theirs, not a
    # session marker.
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8192"
    assert env.get("PATH")


async def test_an_added_account_keeps_its_redirection_while_being_stripped(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves land: the account this module points the CLI at, and the strip.

    The two are written by different code paths and the strip runs last, so this
    is where a fix for one could quietly undo the other.
    """
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    second = agent_accounts.create_account("claude", "Second seat")

    env = await _spawn_one(registry, tmp_path, account=second.id)

    assert env is not None
    assert env["CLAUDE_CONFIG_DIR"] == str(second.config_dir)
    assert "CLAUDE_CODE_CHILD_SESSION" not in env


async def test_a_codex_pane_is_not_confined_to_the_parents_sandbox(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same class, other CLI: a pane inside a parent's sandbox refuses real work."""
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")

    await registry.start(str(tmp_path), [{"agent": "codex"}])
    term = registry.session.terminals[0]
    await registry.attach(term.name, 80, 24, _noop, _noop_exit)
    manager = registry._manager()  # noqa: SLF001
    assert isinstance(manager, FakePtyManager)
    env = manager.spawns[0]["env"]

    assert env is not None
    assert "CODEX_SANDBOX" not in env
    assert "CODEX_SANDBOX_NETWORK_DISABLED" not in env


def test_no_credential_or_config_pointer_is_on_the_strip_list() -> None:
    """The list may only ever hold markers of a RUNNING session.

    Spelled out as a test because the tempting "just sweep ``CLAUDE_*``" edit
    passes every other assertion in this file while opening every pane logged
    out, or pointing it back at the machine's default account.
    """
    forbidden = {
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "PATH",
    }
    assert not (session_mod.PARENT_AGENT_SESSION_VARS & forbidden)
