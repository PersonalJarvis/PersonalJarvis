"""The tools endpoint reports the BrainManager's effective live surface."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.tools_routes import router


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"{name} description",
        risk_tier="monitor",
        schema={"type": "object"},
    )


def test_lists_effective_brain_tools_not_partial_mcp_registry() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.brain = SimpleNamespace(
        _tools={
            "wiki-recall": _tool("wiki-recall"),
            "cli_gh": _tool("cli_gh"),
            "notebooklm-mcp/search": _tool("notebooklm-mcp/search"),
        }
    )
    app.state.tool_registry = {"partial/only": _tool("partial/only")}

    with TestClient(app) as client:
        body = client.get("/api/tools").json()

    assert body["total"] == 3
    assert {item["name"] for item in body["tools"]} == {
        "wiki-recall",
        "cli_gh",
        "notebooklm-mcp/search",
    }
    assert body["by_source"] == {"mcp": 1, "cli": 1, "native": 1}


def test_falls_back_to_user_mcp_registry_before_brain_ready() -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.brain = None
    app.state.tool_registry = {"weather/search": _tool("weather/search")}

    with TestClient(app) as client:
        body = client.get("/api/tools").json()

    assert body["total"] == 1
    assert body["tools"][0]["name"] == "weather/search"
    assert body["by_source"]["mcp"] == 1
