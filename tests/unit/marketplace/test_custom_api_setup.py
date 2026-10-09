"""Name/key provisioning and inherited native actions across service descriptions."""

from __future__ import annotations

import json
import logging
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.protocols import ExecutionContext
from jarvis.marketplace.catalog import PluginCatalog
from jarvis.marketplace.custom_api import ApiConnectionInfo, ApiDefinition, CustomApiStore
from jarvis.marketplace.custom_api_connect import verify_api_key
from jarvis.marketplace.custom_api_discovery import ApiDiscovery, ApiDiscoveryError
from jarvis.marketplace.custom_api_openapi import api_auth, api_base_url, compile_openapi
from jarvis.marketplace.custom_api_runtime import CustomApiRuntime
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.token_store import InMemoryBackend, TokenStore
from jarvis.ui.web.custom_api_routes import router

BASE = "https://93.184.216.34"
KEY = "fixture-only-service-key"


def test_import_counts_incompatible_auth_and_path_server_as_omitted():
    spec = document()
    spec["components"]["securitySchemes"]["second"] = {
        "type": "apiKey",
        "in": "header",
        "name": "Second-Key",
    }
    spec["paths"]["/v1/user"]["get"]["security"] = [{"key": [], "second": []}]
    spec["paths"]["/transcribe"]["servers"] = [{"url": "https://another.example"}]
    actions, _, omitted = compile_openapi(spec, api_auth(spec), BASE)
    assert omitted == 2
    assert {a.path for a in actions} == {"/speak/{voice}", "/voices/{voice}"}


def test_optional_body_and_hyphenated_path_parameter():
    from jsonschema import Draft202012Validator

    spec = document()
    operation = spec["paths"].pop("/speak/{voice}")
    operation["post"]["parameters"][0]["name"] = "voice-id"
    spec["paths"]["/speak/{voice-id}"] = operation
    actions, _, _ = compile_openapi(spec, api_auth(spec), BASE)
    action = next(a for a in actions if a.path == "/speak/{voice-id}")
    assert Draft202012Validator(action.input_schema()).is_valid({"path": {"voice-id": "voice"}})


@pytest.mark.asyncio
async def test_search_cannot_choose_a_service_named_subdomain_on_unrelated_host():
    async def search(name):
        return [{"url": "https://sample.attacker.example/openapi.json"}]

    discovery = ApiDiscovery(directory={}, search=search)
    discovery._brands = []
    try:
        with pytest.raises(ApiDiscoveryError, match="service_not_found"):
            await discovery.resolve("sample")
    finally:
        await discovery.close()


def document():
    return {
        "openapi": "3.1.0",
        "info": {"title": "Sample Cloud", "version": "1"},
        "servers": [{"url": BASE}],
        "components": {
            "securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
            "schemas": {
                "Message": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                }
            },
        },
        "paths": {
            "/v1/user": {
                "get": {"operationId": "get_user", "summary": "Read account", "tags": ["Account"]}
            },
            "/speak/{voice}": {
                "post": {
                    "operationId": "speak",
                    "summary": "Generate speech",
                    "tags": ["Speech"],
                    "parameters": [
                        {
                            "in": "path",
                            "name": "voice",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Message"}}
                        }
                    },
                }
            },
            "/transcribe": {
                "post": {
                    "operationId": "transcribe",
                    "summary": "Transcribe an audio file",
                    "tags": ["Speech"],
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {"type": "string", "format": "binary"},
                                        "language": {"type": "string"},
                                    },
                                    "required": ["file"],
                                }
                            }
                        }
                    },
                }
            },
            "/voices/{voice}": {
                "delete": {
                    "operationId": "delete_voice",
                    "summary": "Delete voice",
                    "tags": ["Voices"],
                    "parameters": [
                        {
                            "in": "path",
                            "name": "voice",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                }
            },
        },
    }


def definition(count: int = 4):
    source = document()
    for i in range(count - 4):
        source["paths"][f"/objects/{i}"] = {"get": {"summary": f"Read object {i}"}}
    auth = api_auth(source)
    actions, categories, omitted = compile_openapi(source, auth, BASE)
    assert omitted == 0
    return ApiDefinition(
        name="Sample Cloud",
        base_url=BASE,
        auth=auth,
        actions=actions,
        connection=ApiConnectionInfo(
            service_id="sample-cloud",
            website=BASE,
            spec_url=BASE + "/openapi.json",
            categories=categories,
        ),
    )


@pytest.fixture
def store(tmp_path):
    return CustomApiStore(tmp_path / "services", TokenStore(InMemoryBackend()))


def ctx():
    return ExecutionContext(
        trace_id=uuid4(), user_utterance="Use my service", config={}, memory_read=None
    )


