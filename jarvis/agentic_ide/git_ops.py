"""Git for the Agentic IDE: where a workspace's agents work, and what they changed.

The IDE runs several coding agents at once. Git decides whether they share one
checkout or each get their own, so this module is the one place that answers:

* **Inspect** — which branch a folder is on, what is uncommitted, how far it is
  ahead of / behind its upstream, which branches and worktrees exist.
* **Prepare** — the git half of opening a workspace or an agent: keep the
  current checkout, ``git init`` a new repository, create or switch a branch in
  place, or give the agents a fresh ``git worktree`` of their own.
* **Finish** — commit everything, push, open a pull request (``gh``), merge a
  worktree's branch back, and remove a worktree safely.

Where worktrees go: ``<repo>/.worktrees/<name>`` — beside the code they belong
to, and listed in the repository's LOCAL ``.git/info/exclude`` so they never
show up as untracked files. Nothing in the committed ``.gitignore`` is changed
on the user's behalf.

Every function blocks (``git`` subprocesses) — callers run them in a worker
thread. Cross-platform: plain ``git`` on PATH, ``pathlib`` paths, UTF-8, and
``NO_WINDOW_CREATIONFLAGS`` so no console window flashes on Windows. A machine
without git gets ``git_available=False`` from :func:`inspect` and a readable
:class:`GitError` from everything else — never a crash.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

#: The folder under the repository root that holds agent worktrees.
WORKTREE_DIR = ".worktrees"

#: Prefix of generated branch names, so agent branches sort together.
BRANCH_PREFIX = "agent/"

_GIT_TIMEOUT_S = 60
# Network operations (push, fetch, PR creation) get longer: a first push of a
# big branch over a slow link legitimately takes a while.
_NETWORK_TIMEOUT_S = 180
# Caps on what one inspection returns — the UI lists, it does not page.
_MAX_BRANCHES = 200
_MAX_CHANGES = 200

PrepareMode = Literal[
    "current", "init", "new_branch", "switch_branch", "new_worktree", "open_worktree"
]

_ADJECTIVES = tuple(
    "amber brave bright calm clever cosmic crisp daring eager gentle golden happy keen"
    " lively lucky mellow nimble quiet rapid silver steady swift tidy vivid witty".split()
)
_NOUNS = tuple(
    "anchor aurora breeze canyon comet delta ember falcon forest glacier harbor lagoon"
    " meadow meteor orbit otter pine prairie river summit tide valley willow zenith lynx".split()
)


class GitError(RuntimeError):
    """A git operation that failed, worded for the person who asked for it.

    ``code`` lets the UI react to the failures it has a follow-up for (``dirty``
    asks "remove anyway?") without parsing the sentence.
    """

    def __init__(self, message: str, *, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------- plumbing


def git_available() -> bool:
    return shutil.which("git") is not None


def gh_available() -> bool:
    """The GitHub CLI, which opens pull requests. Optional — only PRs need it."""
    return shutil.which("gh") is not None


def _run(
    args: list[str], cwd: Path, *, timeout: float = _GIT_TIMEOUT_S, program: str = "git"
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    # Never block on a credential or editor prompt nobody can see.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_EDITOR", "true")
    env["GH_PROMPT_DISABLED"] = "1"
    command = [program, *args] if program != "git" else ["git", "-c", "core.quotepath=off", *args]
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except FileNotFoundError as exc:
        name = "Git" if program == "git" else "The GitHub CLI (gh)"
        raise GitError(f"{name} is not installed.", code="missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"{program} did not answer in time.", code="timeout") from exc


def _reason(result: subprocess.CompletedProcess[str]) -> str:
    """The last meaningful line git printed — usually the one that says why."""
    lines = [
        line.strip()
        for line in (result.stderr or result.stdout or "").splitlines()
        if line.strip() and not line.strip().startswith("hint:")
    ]
    return lines[-1] if lines else f"exited with {result.returncode}"


def _ok(args: list[str], cwd: Path, *, timeout: float = _GIT_TIMEOUT_S, what: str = "") -> str:
    result = _run(args, cwd, timeout=timeout)
    if result.returncode != 0:
        prefix = f"Git could not {what}: " if what else "Git: "
        raise GitError(prefix + _reason(result))
    return result.stdout


def _folder(folder: str | Path) -> Path:
    path = Path(folder).expanduser()
    if not path.is_dir():
        raise GitError(f"There is no folder called {path}.", code="no_folder")
    return path


def _same_path(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:  # unresolvable path: compare the spelling instead
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _inside(child: Path, parent: Path) -> bool:
    try:
        relative = Path(os.path.relpath(child.resolve(), parent.resolve()))
    except ValueError:  # different drives on Windows: not inside
        return False
    return not relative.parts or relative.parts[0] != ".."


# ------------------------------------------------------------------ inspect


@dataclass(slots=True)
class Change:
    path: str
    # One letter per side, git's own: M modified, A added, D deleted, R renamed,
    # ? untracked, U conflicted, . unchanged on that side.
    index: str
    worktree: str


@dataclass(slots=True)
class Branch:
    name: str
    current: bool = False
    upstream: str = ""
    committed_at: int = 0
    # The checkout this branch is open in, if any — a branch can be checked out
    # in only one worktree at a time.
    worktree: str = ""


@dataclass(slots=True)
class Worktree:
    path: str
    branch: str = ""
    head: str = ""
    main: bool = False
    detached: bool = False
    locked: bool = False
    prunable: bool = False
    # The worktree the inspected folder is in.
    current: bool = False


@dataclass(slots=True)
class RepoInfo:
    folder: str
    git_available: bool = True
    gh_available: bool = False
    is_repo: bool = False
    root: str = ""
    # The main checkout (the one that owns ``.git``) — equal to ``root`` unless
    # the folder is inside a linked worktree.
    main_root: str = ""
    is_worktree: bool = False
    branch: str = ""
    detached: bool = False
    # No commit yet (fresh ``git init``): nothing to branch from.
    unborn: bool = False
    head: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    conflicted: int = 0
    insertions: int = 0
    deletions: int = 0
    default_branch: str = ""
    remotes: list[str] = field(default_factory=list)
    branches: list[Branch] = field(default_factory=list)
    remote_branches: list[str] = field(default_factory=list)
    worktrees: list[Worktree] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    suggested_branch: str = ""
    # Where :func:`prepare` would put a worktree for ``suggested_branch``.
    worktree_dir: str = ""

    @property
    def dirty(self) -> bool:
        return bool(self.staged or self.unstaged or self.untracked or self.conflicted)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["dirty"] = self.dirty
        return data


def repo_root(folder: str | Path) -> Path | None:
    """The top of the git checkout ``folder`` is in, or None if it is not one."""
    path = Path(folder).expanduser()
    if not path.is_dir() or not git_available():
        return None
    try:
        result = _run(["rev-parse", "--show-toplevel"], path)
    except GitError:  # git missing or hung: treated as "not a repository"
        return None
    top = result.stdout.strip()
    return Path(top) if result.returncode == 0 and top else None


def _common_dir(folder: Path) -> Path | None:
    try:
        result = _run(["rev-parse", "--path-format=absolute", "--git-common-dir"], folder)
    except GitError:  # git missing or hung: no common dir to compare
        return None
    text = result.stdout.strip()
    return Path(text) if result.returncode == 0 and text else None


def same_repository(folder: str | Path, other: str | Path) -> bool:
    """True when both folders are checkouts of ONE repository (e.g. a worktree of it)."""
    a, b = Path(folder).expanduser(), Path(other).expanduser()
    if not a.is_dir() or not b.is_dir() or not git_available():
        return False
    common_a, common_b = _common_dir(a), _common_dir(b)
    return common_a is not None and common_b is not None and _same_path(common_a, common_b)


def _parse_status(text: str, info: RepoInfo) -> None:
    """Fill branch/ahead/behind/counts from ``git status --porcelain=v2 --branch``."""
    for line in text.splitlines():
        if line.startswith("# branch.oid "):
            oid = line[len("# branch.oid ") :].strip()
            info.unborn = oid == "(initial)"
            info.head = "" if info.unborn else oid[:12]
        elif line.startswith("# branch.head "):
            head = line[len("# branch.head ") :].strip()
            info.detached = head == "(detached)"
            info.branch = "" if info.detached else head
        elif line.startswith("# branch.upstream "):
            info.upstream = line[len("# branch.upstream ") :].strip()
        elif line.startswith("# branch.ab "):
            match = re.match(r"# branch\.ab \+(\d+) -(\d+)", line)
            if match:
                info.ahead, info.behind = int(match.group(1)), int(match.group(2))
        elif line.startswith(("1 ", "2 ")):
            parts = line.split(" ")
            xy = parts[1] if len(parts) > 1 else ".."
            # Ordinary entries have 8 fields before the path, renames 9 (the
            # score) plus "<path>\t<orig>".
            path = " ".join(parts[8 if line.startswith("1 ") else 9 :]).split("\t")[0]
            if xy[0] != ".":
                info.staged += 1
            if xy[1] != ".":
                info.unstaged += 1
            if len(info.changes) < _MAX_CHANGES:
                info.changes.append(Change(path=path, index=xy[0], worktree=xy[1]))
        elif line.startswith("u "):
            info.conflicted += 1
            path = " ".join(line.split(" ")[10:])
            if len(info.changes) < _MAX_CHANGES:
                info.changes.append(Change(path=path, index="U", worktree="U"))
        elif line.startswith("? "):
            info.untracked += 1
            if len(info.changes) < _MAX_CHANGES:
                info.changes.append(Change(path=line[2:], index="?", worktree="?"))


def _parse_worktrees(text: str, current_root: Path) -> list[Worktree]:
    trees: list[Worktree] = []
    entry: Worktree | None = None
    for line in [*text.splitlines(), ""]:
        if not line.strip():
            if entry is not None:
                trees.append(entry)
            entry = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            entry = Worktree(path=str(Path(value)), main=not trees and entry is None)
            entry.current = _same_path(value, current_root)
        elif entry is None:
            continue
        elif key == "HEAD":
            entry.head = value[:12]
        elif key == "branch":
            entry.branch = value.removeprefix("refs/heads/")
        elif key == "detached":
            entry.detached = True
        elif key == "locked":
            entry.locked = True
        elif key == "prunable":
            entry.prunable = True
        elif key == "bare":
            entry.main = False
    return trees


def _default_branch(root: Path, branches: list[Branch], current: str) -> str:
    result = _run(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().split("/", 1)[-1]
    names = {branch.name for branch in branches}
    for candidate in ("main", "master", "trunk", "develop"):
        if candidate in names:
            return candidate
    return current


def inspect(folder: str | Path) -> RepoInfo:
    """Everything the Git panel shows about ``folder``, in a handful of git calls."""
    path = Path(folder).expanduser()
    info = RepoInfo(folder=str(path), gh_available=gh_available())
    if not git_available():
        info.git_available = False
        return info
    if not path.is_dir():
        raise GitError(f"There is no folder called {path}.", code="no_folder")
    root = repo_root(path)
    if root is None:
        info.suggested_branch = suggest_branch_name()
        return info
    info.is_repo = True
    info.root = str(root)

    status = _run(["status", "--porcelain=v2", "--branch", "--untracked-files=normal"], root)
    if status.returncode == 0:
        _parse_status(status.stdout, info)
    if not info.unborn:
        shortstat = _run(["diff", "--shortstat", "HEAD"], root)
        if shortstat.returncode == 0:
            plus = re.search(r"(\d+) insertion", shortstat.stdout)
            minus = re.search(r"(\d+) deletion", shortstat.stdout)
            info.insertions = int(plus.group(1)) if plus else 0
            info.deletions = int(minus.group(1)) if minus else 0

    refs = _run(
        [
            "for-each-ref",
            "--sort=-committerdate",
            f"--count={_MAX_BRANCHES}",
            "--format=%(refname:short)%09%(upstream:short)%09%(committerdate:unix)%09%(worktreepath)",
            "refs/heads",
        ],
        root,
    )
    if refs.returncode == 0:
        for line in refs.stdout.splitlines():
            name, upstream, stamp, tree = ([*line.split("\t"), "", "", ""])[:4]
            if not name:
                continue
            info.branches.append(
                Branch(
                    name=name,
                    current=name == info.branch,
                    upstream=upstream,
                    committed_at=int(stamp) if stamp.isdigit() else 0,
                    worktree=str(Path(tree)) if tree else "",
                )
            )
    remote_refs = _run(
        ["for-each-ref", f"--count={_MAX_BRANCHES}", "--format=%(refname:short)", "refs/remotes"],
        root,
    )
    if remote_refs.returncode == 0:
        info.remote_branches = [
            ref
            for ref in remote_refs.stdout.split()
            if ref and not ref.endswith("/HEAD") and "/" in ref
        ]
    remotes = _run(["remote"], root)
    if remotes.returncode == 0:
        info.remotes = remotes.stdout.split()

    trees = _run(["worktree", "list", "--porcelain"], root)
    if trees.returncode == 0:
        info.worktrees = _parse_worktrees(trees.stdout, root)
    main = next((tree for tree in info.worktrees if tree.main), None)
    info.main_root = main.path if main else str(root)
    info.is_worktree = main is not None and not _same_path(main.path, root)
    info.default_branch = _default_branch(root, info.branches, info.branch)
    info.suggested_branch = suggest_branch_name(root)
    info.worktree_dir = str(worktree_path(Path(info.main_root), info.suggested_branch))
    return info


# ------------------------------------------------------------------ naming


def suggest_branch_name(root: Path | None = None) -> str:
    """A readable, free branch name such as ``agent/brave-river-1a2f``."""
    for _ in range(20):
        words = f"{secrets.choice(_ADJECTIVES)}-{secrets.choice(_NOUNS)}"
        name = f"{BRANCH_PREFIX}{words}-{secrets.token_hex(2)}"
        if root is None or not _branch_exists(root, name):
            return name
    return f"{BRANCH_PREFIX}{secrets.token_hex(4)}"


def normalize_branch(name: str) -> str:
    """The typed name as a branch name: trimmed, inner whitespace to dashes."""
    return re.sub(r"\s+", "-", (name or "").strip())


def _branch_exists(root: Path, branch: str) -> bool:
    try:
        result = _run(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], root)
    except GitError:  # git missing or hung: the name counts as free
        return False
    return result.returncode == 0


def _valid_branch(root: Path, branch: str) -> str:
    branch = normalize_branch(branch)
    if not branch:
        raise GitError("The branch needs a name.", code="invalid")
    if _run(["check-ref-format", "--branch", branch], root).returncode != 0:
        raise GitError(f"{branch!r} is not a valid git branch name.", code="invalid")
    return branch


def worktree_path(main_root: Path, branch: str) -> Path:
    """``<repo>/.worktrees/<branch with / as ->`` — one level, never nested."""
    return main_root / WORKTREE_DIR / normalize_branch(branch).replace("/", "-")


def _exclude_worktree_dir(root: Path) -> None:
    """List ``.worktrees/`` in the repository's LOCAL exclude file, once."""
    result = _run(["rev-parse", "--path-format=absolute", "--git-path", "info/exclude"], root)
    if result.returncode != 0 or not result.stdout.strip():
        return
    exclude = Path(result.stdout.strip())
    line = f"/{WORKTREE_DIR}/"
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if line in existing.splitlines():
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(f"{prefix}{line}\n")
    except OSError as exc:
        # Only cosmetic: the worktree works, `git status` just lists the folder.
        logger.warning("Agentic IDE git: could not exclude {} in {}: {}", line, exclude, exc)


