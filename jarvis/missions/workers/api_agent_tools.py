"""Worktree-scoped file and process tools for the in-process API worker.

These are the hands of :class:`ApiAgentWorker` — the worker that drives an
OpenAI-compatible brain (openai / openrouter) in a tool-use loop. The
CLI workers (claude / codex / agy) get file and process tools from their own
binary; an in-process brain has none, so we supply a minimal, deliberately
small set here.

Safety boundary: file-tool paths are confined to the disposable mission
worktree. RunCommand reduces accidental path escape and secret exposure by
using structured argv, rejecting obvious wrappers/escape arguments, and giving
the child a reduced environment with mission-scoped HOME and temporary paths.
Cancellation, timeout, and normal completion reap the ordinary command process
tree. Deliberately detached POSIX code remains outside this misuse guard.

This is explicitly NOT a filesystem or code-execution sandbox. A workspace
Python/Node script, test suite, compiler, or package script is arbitrary code
running with the Jarvis OS user's rights and can access anything that user can.
The policy is a misuse guard plus secret reduction; the controller's disposable
worktree, process containment, and post-run diff/path review remain mandatory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress as contextlib_suppress
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from jarvis.missions.isolation.job_object import WindowsJobObject

from .process_utils import create_worker_subprocess, resolve_node_executable

logger = logging.getLogger(__name__)

_WINDOWS_GATE_SCRIPT = """param(
    [Parameter(Mandatory=$true)][string]$GatePath,
    [Parameter(Mandatory=$true)][string]$PayloadPath
)
$deadline = [DateTime]::UtcNow.AddSeconds(30)
while (-not (Test-Path -LiteralPath $GatePath -PathType Leaf)) {
    if ([DateTime]::UtcNow -ge $deadline) {
        Write-Error "Containment gate did not open before its deadline."
        exit 125
    }
    Start-Sleep -Milliseconds 10
}
try {
    $payload = Get-Content -LiteralPath $PayloadPath -Raw | ConvertFrom-Json
    $commandArgs = @()
    foreach ($item in $payload.arguments) { $commandArgs += [string]$item }
    & ([string]$payload.executable) @commandArgs
    if ($null -eq $LASTEXITCODE) { exit 1 }
    exit [int]$LASTEXITCODE
} catch {
    Write-Error $_
    exit 1
}
"""

_POSIX_GATE_SCRIPT = """#!/bin/sh
gate_path=$1
shift
gate_attempts=0
while [ ! -f "$gate_path" ]; do
    gate_attempts=$((gate_attempts + 1))
    if [ "$gate_attempts" -ge 3000 ]; then
        echo "Containment gate did not open before its deadline." >&2
        exit 125
    fi
    sleep 0.01
