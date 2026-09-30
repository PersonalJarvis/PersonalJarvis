"""Run a coding-agent CLI turn on another computer over SSH.

A society agent placed on a connected computer (``computer_id``) runs its CLI
there: the turn is planned exactly as a local one (``runner_cli`` planners),
the argv is rewritten for the remote machine, and the process is started over
the computer's pinned SSH connection. :class:`RemoteCliProcess` looks like the
``asyncio.subprocess.Process`` the local path uses — ``stdin`` / ``stdout`` /
``stderr`` / ``wait`` / ``kill`` / ``returncode`` — so the stream translation,
approval control protocol and error handling in ``runner_cli`` stay one code
path for both.

What does NOT travel:

* Local paths and secrets. The environment is an allow-list of a few
  prompt-suppressing variables; the CLI uses the login it has on the remote
  machine (``claude login`` / ``codex login`` there), never this box's.
* Jarvis' own MCP tools. That endpoint listens on this computer's localhost
  and a remote CLI cannot reach it, so ``--mcp-config`` / the Codex
  ``mcp_servers`` overrides are dropped for remote turns.
"""

from __future__ import annotations

import contextlib
import logging
import shlex
from collections.abc import Sequence
from typing import Any, Final

log = logging.getLogger(__name__)

#: Environment variables a remote CLI turn carries: prompt- and colour-free
#: output only. Everything else in the local env (config dirs, account homes,
#: API keys) describes THIS machine and must not leak to the remote one.
REMOTE_ENV_ALLOW: Final[frozenset[str]] = frozenset({"NO_COLOR", "TERM", "LANG", "LC_ALL"})

#: Leading argv parts that are how THIS box launches a CLI (interpreters,
#: shell wrappers) rather than the CLI's own arguments.
_LAUNCHER_PARTS: Final[frozenset[str]] = frozenset(
    {
        "node",
        "node.exe",
        "cmd",
        "cmd.exe",
        "/c",
        "/d",
        "/s",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "-file",
        "-noprofile",
        "-executionpolicy",
        "bypass",
        "-noninteractive",
    }
)

_LOCAL_ONLY_FLAGS_WITH_VALUE: Final[frozenset[str]] = frozenset({"--mcp-config"})


class RemoteCliUnavailable(RuntimeError):
    """The remote machine cannot run this CLI; ``str(exc)`` is user-facing."""


def _looks_like_launcher(part: str) -> bool:
    low = part.lower()
    if low in _LAUNCHER_PARTS:
        return True
    if "/" in part or "\\" in part:
        return True  # a local absolute path to a binary, shim or cli.js
    return low.endswith((".exe", ".cmd", ".bat", ".ps1", ".js", ".mjs"))


def remote_argv(
    argv: Sequence[str],
    *,
    binary: str,
    local_cwd: str,
    remote_cwd: str,
    system_prompt_files: dict[str, str] | None = None,
) -> list[str]:
    """Rewrite a locally planned argv for the remote machine.

    The local launcher prefix becomes the bare ``binary`` (found on the
    remote PATH); the local working folder becomes the remote one; Jarvis'
    localhost MCP wiring is dropped; an ``--append-system-prompt-file`` (a
    local file) is inlined as ``--append-system-prompt``.
    """
    parts = list(argv)
    start = 0
    while start < len(parts) and _looks_like_launcher(parts[start]):
        start += 1
    rest = parts[start:]
    out: list[str] = [binary]
    skip = False
    files = system_prompt_files or {}
    for index, part in enumerate(rest):
        if skip:
            skip = False
            continue
        if part in _LOCAL_ONLY_FLAGS_WITH_VALUE:
            skip = True
            continue
        if part == "-c" and index + 1 < len(rest) and "mcp_servers" in rest[index + 1]:
            skip = True
            continue
        if part == "--append-system-prompt-file" and index + 1 < len(rest):
            text = files.get(rest[index + 1])
            skip = True
            if text:
                out += ["--append-system-prompt", text]
            continue
        out.append(remote_cwd if part == local_cwd else part)
    return out


