"""Folder picking + cheap project profiling for the Agentic IDE.

Two jobs:

*Picking* — the wizard's first step lets the user choose ANY folder on the
machine, so this module lists directories the way a file dialog would, but over
REST (the desktop app is a web UI; a native folder dialog would exist on one OS
and not the others). Start points are discovered per platform: drive letters on
Windows, ``/`` plus the mounted volumes on macOS/Linux, always the home folder
and a handful of conventional code directories.

*Profiling* — once a folder is chosen, Jarvis needs to be able to answer "what
is this codebase?" without reading it file by file. ``probe_project`` collects a
compact profile from a SINGLE shallow directory scan plus a few file-existence
checks: git branch, detected stacks, the agent instruction files that are
actually present, and the skills the repo defines. Deliberately shallow — no
recursive walk — because this runs when a session starts and its result is what
gets folded into the voice turn's context (AP-9: awareness work stays off the
hot path; here it is computed once and cached in the session).
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

# Directory names that are never interesting as a project root and would bury
# the real folders in the picker.
_SKIP_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".turbo",
        "target",
        ".idea",
        ".vscode-test",
        "$RECYCLE.BIN",
        "System Volume Information",
    }
)

# marker file/dir -> stack label. Order is irrelevant; all matches are reported.
_STACK_MARKERS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "Python"),
    ("requirements.txt", "Python"),
    ("setup.py", "Python"),
    ("package.json", "Node / JavaScript"),
    ("tsconfig.json", "TypeScript"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("pom.xml", "Java / Maven"),
    ("build.gradle", "Java / Gradle"),
    ("Gemfile", "Ruby"),
    ("composer.json", "PHP"),
    ("Dockerfile", "Docker"),
    ("docker-compose.yml", "Docker Compose"),
    ("pubspec.yaml", "Dart / Flutter"),
    ("*.csproj", ".NET"),
)

# Files a coding agent reads as its standing instructions. Which of these exist
# tells the user (and Jarvis) how well-briefed an agent in this folder will be.
_INSTRUCTION_FILES: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".cursorrules",
    "CONTRIBUTING.md",
    "README.md",
)

_MAX_ENTRIES = 400
_MAX_WORKSPACE_ENTRIES = 2_000
_MAX_SKILLS = 60


@dataclass(frozen=True, slots=True)
class FolderEntry:
    """One directory in the picker."""

    name: str
    path: str
    is_project: bool
    is_repo: bool


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    """One direct child of a directory inside an open workspace."""

    name: str
    path: str
    is_directory: bool
    is_symlink: bool
    size: int | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceListing:
    """A bounded, workspace-relative directory listing for the file explorer."""

    path: str
    entries: tuple[WorkspaceEntry, ...]
    truncated: bool = False
    error: str | None = None


def _workspace_relative_parts(relative: str | Path) -> tuple[str, ...]:
    """Parse one wire path consistently on Windows, macOS, and Linux.

    Explorer paths are POSIX-style on the wire. Splitting them before the
    native ``Path`` join keeps ordinary separators stable; the resolved native
    path still passes the workspace-containment check below, which also catches
    Windows drive, UNC, and backslash traversal forms without rejecting valid
    Unix filenames that happen to contain a colon or backslash.
    """
    raw = os.fspath(relative) or "."
    posix = PurePosixPath(raw)
    if posix.is_absolute() or ".." in posix.parts:
        raise ValueError("That folder is outside the open workspace.")
    return tuple(part for part in posix.parts if part not in {"", "."})


@dataclass(slots=True)
class ProjectProfile:
    """Compact, cheap description of a chosen folder."""

    path: str
    name: str
    exists: bool = True
    is_repo: bool = False
    branch: str | None = None
    stacks: list[str] = field(default_factory=list)
    instruction_files: list[str] = field(default_factory=list)
    top_level_dirs: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    subagents: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_lines(self) -> list[str]:
        """Plain-text lines for a model-facing context block."""
        out = [f"Folder: {self.path}"]
        if not self.exists:
            out.append("This folder no longer exists on disk.")
            return out
        if self.is_repo:
            out.append(f"Git repository{f' on branch {self.branch}' if self.branch else ''}.")
        if self.stacks:
            out.append("Stack: " + ", ".join(self.stacks))
        if self.instruction_files:
            out.append("Agent instructions present: " + ", ".join(self.instruction_files))
        if self.top_level_dirs:
            out.append("Top-level folders: " + ", ".join(self.top_level_dirs))
        # What the agents in this repo can be ASKED for. Knowing that a repo
        # defines a `publish-new-version` skill or a `code-reviewer` subagent is
        # the difference between "write a release script" and "run the release
        # skill" — the second is what the user meant and what the agent in the
        # pane is actually equipped to do.
        for label, items in (
            ("Skills defined here", self.skills),
            ("Subagents defined here", self.subagents),
            ("Slash commands defined here", self.commands),
        ):
            if not items:
                continue
            shown = ", ".join(items[:20])
            more = "" if len(items) <= 20 else f" (+{len(items) - 20} more)"
            out.append(f"{label} ({len(items)}): {shown}{more}")
        return out


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _looks_like_project(entry_path: Path) -> tuple[bool, bool]:
    """(is_project, is_repo) from marker files, without descending."""
    is_repo = (entry_path / ".git").exists()
    if is_repo:
        return True, True
    for marker, _label in _STACK_MARKERS:
        if "*" in marker:
            continue
        if (entry_path / marker).exists():
            return True, False
    return False, False


def start_points() -> list[FolderEntry]:
    """Platform-appropriate places to begin browsing."""
    seen: set[str] = set()
    out: list[FolderEntry] = []

    def add(path: Path, label: str | None = None) -> None:
        try:
            if not path.is_dir():
                return
            resolved = str(path)
        except OSError:
            return
        if resolved in seen:
            return
        seen.add(resolved)
        is_project, is_repo = _looks_like_project(path)
        out.append(
            FolderEntry(
                name=label or path.name or resolved,
                path=resolved,
                is_project=is_project,
                is_repo=is_repo,
            )
        )

    home = Path.home()
    add(home, "Home")
    for conventional in ("Desktop", "Documents", "Projects", "Code", "dev", "src", "repos"):
        add(home / conventional)

    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            add(Path(f"{letter}:\\"), f"{letter}:\\")
    else:
        add(Path("/"), "/")
        for volumes in ("/Volumes", "/media", "/mnt", "/opt", "/srv"):
            add(Path(volumes))

    return out


def list_dir(
    path: str | Path, *, include_hidden: bool = False
) -> tuple[list[FolderEntry], str | None]:
    """Sub-directories of ``path``. Returns ``(entries, error_message)``."""
    target = Path(path).expanduser()
    try:
        if not target.is_dir():
            return [], f"Not a folder: {target}"
    except OSError as exc:
        return [], f"Cannot read {target}: {exc}"

    entries: list[FolderEntry] = []
    try:
        with os.scandir(target) as it:
            for item in it:
                if len(entries) >= _MAX_ENTRIES:
                    break
                name = item.name
                if name in _SKIP_DIRS:
                    continue
                if not include_hidden and _is_hidden(name):
                    continue
                try:
                    if not item.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                child = Path(item.path)
                is_project, is_repo = _looks_like_project(child)
                entries.append(
                    FolderEntry(
                        name=name,
                        path=str(child),
                        is_project=is_project,
                        is_repo=is_repo,
                    )
                )
    except PermissionError:
        return [], f"No permission to read {target}"
    except OSError as exc:
        return [], f"Cannot read {target}: {exc}"

    # Projects first, then alphabetically — the folder the user came for is
    # almost always a project.
    entries.sort(key=lambda e: (not e.is_project, e.name.lower()))
    return entries, None


def list_workspace_dir(
    root: str | Path,
    relative: str | Path = "",
    *,
    limit: int = _MAX_WORKSPACE_ENTRIES,
) -> WorkspaceListing:
    """List one directory without ever escaping the open workspace.

    The explorer loads one level at a time, so even a large repository stays
    cheap to open. Hidden entries are intentionally included: source control,
    agent instructions, and environment examples are normal project files.
    Symlinks are shown but never reported as expandable directories, which
    prevents a link inside the workspace from becoming a browser for another
    part of the machine.
    """
    root_path = Path(root).expanduser().resolve(strict=True)
    relative_parts = _workspace_relative_parts(relative)

    try:
        target = root_path.joinpath(*relative_parts).resolve(strict=True)
        target.relative_to(root_path)
    except (OSError, ValueError) as exc:
        raise ValueError("That folder is outside the open workspace.") from exc
    if not target.is_dir():
        raise NotADirectoryError("That workspace path is not a folder.")

    normalized_path = "" if target == root_path else target.relative_to(root_path).as_posix()
    entries: list[WorkspaceEntry] = []
    truncated = False
    try:
        with os.scandir(target) as it:
            for item in it:
                if len(entries) >= max(1, limit):
                    truncated = True
                    break
                try:
                    is_symlink = item.is_symlink()
                    is_directory = item.is_dir(follow_symlinks=False)
                    size = None
                    if not is_directory and not is_symlink:
                        size = item.stat(follow_symlinks=False).st_size
                except OSError:
                    # A file can disappear between scandir and stat. The next
                    # refresh will reflect it; one racing entry must not blank
                    # the rest of the folder.
                    continue
                child = Path(item.path).relative_to(root_path).as_posix()
                entries.append(
                    WorkspaceEntry(
                        name=item.name,
                        path=child,
                        is_directory=is_directory,
                        is_symlink=is_symlink,
                        size=size,
                    )
                )
    except PermissionError:
        # Reported through the listing's own error field, not a log call.
        return WorkspaceListing(
            path=normalized_path,
            entries=(),
            error="This folder cannot be read with the current permissions.",
        )
    except OSError:
        # Reported through the listing's own error field, not a log call.
        return WorkspaceListing(
            path=normalized_path,
            entries=(),
            error="This folder could not be read.",
        )

    entries.sort(key=lambda entry: (not entry.is_directory, entry.name.casefold()))
    return WorkspaceListing(
        path=normalized_path,
        entries=tuple(entries),
        truncated=truncated,
    )


def search_folders(
    query: str,
    *,
    roots: list[Path] | None = None,
    limit: int = 40,
    max_depth: int = 5,
) -> list[FolderEntry]:
    """Folders whose name matches ``query``, searched breadth-first.

    Breadth-first on purpose: the folder a person is looking for is almost always
    a few levels down from home or from a code directory, so widening before
    deepening finds it sooner and lets the walk stop early. The traversal is
    bounded three ways — depth, a hard visit budget, and the shared skip list —
    because an unbounded walk of a home directory on a spinning disk or a network
    share would block for minutes.

    Ranking puts exact name matches first, then prefix matches, then substring
    matches; within each tier projects and repositories come before plain
    folders, because that is what someone opening a coding workspace wants.
    """
    needle = query.strip().lower()
    if not needle:
        return []

    if roots is None:
        home = Path.home()
        roots = [home]
        for extra in ("Desktop", "Documents", "Projects", "Code", "dev", "src", "repos"):
            candidate = home / extra
            if candidate.is_dir():
                roots.append(candidate)

    # Visit budget: generous enough to cover a real code tree, small enough that
    # the worst case stays well inside a request timeout.
    budget = 20_000
    seen: set[str] = set()
    hits: list[tuple[int, int, str, FolderEntry]] = []

    queue: deque[tuple[Path, int]] = deque((root, 0) for root in roots)
    while queue and budget > 0 and len(hits) < limit * 4:
        current, depth = queue.popleft()
        key = str(current).lower()
        if key in seen:
            continue
        seen.add(key)
        if depth > max_depth:
            continue
        try:
            with os.scandir(current) as it:
                for item in it:
                    budget -= 1
                    if budget <= 0:
                        break
                    name = item.name
                    if name in _SKIP_DIRS or _is_hidden(name):
                        continue
                    try:
                        if not item.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    child = Path(item.path)
                    lowered = name.lower()
                    if needle in lowered:
                        tier = 0 if lowered == needle else 1 if lowered.startswith(needle) else 2
                        is_project, is_repo = _looks_like_project(child)
                        kind = 0 if is_repo else 1 if is_project else 2
                        hits.append(
                            (
                                tier,
                                kind,
                                lowered,
                                FolderEntry(
                                    name=name,
                                    path=str(child),
                                    is_project=is_project,
                                    is_repo=is_repo,
                                ),
                            )
                        )
                    if depth < max_depth:
                        queue.append((child, depth + 1))
        except (PermissionError, OSError):
            continue

    hits.sort(key=lambda h: (h[0], h[1], h[2]))
    return [entry for _t, _k, _n, entry in hits[:limit]]


def _git_branch(root: Path) -> str | None:
    """Current branch from ``.git/HEAD`` — no subprocess, no git required."""
    head = root / ".git" / "HEAD"
    try:
        text = head.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if text.startswith("ref: refs/heads/"):
        return text[len("ref: refs/heads/") :] or None
    return text[:12] or None  # detached HEAD


def _collect_named(root: Path, kind: str, *, files: bool = False) -> list[str]:
    """Names defined under ``.claude/<kind>`` / ``.agents/<kind>``.

    Both trees are read because the repo convention is that they mirror each
    other (``.claude`` canonical, ``.agents`` the tool-neutral twin), and a
    contributor may have only one of them checked out. Duplicates collapse.

    Skills and subagents are directories; slash commands are Markdown files, so
    ``files`` switches which entries count and strips the ``.md`` suffix.
    """
    found: list[str] = []
    for base in (root / ".claude" / kind, root / ".agents" / kind):
        if not base.is_dir():
            continue
        try:
            with os.scandir(base) as it:
                for item in it:
                    if len(found) >= _MAX_SKILLS:
                        break
                    if _is_hidden(item.name):
                        continue
                    try:
                        is_file = item.is_file(follow_symlinks=False)
                        is_dir = item.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if files and is_file and item.name.endswith(".md"):
                        name = item.name[:-3]
                    elif not files and is_dir:
                        name = item.name
                    else:
                        continue
                    if name not in found:
                        found.append(name)
        except OSError:
            continue
    return found


def _collect_skills(root: Path) -> list[str]:
    """Skill names defined in this repo (``.claude/skills`` / ``.agents/skills``)."""
    return _collect_named(root, "skills")


def probe_project(path: str | Path) -> ProjectProfile:
    """Shallow profile of a folder — one scandir plus a few existence checks."""
    root = Path(path).expanduser()
    profile = ProjectProfile(path=str(root), name=root.name or str(root))

    try:
        if not root.is_dir():
            profile.exists = False
            profile.note = "Folder not found."
            return profile
    except OSError as exc:
        profile.exists = False
        profile.note = f"Folder unreadable: {exc}"
        return profile

    names: set[str] = set()
    dirs: list[str] = []
    try:
        with os.scandir(root) as it:
            for item in it:
                names.add(item.name)
                try:
                    if item.is_dir(follow_symlinks=False):
                        if item.name in _SKIP_DIRS or _is_hidden(item.name):
                            continue
                        if len(dirs) < 24:
                            dirs.append(item.name)
                except OSError:
                    continue
    except OSError as exc:
        profile.note = f"Folder only partially readable: {exc}"

    profile.is_repo = (root / ".git").exists()
    if profile.is_repo:
        profile.branch = _git_branch(root)

    stacks: list[str] = []
    for marker, label in _STACK_MARKERS:
        hit = (
            any(n.endswith(marker[1:]) for n in names)
            if marker.startswith("*")
            else marker in names
        )
        if hit and label not in stacks:
            stacks.append(label)
    profile.stacks = stacks

    profile.instruction_files = [f for f in _INSTRUCTION_FILES if f in names]
    profile.top_level_dirs = sorted(dirs, key=str.lower)
    profile.skills = _collect_skills(root)
    # Subagents and slash commands are both single Markdown files (one per
    # definition); skills are directories with a SKILL.md inside.
    profile.subagents = [
        name for name in _collect_named(root, "agents", files=True) if name != "INDEX"
    ]
    profile.commands = _collect_named(root, "commands", files=True)
    return profile


__all__ = [
    "FolderEntry",
    "ProjectProfile",
    "WorkspaceEntry",
    "WorkspaceListing",
    "list_dir",
    "list_workspace_dir",
    "probe_project",
    "search_folders",
    "start_points",
]
