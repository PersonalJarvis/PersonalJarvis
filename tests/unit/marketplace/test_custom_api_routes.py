"""Custom API management is secret-free and execution stays behind the gateway."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.core.protocols import ToolResult
from jarvis.marketplace.catalog import PluginCatalog
from jarvis.marketplace.custom_api import CustomApiStore
from jarvis.marketplace.custom_api_runtime import CustomApiRuntime
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.token_store import InMemoryBackend, TokenStore
from jarvis.ui.web.custom_api_routes import router


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    store = CustomApiStore(tmp_path, TokenStore(InMemoryBackend()))
    app.state.custom_api_store = store
    app.state.plugin_registry = PluginToolRegistry(
        catalog=PluginCatalog(version=1, schema_version="1", plugins=[]),
        custom_api_runtime=CustomApiRuntime(store),
    )
    app.include_router(router)
    with TestClient(app) as value:
        yield value


def template(client):
    response = client.get("/api/custom-apis/templates/elevenlabs")
    assert response.status_code == 200
    return response.json()


def test_save_list_edit_disable_and_delete(client):
    spec = template(client)
    spec["enabled"] = True
    route = f"/api/custom-apis/{spec['id']}"
    result = client.put(route, json={"definition": spec, "credential": "test-secret-key"})
    assert result.status_code == 200
    assert result.json()["has_credential"]
    assert result.json()["tools_ready"]
    assert "test-secret-key" not in result.text
    listing = client.get("/api/custom-apis").json()
    assert listing == [result.json()]
    spec["enabled"] = False
    changed = client.put(route, json={"definition": spec})
    assert changed.status_code == 200
    assert not changed.json()["tools_ready"]
    assert changed.json()["has_credential"]
    assert client.delete(route).status_code == 200
    assert client.get("/api/custom-apis").json() == []
    assert client.app.state.custom_api_store.credential(spec["id"]) is None


def test_invalid_definition_never_echoes_credential_and_import_has_no_side_effect(client):
    spec = template(client)
    spec["actions"][0]["method"] = "INVALID"
    result = client.put(
        f"/api/custom-apis/{spec['id']}",
        json={
            "definition": spec,
            "credential": "test-secret-key",
            "unexpected": "test-secret-key",
        },
    )
    assert result.status_code == 422
    assert "test-secret-key" not in result.text
    assert client.get("/api/custom-apis").json() == []
    spec = template(client)
    assert client.post("/api/custom-apis/validate", json=spec).json() == spec
    assert client.get("/api/custom-apis").json() == []


def test_run_uses_supervisor_executor_and_never_calls_native_tool_directly(client):
    spec = template(client)
    spec["enabled"] = True
    assert (
        client.put(
            f"/api/custom-apis/{spec['id']}",
            json={
                "definition": spec,
                "credential": "test-secret-key",
            },
        ).status_code
        == 200
    )
    calls = []

    class DenyingExecutor:
        async def execute(self, tool, arguments, **kwargs):
            calls.append((tool, arguments, kwargs))
            return ToolResult(False, None, "Blocked by the user's policy")

    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(
            _tools=client.app.state.plugin_registry.custom_apis.tools,
            _tool_executor=DenyingExecutor(),
        )
    )
    previous = runtime_refs.get_supervisor_tool_gateway()
    runtime_refs.set_supervisor_tool_gateway(gateway)
    try:
        result = client.post(
            f"/api/custom-apis/{spec['id']}/actions/list_voices/run",
            json={"arguments": {"query": {"search": "voice"}}},
        )
        assert result.status_code == 200
        assert not result.json()["success"]
        assert len(calls) == 1
        assert calls[0][1] == {"query": {"search": "voice"}}
        assert calls[0][2]["config_snapshot"]["tool_origin"] == "custom-api-ui"
        assert "test-secret-key" not in result.text
    finally:
        runtime_refs.set_supervisor_tool_gateway(previous)


def test_openapi_exposes_custom_api_cli_routes(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/custom-apis/{api_id}"]["put"]
    assert operation["tags"] == ["custom-apis"]
    assert "save_custom_api" in operation["operationId"]
    assert operation["summary"]
