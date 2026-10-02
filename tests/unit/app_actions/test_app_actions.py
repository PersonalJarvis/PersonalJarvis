"""Every app action for Jarvis, under the person's per-action policy."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI, UploadFile
from pydantic import BaseModel

from jarvis.app_actions import catalog as catalog_mod
from jarvis.app_actions import history
from jarvis.app_actions.catalog import build_catalog, default_tier
from jarvis.app_actions.policy import effective_tier, load_policy, set_mode
from jarvis.plugins.tool.app_action import FindAppActionTool, RunAppActionTool
from jarvis.ui.web.app_actions_routes import router as app_actions_router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(app_actions_router)
    seen: dict[str, Any] = {}

    @app.post("/api/workspace/panes/{pane_id}/rename", tags=["agentic-ide"])
    async def rename_pane(pane_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Give a terminal pane a new title."""
        seen["rename"] = (pane_id, body)
        return {"ok": True, "title": body.get("title")}

    @app.get("/api/skills", tags=["skills"])
    async def list_skills(limit: int = 10) -> dict[str, Any]:
        """List installed skills."""
        return {"skills": [], "limit": limit}

    @app.delete("/api/skills/{name}", tags=["skills"])
    async def delete_skill(name: str) -> dict[str, Any]:
        """Delete one skill."""
        return {"deleted": name}

    @app.post("/api/secrets/{key}", tags=["settings"])
    async def store_secret(key: str) -> dict[str, Any]:
        return {"ok": True}

    app.state.seen = seen
    return app


class _Runtime:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def resolve_transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self._app)

    def control_key(self) -> None:
        return None


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    application = _app()
    monkeypatch.setattr(catalog_mod, "_cache", {})
    monkeypatch.setattr("jarvis.core.runtime_refs.get_web_app", lambda: application, raising=False)
    return application


def _id(app: FastAPI, path: str, method: str) -> str:
    for entry in build_catalog(app.openapi()).values():
        if entry.path == path and entry.method == method:
            return entry.id
    raise AssertionError(f"{method} {path} not in catalog")


def test_catalog_offers_actions_but_never_secrets_or_its_own_policy(app: FastAPI) -> None:
    catalog = build_catalog(app.openapi())
    paths = {e.path for e in catalog.values()}
    assert "/api/workspace/panes/{pane_id}/rename" in paths
    assert "/api/secrets/{key}" not in paths
    assert not any(p.startswith("/api/app-actions") for p in paths)


def test_remote_commands_ask_and_the_brain_switch_is_never_offered() -> None:
    """A prompt-injected page must not reach a shell on the person's server or
    flip the user-only main-brain switch through run-app-action."""
    from jarvis.ui.web.computers_routes import router as computers_router

    application = FastAPI()
    application.include_router(computers_router)

    @application.post("/api/brain/switch", tags=["providers"])
    async def brain_switch() -> dict[str, Any]:
        return {"ok": True}

    catalog = build_catalog(application.openapi())
    by_route = {(e.method, e.path): e for e in catalog.values()}
    for path in ("run", "power", "install"):
        entry = by_route[("POST", f"/api/computers/{{computer_id}}/{path}")]
        assert default_tier(entry) == "ask", path
    assert ("POST", "/api/brain/switch") not in by_route


def test_default_tiers_read_safe_change_monitor_delete_ask(app: FastAPI) -> None:
    catalog = build_catalog(app.openapi())
    tiers = {(e.method, e.path): default_tier(e) for e in catalog.values()}
    assert tiers[("GET", "/api/skills")] == "safe"
    assert tiers[("POST", "/api/workspace/panes/{pane_id}/rename")] == "monitor"
    assert tiers[("DELETE", "/api/skills/{name}")] == "ask"


def test_the_persons_mode_wins_and_can_be_cleared(app: FastAPI) -> None:
    entry = build_catalog(app.openapi())[_id(app, "/api/skills/{name}", "DELETE")]
    set_mode(entry.id, "allow")
    assert effective_tier(entry) == "monitor"
    set_mode(entry.id, "block")
    assert effective_tier(entry) == "block"
    set_mode(entry.id, None)
    assert entry.id not in load_policy() and effective_tier(entry) == "ask"


async def test_find_then_run_an_action_in_process(app: FastAPI) -> None:
    found = await FindAppActionTool().execute({"query": "rename pane"}, None)
    assert found.success
    first = found.output["actions"][0]
    assert first["title"] == "Rename Pane" and first["runs"] == "freely"

    tool = RunAppActionTool(runtime=_Runtime(app))
    result = await tool.execute(
        {"action_id": first["action_id"], "params": {"pane_id": "p1", "body": {"title": "API"}}},
        None,
    )
    assert result.success, result.error
    assert app.state.seen["rename"] == ("p1", {"title": "API"})
    assert history.recent(1)[0]["outcome"] == "ran"


