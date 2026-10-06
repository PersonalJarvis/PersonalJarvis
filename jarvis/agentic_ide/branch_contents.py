"""What one branch holds: its own commits and the files it changes.

Backs the IDE Git tab's folded-open branch row, so a branch can be read in
place instead of in an editor. A branch is compared with the default branch
the way a pull request is: commits reachable from the branch but not from the
base, and files changed since the two split (``base...branch``). The base is
``origin/<default>`` when this clone has it, since the local default branch
may have drifted from what GitHub merges into.

Everything is read-only git on refs this clone already has; a GitHub-only
branch is readable once fetched.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from jarvis.agentic_ide.git_changes import FileDiff, _git, _parse_unified

#: Most commits listed for one branch; ``commits_truncated`` says when more exist.
MAX_COMMITS = 30
#: Most changed files listed for one branch.
MAX_FILES = 300

_STATUS = {"A": "added", "D": "deleted", "M": "modified", "T": "modified"}


@dataclass(slots=True)
class BranchCommit:
    sha: str
    subject: str
    author: str
    committed_at: int
    #: Reachable from an ``origin/*`` ref this clone knows, so GitHub can show it.
    #: False for commits only on this computer: a GitHub link would be a 404.
    on_github: bool = False


@dataclass(slots=True)
class BranchFile:
    path: str
    #: ``added`` | ``deleted`` | ``modified``
    status: str
    #: None for a binary file.
    added: int | None = None
    removed: int | None = None


@dataclass(slots=True)
class BranchContents:
    available: bool
    branch: str
    #: The ref compared against (``origin/main``), "" for the default branch itself.
    base: str = ""
    commits: list[BranchCommit] = field(default_factory=list)
    commits_truncated: bool = False
    files: list[BranchFile] = field(default_factory=list)
    files_truncated: bool = False
    #: Why ``available`` is false, in one plain sentence.
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _valid_name(name: str) -> bool:
    return bool(name) and not name.startswith("-") and ".." not in name and "\0" not in name


def _verify(root: Path, ref: str) -> bool:
    result = _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], root)
    return result is not None and result.returncode == 0


def _branch_ref(root: Path, branch: str, remote: bool) -> str | None:
    if not _valid_name(branch):
        return None
    ref = f"refs/remotes/origin/{branch}" if remote else f"refs/heads/{branch}"
    return ref if _verify(root, ref) else None


def _base_ref(root: Path, default_branch: str) -> str | None:
    if not _valid_name(default_branch):
        return None
    for ref in (f"refs/remotes/origin/{default_branch}", f"refs/heads/{default_branch}"):
        if _verify(root, ref):
            return ref
    return None


def _short(ref: str) -> str:
    return ref.removeprefix("refs/heads/").removeprefix("refs/remotes/")


def _local_only(root: Path, ref: str) -> set[str]:
    """Commits of ``ref`` that no ``origin/*`` ref contains (full SHAs)."""
    result = _git(["rev-list", "--max-count=5000", ref, "--not", "--remotes=origin"], root)
    if result is None or result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _commits(root: Path, spec: str, ref: str) -> tuple[list[BranchCommit], bool]:
    local_only = _local_only(root, ref)
    result = _git(
        [
            "log",
            f"--max-count={MAX_COMMITS + 1}",
            "--format=%H%x1f%s%x1f%an%x1f%ct%x1e",
            spec,
            "--",
        ],
        root,
    )
    commits: list[BranchCommit] = []
    for record in (result.stdout if result and result.returncode == 0 else "").split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) != 4 or not parts[0]:
            continue
        sha, subject, author, stamp = parts
        commits.append(
            BranchCommit(
                sha=sha[:12],
                subject=subject,
                author=author,
                committed_at=int(stamp) if stamp.isdigit() else 0,
                on_github=sha not in local_only,
            )
        )
    return commits[:MAX_COMMITS], len(commits) > MAX_COMMITS


def _files(root: Path, spec: str) -> tuple[list[BranchFile], bool]:
    status = _git(["diff", "--name-status", "-z", "--no-renames", spec, "--"], root)
    numstat = _git(["diff", "--numstat", "-z", "--no-renames", spec, "--"], root)
    counts: dict[str, tuple[int | None, int | None]] = {}
    for record in (numstat.stdout if numstat and numstat.returncode == 0 else "").split("\0"):
        added, _, rest = record.partition("\t")
        removed, _, path = rest.partition("\t")
        if path:
            counts[path] = (
                int(added) if added.isdigit() else None,
                int(removed) if removed.isdigit() else None,
            )
    tokens = (status.stdout if status and status.returncode == 0 else "").split("\0")
    files: list[BranchFile] = []
    for code, path in zip(tokens[0::2], tokens[1::2], strict=False):
        if not path:
            continue
        added, removed = counts.get(path, (None, None))
        files.append(
            BranchFile(
                path=path,
                status=_STATUS.get(code[:1], "modified"),
                added=added,
                removed=removed,
            )
        )
    return files[:MAX_FILES], len(files) > MAX_FILES


def branch_contents(
    folder: str | Path, branch: str, default_branch: str, *, remote: bool = False
) -> BranchContents:
    """The commits ``branch`` adds over the default branch, and the files it changes."""
    root = Path(folder).expanduser()
    out = BranchContents(available=False, branch=branch)
    if not root.is_dir():
        out.reason = "The workspace folder is missing."
        return out
    ref = _branch_ref(root, branch, remote)
    if ref is None:
        out.reason = (
            "This branch is only on GitHub and not fetched yet; fetch to read it here."
            if remote
            else "This branch is not in this repository."
        )
        return out
    out.available = True
    base = _base_ref(root, default_branch)
    if base is None or _short(base) in {branch, f"origin/{branch}"}:
        # The default branch itself (or no base at all): its latest commits.
        out.commits, out.commits_truncated = _commits(root, ref, ref)
        return out
    out.base = _short(base)
    out.commits, out.commits_truncated = _commits(root, f"{base}..{ref}", ref)
    out.files, out.files_truncated = _files(root, f"{base}...{ref}")
    return out


def branch_file_diff(
    folder: str | Path, branch: str, default_branch: str, path: str, *, remote: bool = False
) -> FileDiff:
    """How ``branch`` changed one file since it split from the default branch."""
    root = Path(folder).expanduser()
    rel = path.strip().replace("\\", "/")
    posix = PurePosixPath(rel)
    if not rel or posix.is_absolute() or ".." in posix.parts or rel.startswith("-"):
        raise ValueError("That path is not a file in this repository.")
    ref = _branch_ref(root, branch, remote)
    base = _base_ref(root, default_branch)
    if ref is None or base is None:
        raise ValueError("This branch cannot be compared here.")
    result = _git(
        [
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-renames",
            "-U3",
            f"{base}...{ref}",
            "--",
            rel,
        ],
        root,
    )
    text = result.stdout if result and result.returncode == 0 else ""
    hunks, added, removed, binary, truncated = _parse_unified(text)
    status = "modified"
    if "\nnew file mode" in f"\n{text}":
        status = "added"
    elif "\ndeleted file mode" in f"\n{text}":
        status = "deleted"
    return FileDiff(
        path=rel,
        status=status,
        binary=binary,
        hunks=hunks,
        added=added,
        removed=removed,
        truncated=truncated,
    )
