"""The workspace registry: specs, detection, and what each entry launches.

Detection runs against a fake prober — no real CLI is ever invoked.
"""
from __future__ import annotations

import pytest

from jarvis.clis.spec import CliSpec, CliStatus
from jarvis.workspace.agents import (
    AGENT_NAMES,
    PLAIN_TERMINAL,
    WorkspaceAgent,
    agent_names,
    build_agent_argv,
    build_install_argv,
    coding_agent_names,
    detect_agents,
    get_agent,
    install_command,
    list_agents,
    make_cli_agent,
    needs_trust,
    plain_terminal_argv,
    pty_available,
    register_agent,
)


def test_coding_agents_are_claude_and_codex() -> None:
    assert set(coding_agent_names()) == {"claude", "codex"}
    # The historical constant keeps meaning "the coding agents": every existing
    # caller (the Make-It-Yours launcher, its PTY route) reads it that way.
    assert set(AGENT_NAMES) == {"claude", "codex"}


def test_the_registry_also_holds_a_plain_terminal() -> None:
    assert PLAIN_TERMINAL in agent_names()
    shell = get_agent(PLAIN_TERMINAL)
    assert shell is not None
    assert shell.display_name == "Plain Terminal"
    # It is not an agent: nothing to detect, nothing to install, no trust
    # dialog to skip.
    assert shell.is_coding_agent is False
    assert shell.spec is None
    assert install_command(PLAIN_TERMINAL) is None
    assert needs_trust(PLAIN_TERMINAL) is False


def test_cli_specs_are_valid_clispecs() -> None:
    for agent in list_agents():
        if not agent.is_coding_agent:
            continue
        assert isinstance(agent.spec, CliSpec)
        assert agent.spec.binary_name in ("claude", "codex")
        assert agent.spec.check_command[-1] == "--version"


def test_install_commands_use_npm() -> None:
    assert install_command("claude") == "npm install -g @anthropic-ai/claude-code"
    assert install_command("codex") == "npm install -g @openai/codex"
    assert install_command("nope") is None


def test_launch_command_is_bare_binary() -> None:
    assert get_agent("claude").launch_command == "claude"
    assert get_agent("codex").launch_command == "codex"


def test_build_agent_argv_wraps_command_in_a_shell() -> None:
    argv = build_agent_argv("claude")
    assert argv is not None
    # the agent command appears in the argv, wrapped by a shell
    assert any("claude" in part for part in argv)
    assert len(argv) >= 2  # shell + at least one flag/command
    assert build_agent_argv("nope") is None


def test_plain_terminal_launches_the_shell_itself() -> None:
    """No agent is wrapped around it — the shell IS the process."""
    argv = build_agent_argv(PLAIN_TERMINAL)
    assert argv == plain_terminal_argv()
    assert argv is not None
    # A discovered shell's own interactive argv, and NOT the "run this command
    # then stay open" wrapper a CLI entry gets.
    assert argv[0]
    assert "-Command" not in argv
    assert "/k" not in argv
    assert not any("claude" in part or "codex" in part for part in argv)


def test_build_install_argv_uses_install_command() -> None:
    argv = build_install_argv("codex")
    assert argv is not None
    assert any("@openai/codex" in part for part in argv)
    # Nothing to install for a shell that is already there.
    assert build_install_argv(PLAIN_TERMINAL) is None


def test_pty_available_is_true_on_a_host_with_a_shell() -> None:
    # CI + dev hosts have a shell + a real PTY backend.
    assert pty_available() is True


class FakeProber:
    def __init__(self, statuses: dict[str, CliStatus]) -> None:
        self._statuses = statuses

    async def probe_all(self, specs) -> dict[str, CliStatus]:  # noqa: ANN001
        return {s.name: self._statuses[s.name] for s in specs}


@pytest.mark.asyncio
async def test_detect_reports_installed_and_version() -> None:
    prober = FakeProber(
        {
            "claude": CliStatus(installed=True, version="2.1.195"),
            "codex": CliStatus(installed=False, version=None),
        }
    )
    infos = {i.name: i for i in await detect_agents(prober)}
    assert infos["claude"].installed is True
    assert infos["claude"].version == "2.1.195"
    assert infos["codex"].installed is False
    assert infos["codex"].install_command == "npm install -g @openai/codex"


@pytest.mark.asyncio
async def test_detect_reports_the_plain_terminal_without_probing_it() -> None:
    """A shell cannot answer ``--version``, so it is never asked."""
    prober = FakeProber(
        {
            "claude": CliStatus(installed=False, version=None),
            "codex": CliStatus(installed=False, version=None),
        }
    )
    infos = {i.name: i for i in await detect_agents(prober)}
    shell = infos[PLAIN_TERMINAL]
    assert shell.kind == "shell"
    # Installed on any host with a shell — which every dev/CI machine is.
    assert shell.installed is True
    # Its "version" is the shell that would actually open.
    assert shell.version
    assert shell.install_command is None


@pytest.mark.asyncio
async def test_a_registered_cli_is_detected_and_launchable_like_the_built_ins() -> None:
    """Plugging in a new interactive CLI is one spec, not a code change."""
    entry = register_agent(
        make_cli_agent(
            "acme",
            "Acme Agent",
            binary="acme",
            npm_package="@acme/agent",
            homepage="https://example.invalid/acme",
        )
    )
    try:
        assert isinstance(entry, WorkspaceAgent)
        assert "acme" in agent_names()
        assert "acme" in coding_agent_names()
        assert install_command("acme") == "npm install -g @acme/agent"
        argv = build_agent_argv("acme")
        assert argv is not None and any("acme" in part for part in argv)

        prober = FakeProber(
            {
                "claude": CliStatus(installed=False, version=None),
                "codex": CliStatus(installed=False, version=None),
                "acme": CliStatus(installed=True, version="1.2.3"),
            }
        )
        infos = {i.name: i for i in await detect_agents(prober)}
        assert infos["acme"].installed is True
        assert infos["acme"].version == "1.2.3"
    finally:
        from jarvis.workspace import agents as registry

        registry._AGENTS.pop("acme", None)


def test_registering_a_taken_name_is_refused() -> None:
    """Two things answering to one name is a pane running the wrong tool."""
    with pytest.raises(ValueError):
        register_agent(make_cli_agent("codex", "Impostor", binary="nope"))