async def test_a_blocked_action_never_runs(app: FastAPI) -> None:
    action_id = _id(app, "/api/workspace/panes/{pane_id}/rename", "POST")
    set_mode(action_id, "block")
    tool = RunAppActionTool(runtime=_Runtime(app))
    assert tool.risk_tier_for_args({"action_id": action_id}) == "block"
    result = await tool.execute({"action_id": action_id, "params": {"pane_id": "p1"}}, None)
    assert not result.success and "blocked" in (result.error or "")
    assert "rename" not in app.state.seen
    assert history.recent(1)[0]["outcome"] == "blocked"


async def test_query_parameters_and_unknown_ids(app: FastAPI) -> None:
    tool = RunAppActionTool(runtime=_Runtime(app))
    listed = await tool.execute(
        {"action_id": _id(app, "/api/skills", "GET"), "params": {"limit": 3}}, None
    )
    assert listed.output["response"]["limit"] == 3
    unknown = await tool.execute({"action_id": "nope"}, None)
    assert not unknown.success and "find-app-action" in (unknown.error or "")


async def test_settings_routes_list_set_and_show_history(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        listed = (await client.get("/api/app-actions")).json()
        assert listed["count"] == len(build_catalog(app.openapi()))
        action_id = listed["actions"][0]["id"]
        changed = await client.put(f"/api/app-actions/{action_id}/mode", json={"mode": "ask"})
        assert changed.json()["tier"] == "ask"
        assert (await client.put("/api/app-actions/nope/mode", json={})).status_code == 404
        history.record(action_id, "ran", "x")
        rows = (await client.get("/api/app-actions/history")).json()["history"]
        assert rows[0]["action"] == action_id


@pytest.mark.parametrize("area", ["IDE", "IDE panes and workspaces", "agentic-ide", "nonsense"])
async def test_area_is_a_hint_not_an_exact_slug(app: FastAPI, area: str) -> None:
    # Live 2026-10-01: the model passed the human label from the description
    # and an exact slug match hid every action.
    found = await FindAppActionTool().execute({"query": "rename pane", "area": area}, None)
    assert found.success
    assert found.output["actions"][0]["title"] == "Rename Pane"


class _Hook(BaseModel):
    url: str
    webhook_token: str


class _NewComputer(BaseModel):
    host: str
    password: str


class _Usage(BaseModel):
    tokens_in: int
    max_tokens: int


class _Session(BaseModel):
    permission_mode: str


def test_actions_carrying_a_credential_are_never_offered() -> None:
    # AP-2: the URL alone hid neither a webhook route returning its token nor
    # a computer route taking a password.
    application = FastAPI()

    @application.get("/api/tasks/{task_id}/webhook-connection", response_model=_Hook)
    async def hook(task_id: str) -> _Hook:
        return _Hook(url="", webhook_token="")

    @application.post("/api/computers")
    async def add_computer(body: _NewComputer) -> dict[str, Any]:
        return {}

    @application.get("/api/usage", response_model=_Usage)
    async def usage() -> _Usage:
        return _Usage(tokens_in=0, max_tokens=0)

    @application.patch("/api/agent-chat/sessions/{sid}")
    async def patch_session(sid: str, body: _Session) -> dict[str, Any]:
        return {}

    catalog = build_catalog(application.openapi())
    routes = {(e.method, e.path): e for e in catalog.values()}
    assert ("GET", "/api/tasks/{task_id}/webhook-connection") not in routes
    assert ("POST", "/api/computers") not in routes
    assert ("GET", "/api/usage") in routes
    assert default_tier(routes[("PATCH", "/api/agent-chat/sessions/{sid}")]) == "ask"


async def test_a_misspelled_parameter_is_refused_before_anything_runs(app: FastAPI) -> None:
    action_id = _id(app, "/api/skills", "GET")
    tool = RunAppActionTool(runtime=_Runtime(app))
    result = await tool.execute({"action_id": action_id, "params": {"limt": 3}}, None)
    assert not result.success and result.output["executed"] is False
    assert "limt" in (result.error or "") and "limit" in (result.error or "")
    ran = await tool.execute({"action_id": action_id, "params": {"limit": 3}}, None)
    assert ran.success and ran.output["response"]["limit"] == 3


def test_a_long_response_is_shortened_into_valid_json() -> None:
    import json

    from jarvis.plugins.tool.app_action import _MAX_RESPONSE_CHARS, _trim

    big = {"items": [{"name": f"skill-{i}", "notes": "x" * 300} for i in range(500)]}
    trimmed = _trim(big)
    assert trimmed["truncated"] is True
    assert len(json.dumps(trimmed)) <= _MAX_RESPONSE_CHARS + 100
    assert trimmed["data"]["items"][0]["name"] == "skill-0"
    assert trimmed["data"]["items"][-1].endswith("more")


def test_a_file_upload_is_not_offered_as_an_action() -> None:

    application = FastAPI()

    @application.post("/api/skills/upload")
    async def upload(file: UploadFile) -> dict[str, Any]:
        return {}

    paths = {e.path for e in build_catalog(application.openapi()).values()}
    assert "/api/skills/upload" not in paths