def test_openapi_compilation_resolves_refs_auth_paths_and_uploads():
    spec = definition()
    speak = next(a for a in spec.actions if a.path == "/speak/{voice}")
    assert speak.input_schema()["properties"]["body"]["required"] == ["text"]
    assert speak.parameters[0].location == "path"
    upload = next(a for a in spec.actions if a.path == "/transcribe")
    assert upload.body_encoding == "multipart" and upload.file_fields == ["file"]
    assert next(a for a in spec.actions if a.method == "DELETE").risk_tier == "ask"
    assert api_base_url(document(), BASE + "/openapi.json") == BASE


@pytest.mark.asyncio
async def test_hundreds_of_actions_are_inherited_without_hundreds_of_prompt_tools(store):
    spec = definition(125)
    store.save(spec, KEY)
    calls = []

    def respond(request):
        calls.append(str(request.url))
        assert request.headers["X-API-Key"] == KEY
        return httpx.Response(200, json={"ok": True})

    runtime = CustomApiRuntime(store, transport=httpx.MockTransport(respond))
    await runtime.refresh()
    assert len(runtime.tools) == 2
    search = runtime.tools[f"api_{spec.id}_actions"]
    call = runtime.tools[f"api_{spec.id}_call"]
    assert search.yields_instructions_only and not search.is_action_tool
    first = await search.execute({}, ctx())
    assert first.output["total"] == 125 and first.output["next_offset"] == 15
    assert calls == []
    action = next(a for a in spec.actions if a.path == "/objects/120")
    schema = await search.execute({"action": action.id}, ctx())
    assert schema.success and "input_schema" in schema.output
    result = await call.execute({"action": action.id, "arguments": {}}, ctx())
    assert result.success and calls == [BASE + "/objects/120"]
    store.delete(spec.id)
    assert not (await call.execute({"action": action.id, "arguments": {}}, ctx())).success
    await runtime.stop()


