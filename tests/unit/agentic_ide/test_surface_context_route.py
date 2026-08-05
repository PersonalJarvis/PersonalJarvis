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
