"""Worktree-scoped file/shell tools for the in-process API-agent worker.

These are the hands of :class:`ApiAgentWorker` — the worker that drives an
OpenAI-compatible brain (openai / openrouter) in a tool-use loop. The
CLI workers (claude / codex / agy) get Write/Edit/Read/Bash from their own
binary; an in-process brain has none, so we supply a minimal, deliberately
small set here.

Security model: every path is resolved INSIDE the per-mission git worktree and
rejected if it escapes (``..`` / absolute path outside the tree). The worker is
already isolated in a worktree + Job Object (AD-OE4), so these tools run with no
risk-tier confirmation — but they must never let the model touch the user's real
tree. Tool names match the claude-CLI vocabulary (Write/Edit/Read/Bash/Ls) so
the Critic's evidence extractors (`stream_evidence._WRITE_TOOL_NAMES`,
`_SHELL_TOOL_NAMES`) credit them with zero changes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

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
        "name": "Bash",
        "description": (
            "Run a shell command in the workspace directory and return stdout+stderr. "
            "Use for builds, tests, listing files, git, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_s": {"type": "number", "description": "Optional timeout (default 120)."},
            },
            "required": ["command"],
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
_DEFAULT_BASH_TIMEOUT = 120.0


class WorktreeEscapeError(ValueError):
    """Raised when a tool path resolves outside the worktree."""


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

        if name == "Bash":
            command = str(tool_input["command"])
            timeout_s = float(tool_input.get("timeout_s") or _DEFAULT_BASH_TIMEOUT)
            proc = subprocess.run(  # noqa: S602 — a Bash tool intentionally runs shell commands; the worker is sandboxed in a worktree + Job Object
                command,
                shell=True,
                cwd=str(worktree),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            out = out[:_MAX_OUTPUT_CHARS]
            is_error = proc.returncode != 0
            tag = "" if not is_error else f"[exit {proc.returncode}] "
            return (f"{tag}{out}".strip() or "(no output)", is_error)

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
    except subprocess.TimeoutExpired:
        return (f"Command timed out after {_DEFAULT_BASH_TIMEOUT:.0f}s", True)
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
    "WorktreeEscapeError",
    "execute_worker_tool",
    "tool_writes_file",
]