def remote_env(env: dict[str, str]) -> dict[str, str]:
    """The allow-listed variables plus ``CI=1`` (never a secret, never a path)."""
    kept = {k: v for k, v in env.items() if k in REMOTE_ENV_ALLOW}
    kept["CI"] = "1"
    kept.setdefault("NO_COLOR", "1")
    return kept


class _Stdin:
    """``proc.stdin`` for a remote process: ``close`` ends input, not the channel."""

    def __init__(self, writer: Any) -> None:
        self._writer = writer
        self._eof = False

    def write(self, data: bytes) -> None:
        if not self._eof:
            self._writer.write(data)

    async def drain(self) -> None:
        await self._writer.drain()

    def is_closing(self) -> bool:
        return self._eof or bool(self._writer.is_closing())

    def close(self) -> None:
        if self._eof:
            return
        self._eof = True
        with contextlib.suppress(OSError, BrokenPipeError):
            self._writer.write_eof()


class RemoteCliProcess:
    """An SSH-backed process with the ``asyncio.subprocess.Process`` surface."""

    pid: int | None = None

    def __init__(self, process: Any, stack: contextlib.AsyncExitStack) -> None:
        self._process = process
        self._stack = stack
        self.stdin = _Stdin(process.stdin)
        self.stdout = process.stdout
        self.stderr = process.stderr
        self._closed = False

    @property
    def returncode(self) -> int | None:
        code = self._process.returncode
        return None if code is None else int(code)

    async def wait(self) -> int | None:
        with contextlib.suppress(Exception):  # a torn-down channel reports no status
            await self._process.wait_closed()
        await self._release()
        return self.returncode

    def kill(self) -> None:
        # Many sshd builds ignore signal requests; closing the channel hangs
        # up the remote command's terminal-less session instead.
        with contextlib.suppress(Exception):
            self._process.kill()
        with contextlib.suppress(Exception):
            self._process.close()

    async def _release(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._stack.aclose()


async def spawn(
    computer_id: str,
    *,
    agent_id: str,
    runner: str,
    binary: str,
    argv: Sequence[str],
    local_cwd: str,
    env: dict[str, str],
    system_prompt_files: dict[str, str] | None = None,
) -> RemoteCliProcess:
    """Start the CLI on ``computer_id`` inside the agent's remote workspace."""
    from jarvis.computers.service import ComputerError, get_service
    from jarvis.computers.ssh import SshError, run_command
    from jarvis.society.remote import remote_workspace_expr

    stack = contextlib.AsyncExitStack()
    try:
        opened = await stack.enter_async_context(get_service().session(computer_id))
        folder = remote_workspace_expr(agent_id)
        probe = await run_command(
            opened,
            f"mkdir -p {folder} && cd {folder} && pwd && "
            f"(command -v {shlex.quote(binary)} >/dev/null 2>&1 && echo found || echo missing)",
            timeout_s=20,
        )
        lines = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
        if len(lines) < 2:
            raise RemoteCliUnavailable("The agent's computer did not answer the setup check.")
        remote_cwd, presence = lines[-2], lines[-1]
        computer = get_service().get(computer_id)
        if presence != "found":
            raise RemoteCliUnavailable(
                f"Install {binary} on {computer.name} (and log in there once), "
                "or set the agent to run on this computer."
            )
        command_argv = remote_argv(
            argv,
            binary=binary,
            local_cwd=local_cwd,
            remote_cwd=remote_cwd,
            system_prompt_files=system_prompt_files,
        )
        assignments = " ".join(f"{k}={shlex.quote(v)}" for k, v in remote_env(env).items())
        command = (
            f"cd {shlex.quote(remote_cwd)} && exec env {assignments} {shlex.join(command_argv)}"
        )
        log.info("agent chat: %s turn for %s runs on %s", runner, agent_id, computer.name)
        process = await opened.conn.create_process(command, encoding=None)
    except (ComputerError, SshError) as exc:
        await stack.aclose()
        raise RemoteCliUnavailable(f"Could not reach the agent's computer: {exc.message}") from exc
    except BaseException:
        await stack.aclose()
        raise
    return RemoteCliProcess(process, stack)