# ------------------------------------------------------------------ prepare


@dataclass(slots=True)
class Prepared:
    """Where the workspace/agent should run after :func:`prepare`."""

    folder: str
    branch: str = ""
    created: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def init_repository(folder: str | Path) -> Prepared:
    """``git init`` on ``main`` plus an EMPTY first commit, so branches work at once.

    Only an empty commit — the user's files are never committed on their
    behalf (a folder may hold secrets that belong in ``.gitignore`` first).
    """
    path = _folder(folder)
    if repo_root(path) is not None:
        raise GitError(f"{path.name} is already inside a git repository.", code="exists")
    result = _run(["init", "--initial-branch=main"], path)
    if result.returncode != 0:  # git older than 2.28
        _ok(["init"], path, what="create the repository")
        _ok(["symbolic-ref", "HEAD", "refs/heads/main"], path, what="name the first branch")
    message = ["commit", "--allow-empty", "-m", "chore: initialize repository"]
    first = _run(message, path)
    if first.returncode != 0:
        # No git identity configured on this machine yet: sign only this empty
        # root commit with a neutral local identity rather than failing the init.
        fallback = [
            "-c",
            "user.name=Personal Jarvis",
            "-c",
            "user.email=jarvis@localhost",
            *message,
        ]
        _ok(fallback, path, what="create the first commit")
    logger.info("Agentic IDE git: initialized {}", path)
    return Prepared(
        folder=str(path),
        branch="main",
        created=True,
        message="Initialized a new repository on main.",
    )


