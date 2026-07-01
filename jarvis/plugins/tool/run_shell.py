"""run_shell tool: runs shell commands (via subprocess).

Risk tier: monitor — the safety layer decides via whitelist/blacklist whether confirmation is needed.

The tool does NOT invoke `shell=True` mode. Commands are parsed through
`shlex.split`. The user can move dangerous commands into the safe tier
via `[safety.whitelist].commands` (`browser-use *`, `git *`).
"""
from __future__ import annotations

import asyncio
import shlex
import sys
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import ExecutionContext, ToolResult


class RunShellTool:
    name: str = "run_shell"
    risk_tier: str = "monitor"
    description: str = (
        "Runs a shell command. Commands are matched against the whitelist/"
        "blacklist. Default timeout 30s."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command (incl. arguments)"},
            "timeout_s": {"type": "number", "default": 30},
            "cwd": {"type": "string", "description": "Working directory", "default": ""},
        },
        "required": ["command"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        command = (args.get("command") or "").strip()
        timeout_s = float(args.get("timeout_s", 30))
        cwd = args.get("cwd") or None
        if not command:
            return ToolResult(success=False, output=None, error="command is missing")

        try:
            parts = shlex.split(command, posix=(sys.platform != "win32"))
        except ValueError as exc:
            return ToolResult(success=False, output=None, error=f"Command parse error: {exc}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult(success=False, output=None, error=f"Timeout after {timeout_s}s")
        except FileNotFoundError as exc:
            return ToolResult(success=False, output=None, error=f"Not found: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))

        stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
        stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
        success = proc.returncode == 0
        return ToolResult(
            success=success,
            output={
                "exit_code": proc.returncode,
                "stdout": stdout[:4000],
                "stderr": stderr[:2000],
            },
            error=None if success else f"exit {proc.returncode}",
        )
