"""``CliTool`` — one instance per connected CLI, implements the Tool protocol.

- **One tool per CLI**, not per subcommand.
- **Binary guard** — ``command`` MUST start with ``spec.binary_name``.
- **ENV injection via ``CliAuthManager.env_for()``**.
- **Usage logging** — every call is recorded in the SQLite database.
- **Output truncation** — stdout 4000 chars, stderr 2000 chars.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import sys
import time
from typing import Any

from jarvis.clis.auth import CliAuthManager
from jarvis.clis.spec import CliSpec
from jarvis.clis.usage_log import UsageLog
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS, resolve_executable
from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

TOOL_NAME_PREFIX = "cli_"
DEFAULT_TIMEOUT_S = 60.0
MAX_STDOUT_CHARS = 4000
MAX_STDERR_CHARS = 2000

# Per-CLI environment that forces non-interactive execution. A prompting CLI
# (live repro 2026-06-17: ``gcloud billing budgets list`` emitted
# "Would you like to enable and retry (y/N)?") would otherwise block on stdin
# until the timeout under ``pythonw.exe`` (no console). Keyed by ``binary_name``;
# combined with ``stdin=DEVNULL`` so a prompt fails fast with a real stderr.
_NONINTERACTIVE_ENV: dict[str, dict[str, str]] = {
    "gcloud": {"CLOUDSDK_CORE_DISABLE_PROMPTS": "1"},
}


def _noninteractive_env_for(binary_name: str) -> dict[str, str]:
    """Return the non-interactive env additions for a CLI, or ``{}`` if none."""
    return dict(_NONINTERACTIVE_ENV.get(binary_name, {}))


class CliTool:
    def __init__(
        self,
        spec: CliSpec,
        *,
        auth: CliAuthManager,
        usage_log: UsageLog,
    ) -> None:
        self._spec = spec
        self._auth = auth
        self._usage = usage_log
        self.name: str = f"{TOOL_NAME_PREFIX}{spec.name}"
        self.description: str = self._build_description(spec)
        self.risk_tier: str = spec.risk.default_tier
        self.schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        f"Vollstaendiger {spec.binary_name}-Befehl inklusive Arguments. "
                        f"Muss mit '{spec.binary_name}' beginnen."
                    ),
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximale Laufzeit in Sekunden (default 60).",
                    "default": DEFAULT_TIMEOUT_S,
                },
                "cwd": {
                    "type": "string",
                    "description": "Arbeitsverzeichnis (optional).",
                    "default": "",
                },
            },
            "required": ["command"],
        }

    @staticmethod
    def _build_description(spec: CliSpec) -> str:
        lines = [f"{spec.display_name} — {spec.description}"]
        if spec.tool_schema_examples:
            lines.append("Beispiele:")
            for ex in spec.tool_schema_examples:
                lines.append(f"  - {ex}")
        return "\n".join(lines)

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        command = (args.get("command") or "").strip()
        timeout_s = float(args.get("timeout_s") or DEFAULT_TIMEOUT_S)
        cwd = args.get("cwd") or None

        if not command:
            return ToolResult(success=False, output=None, error="command fehlt")
        if not command.split(maxsplit=1)[0] == self._spec.binary_name:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"command muss mit '{self._spec.binary_name}' beginnen — "
                    f"bekam '{command[:40]}...'"
                ),
            )

        try:
            parts = shlex.split(command, posix=(sys.platform != "win32"))
        except ValueError as exc:
            return ToolResult(success=False, output=None, error=f"parse error: {exc}")
        if not parts:
            return ToolResult(success=False, output=None, error="empty command")
        # Resolve argv[0] to the full on-disk path so .cmd/.bat/.ps1 shims
        # (gcloud, npm, vercel, ...) are exec'able under shell=False on Windows.
        # The binary-guard above already pinned parts[0] to spec.binary_name.
        parts[0] = resolve_executable(parts[0])

        env = os.environ.copy()
        env.update(self._auth.env_for(self._spec))
        # Force non-interactive execution so a prompt (e.g. gcloud's
        # "Would you like to enable and retry (y/N)?") fails fast with a real
        # stderr instead of hanging on stdin (live repro 2026-06-17).
        env.update(_noninteractive_env_for(self._spec.binary_name))

        started_ms = int(time.time() * 1000)
        row_id = self._usage.record_start(
            cli_name=self._spec.name,
            full_command=command,
            caller="brain",
            trace_id=str(ctx.trace_id) if ctx and ctx.trace_id else None,
            cwd=str(cwd) if cwd else None,
            started_at_ms=started_ms,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                self._usage.record_failure(
                    row_id,
                    error=f"Timeout nach {timeout_s}s",
                    finished_at_ms=int(time.time() * 1000),
                )
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Timeout nach {timeout_s}s",
                )
        except FileNotFoundError as exc:
            self._usage.record_failure(
                row_id,
                error=str(exc),
                finished_at_ms=int(time.time() * 1000),
            )
            return ToolResult(success=False, output=None, error=f"binary not found: {exc}")
        except Exception as exc:  # noqa: BLE001
            self._usage.record_failure(
                row_id,
                error=str(exc),
                finished_at_ms=int(time.time() * 1000),
            )
            return ToolResult(success=False, output=None, error=str(exc))

        stdout = (out_b or b"").decode("utf-8", errors="replace")
        stderr = (err_b or b"").decode("utf-8", errors="replace")
        finished_ms = int(time.time() * 1000)
        exit_code = proc.returncode if proc.returncode is not None else -1
        self._usage.record_finish(
            row_id,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            finished_at_ms=finished_ms,
        )

        success = exit_code == 0
        return ToolResult(
            success=success,
            output={
                "exit_code": exit_code,
                "stdout": stdout[:MAX_STDOUT_CHARS],
                "stderr": stderr[:MAX_STDERR_CHARS],
                "duration_ms": finished_ms - started_ms,
            },
            error=None if success else f"exit {exit_code}",
        )


__all__ = ["CliTool", "TOOL_NAME_PREFIX", "DEFAULT_TIMEOUT_S"]
