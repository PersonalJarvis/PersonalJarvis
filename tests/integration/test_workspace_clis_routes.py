"""REST routes for the terminal CLIs the user added themselves.

Every test gets its own scratch store, so nothing here can see or damage the
maintainer's own entries.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.workspace import agents as registry
from jarvis.workspace import custom_clis

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"></svg>'


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "workspace-clis"
    monkeypatch.setattr(custom_clis, "workspace_clis_dir", lambda: root)
    monkeypatch.setattr(
        custom_clis, "workspace_clis_path", lambda: root / "custom.json"
    )
    registry.refresh_custom_agents()

    from jarvis.ui.web import workspace_clis_routes

    app = FastAPI()
    app.include_router(workspace_clis_routes.router)
    yield TestClient(app)

    monkeypatch.undo()
    registry.refresh_custom_agents()


def _create(client: TestClient, **overrides) -> dict:
    payload = {"display_name": "Antigravity", "command": "agy", **overrides}
    response = client.post("/api/workspace-clis", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_empty_list_still_carries_the_form_limits(client: TestClient) -> None:
    """The form has to know what it may accept before anything exists."""
    body = client.get("/api/workspace-clis").json()
    assert body["clis"] == []
    assert body["max_name_length"] > 0
    assert body["max_command_length"] > 0
    assert ".svg" in body["logo_extensions"]


def test_a_created_cli_is_immediately_openable(client: TestClient) -> None:
    """Stored, registered and offered — without a restart, which is the point."""
    entry = _create(client, description="Google's terminal coding CLI.")
    assert entry["id"] == "antigravity"
    assert entry["binary"] == "agy"
    assert entry["runs_through_shell"] is False
    assert entry["logo_url"] == ""

    assert "antigravity" in registry.coding_agent_names()
    agent = registry.get_agent("antigravity")
    assert agent is not None and agent.custom is True


def test_a_shell_command_says_so_in_the_payload(client: TestClient) -> None:
    """It changes what "the pane exited" means, so the UI has to be able to say."""
    entry = _create(client, display_name="Piped", command="agy | tee log.txt")
    assert entry["runs_through_shell"] is True


def test_a_blank_command_is_refused_with_a_readable_reason(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/workspace-clis", json={"display_name": "Nothing", "command": "  "}
    )
    assert response.status_code == 422
    # The message is meant to be read by the person looking at the form.
    assert "command" in response.json()["detail"].lower()


def test_a_patch_leaves_untouched_fields_alone(client: TestClient) -> None:
    entry = _create(client, description="Original description.")
    updated = client.patch(
        f"/api/workspace-clis/{entry['id']}", json={"display_name": "Antigravity CLI"}
    ).json()
    assert updated["id"] == entry["id"]
    assert updated["display_name"] == "Antigravity CLI"
    assert updated["description"] == "Original description."
    assert updated["command"] == "agy"


def test_patching_something_that_is_gone_says_so(client: TestClient) -> None:
    response = client.patch(
        "/api/workspace-clis/never-existed", json={"display_name": "X"}
    )
    assert response.status_code == 422


def test_delete_removes_it_from_the_registry(client: TestClient) -> None:
    entry = _create(client)
    assert client.delete(f"/api/workspace-clis/{entry['id']}").json()["ok"] is True
    assert "antigravity" not in registry.coding_agent_names()


def test_a_logo_round_trips_with_the_headers_that_make_it_safe(
    client: TestClient,
) -> None:
    entry = _create(client)
    uploaded = client.put(
        f"/api/workspace-clis/{entry['id']}/logo",
        files={"file": ("mark.svg", SVG, "image/svg+xml")},
    ).json()
    assert uploaded["logo_url"] == f"/api/workspace-clis/{entry['id']}/logo"

    served = client.get(uploaded["logo_url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/svg+xml")
    # SVG is a document format: the same URL opened in a tab could run script.
    assert "sandbox" in served.headers["content-security-policy"]
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.content == SVG


def test_the_registry_learns_about_a_new_logo(client: TestClient) -> None:
    """A stored logo the registry has not re-read is one no picker can draw."""
    entry = _create(client)
    client.put(
        f"/api/workspace-clis/{entry['id']}/logo",
        files={"file": ("mark.svg", SVG, "image/svg+xml")},
    )
    agent = registry.get_agent(entry["id"])
    assert agent is not None
    assert agent.logo_url == f"/api/workspace-clis/{entry['id']}/logo"


def test_a_file_pretending_to_be_an_image_is_refused(client: TestClient) -> None:
    entry = _create(client)
    response = client.put(
        f"/api/workspace-clis/{entry['id']}/logo",
        files={"file": ("mark.svg", b"PK\x03\x04 a zip file", "image/svg+xml")},
    )
    assert response.status_code == 422


def test_asking_for_a_logo_that_is_not_there(client: TestClient) -> None:
    entry = _create(client)
    assert client.get(f"/api/workspace-clis/{entry['id']}/logo").status_code == 404


def test_removing_a_logo_leaves_the_entry(client: TestClient) -> None:
    entry = _create(client)
    client.put(
        f"/api/workspace-clis/{entry['id']}/logo",
        files={"file": ("mark.svg", SVG, "image/svg+xml")},
    )
    cleared = client.delete(f"/api/workspace-clis/{entry['id']}/logo").json()
    assert cleared["logo_url"] == ""
    assert client.get("/api/workspace-clis").json()["clis"][0]["id"] == entry["id"]


def test_the_write_routes_are_marked_dangerous() -> None:
    """A stored entry is a command line this app will start in a terminal.

    That is what the user is asking for when they type it into the form — and
    what must not happen because a model decided it would be helpful.
    """
    from jarvis.ui.web import workspace_clis_routes

    app = FastAPI()
    app.include_router(workspace_clis_routes.router)
    paths = app.openapi()["paths"]

    assert paths["/api/workspace-clis"]["post"].get("x-jarvis-dangerous") is True
    for method in ("patch", "delete"):
        entry = paths["/api/workspace-clis/{cli_id}"][method]
        assert entry.get("x-jarvis-dangerous") is True
    # Reading the list, and fetching a logo, are not.
    assert "x-jarvis-dangerous" not in paths["/api/workspace-clis"]["get"]
    assert "x-jarvis-dangerous" not in paths["/api/workspace-clis/{cli_id}/logo"]["get"]
