"""An agent's own shell: commands run in ITS workspace folder, nowhere else.

Sandbox stance (maintainer decision 2026-09-02, after checking the field):
Hermes Agent's default terminal backend is local and OpenClaw ships with its
sandbox off, so the society does the same — a local shell with path
containment and the existing risk tiers, no container by default. The
``ShellBackend`` protocol is the seam for an optional container backend
later (capability-probed, one class); it is deliberately not built now.

Containment is the one thing the ordinary folder tools do not do: every path
the agent names is resolved and must stay under the workspace, and the
command itself always starts there. Output is capped, stdin is closed, the
child gets ``CI=1`` / ``NO_COLOR`` so tools drop prompts and colours, and no
console window is ever created (AP-1).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

__all__ = [
    "ContainmentError",
    "DEFAULT_TIMEOUT_S",
    "LocalBackend",
    "MAX_TIMEOUT_S",
    "OUTPUT_CAP_CHARS",
    "ShellBackend",
    "ShellResult",
    "resolve_contained",
]

DEFAULT_TIMEOUT_S: Final[float] = 120.0
MAX_TIMEOUT_S: Final[float] = 900.0
OUTPUT_CAP_CHARS: Final[int] = 30_000


class ContainmentError(ValueError):
    """A path left the agent's workspace."""


def resolve_contained(workspace: Path, raw: str | None) -> Path:
    """Resolve ``raw`` relative to ``workspace``; refuse anything outside it.

    ``..``, absolute paths and symlinks pointing out of the folder all
    resolve to a real location — that location has to sit under the real
    workspace or the call is refused. An empty path is the workspace itself.
    """
    root = workspace.resolve()
    text = (raw or "").strip() or "."
    candidate = Path(os.path.expanduser(text))
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContainmentError(f"path leaves the agent workspace: {text}") from exc
    return resolved


@dataclass(frozen=True, slots=True)
class ShellResult:
    output: str
    exit_code: int | None
    seconds: float
    timed_out: bool = False
    failed_to_start: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.failed_to_start


class ShellBackend(Protocol):
    """Where an agent's commands run. ``LocalBackend`` today; a container
    backend would implement the same two members behind a capability probe."""

    name: str

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ShellResult: ...


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-(limit // 2) :]
    return f"{head}\n…[{len(text) - limit} characters cut]…\n{tail}"


class LocalBackend:
    """The host shell, started inside the workspace (Git Bash / PowerShell /
    bash / sh — the same pick the chat's folder tools make)."""

    name: str = "local"

    async def run(self, command: str, *, cwd: Path, timeout_s: float) -> ShellResult:
        from jarvis.agent_chat.tools import shell_argv

        timeout = max(1.0, min(float(timeout_s or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S))
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env["CI"] = env.get("CI", "1")
        env.setdefault("NO_COLOR", "1")
        argv = [*shell_argv(), command]
        started = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except (OSError, ValueError) as exc:
            return ShellResult(
                output=f"Could not start the shell: {exc}",
                exit_code=None,
                seconds=0.0,
                failed_to_start=True,
            )
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            with contextlib.suppress(OSError, ProcessLookupError):
                proc.kill()
            with contextlib.suppress(OSError, ProcessLookupError):
                await proc.wait()
            return ShellResult(
                output=f"Command timed out after {int(timeout)} s",
                exit_code=None,
                seconds=time.perf_counter() - started,
                timed_out=True,
            )
        except asyncio.CancelledError:
            with contextlib.suppress(OSError, ProcessLookupError):
                proc.kill()
            raise
        text = raw.decode("utf-8", errors="replace").rstrip()
        return ShellResult(
            output=_cap(text, OUTPUT_CAP_CHARS),
            exit_code=proc.returncode,
            seconds=time.perf_counter() - started,
        )


def default_backend() -> ShellBackend:
    """The backend this box runs agents' commands on — local, by decision.

    A container backend would be chosen here behind ``probes.has_container``
    once it exists; today the probe answers False and this returns the host
    shell.
    """
    return LocalBackend()


def summarize_for_card(command: str, cwd: Path) -> dict[str, Any]:
    """What the approval card shows for a shell call."""
    return {"command": command[:300], "folder": str(cwd)}
