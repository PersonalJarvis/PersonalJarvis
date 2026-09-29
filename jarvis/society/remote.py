"""Run a society agent's work on another computer (a VPS, a local VM) over SSH.

An agent with ``computer_id`` set keeps its chat, memory and approvals here;
only the parts that touch a machine move: its shell commands
(:class:`SshShellBackend`) and, for CLI-seated agents, the coding CLI itself
(``jarvis.agent_chat.runner_cli``). Both reach the machine through
``jarvis.computers.service.get_service()`` — the same pinned-host-key SSH
every other feature uses, never a second connection layer.

The agent's remote workspace is ``~/jarvis-agents/<agent_id>`` on that
machine, created on demand. Local containment (``resolve_contained``) still
decides WHICH subfolder a command may use; this module only maps that
subfolder onto the remote workspace.
"""

from __future__ import annotations

import logging
import shlex
import time
from pathlib import Path, PurePosixPath
from typing import Any, Final

from .shell import (
    DEFAULT_TIMEOUT_S,
    MAX_TIMEOUT_S,
    OUTPUT_CAP_CHARS,
    ShellBackend,
    ShellResult,
    _cap,
    default_backend,
)

log = logging.getLogger(__name__)

#: The parent folder of every agent workspace on a remote computer.
REMOTE_ROOT: Final[str] = "jarvis-agents"


def remote_workspace_expr(agent_id: str, relative: str = "") -> str:
    """A shell expression for the agent's remote folder, ``$HOME``-anchored.

    ``$HOME`` stays outside the quotes so the remote shell expands it; the
    agent id and subfolder are quoted, so nothing the agent names is ever
    interpreted by that shell.
    """
    rel = (
        PurePosixPath(REMOTE_ROOT, agent_id, relative)
        if relative
        else PurePosixPath(REMOTE_ROOT, agent_id)
    )
    return '"$HOME"/' + shlex.quote(str(rel))


def wrap_in_workspace(agent_id: str, command: str, *, relative: str = "") -> str:
    """``mkdir -p <ws> && cd <ws> && <command>`` with prompt-free defaults."""
    folder = remote_workspace_expr(agent_id, relative)
    return f"mkdir -p {folder} && cd {folder} && export CI=1 NO_COLOR=1 && {command}"


class SshShellBackend:
    """Runs an agent's shell commands inside its workspace on another computer."""

    name: str = "ssh"

    def __init__(self, computer_id: str, agent_id: str, *, workspace: Path) -> None:
        self._computer_id = computer_id
        self._agent_id = agent_id
        self._workspace = Path(workspace)

    def _relative(self, cwd: Path) -> str:
        try:
            rel = Path(cwd).resolve().relative_to(self._workspace.resolve())
        except ValueError:
            # A folder outside the workspace maps to the workspace root.
            return ""
        text = rel.as_posix()
        return "" if text == "." else text

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ShellResult:
        from jarvis.computers.service import ComputerError, get_service

        timeout = max(1.0, min(float(timeout_s or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S))
        wrapped = wrap_in_workspace(self._agent_id, command, relative=self._relative(cwd))
        started = time.perf_counter()
        try:
            async with get_service().session(self._computer_id) as opened:
                from jarvis.computers.ssh import SshError, run_command

                try:
                    result = await run_command(opened, wrapped, timeout_s=timeout)
                except SshError as exc:
                    if exc.kind == "timeout":
                        return ShellResult(
                            output=f"Command timed out after {int(timeout)} s",
                            exit_code=None,
                            seconds=time.perf_counter() - started,
                            timed_out=True,
                        )
                    raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
        except ComputerError as exc:
            log.info("society: remote shell for %s failed: %s", self._agent_id, exc.message)
            return ShellResult(
                output=f"Could not reach the agent's computer: {exc.message}",
                exit_code=None,
                seconds=time.perf_counter() - started,
                failed_to_start=True,
            )
        text = (result.stdout + (("\n" + result.stderr) if result.stderr else "")).rstrip()
        return ShellResult(
            output=_cap(text, OUTPUT_CAP_CHARS),
            exit_code=result.exit_status,
            seconds=time.perf_counter() - started,
        )


def backend_for(agent: Any, workspace: Path) -> ShellBackend:
    """The shell an agent's commands run in: remote when it is placed on a computer."""
    computer_id = getattr(agent, "computer_id", None)
    if computer_id:
        return SshShellBackend(str(computer_id), str(agent.agent_id), workspace=workspace)
    return default_backend()


async def placement_for_session(session: Any) -> tuple[str, str] | None:
    """``(computer_id, agent_id)`` when a society chat's agent runs elsewhere.

    Society chats are ``society:<agent_id>``; any other surface, an unknown
    agent or an agent on this computer answers ``None`` (run locally).
    """
    if getattr(session, "surface", "") != "society":
        return None
    session_id = str(getattr(session, "session_id", "") or "")
    if not session_id.startswith("society:"):
        return None
    agent_id = session_id.split(":", 1)[1]
    from .runtime import current_runtime

    runtime = current_runtime()
    if runtime is None:
        return None
    agent = await runtime.roster.get(agent_id)
    computer_id = getattr(agent, "computer_id", None) if agent is not None else None
    return (str(computer_id), agent_id) if computer_id else None