done
exec "$@"
"""

# Anthropic-style tool specs (name / description / input_schema). `_openai_base.
# _tools_openai_format` translates these to OpenAI function specs, so the same
# list works for grok / openai / openrouter.
WORKER_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "Write",
        "description": (
            "Create or overwrite a file in the workspace with the given content. "
            "Use a path relative to the workspace root."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path relative to workspace root."},
                "content": {"type": "string", "description": "Full file content to write."},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Read",
        "description": "Read a file from the workspace and return its text content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path relative to workspace root."},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Edit",
        "description": (
            "Replace the first occurrence of old_string with new_string in a file. "
            "old_string must match exactly and be unique enough to identify the spot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "RunCommand",
        "description": (
            "Run one program directly in the workspace and return stdout and stderr. "
            "This is not a shell: provide the executable name and each argument "
            "separately. Shell chaining, redirection, inline code, absolute paths "
            "outside the workspace, and parent traversal are unsupported."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "program": {
                    "type": "string",
                    "description": "Executable name from PATH, without a directory.",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Argument vector. Do not combine arguments into a shell string.",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Optional timeout in seconds (default 120, maximum 600).",
                },
            },
            "required": ["program"],
        },
    },
    {
        "name": "Ls",
        "description": "List the files and directories at a workspace path (default: root).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path, default '.'"}},
            "required": [],
        },
    },
)

_MAX_READ_CHARS = 60_000
_MAX_OUTPUT_CHARS = 16_000
_DEFAULT_COMMAND_TIMEOUT = 120.0
_MAX_COMMAND_TIMEOUT = 600.0
_MIN_COMMAND_TIMEOUT = 0.1
_COMMAND_STOP_GRACE_S = 0.25
_MAX_COMMAND_ARGS = 256
_MAX_COMMAND_ARG_CHARS = 8_192
_MAX_COMMAND_TOTAL_CHARS = 65_536

# A direct process runner has no reason to launch a shell. Blocking every common
# spelling also keeps a model from reintroducing shell evaluation indirectly.
_SHELL_PROGRAMS = frozenset(
    {
        "bash",
        "busybox",
        "cmd",
        "cmd.exe",
        "command",
        "csh",
        "dash",
        "env",
        "env.exe",
        "fish",
        "ksh",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "pythonw",
        "pythonw.exe",
        "sh",
        "setsid",
        "tcsh",
        "wsl",
        "wsl.exe",
        "zsh",
    }
)

# Inline program flags turn an interpreter into another unreviewable command
# string. Script files and ``python -m <module>`` remain available for tests and
# builds, but the script/module must be named explicitly in argv.
_INLINE_PROGRAM_FLAGS: dict[str, frozenset[str]] = {
    "node": frozenset({"-e", "--eval", "-p", "--print"}),
    "node.exe": frozenset({"-e", "--eval", "-p", "--print"}),
    "perl": frozenset({"-e", "-E"}),
    "py": frozenset({"-c"}),
    "py.exe": frozenset({"-c"}),
    "python": frozenset({"-c"}),
    "python.exe": frozenset({"-c"}),
    "python3": frozenset({"-c"}),
    "python3.exe": frozenset({"-c"}),
    "ruby": frozenset({"-e"}),
}

_PATH_VALUE_OPTION = re.compile(
    r"^(?:--?(?:chdir|cwd|directory|git-dir|output|prefix|root|target|work-tree))=(.*)$",
    re.IGNORECASE,
)
_COMPACT_PATH_OPTION = re.compile(r"^(?:-[ILo]|/F[deo])(.+)$", re.IGNORECASE)

# Only process-launch essentials cross into a local command. In particular no
# provider/API credential, keyring selector, cloud token, or user PYTHONPATH is
# inherited from the worker/provider environment.
_SAFE_ENV_KEYS = frozenset(
    {
        "COMSPEC",
        "CC",
        "CXX",
        "AR",
        "CI",
        "GOROOT",
        "JAVA_HOME",
        "LANG",
        "LC_ALL",
        "LIB",
        "LIBPATH",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PKG_CONFIG_PATH",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_ARCHITEW6432",
        "RUSTUP_HOME",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TERM",
        "TZ",
        "VIRTUAL_ENV",
        "WINDIR",
    }
)

_PROXY_ENV_KEYS = frozenset({"ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"})
_CA_FILE_ENV_KEYS = frozenset(
    {
        "CURL_CA_BUNDLE",
        "GIT_SSL_CAINFO",
        "NODE_EXTRA_CA_CERTS",
        "PIP_CERT",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
    }
)
_MAX_CA_BUNDLE_BYTES = 16 * 1024 * 1024


class WorktreeEscapeError(ValueError):
    """Raised when a tool path resolves outside the worktree."""


class CommandPolicyError(ValueError):
    """Raised when a requested process cannot cross the worker boundary."""


def _safe_path(worktree: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``worktree``; reject any escape.

    Absolute paths and ``..`` traversals that land outside the tree raise
    :class:`WorktreeEscapeError`. An absolute path that already points INSIDE
    the worktree is allowed (the model sometimes echoes the full cwd path).
    """
    root = worktree.resolve()
    candidate = Path(rel)
    target = candidate if candidate.is_absolute() else root / candidate
    target = target.resolve()
    if target != root and root not in target.parents:
        raise WorktreeEscapeError(f"path escapes the workspace: {rel!r}")
    return target


