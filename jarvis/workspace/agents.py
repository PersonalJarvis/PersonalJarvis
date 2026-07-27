"""Local agent specs for the workspace launcher (Claude Code + Codex only).

These reuse ``CliSpec`` so we can drive the existing ``CliStatusProber`` for
detection, but they are deliberately NOT registered in the shared CLI catalog
(``jarvis/clis/catalog/seed_catalog.json``) — see ``__init__`` for why.

Each agent carries:
- a detection spec (binary name + ``--version`` + a semver regex),
- the install command shown/run when the binary is missing,
- the bare launch command run inside the terminal (trust is pre-seeded
  separately, so no flags are needed).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from jarvis.clis.prober import CliStatusProber
from jarvis.clis.spec import AuthConfig, CliSpec, InstallMethods, RiskConfig
from jarvis.terminal.shells import discover_shells

log = logging.getLogger(__name__)

# A semver anywhere in the --version output. Matches both
# "2.1.195 (Claude Code)" and "codex-cli 0.142.3".
_SEMVER_RE = r"(\d+\.\d+\.\d+)"


@dataclass(frozen=True, slots=True)
class WorkspaceAgent:
    """An agent the workspace launcher can detect, install, and launch."""

    name: str
    display_name: str
    spec: CliSpec
    # The bare command run inside each terminal. Trust is pre-seeded by
    # ``trust.py`` beforehand, so no permission/trust flags are needed.
    launch_command: str


def _make_spec(
    name: str, display_name: str, binary: str, npm_package: str, homepage: str
) -> CliSpec:
    return CliSpec(
        name=name,
        display_name=display_name,
        description=f"{display_name} coding-agent CLI.",
        homepage=homepage,
        binary_name=binary,
        check_command=(binary, "--version"),
        version_parse_regex=_SEMVER_RE,
        install=InstallMethods(npm_package=npm_package, recommended="npm"),
        # We only care whether the binary is installed; the agent handles its
        # own login interactively on first launch in the terminal.
        auth=AuthConfig(type="none"),
        risk=RiskConfig(default_tier="monitor"),
        category="agent",
    )


_AGENTS: dict[str, WorkspaceAgent] = {
    "claude": WorkspaceAgent(
        name="claude",
        display_name="Claude Code",
        spec=_make_spec(
            "claude",
            "Claude Code",
            binary="claude",
            npm_package="@anthropic-ai/claude-code",
            homepage="https://claude.com/claude-code",
        ),
        launch_command="claude",
    ),
    "codex": WorkspaceAgent(
        name="codex",
        display_name="Codex",
        spec=_make_spec(
            "codex",
            "Codex",
            binary="codex",
            npm_package="@openai/codex",
            homepage="https://github.com/openai/codex",
        ),
        launch_command="codex",
    ),
}

AGENT_NAMES: tuple[str, ...] = tuple(_AGENTS.keys())


def list_agents() -> list[WorkspaceAgent]:
    return list(_AGENTS.values())


def get_agent(name: str) -> WorkspaceAgent | None:
    return _AGENTS.get(name)


def install_command(name: str) -> str | None:
    """The shell command that installs the agent (for display + terminal run)."""
    agent = _AGENTS.get(name)
    if agent is None:
        return None
    pkg = agent.spec.install.npm_package
    return f"npm install -g {pkg}" if pkg else None


@dataclass(slots=True)
class AgentInfo:
    """Runtime status of one agent, returned by the /agents endpoint."""

    name: str
    display_name: str
    installed: bool
    version: str | None
    install_command: str | None
    launch_command: str


async def detect_agents(prober: CliStatusProber | None = None) -> list[AgentInfo]:
    """Probe both agents and return their install status."""
    prober = prober or CliStatusProber()
    statuses = await prober.probe_all([a.spec for a in _AGENTS.values()])
    out: list[AgentInfo] = []
    for agent in _AGENTS.values():
        st = statuses.get(agent.name)
        out.append(
            AgentInfo(
                name=agent.name,
                display_name=agent.display_name,
                installed=bool(st and st.installed),
                version=st.version if st else None,
                install_command=install_command(agent.name),
                launch_command=agent.launch_command,
            )
        )
    return out


def _build_pty_argv(command: str) -> tuple[str, ...] | None:
    """Wrap a command in the platform's default shell so it runs inside a PTY
    and the shell stays open afterwards (the user can re-run / keep typing)."""
    shells = discover_shells()
    if not shells:
        return None
    shell = shells[0]  # preference order: pwsh>powershell>cmd / $SHELL first
    path = shell.argv[0]
    if shell.id in ("pwsh", "powershell"):
        return (path, "-NoLogo", "-NoExit", "-Command", command)
    if shell.id == "cmd":
        return (path, "/k", command)
    # POSIX: run the command, then drop to an interactive shell.
    return (path, "-c", f"{command}; exec {path}")


def build_agent_argv(name: str) -> tuple[str, ...] | None:
    """Full PTY argv that launches the agent in a shell (trust pre-seeded)."""
    agent = _AGENTS.get(name)
    if agent is None:
        return None
    return _build_pty_argv(agent.launch_command)


def build_install_argv(name: str) -> tuple[str, ...] | None:
    """Full PTY argv that runs the agent's install command in a shell."""
    cmd = install_command(name)
    return _build_pty_argv(cmd) if cmd else None


def pty_available() -> bool:
    """True when this host can run an in-app PTY (a shell + a real PTY backend).

    Works on a headless Linux VPS too — a PTY is a kernel feature, not a GUI —
    which is why the in-app terminal grid fits the cloud-first doctrine better
    than spawning OS terminal windows."""
    if not discover_shells():
        return False
    try:
        from jarvis.terminal.backend import make_pty_backend

        return type(make_pty_backend()).__name__ != "NullPtyBackend"
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "AGENT_NAMES",
    "WorkspaceAgent",
    "AgentInfo",
    "list_agents",
    "get_agent",
    "install_command",
    "detect_agents",
    "build_agent_argv",
    "build_install_argv",
    "pty_available",
]
