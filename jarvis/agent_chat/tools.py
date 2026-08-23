"""The hands of the in-process agent chat.

A deliberately small set in the Claude Code shape — ``Read``, ``Write``,
``Edit``, ``Ls``, ``Glob``, ``Grep``, ``RunCommand`` — scoped to the
session's working directory: relative paths resolve against it, absolute
paths are allowed (the chat is the person's own session on their own
machine, not a mission worktree), and anything that changes state is gated
by the session's permission mode in :mod:`jarvis.agent_chat.runner_api`:

* ``READ_ONLY_TOOLS`` run without asking;
* the rest ask first in ``ask`` mode (the default) and run straight away in
  ``auto`` mode — the same two stances Claude Code offers as "ask" and
  "accept edits / bypass".

Commands run through the platform shell the person would use themselves
(Git Bash where it exists, PowerShell on a bare Windows, ``bash``/``sh`` on
POSIX) with the output capped so a noisy build cannot flood the model's
context. ``NO_WINDOW_CREATIONFLAGS`` keeps the console window away (AP-1) and
the child's stdout is read as UTF-8.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Final

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

READ_ONLY_TOOLS: Final[frozenset[str]] = frozenset({"Read", "Ls", "Glob", "Grep"})
#: Tools that change files but run nothing — "Auto-accept edits" lets these through.
EDIT_TOOLS: Final[frozenset[str]] = frozenset({"Write", "Edit"})
MUTATING_TOOLS: Final[frozenset[str]] = frozenset({"Write", "Edit", "RunCommand"})

_READ_CAP_CHARS = 60_000
_COMMAND_CAP_CHARS = 30_000
_GREP_MAX_RESULTS = 200
_GLOB_MAX_RESULTS = 500
_DEFAULT_COMMAND_TIMEOUT_S = 120.0
_MAX_COMMAND_TIMEOUT_S = 900.0

# Directories no search should descend into — the usual dependency and
# build folders that make a Grep over a project take minutes.
_SKIP_DIRS: Final[frozenset[str]] = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".tox"}
)

TOOL_SPECS: Final[tuple[dict[str, Any], ...]] = (
    {
        "name": "Read",
        "description": (
            "Read a text file. Paths are relative to the working directory unless "
            "absolute. Use offset/limit (1-based line numbers) for long files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "offset": {"type": "integer", "description": "First line to return (1-based)."},
                "limit": {"type": "integer", "description": "How many lines to return."},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Write",
        "description": "Create or overwrite a file with the given full content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Edit",
        "description": (
            "Replace old_string with new_string in a file. old_string must match "
            "exactly; it must be unique unless replace_all is true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "Ls",
        "description": "List a directory (default: the working directory).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    },
    {
        "name": "Glob",
        "description": (
            "Find files by glob pattern, e.g. '**/*.py' or 'src/**/*.tsx', under "
            "path (default: the working directory)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": (
            "Search file contents with a regular expression under path (default: "
            "the working directory). Optional glob filters file names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "RunCommand",
        "description": (
            "Run a shell command in the working directory and return its output. "
            "Use for git, tests, builds, package managers and anything the other "
            "tools cannot do. Commands that need a terminal UI will not work."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_s": {"type": "integer", "description": "Seconds, default 120."},
            },
            "required": ["command"],
        },
    },
)


def tool_names() -> tuple[str, ...]:
    return tuple(str(spec["name"]) for spec in TOOL_SPECS)


def summarize_call(name: str, args: dict[str, Any]) -> str:
    """The one-line label a tool call gets in the timeline and approval card."""
    if name == "RunCommand":
        command = str(args.get("command") or "").strip()
        return command.splitlines()[0][:200] if command else ""
    if name in {"Read", "Write", "Edit"}:
        return str(args.get("file_path") or "")
    if name == "Glob":
        return str(args.get("pattern") or "")
    if name == "Grep":
        return str(args.get("pattern") or "")
    if name == "Ls":
        return str(args.get("path") or ".")
    return ""


# ---------------------------------------------------------------- shell


def shell_argv() -> list[str]:
    """The argv prefix a command string is appended to.

    Git Bash first on Windows (what a developer there has and what the
    models write for), then PowerShell; ``bash`` then ``sh`` elsewhere.
    """
    if sys.platform == "win32":
        for candidate in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
            Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe",
        ):
            try:
                if candidate.is_file():
                    return [str(candidate), "-c"]
            except OSError:
                continue
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if pwsh:
            return [pwsh, "-NoProfile", "-NonInteractive", "-Command"]
        return ["cmd.exe", "/d", "/s", "/c"]
    for name in ("bash", "sh"):
        found = shutil.which(name)
        if found:
            return [found, "-c"]
    return ["/bin/sh", "-c"]


def shell_label() -> str:
    argv = shell_argv()
    name = Path(argv[0]).stem.lower()
    if name == "bash":
        return "bash"
    if name in {"pwsh", "powershell"}:
        return "PowerShell"
    if name == "cmd":
        return "cmd.exe"
    return name


# ---------------------------------------------------------------- paths


def resolve_path(cwd: Path, raw: str | None) -> Path:
    text = (raw or "").strip() or "."
    p = Path(os.path.expanduser(text))
    if not p.is_absolute():
        p = cwd / p
    return p.resolve()


def _display(path: Path, cwd: Path) -> str:
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated, {len(text) - limit} more characters]"


# ---------------------------------------------------------------- tools


async def execute_tool(name: str, args: dict[str, Any], *, cwd: Path) -> tuple[str, bool]:
    """Run one tool. Returns ``(output, is_error)`` — never raises."""
    try:
        if name == "Read":
            return await asyncio.to_thread(_read, args, cwd)
        if name == "Write":
            return await asyncio.to_thread(_write, args, cwd)
        if name == "Edit":
            return await asyncio.to_thread(_edit, args, cwd)
        if name == "Ls":
            return await asyncio.to_thread(_ls, args, cwd)
        if name == "Glob":
            return await asyncio.to_thread(_glob, args, cwd)
        if name == "Grep":
            return await asyncio.to_thread(_grep, args, cwd)
        if name == "RunCommand":
            return await _run_command(args, cwd)
        return f"Unknown tool: {name}", True
    except Exception as exc:  # noqa: BLE001 — a tool failure is data for the model, not a crash
        log.debug("agent chat tool %s failed: %s", name, exc)
        return f"{type(exc).__name__}: {exc}", True


def _read(args: dict[str, Any], cwd: Path) -> tuple[str, bool]:
    path = resolve_path(cwd, args.get("file_path"))
    if not path.is_file():
        return f"File not found: {_display(path, cwd)}", True
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Cannot read {_display(path, cwd)}: {exc}", True
    lines = text.splitlines()
    offset = int(args.get("offset") or 1)
    limit = args.get("limit")
    start = max(0, offset - 1)
    end = start + int(limit) if limit else len(lines)
    chunk = lines[start:end]
    body = "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(chunk))
    header = f"{_display(path, cwd)} ({len(lines)} lines"
    if start or end < len(lines):
        header += f", showing {start + 1}-{min(end, len(lines))}"
    header += ")"
    return _cap(header + "\n" + body, _READ_CAP_CHARS), False


def _write(args: dict[str, Any], cwd: Path) -> tuple[str, bool]:
    path = resolve_path(cwd, args.get("file_path"))
    content = str(args.get("content") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_text(content, encoding="utf-8")
    verb = "Updated" if existed else "Created"
    return f"{verb} {_display(path, cwd)} ({len(content)} chars)", False


def _edit(args: dict[str, Any], cwd: Path) -> tuple[str, bool]:
    path = resolve_path(cwd, args.get("file_path"))
    if not path.is_file():
        return f"File not found: {_display(path, cwd)}", True
    old = str(args.get("old_string") or "")
    new = str(args.get("new_string") or "")
    if not old:
        return "old_string must not be empty", True
    text = path.read_text(encoding="utf-8", errors="replace")
    count = text.count(old)
    if count == 0:
        return f"old_string not found in {_display(path, cwd)}", True
    if count > 1 and not args.get("replace_all"):
        return (
            f"old_string occurs {count} times in {_display(path, cwd)}; make it unique "
            "or set replace_all",
            True,
        )
    updated = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
    path.write_text(updated, encoding="utf-8")
    n = count if args.get("replace_all") else 1
    return f"Edited {_display(path, cwd)} ({n} replacement{'s' if n != 1 else ''})", False


def _ls(args: dict[str, Any], cwd: Path) -> tuple[str, bool]:
    path = resolve_path(cwd, args.get("path"))
    if not path.is_dir():
        return f"Not a directory: {_display(path, cwd)}", True
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return f"Cannot list {_display(path, cwd)}: {exc}", True
    lines = [f"{p.name}/" if p.is_dir() else p.name for p in entries[:_GLOB_MAX_RESULTS]]
    more = len(entries) - len(lines)
    out = f"{path}\n" + "\n".join(lines)
    if more > 0:
        out += f"\n…and {more} more"
    return out, False


def _iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            yield Path(dirpath) / fn


def _glob(args: dict[str, Any], cwd: Path) -> tuple[str, bool]:
    root = resolve_path(cwd, args.get("path"))
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return "pattern is required", True
    if not root.is_dir():
        return f"Not a directory: {_display(root, cwd)}", True
    hits: list[str] = []
    for p in _iter_files(root):
        rel = p.relative_to(root).as_posix()
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern):
            hits.append(rel)
            if len(hits) >= _GLOB_MAX_RESULTS:
                break
    if not hits:
        return f"No files match {pattern!r} under {root}", False
    return "\n".join(hits), False


def _grep(args: dict[str, Any], cwd: Path) -> tuple[str, bool]:
    root = resolve_path(cwd, args.get("path"))
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return "pattern is required", True
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"Invalid regular expression: {exc}", True
    name_glob = str(args.get("glob") or "").strip() or None
    limit = min(int(args.get("max_results") or _GREP_MAX_RESULTS), _GREP_MAX_RESULTS)
    files = [root] if root.is_file() else list(_iter_files(root)) if root.is_dir() else []
    if not files:
        return f"Nothing to search at {_display(root, cwd)}", True
    hits: list[str] = []
    for p in files:
        rel_name = p.relative_to(root).as_posix() if root.is_dir() else p.name
        if name_glob and not (
            fnmatch.fnmatch(p.name, name_glob) or fnmatch.fnmatch(rel_name, name_glob)
        ):
            continue
        try:
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                for no, line in enumerate(fh, start=1):
                    if rx.search(line):
                        rel = p.relative_to(root).as_posix() if root.is_dir() else p.name
                        hits.append(f"{rel}:{no}:{line.rstrip()[:300]}")
                        if len(hits) >= limit:
                            break
        except (OSError, UnicodeError):
            continue
        if len(hits) >= limit:
            break
    if not hits:
        return f"No matches for {pattern!r}", False
    out = "\n".join(hits)
    if len(hits) >= limit:
        out += f"\n…stopped at {limit} matches"
    return out, False


async def _run_command(args: dict[str, Any], cwd: Path) -> tuple[str, bool]:
    command = str(args.get("command") or "").strip()
    if not command:
        return "command is required", True
    timeout = float(args.get("timeout_s") or _DEFAULT_COMMAND_TIMEOUT_S)
    timeout = max(1.0, min(timeout, _MAX_COMMAND_TIMEOUT_S))
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["CI"] = env.get("CI", "1")  # most tools drop interactive prompts/colours on CI
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
        return f"Could not start the shell: {exc}", True
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with _suppress_os():
            proc.kill()
        with _suppress_os():
            await proc.wait()
        return f"Command timed out after {int(timeout)} s: {command}", True
    except asyncio.CancelledError:
        with _suppress_os():
            proc.kill()
        raise
    text = raw.decode("utf-8", errors="replace")
    took = time.perf_counter() - started
    code = proc.returncode
    out = _cap(text.rstrip(), _COMMAND_CAP_CHARS)
    footer = f"\n[exit {code} · {took:.1f}s]"
    return (out + footer).strip(), code != 0


class _suppress_os:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, (OSError, ProcessLookupError))
