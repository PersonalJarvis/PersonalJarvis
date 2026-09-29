"""Removing a workspace or a project makes its sidebar row go away for good.

Closing keeps a closed, restorable row; removing must not. The sidebar derives
closed rows — and even whole project rows — from the remembered workspaces in
the resume store, so a removal that only touches the registry or the project
library leaves the row on screen. The folder on disk is never touched.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide import library, resume_store, session, workspace_catalog
from jarvis.ui.web import agentic_ide_routes as routes
from jarvis.ui.web import chat_library_routes
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> session.Registry:
    monkeypatch.setattr(session, "agent_argv", lambda agent: (f"/usr/bin/{agent}",))
    instance = session.Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(routes, "get_registry", lambda: instance)
    return instance


def request() -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bus=None)))


def _row_ids(registry: session.Registry) -> set[str]:
    graph = workspace_catalog.project_graph(registry)
    return {w["id"] for p in graph["projects"] for w in p["workspaces"]}


async def test_removing_an_open_workspace_stops_it_and_drops_the_row(registry, tmp_path):
    first = await registry.start(str(tmp_path), [{"agent": "claude"}], name="First")
    second = await registry.start(str(tmp_path), [{"agent": "codex"}], name="Second")
    result = await routes.remove_workspace(request(), first.id)
    assert result["removed"] == first.id
    assert registry.get(first.id) is None
    assert _row_ids(registry) == {second.id}
    # A later save of the still-open sibling must not bring the record back.
    await registry.rename(second.id, "Still running")
    assert {w.session_id for w in resume_store.load().workspaces} == {second.id}
    assert tmp_path.is_dir()


async def test_removing_a_closed_workspace_drops_the_row(registry, tmp_path):
    first = await registry.start(str(tmp_path), [{"agent": "claude"}], name="First")
    second = await registry.start(str(tmp_path), [{"agent": "codex"}], name="Second")
    await registry.end(first.id)
    assert first.id in _row_ids(registry)
    await routes.remove_workspace(request(), first.id)
    assert _row_ids(registry) == {second.id}


async def test_removing_an_unknown_workspace_is_a_404(registry):
    with pytest.raises(HTTPException) as caught:
        await routes.remove_workspace(request(), "ide_missing")
    assert caught.value.status_code == 404


async def test_deleting_a_project_forgets_its_closed_workspaces(registry, tmp_path: Path):
    kept_dir = tmp_path / "kept"
    gone_dir = tmp_path / "gone"
    kept_dir.mkdir()
    gone_dir.mkdir()
    gone = library.ensure_project(gone_dir, name="Gone")
    kept = await registry.start(str(kept_dir), [{"agent": "claude"}], name="Kept")
    doomed = await registry.start(
        str(gone_dir), [{"agent": "claude"}], project_id=gone.id, name="Doomed"
    )
    await registry.end(doomed.id)

    assert chat_library_routes.delete_project(gone.id).removed is True

    graph = workspace_catalog.project_graph(registry)
    assert gone.id not in {p["id"] for p in graph["projects"]}
    assert _row_ids(registry) == {kept.id}
    assert gone_dir.is_dir()


def test_deleting_a_project_known_only_from_its_workspaces_still_removes_it(tmp_path: Path):
    # Older installs derive the project row from the folder alone; there is no
    # library entry to delete, and the row must still go.
    resume_store.save(
        resume_store.snapshot_now(
            [
                resume_store.SnapshotWorkspace(
                    session_id="ide_old",
                    folder=str(tmp_path),
                    terminals=[resume_store.SnapshotTerminal(key="a", name="A", agent="claude")],
                )
            ]
        )
    )
    derived = library.project_id_for(tmp_path)
    assert chat_library_routes.delete_project(derived).removed is True
    assert resume_store.load() is None
