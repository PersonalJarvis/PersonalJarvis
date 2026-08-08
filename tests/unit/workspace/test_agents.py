"""The workspace registry: specs, detection, and what each entry launches.

Detection runs against a fake prober — no real CLI is ever invoked.
"""
from __future__ import annotations

import asyncio

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

# The entries this build promises. A SUPERSET assertion, deliberately: pinning
# the exact set is what froze the registry at two providers and turned every
# later addition into a test edit, which is the opposite of what an open
# registry is for. What must not break is that a shipped provider silently
# disappears.
REQUIRED_CODING_AGENTS = frozenset({"claude", "codex", "opencode", "kimi", "glm"})


def test_every_promised_coding_agent_is_registered() -> None:
    assert REQUIRED_CODING_AGENTS <= set(coding_agent_names())
    # The historical constant keeps meaning "the coding agents": every existing
    # caller (the Make-It-Yours launcher, its PTY route) reads it that way.
    assert set(AGENT_NAMES) == set(coding_agent_names())
    # The plain terminal is not one of them.
    assert PLAIN_TERMINAL not in coding_agent_names()


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
    """A property every coding entry must hold — not a list of the two we had.

    The binary is deliberately NOT compared against a fixed set: one entry (the
    GLM coding plan) is a launch PROFILE over another entry's binary, which is
    the whole reason a provider with no CLI of its own can be offered at all.
    """
    for agent in list_agents():
        if not agent.is_coding_agent:
            continue
        assert isinstance(agent.spec, CliSpec)
        assert agent.spec.binary_name
        assert agent.spec.check_command[-1] == "--version"
        # The name to resolve on PATH always agrees with what detection probed;
        # a disagreement means a pane launches something the status card never
        # checked.
        assert agent.executable == agent.spec.binary_name


def test_install_commands_are_runnable_or_honestly_absent() -> None:
    """Every entry either yields a usable command or says it has none."""
    for name in coding_agent_names():
        command = install_command(name)
        assert command is None or command.strip()
    # The built-ins keep their exact commands: these are what the user is shown
    # and what gets run in a terminal, so a silent change is a real change.
    assert install_command("claude") == "npm install -g @anthropic-ai/claude-code"
    assert install_command("codex") == "npm install -g @openai/codex"
    assert install_command("opencode") == "npm install -g opencode-ai"
    assert install_command("kimi") == "npm install -g @moonshot-ai/kimi-code"
    # A launch profile installs the binary it borrows.
    assert install_command("glm") == "npm install -g @anthropic-ai/claude-code"
    assert install_command("nope") is None


def test_launch_command_is_bare_binary() -> None:
    assert get_agent("claude").launch_command == "claude"
    assert get_agent("codex").launch_command == "codex"
    assert get_agent("codex").resume_start_limit == 1
    assert get_agent("claude").resume_start_limit == 0
    assert get_agent("codex").input_markers == ("›", "»")
    assert get_agent("codex").requires_visible_input_cursor is True
    assert get_agent("opencode").launch_command == "opencode"
    # The profile runs the borrowed binary, not a command named after itself.
    assert get_agent("glm").launch_command == "claude"


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
    """A prober that answers for the entries a test names and no others.

    Missing names default to "not installed" rather than raising. A test about
    Claude Code has no business failing because a THIRD provider was registered
    since it was written — that turned every new entry into an edit of unrelated
    tests, which is exactly the friction an open registry is meant to remove.
    """

    def __init__(self, statuses: dict[str, CliStatus]) -> None:
        self._statuses = statuses

    async def probe_all(self, specs) -> dict[str, CliStatus]:  # noqa: ANN001
        return {
            s.name: self._statuses.get(s.name, CliStatus(installed=False))
            for s in specs
        }


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


class CountingProber:
    """Counts how many detection sweeps actually reach the machine."""

    def __init__(self) -> None:
        self.sweeps = 0

    async def probe_all(self, specs) -> dict[str, CliStatus]:  # noqa: ANN001
        self.sweeps += 1
        # Yield once, so concurrent callers really do overlap in the test.
        await asyncio.sleep(0)
        return {s.name: CliStatus(installed=True, version="1.0.0") for s in specs}


@pytest.fixture
def _no_cached_detection():
    """Run against a cold cache and leave one behind."""
    from jarvis.workspace import agents as registry

    registry.invalidate_agent_detection()
    yield
    registry.invalidate_agent_detection()


@pytest.mark.asyncio
async def test_detection_is_cached_between_reads(monkeypatch, _no_cached_detection) -> None:
    """A repeated read must not restart a subprocess per CLI.

    The sweep spawns one process per registered CLI — on Windows an npm shim,
    so cmd -> conhost -> node — and the shared event loop stalls while it runs.
    The Agentic-IDE view re-reads its state on every workspace change, and each
    of those used to pay for a fresh sweep, which the wake microphone (delivered
    on that same loop) paid for as added latency.
    """
    from jarvis.workspace import agents as registry

    prober = CountingProber()
    monkeypatch.setattr(registry, "CliStatusProber", lambda: prober)

    first = await detect_agents()
    second = await detect_agents()

    assert prober.sweeps == 1
    assert [i.name for i in first] == [i.name for i in second]


@pytest.mark.asyncio
async def test_concurrent_reads_share_one_sweep(monkeypatch, _no_cached_detection) -> None:
    """Six panes asking at once are six answers, not six subprocess bursts."""
    from jarvis.workspace import agents as registry

    prober = CountingProber()
    monkeypatch.setattr(registry, "CliStatusProber", lambda: prober)

    answers = await asyncio.gather(*(detect_agents() for _ in range(6)))

    assert prober.sweeps == 1
    assert all(a for a in answers)


@pytest.mark.asyncio
async def test_force_and_invalidate_reach_the_machine_again(
    monkeypatch, _no_cached_detection
) -> None:
    """A CLI installed while the app runs must not wait out the TTL."""
    from jarvis.workspace import agents as registry

    prober = CountingProber()
    monkeypatch.setattr(registry, "CliStatusProber", lambda: prober)

    await detect_agents()
    await detect_agents(force=True)
    registry.invalidate_agent_detection()
    await detect_agents()

    assert prober.sweeps == 3


@pytest.mark.asyncio
async def test_an_explicit_prober_never_reads_or_fills_the_cache(
    _no_cached_detection,
) -> None:
    """Handing over a prober is asking for THAT prober's answer."""
    from jarvis.workspace import agents as registry

    mine = FakeProber({"claude": CliStatus(installed=True, version="9.9.9")})
    infos = {i.name: i for i in await detect_agents(mine)}
    assert infos["claude"].version == "9.9.9"
    assert registry._detection_cache is None
