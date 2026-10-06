"""Everything one pane's agent changed, committed or not.

A coding agent commits as it goes, so "what is uncommitted" is usually empty
by the time someone looks — the pane's review must show the agent's work, not
the working tree's leftovers. So the review compares each file the agent's
record says it wrote against the code as it stood BEFORE the agent's first
write: the last commit on the checkout's first-parent line made before that
moment (:func:`session_base`), diffed against the file on disk now. That
covers work the agent committed, work still uncommitted, and new files.

Which files are the agent's (:func:`pane_work`): the ones its editing tools
named, plus every file in the commits its own shell commands made — found by
the ids those commands printed and the subjects they passed. That second half
is what catches a file the agent changed with a script. Files
``.gitattributes`` marks ``linguist-generated`` (a built bundle) are counted,
not listed, so they cannot bury the agent's real changes.

Another agent's commits to the same files after that moment show up in the
diff too — git cannot split one file's history by author once it is merged
into one line. The file list itself stays this agent's own.

Paths are workspace-relative POSIX paths, the shape :mod:`.git_changes` uses;
git runs with ``--relative`` from the workspace folder, so a workspace below
the repository root works the same.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import change_authors
from .git_changes import (
    MAX_CHANGED_FILES,
    ChangedFile,
    FileDiff,
    _count_lines,
    _git,
    _has_head,
    _parse_unified,
    _repo_prefix,
    file_diff,
    normalize_workspace_path,
)

#: Paths handed to one git call; keeps the command line far below Windows' limit.
_PATHS_PER_CALL = 100

#: A commit or tree id as the client may send it back.
BASE_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")


#: The empty tree's id per object format, for "before the first commit".
_EMPTY_TREES = {
    "sha1": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
    "sha256": "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321",
}


def _empty_tree(root: Path) -> str | None:
    """The id of an empty tree in this repository's hash."""
    result = _git(["rev-parse", "--show-object-format"], root)
    fmt = result.stdout.strip() if result is not None and result.returncode == 0 else "sha1"
    return _EMPTY_TREES.get(fmt or "sha1")


def session_base(folder: str | Path, since_ms: int) -> str | None:
    """The commit the checkout stood on just before ``since_ms``; HEAD when the time is unknown.

    The empty tree when every commit is newer (the agent started the repo).
    None when git cannot answer.
    """
    root = Path(folder).expanduser()
    if not _has_head(root):
        return _empty_tree(root)
    if since_ms <= 0:
        head = _git(["rev-parse", "HEAD"], root)
        return head.stdout.strip() if head and head.returncode == 0 else None
    found = _git(
        ["rev-list", "-1", "--first-parent", f"--before=@{since_ms // 1000}", "HEAD"], root
    )
    if found is None or found.returncode != 0:
        return None
    return found.stdout.strip() or _empty_tree(root)


def _chunks(paths: list[str]) -> list[list[str]]:
    return [paths[i : i + _PATHS_PER_CALL] for i in range(0, len(paths), _PATHS_PER_CALL)]


def _status_from_letter(letter: str) -> str:
    return {"A": "added", "D": "deleted"}.get(letter[:1], "modified")


def pane_changes(
    folder: str | Path, paths: set[str], base: str
) -> tuple[list[ChangedFile], set[str], bool]:
    """``(files that differ from base, the ones still uncommitted, truncated)`` among ``paths``."""
    root = Path(folder).expanduser()
    wanted = sorted(paths)
    status: dict[str, str] = {}
    counts: dict[str, tuple[int | None, int | None]] = {}
    untracked: set[str] = set()
    for chunk in _chunks(wanted):
        named = _git(
            ["diff", "--name-status", "-z", "--no-renames", "--relative", base, "--", *chunk], root
        )
        if named is not None and named.returncode == 0:
            parts = named.stdout.split("\0")
            for letter, path in zip(parts[0::2], parts[1::2], strict=False):
                if path:
                    status[path] = _status_from_letter(letter)
        numstat = _git(
            ["diff", "--numstat", "-z", "--no-renames", "--relative", base, "--", *chunk], root
        )
        if numstat is not None and numstat.returncode == 0:
            for record in numstat.stdout.split("\0"):
                bits = record.split("\t")
                if len(bits) == 3:
                    counts[bits[2]] = (
                        int(bits[0]) if bits[0].isdigit() else None,
                        int(bits[1]) if bits[1].isdigit() else None,
                    )
        others = _git(["ls-files", "--others", "--exclude-standard", "-z", "--", *chunk], root)
        if others is not None and others.returncode == 0:
            untracked.update(path for path in others.stdout.split("\0") if path)

    files: list[ChangedFile] = []
    truncated = False
    for path in wanted:
        if path in untracked:
            word, added, removed = "untracked", _count_lines(root / path), 0
        elif path in status:
            word = status[path]
            added, removed = counts.get(path, (None, None))
        else:
            continue  # the agent's edits to it are gone again, or it never left the base
        if len(files) >= MAX_CHANGED_FILES:
            truncated = True
            break
        files.append(ChangedFile(path=path, status=word, added=added, removed=removed))
    return files, _pending(root, [item.path for item in files]), truncated


