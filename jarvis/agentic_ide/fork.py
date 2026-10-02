"""Forking a pane: a second agent that starts from a copy of the first one's chat.

Two shapes, one gesture:

* **Chat fork** — the copy runs in the same folder as the original. Useful to
  try a different direction without losing the one already taken.
* **Worktree fork** — the copy runs in a fresh ``git worktree`` on its own
  branch, so the two agents can edit files at the same time without touching
  each other's work.

This module owns the git half: suggesting a branch name that reads like the
pane it came from, and creating the worktree. The conversation half — which
argv makes a coding CLI copy its own conversation — lives in
:mod:`.agent_sessions` next to the other per-CLI knowledge, and the pane itself
is opened by ``Registry.fork_terminal``.

Where worktrees go: ``<repo>/.worktrees/<name>``. Inside the project, so the
user finds it beside the code it belongs to, and listed in the repository's
LOCAL ``.git/info/exclude`` so it never shows up as untracked files — nothing in
the committed ``.gitignore`` is changed on the user's behalf.

Blocking (``git`` subprocesses and filesystem work) — callers run these in a
worker thread. Cross-platform: plain ``git`` on PATH, ``pathlib`` paths, UTF-8.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

# The folder under the repository root that holds forked worktrees.
WORKTREE_DIR = ".worktrees"

# A branch name longer than this stops reading like a label. The slug is cut at
# a word boundary below it.
_MAX_SLUG = 40

_GIT_TIMEOUT_S = 60


class ForkError(RuntimeError):
    """A fork that cannot be made, worded for the person who asked for it."""


@dataclass(frozen=True, slots=True)
class Worktree:
    """A freshly created worktree and where the forked pane should run in it."""

    branch: str
    root: Path
    # The pane's working folder: the worktree root, or the same sub-folder of
    # it that the workspace had open in the original checkout.
    folder: Path


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except FileNotFoundError as exc:
        raise ForkError("Git is not installed, so a worktree cannot be created.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ForkError("Git did not answer in time while creating the worktree.") from exc


def repo_root(folder: str | Path) -> Path | None:
    """The top of the git checkout ``folder`` is in, or None if it is not one."""
    path = Path(folder).expanduser()
    if not path.is_dir():
        return None
    try:
        result = _git(["rev-parse", "--show-toplevel"], path)
    except ForkError:
        # No git on this box is the same answer as 'not a checkout'.
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


def _slug(text: str) -> str:
    """Lowercase words joined by dashes, safe inside a git branch name."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = ""
    for word in words:
        candidate = f"{slug}-{word}" if slug else word
        if len(candidate) > _MAX_SLUG:
            break
        slug = candidate
    return slug


def _branch_exists(root: Path, branch: str) -> bool:
    result = _git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], root)
    return result.returncode == 0


def _dir_name(branch: str) -> str:
    # A branch may be "fork/t1"; the folder is one level, never nested.
    return branch.replace("/", "-")


def _taken(root: Path, branch: str) -> bool:
    return _branch_exists(root, branch) or (root / WORKTREE_DIR / _dir_name(branch)).exists()


def base_name(pane_name: str, headline: str = "") -> str:
    """The name a fork of this pane is offered: call-sign plus what it works on.

    "T3" working on "Fix the login test" becomes ``t3-fix-the-login-test``; a
    pane with nothing to go on yet becomes ``t3-fork``. Lowercase and dashed,
    because that is how branch names are conventionally written.
    """
    sign = _slug(pane_name) or "pane"
    topic = _slug(headline)
    if topic and topic != sign:
        return f"{sign}-{topic}"
    return f"{sign}-fork"


def suggest_name(folder: str | Path, pane_name: str, headline: str = "") -> str:
    """A branch/worktree name for a fork of this pane that is still free."""
    wanted = base_name(pane_name, headline)
    root = repo_root(folder)
    if root is None:
        return wanted
    candidate = wanted
    number = 2
    while _taken(root, candidate) and number < 100:
        candidate = f"{wanted}-{number}"
        number += 1
    return candidate


def normalize_name(name: str) -> str:
    """The typed name as a branch name: trimmed, inner whitespace to dashes."""
    return re.sub(r"\s+", "-", (name or "").strip())


def _exclude_worktree_dir(root: Path) -> None:
    """List ``.worktrees/`` in the repository's LOCAL exclude file, once."""
    result = _git(["rev-parse", "--git-path", "info/exclude"], root)
    if result.returncode != 0 or not result.stdout.strip():
        return
    exclude = Path(result.stdout.strip())
    if not exclude.is_absolute():
        exclude = root / exclude
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
        # Only cosmetic: the worktree works, git status just lists the folder.
        logger.warning("Agentic IDE: could not exclude {} in {}: {}", line, exclude, exc)


def create_worktree(folder: str | Path, name: str) -> Worktree:
    """Create ``<repo>/.worktrees/<name>`` on a new branch ``name`` off HEAD.

    Only committed work is in the new checkout — git worktrees start from a
    commit, and uncommitted changes stay in the original folder. Raises
    :class:`ForkError` with a sentence fit for the UI on every failure.
    """
    source = Path(folder).expanduser()
    root = repo_root(source)
    if root is None:
        raise ForkError(f"{source.name or source} is not a git repository, so it has no worktrees.")
    branch = normalize_name(name)
    if not branch:
        raise ForkError("The worktree needs a name.")
    check = _git(["check-ref-format", "--branch", branch], root)
    if check.returncode != 0:
        raise ForkError(f"{branch!r} is not a valid git branch name.")
    if _branch_exists(root, branch):
        raise ForkError(f"A branch called {branch!r} already exists — pick another name.")
    target = root / WORKTREE_DIR / _dir_name(branch)
    if target.exists():
        raise ForkError(f"The folder {target} already exists — pick another name.")
    head = _git(["rev-parse", "--verify", "--quiet", "HEAD"], root)
    if head.returncode != 0:
        raise ForkError("This repository has no commit yet, so there is nothing to branch from.")

    _exclude_worktree_dir(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = _git(["worktree", "add", "-b", branch, str(target), "HEAD"], root)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[-1] if detail else f"git exited with {result.returncode}"
        raise ForkError(f"Git could not create the worktree: {reason}")

    # Same place inside the project as the workspace the pane came from.
    try:
        relative = Path(os.path.relpath(source.resolve(), root.resolve()))
    except ValueError:
        # Different drives on Windows: fall back to the worktree root.
        relative = Path(".")
    inside = str(relative) not in ("", ".") and relative.parts[0] != ".."
    pane_folder = target / relative if inside else target
    if not pane_folder.is_dir():
        pane_folder = target
    logger.info("Agentic IDE: created worktree {} on branch {}", target, branch)
    return Worktree(branch=branch, root=target, folder=pane_folder)