@pytest.mark.asyncio
async def test_dynamic_risk_and_multipart_upload(store, tmp_path):
    spec = definition()
    store.save(spec, KEY)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFF-example-audio")
    requests = []

    def respond(request):
        requests.append(request)
        assert "multipart/form-data; boundary=" in request.headers["Content-Type"]
        assert b"RIFF-example-audio" in request.content
        assert b'name="file"' in request.content
        return httpx.Response(200, json={"text": "Hello"})

    runtime = CustomApiRuntime(store, transport=httpx.MockTransport(respond))
    await runtime.refresh()
    tool = runtime.tools[f"api_{spec.id}_call"]
    delete = next(a for a in spec.actions if a.method == "DELETE")
    assert tool.risk_tier_for_args({"action": delete.id}) == "ask"
    assert tool.describe_args({"action": delete.id})["level"] == "modify"
    action = next(a for a in spec.actions if a.path == "/transcribe")
    result = await tool.execute(
        {"action": action.id, "arguments": {"body": {"file": str(audio)}}}, ctx()
    )
    assert result.success and result.output == {"text": "Hello"}
    assert len(requests) == 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_query_credentials_reach_service_but_not_http_logs_or_results(store, caplog):
    spec = definition()
    spec.auth.mode = "query"
    spec.auth.header_name = "appid"
    store.save(spec, KEY)

    def respond(request):
        assert request.url.params["appid"] == KEY
        return httpx.Response(200, json={"echo": KEY})

    runtime = CustomApiRuntime(store, transport=httpx.MockTransport(respond))
    await runtime.refresh()
    action = spec.actions[0]
    with caplog.at_level(logging.INFO, logger="httpx"):
        result = await runtime.tools[f"api_{spec.id}_call"].execute(
            {"action": action.id, "arguments": {}}, ctx()
        )
    assert result.success and result.output == {"echo": "[redacted]"}
    assert KEY not in caplog.text
    await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, expected", [(200, "verified"), (403, "limited"), (503, "configured")]
)
async def test_setup_only_uses_a_documented_account_read(status, expected):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/v1/user"
        assert request.headers["X-API-Key"] == KEY
        return httpx.Response(status)

    assert (
        await verify_api_key(definition(), KEY, transport=httpx.MockTransport(respond)) == expected
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_rejected_key_never_installs_a_connection():
    with pytest.raises(ApiDiscoveryError, match="invalid_key"):
        await verify_api_key(
            definition(), KEY, transport=httpx.MockTransport(lambda request: httpx.Response(401))
        )


@pytest.mark.asyncio
async def test_unlisted_service_is_discovered_from_public_brand_documentation():
    class Discovery(ApiDiscovery):
        def __init__(self):
            super().__init__(directory={})
            self._brands = [{"title": "Sample Cloud", "source": BASE + "/brand"}]
            self.urls = []

        async def _fetch(self, url, maximum=0):
            self.urls.append(url)
            assert KEY not in url
            return json.dumps(document()).encode(), url

        async def _logo(self, domain, image_url=""):
            return ""

    discovery = Discovery()
    spec = await discovery.resolve("Sample Cloud")
    assert spec.name == "Sample Cloud" and len(spec.actions) == 4
    assert spec.base_url == BASE
    assert discovery.urls == [BASE + "/openapi.json"]
    await discovery.close()


def test_directory_match_is_not_a_fixed_service_allowlist():
    index = {
        "sample.example": {
            "preferred": "1",
            "versions": {
                "1": {
                    "info": {"title": "Sample Cloud"},
                    "swaggerUrl": BASE + "/openapi.json",
                }
            },
        }
    }
    assert ApiDiscovery._match("Sample Cloud", index)[0] == "sample.example"
    assert ApiDiscovery._match("Different brand", index) is None
    with pytest.raises(ApiDiscoveryError) as error:
        ApiDiscovery._match("Sampl Cloud", index)
    assert error.value.code == "ambiguous_service"
    assert error.value.suggestions == ["Sample Cloud"]


def test_name_and_key_route_keeps_credentials_out_of_discovery_and_summaries(store):
    calls = []

    class Discovery:
        async def resolve(self, name):
            calls.append(name)
            assert KEY not in name
            return definition(125)

    async def verify(spec, credential):
        assert credential == KEY
        return "verified"

    app = FastAPI()
    app.state.custom_api_store = store
    app.state.custom_api_discovery = Discovery()
    app.state.custom_api_verifier = verify
    app.state.plugin_registry = PluginToolRegistry(
        catalog=PluginCatalog(version=1, schema_version="1", plugins=[]),
        custom_api_runtime=CustomApiRuntime(store),
    )
    app.include_router(router)
    with TestClient(app) as client:
        identifier = uuid4().hex
        response = client.post(
            "/api/custom-apis/connect",
            json={"id": identifier, "name": "Sample Cloud", "credential": KEY},
        )
        assert response.status_code == 200, response.text
        assert response.json()["action_count"] == 125
        assert response.json()["tools_ready"] and response.json()["status"] == "verified"
        assert KEY not in response.text and "body_schema" not in response.text
        assert calls == ["Sample Cloud"]
        assert len(app.state.plugin_registry.active_tools()) == 2
        assert client.get("/api/custom-apis/connections").json() == [response.json()]
        assert (
            len(client.get(f"/api/custom-apis/connections/{identifier}/actions").json()["actions"])
            == 125
        )
        paused = client.patch(f"/api/custom-apis/connections/{identifier}", json={"enabled": False})
        assert not paused.json()["tools_ready"]
        assert len(app.state.plugin_registry.active_tools()) == 0
        assert client.delete(f"/api/custom-apis/{identifier}").status_code == 200


def test_api_definition_cache_cannot_be_mutated_by_a_caller(store):
    spec = definition()
    store.save(spec, KEY)
    first = store.get(spec.id)
    first.name = "Changed in memory"
    first.actions[0].path = "/changed"
    assert store.get(spec.id) == spec


@pytest.mark.asyncio
async def test_managed_service_reaches_composer_society_and_mission_with_existing_denies(store):
    from types import SimpleNamespace

    from jarvis.agent_chat.tool_catalog import build_catalog, resolve_choices, selection_tools
    from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
    from jarvis.core import runtime_refs
    from jarvis.missions.init import _custom_api_worker_tools
    from jarvis.missions.workers.worker_tool_broker import worker_tool_name_allowed
    from jarvis.society.capabilities import build_catalog as society_catalog
    from jarvis.society.capabilities import select_tools

    spec = definition(125)
    spec.connection.brand_id = "sample"
    store.save(spec, KEY)
    runtime = CustomApiRuntime(store)
    await runtime.refresh()
    previous = runtime_refs.get_supervisor_tool_gateway()
    runtime_refs.set_supervisor_tool_gateway(
        BrainSupervisorToolGateway(SimpleNamespace(_tools=runtime.tools))
    )
    try:
        rows = build_catalog(runtime.tools)
        assert len(rows) == 1 and rows[0].brand == "sample"
        choices = resolve_choices([rows[0].id], rows)
        assert selection_tools(choices, runtime.tools) == runtime.tools
        names = _custom_api_worker_tools("Use " + spec.name)
        assert len(names) == 2 and all(worker_tool_name_allowed(n) for n in names)
        assert select_tools(runtime.tools, grant_mode="all", grants=[], focus=[], denies=[]) == (
            runtime.tools
        )
        denied = select_tools(
            runtime.tools,
            grant_mode="all",
            grants=[],
            focus=[],
            denies=[row.id for row in society_catalog(runtime.tools)],
        )
        assert not denied
        assert KEY not in str(rows) + str(names)
    finally:
        runtime_refs.set_supervisor_tool_gateway(previous)
        await runtime.stop()
