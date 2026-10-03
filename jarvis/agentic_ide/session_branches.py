"""Attribute branches to a pane's successful creation calls, never to its CWD.

The CLI transcript is used only as ownership evidence. GitHub remains the sole
source of remote branch, PR and check status. Unrecognised commands fail closed.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from jarvis.agentic_ide import agent_transcript, git_overview


@dataclass(frozen=True)
class PaneBranchRecord:
    pane: str
    history_id: str
    folder: str
    agent: str = ""
    session_id: str = ""
    home: Path | None = None
    # Set by Jarvis only when it creates a fork's new branch/worktree.
    created_branch: str = ""
    started_at: float = 0


@dataclass(frozen=True)
class CreatedBranch:
    branch: str
    folder: str
    created_at: float = 0
    silent_success: bool = False


_STRING = r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')"""
_EXEC = re.compile(r"\b(?:tools\.)?exec_command\s*\(\s*\{(?:" + _STRING + r"""|[^}"'])*\}""")
_FIELD = re.compile(r"""(?<!\w)["']?(cmd|workdir)["']?\s*:\s*(""" + _STRING + r")")
_CREATED = re.compile(r"(?:new branch|[Cc]reated branch)\s+['\"]([^'\"]+)['\"]")


def _commands(arguments: dict[str, Any]) -> Iterable[tuple[str, str]]:
    command = arguments.get("cmd") or arguments.get("command") or arguments.get("CommandLine")
    if isinstance(command, str):
        yield command, str(arguments.get("workdir") or arguments.get("cwd") or "")
        return
    # Codex code-mode records the executed tool calls inside a JS orchestration
    # string. Parse only literal exec_command arguments; never execute JS or
    # interpret agent prose, file contents, templates or dynamic expressions.
    source = arguments.get("input")
    if not isinstance(source, str):
        return
    for match in _EXEC.finditer(source):
        fields: dict[str, str] = {}
        for key, literal in _FIELD.findall(match[0]):
            try:
                value = ast.literal_eval(literal)
            except (SyntaxError, ValueError):
                logger.debug("Session branch: unsupported command argument literal")
                continue
            if isinstance(value, str):
                fields[key] = value
        if re.search(r"""\bworkdir["']?\s*:""", match[0]) and "workdir" not in fields:
            # A dynamic workdir can name a different repository. Its absence
            # from the literal fields is not permission to assume this pane.
            continue
        if fields.get("cmd"):
            yield fields["cmd"], fields.get("workdir", "")


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'" else value


def created_by_command(command: str, folder: str) -> list[CreatedBranch]:
    """Understand literal Git creation commands; checking out a branch is not creation."""
    lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        words = [_unquote(word) for word in lexer]
    except ValueError:
        logger.debug("Session branch: incomplete quoted command")
        return []
    segments: list[list[str]] = [[]]
    for word in words:
        if word and all(character in ";&|\n" for character in word):
            segments.append([])
        else:
            segments[-1].append(word)
    cwd = Path(folder)
    found = []
    for args in segments:
        if not args:
            continue
        if args[0].lower() in {"cd", "set-location", "pushd"} and len(args) == 2:
            cwd = (cwd / args[1]).resolve()
            continue
        if args[0] not in {"git", "git.exe"}:
            continue
        args = args[1:]
        root = cwd
        while len(args) >= 2 and args[0] in {"-C", "-c"}:
            if args[0] == "-C":
                root = (root / args[1]).resolve()
            args = args[2:]
        branch = ""
        silent = False
        if args[:2] == ["worktree", "add"] and "-b" in args:
            index = args.index("-b")
            if index + 1 < len(args):
                branch = args[index + 1]
        elif len(args) >= 3 and args[0] in {"switch", "checkout"}:
            flag = "-c" if args[0] == "switch" else "-b"
            for option in (flag, "--create" if flag == "-c" else "-b"):
                if option in args and args.index(option) + 1 < len(args):
                    branch = args[args.index(option) + 1]
        elif len(args) >= 2 and args[0] == "branch" and not args[1].startswith("-"):
            branch = args[1]
            silent = len(segments) == 1 and len(args) in {2, 3}
        if branch and not branch.startswith("-"):
            found.append(CreatedBranch(branch=branch, folder=str(root), silent_success=silent))
    return found


def created_branches(
    events: Iterable[dict[str, Any]], folder: str, *, not_before: float = 0
) -> list[CreatedBranch]:
    calls: dict[str, list[CreatedBranch]] = {}
    found: list[CreatedBranch] = []
    for event in events:
        # A native conversation fork copies earlier tool records. Those belong
        # to the parent session, not to the newly spawned pane.
        if not_before and (event.get("ts_ms") or 0) < not_before * 1000:
            continue
        payload = event.get("payload") or {}
        identity = str(payload.get("call_id") or "")
        if event.get("kind") == "tool_call":
            tool = str(payload.get("name") or "").rsplit(".", 1)[-1].lower()
            if tool not in {
                "exec",
                "exec_command",
                "bash",
                "shell",
                "run_shell_command",
                "run_command",
                "run_terminal_cmd",
            }:
                continue
            arguments = payload.get("input") or {}
            if not isinstance(arguments, dict):
                continue
            candidates = []
            for command, cwd in _commands(arguments):
                candidates.extend(
                    created_by_command(
                        command, str((Path(folder) / cwd).resolve()) if cwd else folder
                    )
                )
            calls[identity] = candidates
        elif event.get("kind") == "tool_result" and not payload.get("is_error"):
            output = str(payload.get("output") or "")
            names = set(_CREATED.findall(output))
            # A bare `git branch name` is silent on success. Require an actual
            # zero exit code for that case, not merely the absence of is_error.
            succeeded = bool(
                re.search(r'(?:"exit_code"\s*:\s*0\b|Process exited with code 0\b)', output)
            )
            for candidate in calls.pop(identity, []):
                if candidate.branch in names or (
                    candidate.silent_success and succeeded and not names and "fatal:" not in output
                ):
                    found.append(
                        CreatedBranch(
                            candidate.branch, candidate.folder, (event.get("ts_ms") or 0) / 1000
                        )
                    )
    return found


_cache: dict[tuple[str, str, str, float], tuple[float, list[CreatedBranch]]] = {}
_cache_lock = threading.Lock()


def _common(folder: str) -> str:
    path = Path(folder)
    value = git_overview._out(["rev-parse", "--git-common-dir"], path) if path.is_dir() else None
    return os.path.normcase(str((path / value.strip()).resolve())) if value else ""


def owned_branch(record: PaneBranchRecord) -> CreatedBranch | None:
    if record.created_branch:
        return CreatedBranch(record.created_branch, record.folder)
    if not record.session_id or not agent_transcript.can_read(record.agent):
        return None
    key = (record.history_id, record.session_id, record.folder, record.started_at)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
    if cached is None or now - cached[0] >= 15:
        events = (
            agent_transcript.read_events(record.agent, record.session_id, home=record.home) or []
        )
        candidates = created_branches(events, record.folder, not_before=record.started_at)
        with _cache_lock:
            _cache[key] = (now, candidates)
            for expired in [key for key, (at, _) in _cache.items() if now - at > 300]:
                del _cache[expired]
    else:
        candidates = cached[1]
    if not candidates:
        return None
    root = _common(record.folder)
    if not root:
        return None
    for candidate in reversed(candidates):
        if _common(candidate.folder) == root:
            return candidate
    return None