def _validate_command_path(value: str, *, worktree: Path) -> None:
    """Reject direct and option-embedded paths that can leave ``worktree``."""
    if not value:
        return
    if "\x00" in value or "\r" in value or "\n" in value:
        raise CommandPolicyError("command arguments must not contain control lines")

    candidate = value
    option_match = _PATH_VALUE_OPTION.match(value)
    if option_match:
        candidate = option_match.group(1)
    elif value.startswith("-") and "=" in value:
        # Treat every GNU-style ``--option=/path`` value as a possible path,
        # not only the common names above. Unknown tools invent option names.
        candidate = value.split("=", 1)[1]
    else:
        compact_match = _COMPACT_PATH_OPTION.match(value)
        if compact_match:
            candidate = compact_match.group(1)

    embedded_windows_absolute = re.search(r"(?i)(?:^|[=:])([a-z]:[\\/].*)", candidate)
    if embedded_windows_absolute:
        candidate = embedded_windows_absolute.group(1)

    # Tilde and environment forms can be expanded by programs even without a
    # shell. Reject path-position expansions instead of pretending they are
    # confined to the mission workspace.
    upper = candidate.upper()
    if candidate.startswith("~") or upper.startswith(("$HOME", "${HOME}", "%USERPROFILE%")):
        raise WorktreeEscapeError(f"path escapes the workspace: {value!r}")

    segments = re.split(r"[\\/]", candidate)
    if ".." in segments:
        raise WorktreeEscapeError(f"path escapes the workspace: {value!r}")

    native = Path(candidate)
    windows = PureWindowsPath(candidate)
    if native.is_absolute() or windows.is_absolute() or bool(windows.drive):
        _safe_path(worktree, candidate)
        return

    # Resolve every relative token as a potential path. Harmless flags and
    # literals simply map to a nonexistent name under the workspace, while an
    # existing single-component symlink to a host directory is caught here.
    _safe_path(worktree, candidate)


def _validated_command(
    program: Any,
    args: Any,
    *,
    worktree: Path,
) -> tuple[str, tuple[str, ...]]:
    """Return a policy-checked executable name and argument vector."""
    executable = str(program or "").strip()
    if not executable:
        raise CommandPolicyError("program must be a non-empty executable name")
    if len(executable) > 255:
        raise CommandPolicyError("program name is too long")
    if any(char in executable for char in ("\x00", "\r", "\n")):
        raise CommandPolicyError("program must not contain control lines")
    if (
        Path(executable).is_absolute()
        or PureWindowsPath(executable).drive
        or "/" in executable
        or "\\" in executable
        or executable in {".", ".."}
    ):
        raise CommandPolicyError("program must be an executable name from PATH, not a path")

    normalized = executable.casefold()
    if normalized in _SHELL_PROGRAMS:
        raise CommandPolicyError(
            f"unsupported command {executable!r}: shell and command-wrapper "
            "programs are not available; "
            "use one RunCommand call with a program and args"
        )

    if args is None:
        argv: tuple[str, ...] = ()
    elif (
        isinstance(args, Sequence)
        and not isinstance(args, (str, bytes, bytearray))
        and all(isinstance(item, str) for item in args)
    ):
        argv = tuple(args)
    else:
        raise CommandPolicyError("args must be an array of strings")
    if len(argv) > _MAX_COMMAND_ARGS:
        raise CommandPolicyError(f"args must contain at most {_MAX_COMMAND_ARGS} items")
    if any(len(arg) > _MAX_COMMAND_ARG_CHARS for arg in argv):
        raise CommandPolicyError(
            f"each command argument must be at most {_MAX_COMMAND_ARG_CHARS} characters"
        )
    if sum(len(arg) for arg in argv) > _MAX_COMMAND_TOTAL_CHARS:
        raise CommandPolicyError(
            f"command arguments must total at most {_MAX_COMMAND_TOTAL_CHARS} characters"
        )

    inline_flags = _INLINE_PROGRAM_FLAGS.get(normalized, frozenset())
    if any(arg in inline_flags for arg in argv):
        raise CommandPolicyError(
            f"unsupported command {executable!r}: inline program text is disabled; "
            "run a workspace script or module instead"
        )
    command_name = Path(normalized).stem
    if command_name == "find" and any(
        arg.casefold() in {"-exec", "-execdir", "--exec"} for arg in argv
    ):
        raise CommandPolicyError("unsupported command: find execution actions are disabled")
    if command_name == "git":
        lowered = tuple(arg.casefold() for arg in argv)
        if "config" in lowered or "-c" in lowered or any(
            arg.startswith("--config-env")
            or (arg.startswith("-c") and "=" in arg)
            or arg.startswith("--exec-path")
            or arg.startswith("alias.")
            or "=!" in arg
            for arg in lowered
        ):
            raise CommandPolicyError(
                "unsupported command: git configuration and executable aliases are disabled"
            )
    if command_name in {"npm", "npx"}:
        subcommand = argv[0].casefold() if argv else ""
        if command_name == "npx" or subcommand in {"exec", "x"}:
            raise CommandPolicyError(
                "unsupported command: package-exec wrappers are disabled; use a "
                "reviewed workspace package script"
            )
    if command_name in {"curl", "wget"}:
        lowered = tuple(arg.casefold() for arg in argv)
        blocked_options = {"--config", "-k", "--input-file", "-i"}
        blocked_indirection = any(
            arg in blocked_options
            or arg.startswith(("--config=", "--input-file=", "-k", "-i"))
            or "file:" in arg
            or "@/" in arg
            or "@\\" in arg
            or "@.." in arg
            or re.search(r"(?i)@[a-z]:[\\/]", arg) is not None
            for arg in lowered
        )
        if blocked_indirection:
            raise CommandPolicyError(
                "unsupported command: local-file/config indirection is disabled for "
                f"{command_name}"
            )

    for arg in argv:
        _validate_command_path(arg, worktree=worktree)
    return executable, argv


