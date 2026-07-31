"""A pane that runs this machine's shell instead of a coding agent.

Three things have to hold, and each one has bitten a terminal product before:

1. A plain terminal launches the SHELL, not a shell wrapped around an agent.
2. It is a first-class pane — its own process, its own call-sign, closable and
   countable like any other.
3. Jarvis never types into it. A plain terminal is a live shell prompt, so an
   injected line would not be read, it would RUN — which would turn the one
   keystroke channel voice can reach into arbitrary command execution.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import PLAIN_TERMINAL, Registry, SessionError
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    # Both coding CLIs "installed", so these tests do not depend on the machine
    # running them. The plain terminal keeps its real argv resolution — that is
    # part of what is under test.
    real_argv = session_mod.agent_argv

    def _argv(name: str):
        return (f"/usr/bin/{name}",) if name in session_mod.AGENT_BINARIES else real_argv(name)

    monkeypatch.setattr(session_mod, "agent_argv", _argv)
    return Registry(pty_manager=fake_pty)


# --------------------------------------------------------------- what it runs
def test_it_is_offered_as_a_runnable_that_takes_no_prompts() -> None:
    assert session_mod.is_runnable(PLAIN_TERMINAL)
    assert session_mod.agent_display(PLAIN_TERMINAL) == "Plain Terminal"
    assert session_mod.accepts_prompts(PLAIN_TERMINAL) is False
    assert session_mod.accepts_prompts("claude") is True


def test_argv_is_the_host_shell_with_no_agent_wrapped_around_it() -> None:
    from jarvis.terminal.shells import default_shell

    shell = default_shell()
    assert shell is not None, "the test host has no shell"
    assert session_mod.agent_argv(PLAIN_TERMINAL) == tuple(shell.argv)


# ------------------------------------------------------------- as a real pane
@pytest.mark.asyncio
async def test_a_split_can_open_one_beside_an_agent(
    registry: Registry, tmp_path: Path, fake_pty: FakePtyManager
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Alex"}])
    term = await registry.add_terminal(anchor="Alex", direction="right", agent=PLAIN_TERMINAL)

    assert term.agent == PLAIN_TERMINAL
    assert term.display_name == "Plain Terminal"
    # A pane of its own, not a view of the agent's: its own call-sign, its own
    # column, and no account to bill.
    assert term.name != "Alex"
    assert term.column == 1
    assert term.account is None
    assert len(registry.session.terminals) == 2


@pytest.mark.asyncio
async def test_it_spawns_its_own_process(
    registry: Registry, tmp_path: Path, fake_pty: FakePtyManager
) -> None:
    """Every terminal is a separate child — the shell pane included."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Alex"}])
    plain = await registry.add_terminal(anchor="Alex", direction="right", agent=PLAIN_TERMINAL)

    async def _out(_text: str) -> None: ...

    async def _exit(_code: int) -> None: ...

    agent_pane = await registry.attach("alex", 80, 24, _out, _exit)
    shell_pane = await registry.attach(plain.key, 80, 24, _out, _exit)

    assert agent_pane.pty_id and shell_pane.pty_id
    assert agent_pane.pty_id != shell_pane.pty_id
    assert shell_pane.status == "live"
    # ...and it is the shell that was started, not a coding CLI.
    argv = " ".join(fake_pty.spawns[-1]["argv"])
    assert "claude" not in argv
    assert "codex" not in argv


@pytest.mark.asyncio
async def test_a_workspace_can_be_opened_with_one_from_the_start(
    registry: Registry, tmp_path: Path
) -> None:
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": PLAIN_TERMINAL}]
    )
    assert [t.agent for t in session.terminals] == ["claude", PLAIN_TERMINAL]


# -------------------------------------------------------- the injection guard
@pytest.mark.asyncio
async def test_jarvis_refuses_to_type_into_a_shell(
    registry: Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Alex"}])
    plain = await registry.add_terminal(anchor="Alex", direction="right", agent=PLAIN_TERMINAL)

    async def _out(_text: str) -> None: ...

    async def _exit(_code: int) -> None: ...

    await registry.attach(plain.key, 80, 24, _out, _exit)
    assert plain.status == "live"  # refused because of WHAT it is, not its state

    with pytest.raises(SessionError) as excinfo:
        await registry.send_prompt(plain.name, "rm -rf /")
    assert "shell" in str(excinfo.value).lower()
    assert plain.prompts_sent == 0


@pytest.mark.asyncio
async def test_the_pane_payload_says_it_takes_no_prompts(
    registry: Registry, tmp_path: Path
) -> None:
    """The UI needs this to keep such a pane out of the prompt bar's targets."""
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": PLAIN_TERMINAL}]
    )
    payload = {t["agent"]: t for t in session.to_dict()["terminals"]}
    assert payload["claude"]["accepts_prompts"] is True
    assert payload[PLAIN_TERMINAL]["accepts_prompts"] is False


@pytest.mark.asyncio
async def test_a_fleet_order_skips_it_with_an_honest_reason(
    registry: Registry, tmp_path: Path
) -> None:
    """Naming one in "everyone, do X" costs neither a composition nor a crash."""
    from jarvis.agentic_ide import fanout

    session = await registry.start(
        str(tmp_path),
        [{"agent": "claude", "name": "Alex"}, {"agent": PLAIN_TERMINAL, "name": "Nova"}],
    )

    composed: list[str] = []

    async def _compose(utterance: str, **kwargs):  # noqa: ANN001, ANN202
        composed.append(kwargs.get("terminal_name", ""))
        raise AssertionError("a plain terminal must never reach composition")

    result = await fanout.deliver(
        session=session,
        terminals=["Nova"],
        utterance="run the tests",
        compose=_compose,
        send=lambda name, text: None,  # type: ignore[arg-type]
    )
    assert composed == []
    assert result.undelivered
    assert result.undelivered[0].reason_code == "not_an_agent"