def _sub_folder(source: Path, root: Path, target: Path) -> Path:
    """The same place inside ``target`` that ``source`` has inside ``root``."""
    try:
        relative = Path(os.path.relpath(source.resolve(), root.resolve()))
    except ValueError:  # different drives on Windows: use the worktree root
        return target
    if not relative.parts or relative.parts[0] == ".." or str(relative) == ".":
        return target
    candidate = target / relative
    return candidate if candidate.is_dir() else target


def _require_commit(root: Path) -> None:
    if _run(["rev-parse", "--verify", "--quiet", "HEAD"], root).returncode != 0:
        raise GitError(
            "This repository has no commit yet, so there is nothing to branch from. "
            "Make a first commit, then try again.",
            code="unborn",
        )


def prepare(folder: str | Path, mode: PrepareMode, *, branch: str = "", base: str = "") -> Prepared:
    """Do the git half of opening a workspace/agent in ``folder``; say where to run it."""
    path = _folder(folder)
    if mode == "current":
        root = repo_root(path)
        current = inspect(path).branch if root is not None else ""
        return Prepared(folder=str(path), branch=current)
    if mode == "init":
        return init_repository(path)

    root = repo_root(path)
    if root is None:
        raise GitError(f"{path.name} is not a git repository.", code="not_repo")

    if mode == "open_worktree":
        # `branch` names the worktree's branch, or `base` its path.
        info = inspect(root)
        wanted = normalize_branch(branch)
        for tree in info.worktrees:
            if (wanted and tree.branch == wanted) or (base and _same_path(tree.path, base)):
                if tree.prunable or not Path(tree.path).is_dir():
                    raise GitError(
                        f"The worktree {tree.path} no longer exists — prune it.", code="prunable"
                    )
                target = Path(tree.path)
                return Prepared(folder=str(_sub_folder(path, root, target)), branch=tree.branch)
        raise GitError("That worktree does not exist.", code="not_found")

    name = _valid_branch(root, branch or suggest_branch_name(root))
    base_ref = (base or "").strip()
    if (
        base_ref
        and _run(["rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"], root).returncode
        != 0
    ):
        raise GitError(f"There is no branch or commit called {base_ref!r}.", code="invalid")

    if mode == "switch_branch":
        _ok(["switch", name], root, what=f"switch to {name}")
        return Prepared(folder=str(path), branch=name, message=f"Switched to {name}.")

    if mode == "new_branch":
        if _branch_exists(root, name):
            _ok(["switch", name], root, what=f"switch to {name}")
            return Prepared(
                folder=str(path), branch=name, message=f"Switched to the existing branch {name}."
            )
        _require_commit(root)
        _ok(["switch", "-c", name, *([base_ref] if base_ref else [])], root, what=f"create {name}")
        return Prepared(
            folder=str(path), branch=name, created=True, message=f"Created and switched to {name}."
        )

    if mode == "new_worktree":
        _require_commit(root)
        info = inspect(root)
        main_root = Path(info.main_root or root)
        existing = next((b for b in info.branches if b.name == name), None)
        if existing and existing.worktree:
            # Already checked out somewhere: open that checkout instead of failing.
            target = Path(existing.worktree)
            return Prepared(
                folder=str(_sub_folder(path, root, target)),
                branch=name,
                message=f"{name} is already checked out in {target} — opened that.",
            )
        target = worktree_path(main_root, name)
        if target.exists():
            raise GitError(
                f"The folder {target} already exists — pick another name.", code="exists"
            )
        _exclude_worktree_dir(main_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if existing:
            args = ["worktree", "add", str(target), name]
        else:
            args = ["worktree", "add", "-b", name, str(target), base_ref or "HEAD"]
        _ok(args, main_root, what="create the worktree")
        logger.info("Agentic IDE git: created worktree {} on {}", target, name)
        return Prepared(
            folder=str(_sub_folder(path, root, target)),
            branch=name,
            created=True,
            message=f"Created a worktree for {name}.",
        )
    raise GitError(f"Unknown git option {mode!r}.", code="invalid")


# ------------------------------------------------------------------- finish


def commit_all(folder: str | Path, message: str) -> str:
    """Stage everything (respecting ``.gitignore``) and commit it. Returns the short sha."""
    path = _folder(folder)
    root = repo_root(path)
    if root is None:
        raise GitError(f"{path.name} is not a git repository.", code="not_repo")
    text = (message or "").strip()
    if not text:
        raise GitError("The commit needs a message.", code="invalid")
    _ok(["add", "--all"], root, what="stage the changes")
    if _run(["diff", "--cached", "--quiet"], root).returncode == 0:
        raise GitError("There is nothing to commit.", code="clean")
    _ok(["commit", "-m", text], root, what="commit")
    return _ok(["rev-parse", "--short", "HEAD"], root).strip()


def push(folder: str | Path) -> str:
    """Push the current branch, setting its upstream on the first push."""
    path = _folder(folder)
    info = inspect(path)
    if not info.is_repo:
        raise GitError(f"{path.name} is not a git repository.", code="not_repo")
    if info.detached or not info.branch:
        raise GitError("Check out a branch before pushing.", code="detached")
    root = Path(info.root)
    if info.upstream:
        _ok(["push"], root, timeout=_NETWORK_TIMEOUT_S, what="push")
        return info.upstream
    if not info.remotes:
        raise GitError("This repository has no remote to push to.", code="no_remote")
    remote = "origin" if "origin" in info.remotes else info.remotes[0]
    _ok(
        ["push", "--set-upstream", remote, info.branch],
        root,
        timeout=_NETWORK_TIMEOUT_S,
        what="push",
    )
    return f"{remote}/{info.branch}"


def fetch(folder: str | Path) -> None:
    path = _folder(folder)
    root = repo_root(path)
    if root is None:
        raise GitError(f"{path.name} is not a git repository.", code="not_repo")
    _ok(["fetch", "--all", "--prune"], root, timeout=_NETWORK_TIMEOUT_S, what="fetch")


def create_pull_request(folder: str | Path, *, title: str = "", draft: bool = False) -> str:
    """Push if needed, then open a PR with the GitHub CLI. Returns its URL."""
    if not gh_available():
        raise GitError(
            "Install the GitHub CLI (gh) to open pull requests from here.", code="missing"
        )
    path = _folder(folder)
    info = inspect(path)
    if not info.is_repo or not info.branch:
        raise GitError("Check out a branch before opening a pull request.", code="detached")
    if info.branch == info.default_branch:
        raise GitError(
            f"You are on {info.branch} itself — open the PR from a feature branch.", code="invalid"
        )
    if not info.upstream or info.ahead:
        push(path)
    args = ["pr", "create", "--base", info.default_branch, "--head", info.branch]
    args += ["--title", title.strip(), "--body", ""] if title.strip() else ["--fill"]
    if draft:
        args.append("--draft")
    result = _run(args, Path(info.root), timeout=_NETWORK_TIMEOUT_S, program="gh")
    if result.returncode != 0:
        raise GitError(f"The pull request could not be opened: {_reason(result)}")
    urls = re.findall(r"https?://\S+", result.stdout)
    return urls[-1] if urls else result.stdout.strip()


def merge_back(folder: str | Path, *, into: str = "") -> str:
    """Merge the branch checked out in ``folder`` into ``into`` (default branch).

    Runs in the checkout where ``into`` is checked out, which must have no
    uncommitted changes. A conflicting merge is aborted, never left half-done.
    """
    path = _folder(folder)
    info = inspect(path)
    if not info.is_repo or not info.branch:
        raise GitError("Check out a branch before merging it back.", code="detached")
    if info.staged or info.unstaged or info.conflicted:
        raise GitError("Commit this branch's changes first, then merge it back.", code="dirty")
    target = (into or info.default_branch).strip()
    if not target or target == info.branch:
        raise GitError("Pick a different branch to merge into.", code="invalid")
    holder = next((tree for tree in info.worktrees if tree.branch == target), None)
    if holder is None:
        raise GitError(
            f"{target} is not checked out anywhere. Check it out in the main folder first.",
            code="not_checked_out",
        )
    where = Path(holder.path)
    clean = _run(["status", "--porcelain", "--untracked-files=no"], where)
    if clean.returncode != 0 or clean.stdout.strip():
        raise GitError(
            f"{where} has uncommitted changes on {target} — commit them first.", code="dirty"
        )
    result = _run(["merge", "--no-ff", "--no-edit", info.branch], where)
    if result.returncode != 0:
        conflicts = _run(["diff", "--name-only", "--diff-filter=U"], where).stdout.split()
        _run(["merge", "--abort"], where)
        if conflicts:
            shown = ", ".join(conflicts[:5]) + (" …" if len(conflicts) > 5 else "")
            raise GitError(
                f"Merging {info.branch} into {target} conflicts in {shown}. Nothing was changed — "
                "ask the agent to rebase onto the target branch, then merge again.",
                code="conflict",
            )
        raise GitError(f"Git could not merge: {_reason(result)}")
    return _ok(["rev-parse", "--short", "HEAD"], where).strip()


def remove_worktree(
    folder: str | Path, worktree: str | Path, *, force: bool = False, delete_branch: bool = False
) -> str:
    """Remove a linked worktree of ``folder``'s repository. Returns a summary.

    Refuses the main checkout. Uncommitted work raises ``code="dirty"`` unless
    ``force`` — the UI asks a second, explicit question first. The branch is
    deleted only when asked AND fully merged (``git branch -d``, never ``-D``).
    """
    path = _folder(folder)
    info = inspect(path)
    if not info.is_repo:
        raise GitError(f"{path.name} is not a git repository.", code="not_repo")
    tree = next((entry for entry in info.worktrees if _same_path(entry.path, worktree)), None)
    if tree is None:
        raise GitError("That worktree does not belong to this repository.", code="not_found")
    if tree.main:
        raise GitError("The main checkout cannot be removed.", code="invalid")
    main_root = Path(info.main_root)
    if Path(tree.path).is_dir() and not force:
        dirty = _run(["status", "--porcelain"], Path(tree.path))
        if dirty.returncode == 0 and dirty.stdout.strip():
            count = len(dirty.stdout.strip().splitlines())
            plural = "s" if count != 1 else ""
            raise GitError(
                f"{Path(tree.path).name} has {count} uncommitted change{plural}. Remove it anyway?",
                code="dirty",
            )
    args = ["worktree", "remove", *(["--force"] if force else []), tree.path]
    _ok(args, main_root, what="remove the worktree")
    summary = f"Removed {Path(tree.path).name}."
    if delete_branch and tree.branch:
        result = _run(["branch", "-d", tree.branch], main_root)
        if result.returncode == 0:
            summary += f" Deleted branch {tree.branch}."
        else:
            summary += f" Kept branch {tree.branch} — it is not merged yet."
    logger.info("Agentic IDE git: {}", summary)
    return summary


def prune_worktrees(folder: str | Path) -> None:
    """Forget worktrees whose folders were deleted by hand."""
    path = _folder(folder)
    root = repo_root(path)
    if root is None:
        raise GitError(f"{path.name} is not a git repository.", code="not_repo")
    _ok(["worktree", "prune"], root, what="prune worktrees")


def worktree_in_use(worktree: str | Path, open_folders: list[str]) -> bool:
    """True when an open workspace runs inside ``worktree`` (its files may be locked)."""
    target = Path(worktree)
    return any(_inside(Path(folder), target) for folder in open_folders if folder)
