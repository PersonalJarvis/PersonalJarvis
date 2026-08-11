"""The one-pane chat stage is the grounding for "this terminal"."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry
from jarvis.ui.web import agentic_ide_routes
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    instance = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(agentic_ide_routes, "get_registry", lambda: instance)
    return instance


@pytest.fixture
def client(registry: Registry) -> TestClient:
    app = FastAPI()
    app.include_router(agentic_ide_routes.router)
    return TestClient(app)


async def test_chat_surface_records_the_visible_terminal(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    session = await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "codex"}])

    response = client.put(
        "/api/agentic-ide/surface-context",
        json={
            "workspace_id": session.id,
            "chat_view": True,
            "on_screen": True,
            "terminal": "T2",
            "prompt_target": "T2",
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert session.contextual_terminal() is session.find("T2")
    assert session.prompt_target_terminal() is session.find("T2")


async def test_grid_or_hidden_view_clears_the_implicit_terminal(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    payload = {
        "workspace_id": session.id,
        "chat_view": True,
        "on_screen": True,
        "terminal": "T1",
    }
    client.put("/api/agentic-ide/surface-context", json=payload)
    payload["chat_view"] = False

    client.put("/api/agentic-ide/surface-context", json=payload)

    assert session.contextual_terminal() is None


async def test_grid_keeps_the_explicit_prompt_target_for_bar_drops(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    session = await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "codex"}])

    response = client.put(
        "/api/agentic-ide/surface-context",
        json={
            "workspace_id": session.id,
            "chat_view": False,
            "on_screen": True,
            "terminal": None,
            "prompt_target": "T2",
        },
    )

    assert response.status_code == 200
    assert session.contextual_terminal() is None
    assert session.prompt_target_terminal() is session.find("T2")

    response = client.put(
        "/api/agentic-ide/surface-context",
        json={
            "workspace_id": session.id,
            "chat_view": False,
            "on_screen": False,
            "terminal": None,
            "prompt_target": "T2",
        },
    )

    assert response.status_code == 200
    assert session.prompt_target_terminal() is None


async def test_stale_workspace_cannot_replace_the_active_surface(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = await registry.start(str(first_dir), [{"agent": "claude"}])
    second = await registry.start(str(second_dir), [{"agent": "codex"}])

    response = client.put(
        "/api/agentic-ide/surface-context",
        json={
            "workspace_id": first.id,
            "chat_view": True,
            "on_screen": True,
            "terminal": "T1",
        },
    )

    assert response.json()["accepted"] is False
    assert second.contextual_terminal() is None


# --------------------------------------------------------------- named views
#
# The boolean above became a named enum once the view had to survive a third
# mode being added. Both spellings are served: `view` is the contract,
# `chat_view` is what a desktop WebView still holding an older bundle posts
# for the seconds before it reloads itself.


def test_the_pydantic_literal_still_matches_the_source_of_truth() -> None:
    """Layer 3 against layer 0 — the drift BUG-008 is a repeat of.

    The route's ``Literal`` has to spell the values a second time (Pydantic
    cannot take a tuple), and this is the assertion that makes the duplication
    safe. It also runs at import; the test states it out loud so a failure
    reads as "the enum drifted" rather than as a collection error.
    """
    from typing import get_args

    from jarvis.agentic_ide.workspace_view import WORKSPACE_VIEWS

    assert set(get_args(agentic_ide_routes.WorkspaceViewName)) == set(WORKSPACE_VIEWS)


async def test_a_named_chat_view_stages_one_pane(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """Chat view puts exactly one pane on screen, so "this one" resolves."""
    session = await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "codex"}])

    response = client.put(
        "/api/agentic-ide/surface-context",
        json={
            "workspace_id": session.id,
            "view": "chat",
            "on_screen": True,
            "terminal": "T2",
            "prompt_target": "T2",
        },
    )

    assert response.status_code == 200
    assert session.surface_view == "chat"
    assert session.contextual_terminal() is session.find("T2")


async def test_a_named_grid_view_clears_the_staged_pane(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    body = {
        "workspace_id": session.id,
        "view": "chat",
        "on_screen": True,
        "terminal": "T1",
    }
    client.put("/api/agentic-ide/surface-context", json=body)

    client.put("/api/agentic-ide/surface-context", json={**body, "view": "grid"})

    assert session.surface_view == "grid"
    assert session.contextual_terminal() is None


async def test_a_hidden_section_reports_the_grid_however_it_was_left(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """A workspace nobody is looking at must not answer "this terminal".

    Navigating to another section leaves this grid mounted behind it, so
    without this the workspace would still be filed as "chat on screen" and
    would go on resolving "this one" to a pane hidden behind whatever the user
    actually switched to.
    """
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])
    body = {
        "workspace_id": session.id,
        "view": "chat",
        "on_screen": True,
        "terminal": "T1",
    }
    client.put("/api/agentic-ide/surface-context", json=body)
    assert session.surface_view == "chat"

    client.put("/api/agentic-ide/surface-context", json={**body, "on_screen": False})

    assert session.surface_view == "grid"
    assert session.contextual_terminal() is None


async def test_an_older_bundle_is_still_understood(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """`chat_view` with no `view` is an old window, not a malformed request."""
    session = await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "codex"}])

    client.put(
        "/api/agentic-ide/surface-context",
        json={
            "workspace_id": session.id,
            "chat_view": True,
            "on_screen": True,
            "terminal": "T2",
        },
    )

    assert session.surface_view == "chat"
    assert session.contextual_terminal() is session.find("T2")


async def test_a_body_naming_no_view_at_all_reports_the_grid(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """The view that promises the least is what an unclear answer falls to."""
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])

    response = client.put(
        "/api/agentic-ide/surface-context",
        json={"workspace_id": session.id, "on_screen": True, "terminal": "T1"},
    )

    assert response.status_code == 200
    assert session.surface_view == "grid"
    assert session.contextual_terminal() is None


async def test_an_unknown_view_is_refused_rather_than_guessed(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """A view nobody defined is a bug in the caller, and says so at the door.

    Deliberately a 422 rather than a quiet fall back to the grid: this body
    comes from our own frontend, and a silent coercion here is exactly how the
    two layers drift apart without anyone noticing.
    """
    session = await registry.start(str(tmp_path), [{"agent": "claude"}])

    response = client.put(
        "/api/agentic-ide/surface-context",
        json={
            "workspace_id": session.id,
            "view": "cockpit",
            "on_screen": True,
            "terminal": "T1",
        },
    )

    assert response.status_code == 422
