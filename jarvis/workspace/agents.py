"""What a workspace terminal can RUN — a small, open registry.

Two kinds of entry live here, and the difference is the whole design:

* **A coding-agent CLI** (Claude Code, Codex, whatever is registered next) — a
  binary that has to be detected, may have to be installed, and is launched by
  name inside a shell.
* **A plain terminal** — this machine's own shell and nothing else. Nothing to
  detect beyond "does this host have a shell", nothing to install, and no agent
  wrapped around it. It is what you want when the job is `git rebase -i`, not
  "ask an agent to do it".

The registry is deliberately open (:func:`register_agent`): a new interactive
CLI is a spec, not a code change spread across detection, launching and the UI.
Everything downstream — the ``/agents`` endpoints, the pane split menu, the
Agentic-IDE grid — reads this list rather than a hardcoded pair of names, so
registering one entry is enough to make it offerable everywhere.

CLI entries reuse ``CliSpec`` so the existing ``CliStatusProber`` can detect
them, but they are deliberately NOT registered in the shared CLI catalog
(``jarvis/clis/catalog/seed_catalog.json``) — see ``__init__`` for why.

Cross-platform: the shell entry resolves through
:func:`jarvis.terminal.shells.default_shell`, which knows pwsh/PowerShell/cmd/
Git Bash on Windows and ``$SHELL``/``/etc/shells`` elsewhere. On a host with no
shell at all (a stripped container) the entry reports itself as not installed
rather than handing the PTY an argv that cannot start.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from jarvis.clis.prober import CliStatusProber
from jarvis.clis.spec import AuthConfig, CliSpec, InstallMethods, RiskConfig
from jarvis.terminal.shells import default_shell

log = logging.getLogger(__name__)

# A semver anywhere in the --version output. Matches both
# "2.1.195 (Claude Code)" and "codex-cli 0.142.3".
_SEMVER_RE = r"(\d+\.\d+\.\d+)"

# The same, with the patch component optional. Not every CLI ships three parts:
# the Python-generation Kimi answers ``kimi, version 1.3``, which the strict
# pattern above misses entirely — and a missed version is not a cosmetic loss.
# It reads downstream as ``version=None``, which the wizard and the resume offer
# render as a blank field next to an installed binary, i.e. exactly what a
# broken install looks like. Entries whose CLI numbers itself in two parts opt
# into this instead.
_LOOSE_SEMVER_RE = r"(\d+\.\d+(?:\.\d+)?)"

# The registry key of the plain-shell entry. A name rather than a literal
# everywhere, because several layers (the IDE session registry, the pane split
# menu, the tests) have to agree on it.
PLAIN_TERMINAL = "shell"


@dataclass(frozen=True, slots=True)
class WorkspaceAgent:
    """One thing a workspace terminal can run.

    ``kind`` decides how every question about it is answered:

    * ``"cli"`` — detect with ``spec``, install with ``spec.install``, launch
      ``launch_command`` inside a shell.
    * ``"shell"`` — no detection spec and no launch command; the terminal IS the
      host's shell, so "installed" means "this host has one".

    ``needs_trust`` says whether the folder has to be pre-marked as trusted for
    this entry before a pane opens (``jarvis.workspace.trust``). A shell has no
    trust dialog to skip, and neither does a CLI that never asks.
    """

    name: str
    display_name: str
    kind: str = "cli"
    # Detection spec — CLI entries only.
    spec: CliSpec | None = None
    # The bare command run inside each terminal, for CLI entries. Trust is
    # pre-seeded by ``trust.py`` beforehand, so no permission/trust flags are
    # needed.
    launch_command: str | None = None
    needs_trust: bool = True
    # One line the UI can show under the name in a picker.
    description: str = ""

    @property
    def is_coding_agent(self) -> bool:
        """True for an entry that runs an agent rather than a bare shell."""
        return self.kind == "cli"


def make_cli_agent(
    name: str,
    display_name: str,
    *,
    binary: str,
    npm_package: str = "",
    homepage: str = "",
    launch_command: str | None = None,
    description: str = "",
) -> WorkspaceAgent:
    """Build a registrable entry for an interactive CLI.

    The convenience path for plugging in a new coding agent: give it a name, a
    binary and (optionally) the npm package that installs it, and detection,
    the install command and the launch argv all follow. A CLI installed some
    other way simply passes no package — it is then detected and launched, and
    reports "not installed" without offering an install command it does not
    have.
    """
    spec = CliSpec(
        name=name,
        display_name=display_name,
        description=description or f"{display_name} coding-agent CLI.",
        homepage=homepage,
        binary_name=binary,
        check_command=(binary, "--version"),
        version_parse_regex=_SEMVER_RE,
        install=InstallMethods(npm_package=npm_package or None, recommended="npm"),
        # We only care whether the binary is installed; the agent handles its
        # own login interactively on first launch in the terminal.
        auth=AuthConfig(type="none"),
        risk=RiskConfig(default_tier="monitor"),
        category="agent",
    )
    return WorkspaceAgent(
        name=name,
        display_name=display_name,
        kind="cli",
        spec=spec,
        launch_command=launch_command or binary,
        description=description,
    )


_AGENTS: dict[str, WorkspaceAgent] = {
    "claude": make_cli_agent(
        "claude",
        "Claude Code",
        binary="claude",
        npm_package="@anthropic-ai/claude-code",
        homepage="https://claude.com/claude-code",
    ),
    "codex": make_cli_agent(
        "codex",
        "Codex",
        binary="codex",
        npm_package="@openai/codex",
        homepage="https://github.com/openai/codex",
    ),
    PLAIN_TERMINAL: WorkspaceAgent(
        name=PLAIN_TERMINAL,
        display_name="Plain Terminal",
        kind="shell",
        needs_trust=False,
        description="This machine's own shell — no agent, just a prompt.",
    ),
}


def register_agent(agent: WorkspaceAgent, *, replace: bool = False) -> WorkspaceAgent:
    """Add ``agent`` to the registry and return it.

    Refuses to overwrite an existing name unless ``replace`` is set: two
    different things answering to "codex" is the kind of collision that shows up
    as a pane running the wrong tool, which is worse than an early error.
    """
    if not agent.name:
        raise ValueError("A workspace agent needs a name.")
    if agent.name in _AGENTS and not replace:
        raise ValueError(f"A workspace agent called {agent.name!r} is already registered.")
    _AGENTS[agent.name] = agent
    _forget_command_catalog()
    return agent


def _forget_command_catalog() -> None:
    """Drop the cached command catalog, which spells this registry out.

    The voice/LLM schema for "open a pane running X" lists the registered
    coding CLIs by name, and that catalog is built once and cached. An agent
    registered after the first build would therefore be openable from the UI,
    resumable, promptable — and invisible to the one surface meant to drive it.
    Never raises: the catalog is optional to this module, and a registration
    that fails because of it would be the worse outcome.
    """
    try:
        from jarvis.commands.registry import get_registry

        get_registry.cache_clear()
    except Exception:  # noqa: BLE001 - the catalog is not this module's job
        return


def list_agents() -> list[WorkspaceAgent]:
    """Every registered entry, in registration order."""
    return list(_AGENTS.values())


def get_agent(name: str) -> WorkspaceAgent | None:
    return _AGENTS.get(name)


def agent_names() -> tuple[str, ...]:
    """Every registered name, plain terminal included."""
    return tuple(_AGENTS)


def coding_agent_names() -> tuple[str, ...]:
    """Only the entries that run a coding agent — no plain shell.

    The "Make It Yours" launcher plans a grid of agents that extend Jarvis, so
    it asks this rather than :func:`agent_names`.
    """
    return tuple(name for name, agent in _AGENTS.items() if agent.is_coding_agent)


def needs_trust(name: str) -> bool:
    """Does opening this entry require pre-seeding folder trust?"""
    agent = _AGENTS.get(name)
    return bool(agent and agent.needs_trust)


# Kept as a module constant for the many call sites that read it directly. It is
# a snapshot of the CODING agents at import time, which is what every existing
# reader means by "the agents" — a plain terminal is not one, and a dynamically
# registered CLI is reachable through ``coding_agent_names()``.
AGENT_NAMES: tuple[str, ...] = coding_agent_names()


def install_command(name: str) -> str | None:
    """The shell command that installs the agent (for display + terminal run)."""
    agent = _AGENTS.get(name)
    if agent is None or agent.spec is None:
        return None
    pkg = agent.spec.install.npm_package
    return f"npm install -g {pkg}" if pkg else None


@dataclass(slots=True)
class AgentInfo:
    """Runtime status of one entry, returned by the /agents endpoints."""

    name: str
    display_name: str
    installed: bool
    version: str | None
    install_command: str | None
    launch_command: str
    kind: str = "cli"
    description: str = ""


async def detect_agents(prober: CliStatusProber | None = None) -> list[AgentInfo]:
    """Probe every registered entry and report what this machine can run.

    CLI entries go through the shared prober; the plain terminal answers from
    :func:`~jarvis.terminal.shells.default_shell`, because "is a shell
    installed" is not a question a ``--version`` probe can ask. Its reported
    version is the shell that would actually open ("PowerShell 7", "zsh"), which
    is the one thing a user picking it wants to know.
    """
    prober = prober or CliStatusProber()
    specs = [a.spec for a in _AGENTS.values() if a.spec is not None]
    statuses = await prober.probe_all(specs) if specs else {}
    shell = default_shell()
    out: list[AgentInfo] = []
    for agent in _AGENTS.values():
        if agent.spec is None:
            out.append(
                AgentInfo(
                    name=agent.name,
                    display_name=agent.display_name,
                    installed=shell is not None,
                    version=shell.label if shell else None,
                    install_command=None,
                    launch_command="",
                    kind=agent.kind,
                    description=agent.description,
                )
            )
            continue
        st = statuses.get(agent.name)
        out.append(
            AgentInfo(
                name=agent.name,
                display_name=agent.display_name,
                installed=bool(st and st.installed),
                version=st.version if st else None,
                install_command=install_command(agent.name),
                launch_command=agent.launch_command or "",
                kind=agent.kind,
                description=agent.description,
            )
        )
    return out


def _build_pty_argv(command: str) -> tuple[str, ...] | None:
    """Wrap a command in the platform's default shell so it runs inside a PTY
    and the shell stays open afterwards (the user can re-run / keep typing)."""
    shell = default_shell()  # pwsh>powershell>cmd / $SHELL first
    if shell is None:
        return None
    path = shell.argv[0]
    if shell.id in ("pwsh", "powershell"):
        return (path, "-NoLogo", "-NoExit", "-Command", command)
    if shell.id == "cmd":
        return (path, "/k", command)
    # POSIX: run the command, then drop to an interactive shell.
    return (path, "-c", f"{command}; exec {path}")


def plain_terminal_argv() -> tuple[str, ...] | None:
    """Full PTY argv for a plain shell session — no agent wrapped around it.

    The shell's OWN interactive argv, exactly as ``discover_shells()`` describes
    it, rather than the ``-Command``/``/k`` wrapper CLI entries need: there is no
    command to run first, and adding one would only cost the user a startup line
    they did not ask for.
    """
    shell = default_shell()
    return tuple(shell.argv) if shell is not None else None


def build_agent_argv(name: str) -> tuple[str, ...] | None:
    """Full PTY argv that opens this entry, or None when it cannot run here.

    A CLI is launched inside a shell (trust pre-seeded); the plain terminal IS
    the shell.
    """
    agent = _AGENTS.get(name)
    if agent is None:
        return None
    if agent.launch_command is None:
        return plain_terminal_argv()
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
    if default_shell() is None:
        return False
    try:
        from jarvis.terminal.backend import make_pty_backend

        return type(make_pty_backend()).__name__ != "NullPtyBackend"
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "AGENT_NAMES",
    "PLAIN_TERMINAL",
    "AgentInfo",
    "WorkspaceAgent",
    "agent_names",
    "build_agent_argv",
    "build_install_argv",
    "coding_agent_names",
    "detect_agents",
    "get_agent",
    "install_command",
    "list_agents",
    "make_cli_agent",
    "needs_trust",
    "plain_terminal_argv",
    "pty_available",
    "register_agent",
]
