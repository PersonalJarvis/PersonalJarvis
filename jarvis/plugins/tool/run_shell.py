"""run_shell tool: runs shell commands (via subprocess).

Risk tier: monitor — the safety layer decides via whitelist/blacklist whether
confirmation is needed. The whitelist/blacklist matching runs on the FULL
command string inside ``ToolExecutor`` before this tool executes, so it is
independent of how the string is executed below.

POSIX keeps the historical no-``shell=True`` contract: commands are parsed
through ``shlex.split`` and exec'd directly.

Windows hands the ORIGINAL string to ``cmd.exe`` instead. Tokenizing with
``shlex.split(posix=False)`` KEPT the surrounding quotes inside the tokens,
so ``powershell -Command "X"`` received a string LITERAL and echoed it back
with exit 0 — the tool reported success with garbage output, which sent the
delegated brain into a retry loop until its iteration budget died (forensic
2026-07-13 18:15). cmd builtins (``dir``, ``type``, ``copy``) additionally
failed with WinError 2 because they are not programs. ``cmd.exe`` parses its
own quoting and provides the builtins.
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

        if sys.platform != "win32":
            try:
                parts = shlex.split(command)
            except ValueError as exc:
                return ToolResult(
                    success=False, output=None, error=f"Command parse error: {exc}"
                )

        try:
            if sys.platform == "win32":
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    creationflags=NO_WINDOW_CREATIONFLAGS,
                )
            else:
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
