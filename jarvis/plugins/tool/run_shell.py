"""run_shell tool: runs shell commands (via subprocess).

Risk tier: monitor — the safety layer decides via whitelist/blacklist whether
confirmation is needed. The whitelist/blacklist matching runs on the FULL
command string inside ``ToolExecutor`` before this tool executes, so it is
independent of how the string is executed below.

Shell choice (redesign 2026-08-08): the brain writes commands for the shell it
ASSUMES, so the tool must (a) run a shell the model expects and (b) declare
that shell in its description. Session forensics (2026-08-04/06/07) showed the
model emitting PowerShell (``Add-Type``, ``Start-Process``) and POSIX
(``head``) commands into the previous ``cmd.exe`` backend — roughly half of
all first attempts failed with "command not found" and only succeeded after a
blind reformulation retry.

- Windows: ``powershell.exe -EncodedCommand`` (Windows PowerShell 5.1, present
  on every supported Windows). Base64-encoding the script sidesteps the
  quoting bugs entirely (forensic 2026-07-13: ``shlex.split(posix=False)``
  kept surrounding quotes, so quoted payloads were echoed back as literals
  with exit 0). A wrapper propagates the native exit code, because
  ``powershell -Command``/-EncodedCommand`` otherwise reports 0 even when the
  last native command failed.
- macOS/Linux: ``/bin/sh -c`` so pipes, ``&&`` and redirects work — the
  historical no-shell exec could not run ``ls | grep foo`` at all. Safety is
  unaffected: pattern matching happens upstream on the raw string.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.safety.command_impact import DESTRUCTIVE, classify_command
from jarvis.safety.explicit_intent import utterance_confirms_destruction

if sys.platform == "win32":
    _SHELL_LABEL = "Windows PowerShell 5.1"
    _SHELL_HINT = (
        "PowerShell cmdlets, aliases (ls, dir, cat) and pipes work. "
        "POSIX tools like head/grep/awk and cmd-only syntax like 'dir /b' "
        "do NOT work unless explicitly installed."
    )
else:
    _SHELL_LABEL = "/bin/sh (POSIX)"
    _SHELL_HINT = (
        "POSIX sh syntax with pipes, && and redirects works. "
        "Write portable sh, not bash-isms; PowerShell cmdlets do NOT exist."
    )


def _windows_subprocess_env() -> dict[str, str]:
    """Return an environment with a deduplicated PATH.

    Desktop integrations commonly append the same native-runtime directories
    repeatedly, inflating PATH towards the per-variable limit. Remove
    duplicates while keeping the original lookup order. Other platforms never
    use this helper.
    """
    env = dict(os.environ)
    path_key = next((key for key in env if key.lower() == "path"), "PATH")
    entries = env.get(path_key, "").split(os.pathsep)
    deduplicated: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not entry:
            continue
        identity_source = os.path.expandvars(entry.strip().strip('"'))
        identity = os.path.normcase(os.path.normpath(identity_source))
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(entry)
    env[path_key] = os.pathsep.join(deduplicated)
    return env


def _powershell_argv(command: str) -> list[str]:
    """Build the ``powershell.exe`` invocation for ``command``.

    The wrapper script exists for three reasons:
    - ``[Console]::OutputEncoding`` makes native tool output decode as UTF-8
      (guarded: under CREATE_NO_WINDOW there may be no console to configure).
    - PowerShell's process exit code is 0 even when the command inside failed
      (both for native commands and for cmdlet errors — verified by test),
      so failures must be surfaced explicitly: cmdlet errors become
      terminating via ``$ErrorActionPreference = 'Stop'`` and are caught,
      native exit codes propagate via ``$LASTEXITCODE``. Otherwise a failing
      command reports success and sends the brain into the false-success
      retry loop documented in the module docstring.
    - Caught errors are written with ``[Console]::Error.WriteLine`` — raw
      text. Letting them reach PowerShell's own error stream would serialize
      them as CLIXML noise on stderr, which the brain cannot read.
      ``$ProgressPreference`` suppresses progress records for the same reason.
    """
    script = (
        "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}\n"
        "$ProgressPreference = 'SilentlyContinue'\n"
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "  & { " + command + " }\n"
        "} catch {\n"
        "  [Console]::Error.WriteLine($_.ToString())\n"
        "  exit 1\n"
        "}\n"
        "if ($LASTEXITCODE -ne $null) { exit $LASTEXITCODE }\n"
        "exit 0\n"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


class RunShellTool:
    name: str = "run_shell"
    risk_tier: str = "monitor"
    description: str = (
        f"Runs a shell command via {_SHELL_LABEL} on this machine. "
        "Use it for ANY local outcome a command can achieve: create/delete/"
        "rename/move/copy files and folders, list directories, read file "
        "contents, start programs, timers, system queries — the user never "
        "needs to mention a terminal; translate their goal into a command. "
        f"{_SHELL_HINT} "
        "Commands are matched against the whitelist/blacklist. "
        "Default timeout 30s."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    f"The command (incl. arguments), written for {_SHELL_LABEL}"
                ),
            },
            "timeout_s": {"type": "number", "default": 30},
            "cwd": {"type": "string", "description": "Working directory", "default": ""},
        },
        "required": ["command"],
    }

    def risk_tier_for_args(self, args: dict[str, Any]) -> str | None:
        """Escalate destructive commands to the ``ask`` tier.

        Same per-action pattern as the gmail tool (forensic 2026-06-19): the
        static ``monitor`` tier stays for reading/modifying commands, but a
        command classified as destructive (rm, del, format, Remove-Item, …)
        requires user confirmation. Whitelist and blacklist keep priority in
        ``RiskTierEvaluator`` — a ``run_shell *`` whitelist entry still
        downgrades everything, so confirmation-averse setups are unchanged.
        Returning ``None`` falls back to the static tier.
        """
        command = (args.get("command") or "").strip()
        if not command:
            return None
        if classify_command(command).level == DESTRUCTIVE:
            return "ask"
        return None

    def intent_confirms_args(self, args: dict[str, Any], utterance: str) -> bool:
        """True when the user's own words already authorize this command.

        Claude-Code permission model: a destructive command the USER asked
        for by name ("delete the folder Urlaub") needs no second question —
        the ToolExecutor consults this hook before arming a confirmation.
        Only the destruction verb-class is matched (deterministic vocabulary,
        no entity matching — STT garbles names); a brain-initiated deletion
        whose utterance never mentioned deleting still confirms.
        """
        command = (args.get("command") or "").strip()
        if not command or classify_command(command).level != DESTRUCTIVE:
            return False
        return utterance_confirms_destruction(utterance)

    def describe_args(self, args: dict[str, Any]) -> dict[str, str] | None:
        """Plain-language impact summary for the confirmation question.

        Consumed by ``ToolExecutor`` when this call is deferred for two-turn
        voice/chat confirmation: the spoken question then says WHAT the
        command would do ("would delete something (rm)") instead of a generic
        "run that?". The command words are DATA (English tool names), the
        surrounding phrasing is localized in
        ``jarvis/voice/tool_confirmation.py``.
        """
        command = (args.get("command") or "").strip()
        if not command:
            return None
        impact = classify_command(command)
        commands = ", ".join(dict.fromkeys(impact.commands))
        return {"level": impact.level, "commands": commands}

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        command = (args.get("command") or "").strip()
        timeout_s = float(args.get("timeout_s", 30))
        cwd = args.get("cwd") or None
        if not command:
            return ToolResult(success=False, output=None, error="command is missing")

        try:
            if sys.platform == "win32":
                proc = await asyncio.create_subprocess_exec(
                    *_powershell_argv(command),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    creationflags=NO_WINDOW_CREATIONFLAGS,
                    env=_windows_subprocess_env(),
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    "/bin/sh",
                    "-c",
                    command,
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
