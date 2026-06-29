"""Agent specs + detection (with a fake prober — no real CLIs invoked)."""
from __future__ import annotations

import pytest

from jarvis.clis.spec import CliSpec, CliStatus
from jarvis.workspace.agents import (
    AGENT_NAMES,
    build_agent_argv,
    build_install_argv,
    detect_agents,
    get_agent,
    install_command,
    list_agents,
    pty_available,
)


def test_only_claude_and_codex() -> None:
    assert set(AGENT_NAMES) == {"claude", "codex"}


def test_specs_are_valid_clispecs() -> None:
    for agent in list_agents():
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


def test_build_install_argv_uses_install_command() -> None:
    argv = build_install_argv("codex")
    assert argv is not None
    assert any("@openai/codex" in part for part in argv)


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
