"""The chat library's REST surface — what the sidebar is allowed to cost.

The properties under test are the ones that decide whether this scales past the
maintainer's own machine: listing projects must not load their chats, listing
chats must not load their messages, and an unreachable folder must be reported
rather than cleaned up.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import library
from jarvis.ui.web.chat_library_routes import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_the_project_list_carries_counts_not_chats(client: TestClient, tmp_path: Path) -> None:
    """A thousand conversations must not travel with a forty-project sidebar.

    The list may look at each project's chat file to COUNT, but the chats
    themselves never leave the server — and no message file is opened at all,
    because this router never touches one.
    """
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")
    for i in range(3):
        library.create_thread(project.id, agent="claude", title=f"chat {i}")

    body = client.get("/api/chat-library/projects").json()

    assert len(body["projects"]) == 1
    entry = body["projects"][0]
    assert entry["chats"] == 3
    # The count is the ONLY thing the list says about them.
    assert "threads" not in entry
    assert "preview" not in entry


def test_chats_load_only_when_the_project_is_asked_for(client: TestClient, tmp_path: Path) -> None:
    """Two calls, on purpose: the second one is what a click pays for."""
    for name in ("left", "right"):
        (tmp_path / name).mkdir()
    left = library.ensure_project(tmp_path / "left")
    right = library.ensure_project(tmp_path / "right")
    library.create_thread(left.id, agent="claude", title="only mine")

    body = client.get(f"/api/chat-library/projects/{left.id}/chats").json()
    other = client.get(f"/api/chat-library/projects/{right.id}/chats").json()

    assert [c["title"] for c in body["chats"]] == ["only mine"]
    assert other["chats"] == []


def test_a_chat_row_never_carries_a_message(client: TestClient, tmp_path: Path) -> None:
    """The row is a title, an agent and a one-line preview — never a transcript.

    The conversation lives in the coding CLI's own files and is fetched
    separately; a row that carried it would make opening the sidebar as
    expensive as opening every chat in it.
    """
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")
    thread = library.create_thread(project.id, agent="codex")
    library.update_thread(project.id, thread.id, preview="last thing said")

    row = client.get(f"/api/chat-library/projects/{project.id}/chats").json()["chats"][0]

    assert row["preview"] == "last thing said"
    assert "messages" not in row
    # The resume handle is an internal pointer, not something the UI needs the
    # contents of — it is reported as a yes/no.
    assert "resume" not in row
    assert row["resumable"] is False


def test_an_unreachable_folder_is_reported_not_cleaned_up(
    client: TestClient, tmp_path: Path
) -> None:
    """An unplugged drive is a temporary state, not permission to delete history."""
    folder = tmp_path / "repo"
    folder.mkdir()
    project = library.ensure_project(folder)
    library.create_thread(project.id, agent="claude")
    folder.rmdir()

    entry = client.get("/api/chat-library/projects").json()["projects"][0]

    assert entry["id"] == project.id
    assert entry["exists"] is False
    assert entry["chats"] == 1


def test_opening_a_project_is_idempotent(client: TestClient, tmp_path: Path) -> None:
    """Every entry point can just open — none has to check first."""
    (tmp_path / "repo").mkdir()

    first = client.post("/api/chat-library/projects", json={"path": str(tmp_path / "repo")}).json()
    second = client.post("/api/chat-library/projects", json={"path": str(tmp_path / "repo")}).json()

    assert first["id"] == second["id"]
    assert len(client.get("/api/chat-library/projects").json()["projects"]) == 1


def test_a_folder_that_is_not_there_can_still_be_registered(
    client: TestClient, tmp_path: Path
) -> None:
    """Refusing here would make the library disagree with itself.

    A project whose folder vanishes LATER is kept (see the test above), so
    refusing to create one whose folder is missing NOW would be two rules for
    the same situation. The response says the folder is unreachable instead.
    """
    response = client.post("/api/chat-library/projects", json={"path": str(tmp_path / "not-there")})

    assert response.status_code == 200
    assert response.json()["exists"] is False


def test_archiving_hides_a_chat_without_destroying_it(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")
    thread = library.create_thread(project.id, agent="claude", title="old work")

    client.patch(
        f"/api/chat-library/projects/{project.id}/chats/{thread.id}",
        json={"archived": True},
    )

    assert client.get(f"/api/chat-library/projects/{project.id}/chats").json()["chats"] == []
    archived = client.get(
        f"/api/chat-library/projects/{project.id}/chats",
        params={"include_archived": True},
    ).json()["chats"]
    assert [c["title"] for c in archived] == ["old work"]


def test_renaming_a_chat_survives_the_next_read(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")
    thread = library.create_thread(project.id, agent="claude")

    client.patch(
        f"/api/chat-library/projects/{project.id}/chats/{thread.id}",
        json={"title": "Wake path"},
    )

    rows = client.get(f"/api/chat-library/projects/{project.id}/chats").json()["chats"]
    assert [c["title"] for c in rows] == ["Wake path"]


def test_deleting_a_chat_is_idempotent(client: TestClient, tmp_path: Path) -> None:
    """A double-click on delete must not become an error the user has to read."""
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")
    thread = library.create_thread(project.id, agent="claude")
    path = f"/api/chat-library/projects/{project.id}/chats/{thread.id}"

    assert client.delete(path).json() == {"removed": True}
    second = client.delete(path)
    assert second.status_code == 200
    assert second.json() == {"removed": False}


def test_unknown_ids_are_a_404_not_an_empty_list(client: TestClient) -> None:
    """ "No such project" and "a project with no chats" are different answers."""
    assert client.get("/api/chat-library/projects/nope/chats").status_code == 404
    assert client.patch("/api/chat-library/projects/nope", json={"name": "x"}).status_code == 404
    assert (
        client.patch("/api/chat-library/projects/nope/chats/nope", json={"title": "x"}).status_code
        == 404
    )


def test_creating_a_chat_starts_nothing(client: TestClient, tmp_path: Path) -> None:
    """No pane, no process, no tokens — a chat goes live on its first prompt."""
    (tmp_path / "repo").mkdir()
    project = library.ensure_project(tmp_path / "repo")

    row = client.post(
        f"/api/chat-library/projects/{project.id}/chats", json={"agent": "claude"}
    ).json()

    assert row["terminal"] is None
    assert row["prompts_sent"] == 0
    assert row["title"] == ""


def test_destructive_routes_declare_themselves(client: TestClient) -> None:
    """The danger flag is what keeps a delete out of an unattended yes (CLAUDE.md §5)."""
    schema = client.get("/openapi.json").json()["paths"]

    for path in (
        "/api/chat-library/projects/{project_id}",
        "/api/chat-library/projects/{project_id}/chats/{chat_id}",
    ):
        assert schema[path]["delete"]["x-jarvis-dangerous"] is True
