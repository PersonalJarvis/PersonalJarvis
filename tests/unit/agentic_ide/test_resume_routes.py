"""The resume offer, as the UI and the `jarvis` CLI both see it.

Nothing here may raise. This is the first screen of the Agentic IDE, so a fresh
install with no history, a deleted folder and a machine without either coding
CLI all have to produce a readable answer rather than a 500.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from jarvis.ui.web import agentic_ide_routes


# The real probe, kept aside so one test can put it back and prove that IT, and
# not the test's own stand-in, is what survives a failing agent detection.
_real_probe = agentic_ide_routes._installed_agents


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    monkeypatch.setattr(agentic_ide_routes, "agent_argv", lambda name: (f"/usr/bin/{name}",))

    async def _both_installed() -> set[str]:
        return {"claude", "codex"}

    monkeypatch.setattr(agentic_ide_routes, "_installed_agents", _both_installed)
    ide.reset_registry()
    app = FastAPI()
    app.include_router(agentic_ide_routes.router)
    with TestClient(app) as client:
        yield client
    ide.reset_registry()


def _store(*folders: Path, with_conversation: bool = True) -> None:
    """Put a restore point on disk holding one workspace per folder."""
    resume_store.save(
        resume_store.Snapshot(
            saved_at=100.0,
            workspaces=[_workspace(f, with_conversation=with_conversation) for f in folders],
        )
    )


def _workspace(folder: Path, *, with_conversation: bool = True) -> resume_store.SnapshotWorkspace:
    return resume_store.SnapshotWorkspace(
        session_id="ide_old",
        folder=str(folder),
        terminals=[
            resume_store.SnapshotTerminal(
                key="alex",
                name="Alex",
                agent="claude",
                column=0,
                slot=0,
                resume=(
                    ResumeHandle(kind="claude_session", id="u", captured_at=1.0)
                    if with_conversation
                    else None
                ),
                prompts_sent=2,
            ),
            resume_store.SnapshotTerminal(
                key="blake", name="Blake", agent="codex", column=1, slot=0
            ),
        ],
    )


# ------------------------------------------------------------------- offer
def test_no_snapshot_means_no_offer(client: TestClient) -> None:
    """A fresh install: an empty answer, never an error."""
    body = client.get("/api/agentic-ide/resume").json()
    assert body["available"] is False
    assert body["workspaces"] == []


def test_the_offer_names_the_panes_and_where_they_sat(client: TestClient, tmp_path: Path) -> None:
    _store(tmp_path)
    body = client.get("/api/agentic-ide/resume").json()
    assert body["available"] is True
    space = body["workspaces"][0]
    assert space["folder"] == str(tmp_path)
    assert space["folder_name"] == tmp_path.name
    panes = space["terminals"]
    assert [t["name"] for t in panes] == ["Alex", "Blake"]
    assert [(t["column"], t["slot"]) for t in panes] == [(0, 0), (1, 0)]
    assert [t["display_name"] for t in panes] == ["Claude Code", "Codex"]


def test_the_offer_says_which_conversations_come_back(
    client: TestClient, tmp_path: Path, existing_conversation
) -> None:
    _store(tmp_path)
    existing_conversation("u")
    body = client.get("/api/agentic-ide/resume").json()
    panes = {t["name"]: t for t in body["workspaces"][0]["terminals"]}
    assert panes["Alex"]["resumable"] is True
    assert panes["Blake"]["resumable"] is False  # never got a conversation id
    assert body["resumable_count"] == 1


def test_the_offer_survives_a_machine_with_no_coding_cli(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _none() -> set[str]:
        return set()

    monkeypatch.setattr(agentic_ide_routes, "_installed_agents", _none)
    _store(tmp_path)
    body = client.get("/api/agentic-ide/resume").json()
    assert body["available"] is False
    panes = body["workspaces"][0]["terminals"]
    assert all(t["available"] is False for t in panes)


def test_a_broken_agent_probe_does_not_take_the_screen_down(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is the IDE's first screen — it must render even when a probe fails."""
    from jarvis.workspace import agents as workspace_agents

    async def _explode() -> list[object]:
        raise RuntimeError("the CLI prober fell over")

    monkeypatch.setattr(agentic_ide_routes, "_installed_agents", _real_probe)
    monkeypatch.setattr(workspace_agents, "detect_agents", _explode)
    _store(tmp_path)

    res = client.get("/api/agentic-ide/resume")
    assert res.status_code == 200
    body = res.json()
    # Honest rather than optimistic: nothing is promised that cannot be checked.
    assert body["available"] is False
    assert [t["name"] for t in body["workspaces"][0]["terminals"]] == ["Alex", "Blake"]