def _command_timeout(value: Any) -> float:
    """Return a finite timeout inside the worker command budget."""
    try:
        timeout_s = float(value if value is not None else _DEFAULT_COMMAND_TIMEOUT)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CommandPolicyError("timeout_s must be a finite number") from exc
    if not (_MIN_COMMAND_TIMEOUT <= timeout_s <= _MAX_COMMAND_TIMEOUT):
        raise CommandPolicyError(
            f"timeout_s must be between {_MIN_COMMAND_TIMEOUT:g} and "
            f"{_MAX_COMMAND_TIMEOUT:g} seconds"
        )
    return timeout_s


def _safe_proxy_value(key: str, value: str) -> str | None:
    """Keep proxy routing only when it carries no embedded credentials."""
    stripped = value.strip()
    if not stripped:
        return None
    if key == "NO_PROXY":
        return stripped
    try:
        parsed = urlsplit(stripped if "://" in stripped else f"http://{stripped}")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
    except ValueError:
        return None
    if "@" in stripped:
        return None
    return stripped


def _copy_ca_environment(
    raw_env: Mapping[str, str], *, runtime_dir: Path
) -> dict[str, str]:
    """Copy public CA bundles into mission state without exposing host paths."""
    copied: dict[str, str] = {}
    cert_dir = runtime_dir / "certificates"
    for key in _CA_FILE_ENV_KEYS:
        raw_path = raw_env.get(key)
        if not raw_path:
            continue
        source = Path(raw_path).expanduser()
        try:
            if not source.is_file() or source.stat().st_size > _MAX_CA_BUNDLE_BYTES:
                continue
            content = source.read_bytes()
            if b"PRIVATE KEY" in content.upper():
                logger.warning("Refusing to stage %s because it contains private key data", key)
                continue
            cert_dir.mkdir(parents=True, exist_ok=True)
            target = cert_dir / f"{key.casefold()}.pem"
            target.write_bytes(content)
            copied[key] = str(target)
        except OSError:
            logger.debug("Could not stage %s for RunCommand", key, exc_info=True)
    return copied


def _command_environment(
    source: Mapping[str, str] | None,
    *,
    runtime_dir: Path,
) -> dict[str, str]:
    """Build a credential-reduced, mission-scoped subprocess environment."""
    source_env = source if source is not None else os.environ
    raw_env = {str(key).upper(): str(value) for key, value in source_env.items()}
    host_env = {str(key).upper(): str(value) for key, value in os.environ.items()}
    for key in _SAFE_ENV_KEYS | _PROXY_ENV_KEYS | _CA_FILE_ENV_KEYS:
        if key not in raw_env and key in host_env:
            raw_env[key] = host_env[key]
    env = {
        key: value for key, value in raw_env.items() if key in _SAFE_ENV_KEYS
    }
    # Tests and direct WorkerProtocol callers may pass an intentionally sparse
    # mapping. Fill only non-secret process essentials from the host; never fall
    # back to the complete host environment.
    env.setdefault("PATH", os.environ.get("PATH", os.defpath))
    runtime_dir.mkdir(parents=True, exist_ok=True)
    home = runtime_dir / "home"
    temp = runtime_dir / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMP": str(temp),
            "TEMP": str(temp),
            "TMPDIR": str(temp),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "NO_COLOR": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
        }
    )
    for key in _PROXY_ENV_KEYS:
        safe_value = _safe_proxy_value(key, raw_env.get(key, ""))
        if safe_value is not None:
            env[key] = safe_value
            env[key.casefold()] = safe_value
    env.update(_copy_ca_environment(raw_env, runtime_dir=runtime_dir))
    return env


