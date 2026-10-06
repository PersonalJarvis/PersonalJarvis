"""Which folders a thread's diff panel and terminal drawer may read.

A thread runs in its project's folder or in a worktree of it, and the IDE
creates worktrees inside the repository (``<repo>/.worktrees/<branch>``). So
the folder routes answer for a folder inside a connected project or an open
workspace, and for nothing else on the machine.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger


def _resolved(path: str | Path) -> Path | None:
    try:
        return Path(path).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):  # unresolvable: not a known folder
        return None


def _inside(child: Path, parent: Path) -> bool:
    a = os.path.normcase(str(child))
    b = os.path.normcase(str(parent))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def _known_roots() -> list[Path]:
    roots: list[str] = []
    try:
        from jarvis.agentic_ide.library import list_projects

        roots += [project.path for project in list_projects(include_archived=True) if project.path]
    except Exception as exc:  # noqa: BLE001 - an unreadable library leaves the workspaces
        logger.debug("Agentic IDE: project list unreadable for a folder check: {}", exc)
    try:
        from jarvis.agentic_ide.session import get_registry

        roots += [str(session.folder) for session in get_registry().sessions() if session.folder]
    except Exception as exc:  # noqa: BLE001 - no registry (a bare test app) leaves the projects
        logger.debug("Agentic IDE: workspace list unreadable for a folder check: {}", exc)
    return [path for path in (_resolved(root) for root in roots) if path is not None]


def allowed_thread_folder(folder: str | Path) -> Path | None:
    """``folder`` resolved, when it is an existing directory inside a known root."""
    path = _resolved(folder)
    if path is None or not path.is_absolute() or not path.is_dir():
        return None
    if any(_inside(path, root) for root in _known_roots()):
        return path
    return None
