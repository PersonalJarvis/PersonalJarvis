"""Project ownership and open/saved workspace navigation, without launching agents."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import library, resume_store

if TYPE_CHECKING:
    from .session import Registry


def project_graph(registry: Registry) -> dict[str, Any]:
    """Combine the project library and workspace registry by durable IDs.

    Older installations have folder-based snapshots but no project library.
    Their project rows are derived without writing during a read. Selecting a
    saved workspace remains an explicit action; this function never starts it.
    """
    from .session import MAX_TERMINALS

    projects = {
        project.id: {**project.to_dict(), "workspaces": []}
        for project in library.list_projects()
        if not project.scratch
    }

    def owner(project_id: str, folder: str) -> dict[str, Any]:
        if project_id not in projects:
            projects[project_id] = {
                **library.Project(
                    id=project_id,
                    path=folder,
                    name=Path(folder).name or folder,
                ).to_dict(),
                "workspaces": [],
            }
        return projects[project_id]

    open_ids: set[str] = set()
    for session in registry.sessions:
        project_id = session.project_id or library.project_id_for(session.folder)
        owner(project_id, session.folder)["workspaces"].append(
            {
                **session.to_card(active=session.id == registry.active_id),
                "status": "open",
                "restorable": False,
            }
        )
        open_ids.add(session.id)

    saved = resume_store.load()
    for space in saved.workspaces if saved else []:
        if space.session_id in open_ids:
            continue
        project_id = space.project_id or library.project_id_for(space.folder)
        try:
            folder_exists = Path(space.folder).is_dir()
        except OSError:
            # A disconnected volume is unavailable, not a reason to lose history.
            folder_exists = False
        owner(project_id, space.folder)["workspaces"].append(
            {
                "id": space.session_id,
                "project_id": project_id,
                "folder": space.folder,
                "name": space.name or Path(space.folder).name or "Workspace",
                "branch": None,
                "terminals": len(space.terminals),
                "live_terminals": 0,
                "focus_mode": False,
                "created_at": space.saved_at,
                "last_active_at": space.saved_at,
                "active": False,
                "status": "closed",
                "restorable": folder_exists and len(space.terminals) <= MAX_TERMINALS,
            }
        )
        open_ids.add(space.session_id)

    active = registry.session
    return {
        "projects": list(projects.values()),
        "active_project_id": (
            active.project_id or library.project_id_for(active.folder) if active else None
        ),
        "active_workspace_id": registry.active_id,
        "max_terminals": MAX_TERMINALS,
    }
