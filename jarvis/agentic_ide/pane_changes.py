"""Everything one pane's agent changed, committed or not.

A coding agent commits as it goes, so "what is uncommitted" is usually empty
by the time someone looks — the pane's review must show the agent's work, not
the working tree's leftovers. So the review compares each file the agent's
record says it wrote against the code as it stood BEFORE the agent's first
write: the last commit on the checkout's first-parent line made before that
moment (:func:`session_base`), diffed against the file on disk now. That
covers work the agent committed, work still uncommitted, and new files.

Another agent's commits to the same files after that moment show up in the
diff too — git cannot split one file's history by author once it is merged
into one line. The file list itself stays this agent's own.

Paths are workspace-relative POSIX paths, the shape :mod:`.git_changes` uses;
git runs with ``--relative`` from the workspace folder, so a workspace below
the repository root works the same.
"""

from __future__ import annotations

import re
from pathlib import Path

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
    workspace_changes,
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
    pending = {item.path for item in workspace_changes(root, only=set(wanted)).files}
    return files, pending, truncated


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


__all__ = ["BASE_PATTERN", "pane_changes", "pane_file_diff", "session_base"]
