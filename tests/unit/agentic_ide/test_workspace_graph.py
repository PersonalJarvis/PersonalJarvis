"""The durable Project -> Workspace -> coding-session navigation contract."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide import library, resume_store, session, workspace_catalog
from jarvis.ui.web import agentic_ide_routes as routes
from tests.fakes.fake_pty_manager import FakePtyManager


def test_concurrent_readers_initialize_one_lazy_registry(monkeypatch: pytest.MonkeyPatch):
    ready = threading.Barrier(8)
    release_constructor = threading.Event()
    initialized = []

    class SlowRegistry:
        def __init__(self):
            initialized.append(self)
            # Release the GIL while simultaneous REST/tool readers reach the
            # constructor, making an unguarded singleton race reproducible.
            release_constructor.wait(timeout=0.05)

    monkeypatch.setattr(session, "_REGISTRY", None)
    monkeypatch.setattr(session, "Registry", SlowRegistry)
    assert initialized == []

    def read_registry(_index):
        ready.wait(timeout=5)
        return session.get_registry()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read_registry, range(8)))
    assert len(initialized) == 1
    assert all(result is initialized[0] for result in results)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> session.Registry:
    monkeypatch.setattr(session, "agent_argv", lambda agent: (f"/usr/bin/{agent}",))
    instance = session.Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(routes, "get_registry", lambda: instance)
    return instance


def request() -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bus=None)))


async def test_empty_project_needs_no_workspace_or_process(registry, tmp_path):
    project = library.ensure_project(tmp_path, name="Example")
    graph = workspace_catalog.project_graph(registry)
    assert graph["projects"][0]["id"] == project.id
    assert graph["projects"][0]["workspaces"] == []
    assert graph["active_workspace_id"] is None
    assert registry._pty.spawns == []


async def test_two_workspaces_share_project_but_keep_independent_sessions(registry, tmp_path):
    project = library.ensure_project(tmp_path)
    first = await registry.start(
        str(tmp_path),
        [{"agent": "claude"}],
        project_id=project.id,
        name="Installer",
    )
    second = await registry.start(
        str(tmp_path),
        [{"agent": "codex"}],
        project_id=project.id,
        name="Interface",
    )
    assert first.id != second.id
    assert first.project_id == second.project_id == project.id
    assert first.terminals[0].history_id != second.terminals[0].history_id
    graph = workspace_catalog.project_graph(registry)
    assert graph["active_project_id"] == project.id
    assert [w["name"] for w in graph["projects"][0]["workspaces"]] == ["Installer", "Interface"]
    assert graph["active_workspace_id"] == second.id
    assert first.to_dict()["project_id"] == project.id
    assert first.to_card(active=False)["project_id"] == project.id


async def test_project_ownership_rejects_another_folder(registry, tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    project = library.ensure_project(first)
    with pytest.raises(session.SessionError, match="belong"):
        await registry.start(str(second), [{"agent": "claude"}], project_id=project.id)
    assert registry.sessions == []


async def test_eight_cap_applies_to_create_add_and_batch(registry, tmp_path):
    with pytest.raises(session.SessionError, match="At most 8"):
        await registry.start(str(tmp_path), [{"agent": "claude"}] * 9)
    space = await registry.start(str(tmp_path), [{"agent": "claude"}] * 7)
    with pytest.raises(session.SessionError, match="at most 8"):
        await registry.add_terminals(2)
    assert len(space.terminals) == 7
    await registry.add_terminal(agent="claude")
    with pytest.raises(session.SessionError, match="maximum of 8"):
        await registry.add_terminal(agent="claude")
    assert len(space.terminals) == 8


async def test_closed_sibling_stays_restorable_when_project_has_an_open_workspace(
    registry, tmp_path
):
    first = await registry.start(str(tmp_path), [{"agent": "claude"}], name="First")
    second = await registry.start(str(tmp_path), [{"agent": "codex"}], name="Second")
    await registry.end(first.id)
    await registry.rename(second.id, "Still running")
    saved = resume_store.load()
    assert {space.session_id for space in saved.workspaces} == {first.id, second.id}
    graph = workspace_catalog.project_graph(registry)
    cards = {card["id"]: card for card in graph["projects"][0]["workspaces"]}
    assert cards[first.id]["status"] == "closed"
    assert cards[first.id]["restorable"] is True
    assert cards[second.id]["status"] == "open"
    restored = await registry.restore_workspace(first.id)
    assert restored.id == first.id
    assert restored.terminals[0].history_id == first.terminals[0].history_id
    assert restored.project_id == first.project_id
    assert registry._pty.spawns == []
    assert await registry.restore_workspace(first.id) is restored
    assert len(registry.sessions) == 2


async def test_order_survives_close_and_restore_without_changing_terminal_identity(
    registry, tmp_path
):
    space = await registry.start(str(tmp_path), [{"agent": "claude"}] * 6)
    ids = [term.history_id for term in reversed(space.terminals)]
    keys = {term.history_id: term.key for term in space.terminals}
    names = {term.history_id: term.name for term in space.terminals}
    result = await routes.reorder_workspace_terminals(
        request(),
        space.id,
        routes.TerminalOrderRequest(terminal_ids=ids),
    )
    assert [t["history_id"] for t in result["workspace"]["terminals"]] == ids
    assert max(term.column for term in space.terminals) <= 3
    assert max(term.slot for term in space.terminals) <= 1
    assert {term.history_id: term.key for term in space.terminals} == keys
    assert {term.history_id: term.name for term in space.terminals} == names
    await registry.end(space.id)
    restored = await routes.restore_saved_workspace(request(), space.id)
    assert [t["history_id"] for t in restored["session"]["terminals"]] == ids
    assert registry._pty.spawns == []


@pytest.mark.parametrize("invalid", [[], ["unknown"], ["duplicate", "duplicate"]])
async def test_invalid_order_is_rejected_without_mutation(registry, tmp_path, invalid):
    space = await registry.start(str(tmp_path), [{"agent": "claude"}] * 2)
    before = [term.history_id for term in space.terminals]
    with pytest.raises(HTTPException) as caught:
        await routes.reorder_workspace_terminals(
            request(),
            space.id,
            routes.TerminalOrderRequest(terminal_ids=invalid),
        )
    assert caught.value.status_code == 422
    assert [term.history_id for term in space.terminals] == before


def test_legacy_snapshot_derives_stable_workspace_and_project_ids(tmp_path: Path):
    raw = {"folder": str(tmp_path), "terminals": [{"name": "T1", "agent": "claude"}]}
    first = resume_store.SnapshotWorkspace.from_dict(raw)
    second = resume_store.SnapshotWorkspace.from_dict(raw)
    assert first.session_id == second.session_id
    assert first.session_id.startswith("ide_")
    assert first.project_id == library.project_id_for(tmp_path)
    assert first.to_dict()["project_id"] == first.project_id


async def test_duplicate_legacy_workspace_ids_do_not_overwrite_another_project(registry, tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    raw = {
        "version": 2,
        "workspaces": [
            {
                "session_id": "ide_legacy",
                "folder": str(folder),
                "terminals": [{"name": "T1", "agent": "claude"}],
            }
            for folder in (first, second)
        ],
    }
    snapshot = resume_store.Snapshot.from_dict(raw)
    again = resume_store.Snapshot.from_dict(raw)
    ids = [space.session_id for space in snapshot.workspaces]
    assert ids[0] == "ide_legacy"
    assert ids[0] != ids[1]
    assert ids == [space.session_id for space in again.workspaces]
    restored = await registry.restore(snapshot)
    assert len(restored.sessions) == len(registry.sessions) == 2
    assert {space.folder for space in registry.sessions} == {str(first), str(second)}
    assert {card["id"] for card in registry.state()["workspaces"]} == set(ids)
    saved = resume_store.load()
    assert {space.session_id for space in saved.workspaces} == set(ids)


async def test_oversized_legacy_workspace_is_preserved_without_partial_restore(registry, tmp_path):
    saved = resume_store.SnapshotWorkspace(
        session_id="legacy-large",
        folder=str(tmp_path),
        terminals=[
            resume_store.SnapshotTerminal(key=f"t{i}", name=f"T{i}", agent="claude")
            for i in range(9)
        ],
    )
    resume_store.save(resume_store.snapshot_now([saved]))
    with pytest.raises(session.SessionError, match="limit is 8"):
        await registry.restore_workspace(saved.session_id)
    assert len(resume_store.load().workspaces[0].terminals) == 9
    assert registry.sessions == []


async def test_session_route_accepts_project_and_workspace_name(registry, tmp_path):
    project = library.ensure_project(tmp_path)
    result = await routes.start_session(
        request(),
        routes.StartSessionRequest(
            folder=str(tmp_path),
            project_id=project.id,
            name="Linux installer",
            terminals=[routes.TerminalRequest(agent="claude")],
        ),
    )
    assert result["session"]["name"] == "Linux installer"
    assert result["session"]["project_id"] == project.id


async def test_add_agent_is_pinned_to_workspace_even_when_another_is_visible(registry, tmp_path):
    first = await registry.start(str(tmp_path), [{"agent": "claude"}])
    second = await registry.start(str(tmp_path), [{"agent": "codex"}])
    result = await routes.add_terminal(
        routes.AddTerminalRequest(
            workspace_id=first.id,
            agent="claude",
        )
    )
    assert len(first.terminals) == 2
    assert len(second.terminals) == 1
    assert result["state"]["active_id"] == second.id
    await registry.add_terminals(2, workspace_id=first.id)
    assert len(first.terminals) == 4
    assert len(second.terminals) == 1


@pytest.mark.parametrize("count", range(1, 9))
async def test_initial_grid_order_survives_restore_at_every_supported_size(
    registry, tmp_path, count
):
    space = await registry.start(str(tmp_path), [{"agent": "claude"}] * count)
    ids = [term.history_id for term in space.terminals]
    await registry.end(space.id)
    restored = await registry.restore_workspace(space.id)
    assert [term.history_id for term in restored.terminals] == ids
    assert max(term.column for term in restored.terminals) <= 3
    assert max(term.slot for term in restored.terminals) <= 1