def _pending(root: Path, paths: list[str]) -> set[str]:
    """Which of ``paths`` git still reports as changed or untracked in the working tree."""
    pending: set[str] = set()
    _is_repo, prefix, _reason = _repo_prefix(root)
    for chunk in _chunks(paths):
        status = _git(
            ["status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all"]
            + ["--", *chunk],
            root,
        )
        if status is None or status.returncode != 0:
            continue
        for record in status.stdout.split("\0"):
            if len(record) >= 4 and record[3:].startswith(prefix):
                pending.add(record[3 + len(prefix) :])
    return pending


def pane_file_diff(folder: str | Path, path: str, base: str) -> FileDiff:
    """How one file differs from ``base`` now; an untracked file is all new."""
    rel = normalize_workspace_path(folder, path)
    root = Path(folder).expanduser()
    if not BASE_PATTERN.match(base):
        raise ValueError("That is not a commit id.")
    is_repo, _prefix, _reason = _repo_prefix(root)
    if not is_repo:
        return FileDiff(path=rel, status="unchanged")
    others = _git(["ls-files", "--others", "--exclude-standard", "-z", "--", rel], root)
    if others is not None and others.returncode == 0 and others.stdout.strip("\0"):
        return file_diff(root, rel)
    diff = _git(
        ["diff", "--no-color", "--no-ext-diff", "--no-renames", "--relative", "-U3"]
        + [base, "--", rel],
        root,
    )
    if diff is None or diff.returncode != 0:
        return FileDiff(path=rel, status="unchanged")
    named = _git(["diff", "--name-status", "--no-renames", "--relative", base, "--", rel], root)
    letter = named.stdout.strip()[:1] if named is not None and named.returncode == 0 else ""
    hunks, added, removed, binary, truncated = _parse_unified(diff.stdout)
    return FileDiff(
        path=rel,
        status=_status_from_letter(letter) if letter else "unchanged",
        binary=binary,
        hunks=hunks,
        added=added,
        removed=removed,
        truncated=truncated,
    )


#: A tool that runs a shell command: Bash, PowerShell, Codex's shell, exec tools.
_SHELL_TOOL = re.compile(r"bash|shell|powershell|pwsh|exec|command|terminal", re.IGNORECASE)
#: git commit's summary line: ``[main 4f7ae66] subject`` or ``[main (root-commit) 4f7ae66]``.
_COMMIT_SUMMARY = re.compile(r"^\[[^\]\n]*?\b([0-9a-f]{7,40})\]", re.MULTILINE)
#: A full commit id printed by a script around ``git commit-tree`` and the like.
_FULL_ID = re.compile(r"\b[0-9a-f]{40}\b")
#: ``-m "…"`` / ``-am '…'`` / ``--message=…`` in a commit command.
_MESSAGE_FLAG = re.compile(
    r"""(?:\s-[A-Za-z]*m|--message)(?:\s+|=)(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""", re.DOTALL
)
#: A here-document body, as agents write multi-line commit messages.
_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n\s*\1\b", re.DOTALL)
#: Most commit ids checked per record; one git call each.
_MAX_COMMIT_IDS = 200


@dataclass(frozen=True, slots=True)
class CommitRefs:
    """What an agent's own shell commands say about the commits they made."""

    ids: frozenset[str]
    subjects: frozenset[str]


@dataclass(slots=True)
class PaneWork:
    """The files one agent changed: ``{path: last change ms}``, and when it started."""

    files: dict[str, int] = field(default_factory=dict)
    #: The earliest change (edit or commit), 0 when unknown.
    since_ms: int = 0


def _command_text(args: Any) -> str:
    if isinstance(args, str):
        return args
    if isinstance(args, dict):
        for key in ("command", "cmd", "script", "commands"):
            value = args.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                return " ".join(str(part) for part in value)
    return ""


def _subject(message: str) -> str:
    """A commit message's first line, from a here-document when it is written as one."""
    heredoc = _HEREDOC.search(message)
    if heredoc:
        message = heredoc.group(2)
    return next((line.strip() for line in message.splitlines() if line.strip()), "")


def agent_commit_refs(events: Iterable[dict[str, Any]]) -> CommitRefs:
    """The commits an agent's shell commands made, by id (from the output) and subject.

    Only commands that mention ``commit`` count, so an id printed by ``git log``
    in some unrelated command never makes another person's commit look like
    this agent's.
    """
    commands: set[str] = set()
    ids: set[str] = set()
    subjects: set[str] = set()
    for event in events:
        payload = event.get("payload") or {}
        call_id = str(payload.get("call_id"))
        if event.get("kind") == "tool_call":
            if not _SHELL_TOOL.search(str(payload.get("name") or "")):
                continue
            text = _command_text(payload.get("input"))
            if "commit" not in text:
                continue
            commands.add(call_id)
            for match in _MESSAGE_FLAG.finditer(text):
                if match.group(1) is not None:
                    message = re.sub(r"\\(.)", r"\1", match.group(1))
                else:
                    message = match.group(2)
                if subject := _subject(message):
                    subjects.add(subject)
            # `git commit -F - <<'EOF'`: the message is the here-document itself.
            for heredoc in _HEREDOC.finditer(text):
                opener = text[: heredoc.start()].rsplit("\n", 1)[-1]
                if "commit" in opener and (subject := _subject(heredoc.group(2))):
                    subjects.add(subject)
        elif event.get("kind") == "tool_result" and call_id in commands:
            output = payload.get("output")
            text = output if isinstance(output, str) else json.dumps(output)
            ids.update(_COMMIT_SUMMARY.findall(text))
            ids.update(_FULL_ID.findall(text))
    return CommitRefs(frozenset(ids), frozenset(subjects))