def _resolve_program(program: str, env: Mapping[str, str]) -> tuple[str, tuple[str, ...]]:
    """Resolve a policy-checked program without invoking a platform shell.

    npm is a batch shim on Windows. It is mapped to its documented JavaScript
    entry point and the native ``node.exe`` instead of handing the shim to
    ``cmd.exe``. Other batch files fail honestly.
    """
    normalized = program.casefold()
    if normalized in {"python", "python.exe", "python3", "python3.exe"}:
        if not bool(getattr(sys, "frozen", False)):
            return sys.executable, ()
        candidates = tuple(
            dict.fromkeys((program, "python3", "python", "py" if os.name == "nt" else ""))
        )
        for candidate in candidates:
            if not candidate:
                continue
            interpreter = shutil.which(candidate, path=env.get("PATH"))
            if not interpreter:
                continue
            try:
                launches_frozen_jarvis = os.path.samefile(interpreter, sys.executable)
            except OSError:
                launches_frozen_jarvis = os.path.normcase(
                    os.path.realpath(interpreter)
                ) == os.path.normcase(os.path.realpath(sys.executable))
            if launches_frozen_jarvis:
                continue
            if Path(interpreter).suffix.casefold() in {".bat", ".cmd"}:
                continue
            return interpreter, ()
        raise CommandPolicyError(
            "Python is unavailable in this packaged Jarvis build. Install a real "
            "Python interpreter on PATH or use another supported build tool; "
            "Jarvis.exe will never be launched as Python."
        )
    resolved = shutil.which(program, path=env.get("PATH"))
    if not resolved:
        raise CommandPolicyError(f"program is not available on PATH: {program!r}")
    suffix = Path(resolved).suffix.casefold()
    if suffix in {".bat", ".cmd"}:
        bare_name = Path(program).stem.casefold()
        if bare_name == "npm":
            node = resolve_node_executable() or shutil.which(
                "node", path=env.get("PATH")
            )
            script = Path(resolved).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
            if node and script.is_file():
                return node, (str(script),)
        raise CommandPolicyError(
            f"unsupported command {program!r}: Windows batch shims require cmd.exe; "
            "install a native executable or use a supported module runner"
        )
    return resolved, ()