# ------------------------------------------------------------------ resume
def test_resuming_reopens_the_workspace(client: TestClient, tmp_path: Path) -> None:
    _store(tmp_path)
    res = client.post("/api/agentic-ide/resume")
    assert res.status_code == 200
    body = res.json()
    session = body["state"]["session"]
    assert [t["name"] for t in session["terminals"]] == ["Alex", "Blake"]
    assert session["folder"] == str(tmp_path)
    assert body["workspace_count"] == 1
    # The running state agrees, so a client may read either.
    assert client.get("/api/agentic-ide/state").json()["active"] is True


def test_resuming_reports_how_much_actually_came_back(
    client: TestClient, tmp_path: Path, existing_conversation
) -> None:
    """One pane continues, one reopens empty — and the caller is told."""
    _store(tmp_path)
    existing_conversation("u")
    body = client.post("/api/agentic-ide/resume").json()
    assert body["resumable_count"] == 1
    assert body["started_fresh"] == 1


def test_resuming_without_a_snapshot_is_a_conflict_not_a_crash(
    client: TestClient,
) -> None:
    res = client.post("/api/agentic-ide/resume")
    assert res.status_code == 409
    assert "no previous workspace" in res.json()["detail"]


def test_resuming_a_deleted_folder_says_so_plainly(client: TestClient, tmp_path: Path) -> None:
    _store(tmp_path / "gone")
    res = client.post("/api/agentic-ide/resume")
    assert res.status_code == 422
    assert "no longer" in res.json()["detail"]


def test_resuming_starts_no_agent_by_itself(client: TestClient, tmp_path: Path) -> None:
    """The panes connect as they always do; that is what starts them."""
    _store(tmp_path)
    body = client.post("/api/agentic-ide/resume").json()
    panes = body["state"]["session"]["terminals"]
    assert all(t["status"] == "pending" for t in panes)


# ------------------------------------------------------------- start fresh
def test_starting_fresh_withdraws_the_offer(client: TestClient, tmp_path: Path) -> None:
    _store(tmp_path)
    assert client.delete("/api/agentic-ide/resume").json()["removed"] is True
    assert client.get("/api/agentic-ide/resume").json()["available"] is False


def test_starting_fresh_twice_is_harmless(client: TestClient) -> None:
    assert client.delete("/api/agentic-ide/resume").json()["removed"] is False


def test_discarding_a_restore_point_is_declared_dangerous() -> None:
    """It cannot be undone, so the CLI has to warn before running it."""
    route = next(
        r
        for r in agentic_ide_routes.router.routes
        if getattr(r, "path", "") == "/api/agentic-ide/resume"
        and "DELETE" in getattr(r, "methods", set())
    )
    assert route.openapi_extra == {"x-jarvis-dangerous": True}


def test_resuming_counts_conversations_not_handles(client: TestClient, tmp_path: Path) -> None:
    """A pane that was never used holds an id pointing at nothing.

    Counting handles would report it as continued, which is the same lie the
    offer screen exists to prevent — and the one that reached the user as
    "12 conversations restored" for twelve panes that came back empty.
    """
    _store(tmp_path)  # Alex's id is deliberately not backed by a conversation
    body = client.post("/api/agentic-ide/resume").json()
    assert body["resumable_count"] == 0
    assert body["started_fresh"] == 2


def test_resuming_brings_back_every_workspace(
    client: TestClient, tmp_path: Path
) -> None:
    """"Resume all sessions" means all of them, not whichever was on screen."""
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    _store(first, second)

    body = client.post("/api/agentic-ide/resume").json()
    assert body["workspace_count"] == 2
    assert body["terminal_count"] == 4
    folders = {w["folder"] for w in body["state"]["workspaces"]}
    assert folders == {str(first), str(second)}
    # The first remembered workspace is the one on screen again.
    assert body["state"]["session"]["folder"] == str(first)


def test_a_workspace_that_cannot_come_back_does_not_fail_the_rest(
    client: TestClient, tmp_path: Path
) -> None:
    """Three folders, one deleted: two come back and the third is NAMED."""
    alive, gone = tmp_path / "alive", tmp_path / "deleted"
    alive.mkdir()
    _store(alive, gone)

    body = client.post("/api/agentic-ide/resume").json()
    assert body["workspace_count"] == 1
    assert [s["folder"] for s in body["skipped"]] == [str(gone)]
    assert "no longer" in body["skipped"][0]["detail"]


def test_the_offer_lists_each_workspace_it_remembers(
    client: TestClient, tmp_path: Path
) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    _store(first, second)

    body = client.get("/api/agentic-ide/resume").json()
    assert body["workspace_count"] == 2
    assert body["terminal_count"] == 4
    assert [w["folder_name"] for w in body["workspaces"]] == ["one", "two"]
