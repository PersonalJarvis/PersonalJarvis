"""What the agents changed in a workspace: git status and per-file diffs.

The IDE's Explorer tab shows which files an agent touched and, on a click,
exactly how — removed lines red, added lines green. Everything here is read
through the ``git`` CLI, the one git implementation that is on every machine
that has git at all, and every call is bounded:

* **Workspace-scoped.** A workspace may be a sub-folder of a repository; only
  changes under that folder are reported, with paths relative to it — the same
  paths the file tree uses.
* **Degrades, never raises.** No git, not a repository, a repository without
  commits, a timeout: each comes back as an honest ``available=False`` or an
  empty list, never an exception into a route.
* **Capped.** A repository with thousands of generated files must not turn one
  poll into a multi-megabyte answer; untracked folders stay collapsed the way
  ``git status`` shows them, and lists and diffs are cut with ``truncated``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from loguru import logger

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

_GIT_TIMEOUT_S = 8.0
#: Most changed files reported in one answer.
MAX_CHANGED_FILES = 500
#: Most diff lines returned for one file; the rest is summarised by ``truncated``.
MAX_DIFF_LINES = 4000
#: Untracked text files up to this size have their lines counted and shown.
MAX_UNTRACKED_BYTES = 512 * 1024


@dataclass(slots=True)
class ChangedFile:
    """One changed path, relative to the workspace root."""

    path: str
    #: ``modified`` | ``added`` | ``deleted`` | ``untracked`` | ``conflicted``
    status: str
    added: int | None = None
    removed: int | None = None
    is_directory: bool = False


@dataclass(slots=True)
class WorkspaceChanges:
    available: bool
    branch: str = ""
    files: list[ChangedFile] = field(default_factory=list)
    truncated: bool = False
    #: Why ``available`` is false, in one plain sentence.
    reason: str = ""


@dataclass(slots=True)
class DiffLine:
    #: ``add`` | ``del`` | ``ctx``
    kind: str
    text: str
    old_no: int | None = None
    new_no: int | None = None


@dataclass(slots=True)
class DiffHunk:
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass(slots=True)
class FileDiff:
    path: str
    status: str
    binary: bool = False
    hunks: list[DiffHunk] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    truncated: bool = False


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    """Run git; None when git is missing or does not answer in time."""
    try:
        return subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except FileNotFoundError:
        # No git binary installed: the caller shows 'no changes' honestly.
        return None
    except subprocess.TimeoutExpired:
        logger.info("Agentic IDE git changes: git timed out in {}", cwd)
        return None


def _repo_prefix(folder: Path) -> tuple[bool, str, str]:
    """(is a repo, the folder's path inside the repo with a trailing /, reason)."""
    result = _git(["rev-parse", "--show-prefix"], folder)
    if result is None:
        return False, "", "Git is not installed or did not answer."
    if result.returncode != 0:
        return False, "", "This folder is not a git repository."
    return True, result.stdout.strip(), ""


def _strip_prefix(repo_path: str, prefix: str) -> str | None:
    """A repo-relative path as a workspace-relative one, or None if outside."""
    if not prefix:
        return repo_path
    return repo_path[len(prefix) :] if repo_path.startswith(prefix) else None


def _status_word(xy: str) -> str:
    if xy == "??":
        return "untracked"
    if "U" in xy or xy in {"AA", "DD"}:
        return "conflicted"
    if "D" in xy:
        return "deleted"
    if "A" in xy:
        return "added"
    return "modified"


def _has_head(folder: Path) -> bool:
    result = _git(["rev-parse", "--verify", "--quiet", "HEAD"], folder)
    return result is not None and result.returncode == 0


def _count_lines(path: Path) -> int | None:
    try:
        if path.stat().st_size > MAX_UNTRACKED_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        # Unreadable file: the count is unknown and None says exactly that.
        return None
    if b"\0" in data[:8000]:
        return None
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def workspace_changes(folder: str | Path) -> WorkspaceChanges:
    """Every changed path under ``folder``, with line counts where cheap."""
    root = Path(folder).expanduser()
    if not root.is_dir():
        return WorkspaceChanges(available=False, reason="The workspace folder is missing.")
    is_repo, prefix, reason = _repo_prefix(root)
    if not is_repo:
        return WorkspaceChanges(available=False, reason=reason)

    branch_result = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    branch = branch_result.stdout.strip() if branch_result and branch_result.returncode == 0 else ""

    status = _git(
        ["status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=normal", "--", "."],
        root,
    )
    if status is None or status.returncode != 0:
        return WorkspaceChanges(
            available=False, branch=branch, reason="Git could not read the changes."
        )

    counts: dict[str, tuple[int | None, int | None]] = {}
    numstat_args = ["diff", "--numstat", "-z", "--no-renames"]
    if _has_head(root):
        numstat_args.append("HEAD")
    numstat = _git([*numstat_args, "--", "."], root)
    if numstat is not None and numstat.returncode == 0:
        for record in numstat.stdout.split("\0"):
            parts = record.split("\t")
            if len(parts) != 3:
                continue
            added, removed, repo_path = parts
            counts[repo_path] = (
                int(added) if added.isdigit() else None,
                int(removed) if removed.isdigit() else None,
            )

    files: list[ChangedFile] = []
    truncated = False
    for record in status.stdout.split("\0"):
        if len(record) < 4:
            continue
        xy, repo_path = record[:2], record[3:]
        rel = _strip_prefix(repo_path, prefix)
        if rel is None or not rel:
            continue
        if len(files) >= MAX_CHANGED_FILES:
            truncated = True
            break
        word = _status_word(xy)
        is_dir = rel.endswith("/")
        added, removed = counts.get(repo_path, (None, None))
        if word == "untracked" and not is_dir:
            added, removed = _count_lines(root / rel), 0
        files.append(
            ChangedFile(
                path=rel.rstrip("/"),
                status=word,
                added=added,
                removed=removed,
                is_directory=is_dir,
            )
        )
    files.sort(key=lambda item: item.path.lower())
    return WorkspaceChanges(available=True, branch=branch, files=files, truncated=truncated)


def normalize_workspace_path(folder: str | Path, path: str) -> str:
    """A path printed or picked in the IDE as a workspace-relative POSIX path.

    Accepts a workspace-relative path or an absolute one inside the workspace.
    The file need not exist — a deleted file still has a diff. Raises
    ``ValueError`` for anything that would leave the workspace.
    """
    value = path.strip().replace("\\", "/")
    if not value:
        raise ValueError("No path given.")
    root = Path(folder).expanduser().resolve()
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        try:
            rel = candidate.resolve().relative_to(root).as_posix()
        except (OSError, ValueError) as exc:
            raise ValueError("That path is outside the open workspace.") from exc
    else:
        posix = PurePosixPath(value[2:] if value.startswith("./") else value)
        if ".." in posix.parts:
            raise ValueError("That path is outside the open workspace.")
        rel = posix.as_posix()
        try:
            (root / rel).resolve().relative_to(root)
        except (OSError, ValueError) as exc:
            raise ValueError("That path is outside the open workspace.") from exc
    if rel in {"", "."}:
        raise ValueError("That is the workspace folder itself.")
    return rel


def _parse_unified(text: str) -> tuple[list[DiffHunk], int, int, bool, bool]:
    """Hunks, added, removed, binary, truncated from ``git diff`` output."""
    hunks: list[DiffHunk] = []
    added = removed = shown = 0
    old_no = new_no = 0
    truncated = False
    for line in text.splitlines():
        if line.startswith("Binary files ") and line.endswith(" differ"):
            return [], 0, 0, True, False
        if line.startswith("@@"):
            header = line
            try:
                spans = line.split("@@")[1].strip().split(" ")
                old_no = int(spans[0][1:].split(",")[0])
                new_no = int(spans[1][1:].split(",")[0])
            except (IndexError, ValueError):
                # Malformed hunk header: keep the hunk, only its line numbers are lost.
                old_no = new_no = 0
            hunks.append(DiffHunk(header=header))
            continue
        if not hunks:
            continue  # the file header before the first hunk
        if line.startswith("\\"):
            continue  # "\ No newline at end of file"
        kind = {"+": "add", "-": "del"}.get(line[:1], "ctx")
        if kind == "add":
            added += 1
        elif kind == "del":
            removed += 1
        if shown >= MAX_DIFF_LINES:
            truncated = True
            continue
        shown += 1
        hunks[-1].lines.append(
            DiffLine(
                kind=kind,
                text=line[1:],
                old_no=None if kind == "add" else old_no,
                new_no=None if kind == "del" else new_no,
            )
        )
        if kind != "add":
            old_no += 1
        if kind != "del":
            new_no += 1
    return hunks, added, removed, False, truncated


def file_diff(folder: str | Path, path: str) -> FileDiff:
    """How one file differs from the last commit; an untracked file is all new."""
    rel = normalize_workspace_path(folder, path)
    root = Path(folder).expanduser()
    is_repo, prefix, _reason = _repo_prefix(root)
    if not is_repo:
        return FileDiff(path=rel, status="unchanged")

    status = _git(
        ["status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all", "--", rel], root
    )
    record = next(
        (item for item in (status.stdout.split("\0") if status else []) if len(item) >= 4), ""
    )
    word = _status_word(record[:2]) if record else "unchanged"

    if word == "untracked":
        target = root / rel
        count = _count_lines(target)
        if count is None:
            return FileDiff(path=rel, status=word, binary=target.is_file())
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        shown = lines[:MAX_DIFF_LINES]
        hunk = DiffHunk(
            header=f"@@ -0,0 +1,{len(lines)} @@",
            lines=[
                DiffLine(kind="add", text=line, new_no=index + 1)
                for index, line in enumerate(shown)
            ],
        )
        return FileDiff(
            path=rel,
            status=word,
            hunks=[hunk],
            added=len(lines),
            truncated=len(lines) > len(shown),
        )

    args = ["diff", "--no-color", "--no-ext-diff", "--no-renames", "-U3"]
    if _has_head(root):
        args.append("HEAD")
    diff = _git([*args, "--", rel], root)
    if diff is None or diff.returncode != 0:
        return FileDiff(path=rel, status=word)
    hunks, added, removed, binary, truncated = _parse_unified(diff.stdout)
    return FileDiff(
        path=rel,
        status=word,
        binary=binary,
        hunks=hunks,
        added=added,
        removed=removed,
        truncated=truncated,
    )


__all__ = [
    "MAX_CHANGED_FILES",
    "MAX_DIFF_LINES",
    "ChangedFile",
    "DiffHunk",
    "DiffLine",
    "FileDiff",
    "WorkspaceChanges",
    "file_diff",
    "normalize_workspace_path",
    "workspace_changes",
]