def _prepare_windows_gated_launch(
    executable: str,
    args: Sequence[str],
    *,
    env: Mapping[str, str],
    runtime_dir: Path,
) -> tuple[list[str], Path]:
    """Build a trusted launcher that cannot start the target before assignment.

    ``asyncio.create_subprocess_exec`` does not retain the primary thread handle
    needed to safely resume a ``CREATE_SUSPENDED`` child. A fixed PowerShell
    launcher therefore waits on a random gate file. Jarvis assigns that waiting
    launcher to a non-breakaway Job Object first and creates the gate only after
    assignment succeeds. The trusted launcher self-exits after 30 seconds if
    Jarvis crashes in that narrow pre-assignment window. Target argv is JSON
    data, never interpolated into PowerShell source.
    """
    powershell = (
        shutil.which("powershell.exe", path=env.get("PATH"))
        or shutil.which("pwsh.exe", path=env.get("PATH"))
        or shutil.which("pwsh", path=env.get("PATH"))
    )
    if not powershell:
        system_root = env.get("SYSTEMROOT") or env.get("WINDIR")
        if system_root:
            candidate = (
                Path(system_root)
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
            if candidate.is_file():
                powershell = str(candidate)
    if not powershell:
        raise CommandPolicyError(
            "RunCommand is unavailable on Windows because the trusted containment "
            "launcher (PowerShell) is not installed. No target process was started."
        )

    launch_dir = runtime_dir / "windows-launch"
    launch_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    script_path = launch_dir / "contained-command.ps1"
    payload_path = launch_dir / f"payload-{token}.json"
    gate_path = launch_dir / f"gate-{token}"
    with contextlib_suppress(FileNotFoundError):
        gate_path.unlink()
    script_path.write_text(_WINDOWS_GATE_SCRIPT, encoding="utf-8")
    payload_path.write_text(
        json.dumps(
            {"executable": executable, "arguments": list(args)},
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-GatePath",
        str(gate_path),
        "-PayloadPath",
        str(payload_path),
    ]
    return command, gate_path


def _prepare_posix_gated_launch(
    executable: str,
    args: Sequence[str],
    *,
    runtime_dir: Path,
) -> tuple[list[str], Path]:
    """Gate direct POSIX exec until its process group is registered.

    The fixed script consumes only positional argv and uses ``exec \"$@\"``;
    model text is never evaluated as shell source. The waiting launcher
    self-exits after approximately 30 seconds if Jarvis crashes before process-
    group registration. ``/bin/sh`` is part of the supported Linux/macOS floor;
    an unusual host without it fails honestly.
    """
    shell = Path("/bin/sh")
    if not shell.is_file():
        raise CommandPolicyError(
            "RunCommand is unavailable because /bin/sh is missing; no target "
            "process was started."
        )
    launch_dir = runtime_dir / "posix-launch"
    launch_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    script_path = launch_dir / "contained-command.sh"
    gate_path = launch_dir / f"gate-{token}"
    with contextlib_suppress(FileNotFoundError):
        gate_path.unlink()
    script_path.write_text(_POSIX_GATE_SCRIPT, encoding="ascii")
    return [str(shell), str(script_path), str(gate_path), executable, *args], gate_path


async def _read_limited(stream: asyncio.StreamReader | None) -> str:
    """Drain a process stream while retaining only bounded UTF-8 output."""
    if stream is None:
        return ""
    kept = bytearray()
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        if len(kept) < _MAX_OUTPUT_CHARS:
            kept.extend(chunk[: _MAX_OUTPUT_CHARS - len(kept)])
    return bytes(kept).decode("utf-8", errors="replace")


async def _wait_for_exit(proc: asyncio.subprocess.Process, timeout_s: float = 2.0) -> None:
    with contextlib_suppress(TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), timeout=timeout_s)


async def _terminate_windows_tree(proc: asyncio.subprocess.Process) -> None:
    """Best-effort psutil fallback when a Windows Job Object is unavailable."""
    try:
        import psutil  # noqa: PLC0415

        parent = psutil.Process(proc.pid)
        processes = parent.children(recursive=True) + [parent]
        for process in reversed(processes):
            with contextlib_suppress(psutil.Error):
                process.terminate()
        _, alive = await asyncio.to_thread(psutil.wait_procs, processes, timeout=0.25)
        for process in alive:
            with contextlib_suppress(psutil.Error):
                process.kill()
    except Exception:  # noqa: BLE001 - final fallback below still runs
        logger.debug("RunCommand psutil tree cleanup failed", exc_info=True)
    if proc.returncode is None:
        with contextlib_suppress(ProcessLookupError):
            proc.kill()
    await _wait_for_exit(proc)


async def _terminate_posix_group(proc: asyncio.subprocess.Process) -> None:
    """Terminate the process session created by ``create_worker_subprocess``."""
    import signal  # noqa: PLC0415

    with contextlib_suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=_COMMAND_STOP_GRACE_S)
    except TimeoutError:
        pass
    # The leader may already have exited while a background grandchild still
    # owns the group, so always attempt the final group kill.
    with contextlib_suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)
    await _wait_for_exit(proc)


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    if sys.platform == "win32":
        await _terminate_windows_tree(proc)
    else:
        await _terminate_posix_group(proc)