def resolve_commits(folder: str | Path, refs: CommitRefs, since_ms: int) -> dict[str, int]:
    """``{commit id: commit time ms}`` for the refs that are real commits of this session.

    A commit older than the record (less a minute of clock slack) is not this
    session's, whatever printed its id.
    """
    root = Path(folder).expanduser()
    floor = max(0, since_ms // 1000 - 60) if since_ms > 0 else 0
    found: dict[str, int] = {}
    for ref in sorted(refs.ids)[:_MAX_COMMIT_IDS]:
        result = _git(["log", "-1", "--format=%H%x1f%ct", f"{ref}^{{commit}}", "--"], root)
        if result is None or result.returncode != 0:
            continue
        sha, _sep, seconds = result.stdout.strip().partition("\x1f")
        if sha and seconds.isdigit() and int(seconds) >= floor:
            found[sha] = int(seconds) * 1000
    if refs.subjects:
        window = [f"--since=@{floor}"] if floor else ["-n", "2000"]
        result = _git(["log", "--branches", *window, "--format=%H%x1f%ct%x1f%s"], root)
        for line in (result.stdout.splitlines() if result and result.returncode == 0 else []):
            sha, _a, rest = line.partition("\x1f")
            seconds, _b, subject = rest.partition("\x1f")
            if subject.strip() in refs.subjects and seconds.isdigit():
                found[sha] = int(seconds) * 1000
    return found


def commit_files(folder: str | Path, commits: dict[str, int]) -> dict[str, int]:
    """``{workspace-relative path: newest commit time ms}`` across ``commits``."""
    root = Path(folder).expanduser()
    files: dict[str, int] = {}
    shas = sorted(commits)
    for start in range(0, len(shas), 50):
        chunk = shas[start : start + 50]
        result = _git(
            ["show", "--no-renames", "--name-only", "--relative", "--format=%x1e%H", *chunk, "--"],
            root,
        )
        if result is None or result.returncode != 0:
            continue
        for block in result.stdout.split("\x1e"):
            lines = [line for line in block.splitlines() if line.strip()]
            if not lines or lines[0] not in commits:
                continue
            when = commits[lines[0]]
            for path in lines[1:]:
                files[path] = max(files.get(path, 0), when)
    return files


def generated_paths(folder: str | Path, paths: set[str]) -> set[str]:
    """The paths ``.gitattributes`` marks ``linguist-generated`` (build output, bundles)."""
    root = Path(folder).expanduser()
    found: set[str] = set()
    if not paths:
        return found
    result = _git(
        ["check-attr", "--stdin", "-z", "linguist-generated"], root, stdin="\0".join(sorted(paths))
    )
    if result is None or result.returncode != 0:
        return found
    parts = result.stdout.split("\0")
    for path, _attr, value in zip(parts[0::3], parts[1::3], parts[2::3], strict=False):
        if value in ("set", "true"):
            found.add(path)
    return found


def pane_work(
    folder: str | Path, events: list[dict[str, Any]], record_folder: str | Path | None = None
) -> PaneWork:
    """Every file one agent changed: through its editing tools, and in the commits it made.

    The commits catch what an editing tool never names — a file a script or
    a shell command changed, once the agent committed it.
    """
    root = Path(folder).expanduser().resolve(strict=False)
    base = Path(record_folder or root).expanduser()
    work = PaneWork()
    starts: list[int] = []
    for raw, (first, last) in change_authors.write_spans(events).items():
        relative = change_authors.relative_path(raw, base, root)
        if relative is None:
            continue
        work.files[relative] = max(work.files.get(relative, 0), last)
        if first:
            starts.append(first)
    session_start = min((int(e.get("ts_ms") or 0) for e in events if e.get("ts_ms")), default=0)
    commits = resolve_commits(root, agent_commit_refs(events), session_start)
    for path, when in commit_files(root, commits).items():
        work.files[path] = max(work.files.get(path, 0), when)
    # A commit carries whole seconds: one second earlier is safely before it.
    starts.extend(when - 1000 for when in commits.values())
    work.since_ms = min(starts, default=0)
    return work


__all__ = [
    "BASE_PATTERN",
    "CommitRefs",
    "PaneWork",
    "agent_commit_refs",
    "commit_files",
    "generated_paths",
    "pane_changes",
    "pane_file_diff",
    "pane_work",
    "resolve_commits",
    "session_base",
]