async def _run_command(
    tool_input: Mapping[str, Any],
    *,
    worktree: Path,
    env: Mapping[str, str] | None,
    job: Any | None,
    runtime_dir: Path,
    on_spawn: Callable[[int], None] | None,
) -> tuple[str, bool]:
    """Run one direct command with cancellation-safe tree containment."""
    root = worktree.resolve()  # noqa: ASYNC240 - one fast boundary check before spawn
    if not root.is_dir():  # noqa: ASYNC240 - one fast boundary check before spawn
        return (f"Workspace is not a directory: {worktree}", True)
    program, args = _validated_command(
        tool_input.get("program"), tool_input.get("args"), worktree=root
    )
    timeout_s = _command_timeout(tool_input.get("timeout_s"))
    command_env = _command_environment(env, runtime_dir=runtime_dir)
    executable, launcher_args = _resolve_program(program, command_env)

    # Windows must never start a model-selected target until a strict,
    # non-breakaway Job Object owns the trusted waiting launcher. A no-op Job is
    # an honest capability failure, not permission to run uncontained.
    command_job = (
        WindowsJobObject(
            f"api-command-{uuid.uuid4().hex}", allow_breakaway=False
        )
        if sys.platform == "win32"
        else None
    )
    if command_job is not None and command_job.handle is None:
        await command_job.close()
        return (
            "RunCommand is unavailable because strict Windows process-tree "
            "containment could not be created. No target process was started.",
            True,
        )

    spawn_command = [executable, *launcher_args, *args]
    gate_path: Path | None = None
    if sys.platform == "win32":
        try:
            spawn_command, gate_path = _prepare_windows_gated_launch(
                executable,
                (*launcher_args, *args),
                env=command_env,
                runtime_dir=runtime_dir,
            )
        except BaseException:
            if command_job is not None:
                await command_job.close()
            raise
    else:
        spawn_command, gate_path = _prepare_posix_gated_launch(
            executable,
            (*launcher_args, *args),
            runtime_dir=runtime_dir,
        )
    proc: asyncio.subprocess.Process | None = None
    stdout_task: asyncio.Task[str] | None = None
    stderr_task: asyncio.Task[str] | None = None
    timed_out = False
    try:
        proc = await create_worker_subprocess(
            spawn_command,
            cwd=str(root),
            env=command_env,
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if on_spawn is not None:
            on_spawn(proc.pid)
        if command_job is not None:
            try:
                command_job.assign(proc.pid)
            except Exception as exc:  # noqa: BLE001 - fail closed before target gate
                await _terminate_windows_tree(proc)
                raise CommandPolicyError(
                    "RunCommand is unavailable because strict Windows process-tree "
                    "assignment failed. No target process was started."
                ) from exc

        # The mission container is a second ownership layer. Failure is
        # non-fatal only when the dedicated strict command Job already owns the
        # Windows launcher; POSIX uses the mission process-group registry.
        if job is not None:
            try:
                job.assign(proc.pid)
            except Exception as exc:  # noqa: BLE001
                if command_job is None:
                    await _terminate_posix_group(proc)
                    raise CommandPolicyError(
                        "RunCommand process-group assignment failed; the command was "
                        "stopped and no result was accepted."
                    ) from exc
                logger.warning(
                    "RunCommand pid=%d is strictly command-contained but could not "
                    "join the additional mission container",
                    proc.pid,
                    exc_info=True,
                )

        if gate_path is not None:
            # The fixed launcher cannot start the target before this exact line.
            gate_path.write_text("go", encoding="ascii")  # noqa: ASYNC240

        stdout_task = asyncio.create_task(_read_limited(proc.stdout))
        stderr_task = asyncio.create_task(_read_limited(proc.stderr))
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_s)
        except TimeoutError:
            timed_out = True
            await _terminate_process_tree(proc)
        except asyncio.CancelledError:
            await _terminate_process_tree(proc)
            raise
        finally:
            # Reap background descendants even when their command leader already
            # returned successfully. On Windows the dedicated Job Object is the
            # atomic path; POSIX uses the command's private process group.
            if command_job is not None:
                await command_job.close()
            elif proc is not None:
                await _terminate_posix_group(proc)

        stdout = await stdout_task
        stderr = await stderr_task
        output = (stdout + stderr)[:_MAX_OUTPUT_CHARS]
        if timed_out:
            return (f"Command timed out after {timeout_s:g}s", True)
        returncode = proc.returncode
        is_error = returncode not in (0, None)
        tag = f"[exit {returncode}] " if is_error else ""
        return (f"{tag}{output}".strip() or "(no output)", is_error)
    finally:
        if proc is not None and proc.returncode is None:
            await _terminate_process_tree(proc)
        if command_job is not None and not command_job.closed:
            await command_job.close()
        if proc is not None and proc.returncode is not None and job is not None:
            release = getattr(job, "release", None)
            if callable(release):
                with contextlib_suppress(Exception):
                    release(proc.pid)
        for task in (stdout_task, stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib_suppress(asyncio.CancelledError):
                    await task


def execute_worker_tool(
    name: str, tool_input: dict[str, Any], *, worktree: Path
) -> tuple[str, bool]:
    """Execute one worker tool. Returns ``(result_text, is_error)``.

    Never raises — every failure (bad args, escape, OS error, non-zero command)
    comes back as ``(message, True)`` so the loop can feed it to the brain as a
    tool_result and let it correct course.
    """
    try:
        if name == "Write":
            path = _safe_path(worktree, str(tool_input["file_path"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            content = str(tool_input.get("content", ""))
            path.write_text(content, encoding="utf-8")
            return (f"Wrote {len(content)} chars to {tool_input['file_path']}", False)

        if name == "Read":
            path = _safe_path(worktree, str(tool_input["file_path"]))
            if not path.is_file():
                return (f"File not found: {tool_input['file_path']}", True)
            text = path.read_text(encoding="utf-8", errors="replace")
            return (text[:_MAX_READ_CHARS], False)

        if name == "Edit":
            path = _safe_path(worktree, str(tool_input["file_path"]))
            if not path.is_file():
                return (f"File not found: {tool_input['file_path']}", True)
            text = path.read_text(encoding="utf-8", errors="replace")
            old = str(tool_input["old_string"])
            new = str(tool_input["new_string"])
            if old not in text:
                return ("old_string not found in file; nothing changed.", True)
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            return (f"Edited {tool_input['file_path']}", False)

        if name == "RunCommand":
            return (
                "RunCommand requires the async worker executor; no process was started.",
                True,
            )
        if name == "Bash":
            return (
                "Bash is unsupported in API workers. Use RunCommand with separate "
                "program and args fields.",
                True,
            )

        if name == "Ls":
            path = _safe_path(worktree, str(tool_input.get("path") or "."))
            if not path.exists():
                return (f"Path not found: {tool_input.get('path', '.')}", True)
            if path.is_file():
                return (path.name, False)
            entries = sorted(
                (e.name + ("/" if e.is_dir() else "")) for e in path.iterdir()
            )
            return ("\n".join(entries) or "(empty)", False)

        return (f"Unknown tool: {name}", True)

    except WorktreeEscapeError as exc:
        return (str(exc), True)
    except KeyError as exc:
        return (f"Missing required argument: {exc}", True)
    except OSError as exc:
        return (f"OS error: {exc}", True)


async def execute_worker_tool_async(
    name: str,
    tool_input: dict[str, Any],
    *,
    worktree: Path,
    env: Mapping[str, str] | None = None,
    job: Any | None = None,
    runtime_dir: Path | None = None,
    on_spawn: Callable[[int], None] | None = None,
) -> tuple[str, bool]:
    """Execute a local worker tool without blocking the event loop.

    Cancellation deliberately propagates to the caller after the active command
    tree is reaped. Policy and operating-system failures are ordinary tool
    errors so the model can select a supported command or correct its arguments.
    """
    try:
        if name == "RunCommand":
            return await _run_command(
                tool_input,
                worktree=worktree,
                env=env,
                job=job,
                runtime_dir=runtime_dir or worktree / ".jarvis-agent-runtime",
                on_spawn=on_spawn,
            )
        return await asyncio.to_thread(
            execute_worker_tool, name, tool_input, worktree=worktree
        )
    except (CommandPolicyError, WorktreeEscapeError) as exc:
        return (str(exc), True)
    except KeyError as exc:
        return (f"Missing required argument: {exc}", True)
    except OSError as exc:
        return (f"OS error: {exc}", True)


_WRITE_TOOLS = frozenset({"Write", "Edit"})


def tool_writes_file(name: str) -> bool:
    """True if a successful call to this tool materialises a worktree file."""
    return name in _WRITE_TOOLS


__all__ = [
    "WORKER_TOOL_SPECS",
    "CommandPolicyError",
    "WorktreeEscapeError",
    "execute_worker_tool",
    "execute_worker_tool_async",
    "tool_writes_file",
]
