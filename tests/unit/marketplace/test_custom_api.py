"""Native API definitions, safe request mapping and shared tool lifecycle."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from jarvis.core.protocols import ExecutionContext
from jarvis.marketplace.catalog import PluginCatalog
from jarvis.marketplace.custom_api import ApiDefinition, CustomApiStore
from jarvis.marketplace.custom_api_runtime import CustomApiRuntime
from jarvis.marketplace.plugin_registry import PluginToolRegistry
from jarvis.marketplace.token_store import InMemoryBackend, TokenStore


@pytest.fixture
def store(tmp_path):
    return CustomApiStore(tmp_path / "apis", TokenStore(InMemoryBackend()))


def definition(**changes):
    values = dict(
        name="Audio service",
        base_url="https://93.184.216.34/v1",
        auth={"mode": "header", "header_name": "xi-api-key"},
        actions=[
            {
                "id": "speak",
                "description": "Create audio",
                "method": "POST",
                "path": "/speech/{voice}",
                "response": "auto",
                "parameters": [{"name": "voice", "location": "path"}, {"name": "format"}],
                "body_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            }
        ],
    )
    values.update(changes)
    return ApiDefinition(**values)


def context():
    return ExecutionContext(
        trace_id=uuid4(), user_utterance="Create audio", config={}, memory_read=None
    )


def args():
    return {"path": {"voice": "voice 1"}, "query": {"format": "mp3"}, "body": {"text": "Hello"}}


def test_definition_roundtrip_separates_credentials_and_survives_restart(store):
    spec = definition()
    store.save(spec, "test-secret-key")
    raw = (store.directory / f"{spec.id}.json").read_text()
    assert "test-secret-key" not in raw
    restarted = CustomApiStore(store.directory, store.tokens)
    assert restarted.get(spec.id) == spec
    assert restarted.credential(spec.id) == "test-secret-key"
    assert restarted.list() == [spec]


def test_key_is_required_and_cannot_silently_move_to_another_host(store):
    spec = definition()
    with pytest.raises(ValueError, match="API key"):
        store.save(spec)
    store.save(spec, "test-secret-key")
    changed = spec.model_copy(update={"base_url": "https://other.example"})
    with pytest.raises(ValueError, match="Re-enter"):
        store.save(changed)
    assert store.get(spec.id) == spec
    with pytest.raises(ValueError, match="protected"):
        store.save(spec.model_copy(update={"description": "test-secret-key"}))


@pytest.mark.parametrize(
    "path", ["//evil.example", "/../private", "/%2e%2e/private", "/a?key=x", "/a\\b"]
)
def test_unsafe_action_paths_are_rejected(path):
    with pytest.raises(ValueError):
        definition(actions=[{"id": "read", "description": "Read", "path": path}])


@pytest.mark.parametrize(
    "url", ["http://example.com", "https://user:pass@example.com", "https://example.com?token=x"]
)
def test_unsafe_base_urls_are_rejected(url):
    with pytest.raises(ValueError):
        definition(base_url=url)


def test_remote_schema_references_are_rejected():
    with pytest.raises(ValueError, match="references"):
        definition(
            actions=[
                {
                    "id": "read",
                    "description": "Read",
                    "method": "POST",
                    "path": "/x",
                    "body_schema": {"$ref": "https://example.com/schema"},
                }
            ]
        )


@pytest.mark.asyncio
async def test_native_request_mapping_audio_artifact_and_no_boot_probe(store, tmp_path):
    spec = definition()
    store.save(spec, "test-secret-key")
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["xi-api-key"] == "test-secret-key"
        assert request.url.raw_path == b"/v1/speech/voice%201?format=mp3"
        assert json.loads(request.content) == {"text": "Hello"}
        return httpx.Response(200, content=b"audio-bytes", headers={"Content-Type": "audio/mpeg"})

    runtime = CustomApiRuntime(
        store, transport=httpx.MockTransport(handle), output_dir=tmp_path / "outputs"
    )
    await runtime.refresh()
    assert calls == []
    tool = next(iter(runtime.tools.values()))
    assert not getattr(tool, "is_mcp_tool", False)
    result = await tool.execute(args(), context())
    assert result.success
    assert len(calls) == 1
    from pathlib import Path

    assert await asyncio.to_thread(Path(result.artifacts[0]).read_bytes) == b"audio-bytes"
    assert result.artifacts[0].endswith(".mp3")
    assert "/api/outputs/" in result.output["download_url"]
    assert "/tasks/api/artifacts/files/speak.mp3/download" in result.output["download_url"]
    from jarvis.missions.standalone_run import read_marker

    assert await asyncio.to_thread(read_marker, Path(result.artifacts[0]).parents[4])
    await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("response_mode", ["auto", "file"])
async def test_json_escaped_credentials_are_redacted_after_decoding(store, tmp_path, response_mode):
    spec = definition()
    spec.actions[0].response = response_mode
    store.save(spec, "test-secret-key")
    response = b'{"echo":"test-secret-\\u006bey"}'
    runtime = CustomApiRuntime(
        store,
        output_dir=tmp_path / "outputs",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=response, headers={"Content-Type": "application/json"}
            ),
        ),
    )
    await runtime.refresh()
    result = await next(iter(runtime.tools.values())).execute(args(), context())
    assert result.success
    if response_mode == "file":
        from pathlib import Path

        raw = await asyncio.to_thread(Path(result.artifacts[0]).read_text)
        assert json.loads(raw) == {"echo": "[redacted]"}
    else:
        assert result.output == {"echo": "[redacted]"}
    await runtime.stop()


def test_key_scope_fails_closed_after_an_interrupted_definition_write(store, monkeypatch):
    spec = definition()
    store.save(spec, "original-test-key")
    changed = spec.model_copy(update={"base_url": "https://other.example"})

    def fail_replace(*args):
        raise OSError("simulated interrupted disk write")

    monkeypatch.setattr("jarvis.marketplace.custom_api.os.replace", fail_replace)
    with pytest.raises(OSError):
        store.save(changed, "different-service-key")
    assert store.get(spec.id) == spec
    assert store.credential(spec.id) is None
    with pytest.raises(ValueError, match="key missing"):
        store.resolve(spec)


@pytest.mark.asyncio
async def test_generated_file_is_downloadable_from_the_outputs_api(store, tmp_path, monkeypatch):
    from fastapi import FastAPI

    from jarvis.ui.web.outputs_routes import router

    root = tmp_path / "outputs"
    monkeypatch.setenv("JARVIS_ISOLATION_ROOT", str(root))
    store.save(definition(), "test-secret-key")
    runtime = CustomApiRuntime(
        store,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"audio-result", headers={"Content-Type": "audio/mpeg"}
            ),
        ),
    )
    await runtime.refresh()
    result = await next(iter(runtime.tools.values())).execute(args(), context())
    assert result.success
    app = FastAPI()
    app.state.outputs_root = root
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(result.output["download_url"])
        assert response.status_code == 200
        assert response.content == b"audio-result"
        listing = await client.get("/api/outputs")
        assert listing.status_code == 200
        assert "Audio service" in listing.text
    await runtime.stop()
    await runtime.refresh()
    assert runtime.tools == {}


def test_definitions_honor_the_headless_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    assert CustomApiStore().directory == tmp_path / "custom-apis"


@pytest.mark.asyncio
async def test_explicitly_blocked_action_cannot_run_even_if_an_outer_gate_allows_it(store):
    spec = definition()
    spec.actions[0].risk_tier = "block"
    store.save(spec, "test-secret-key")
    runtime = CustomApiRuntime(
        store,
        transport=httpx.MockTransport(
            lambda request: pytest.fail("Blocked action reached the provider"),
        ),
    )
    await runtime.refresh()
    result = await next(iter(runtime.tools.values())).execute(args(), context())
    assert not result.success
    assert "blocked" in result.error
    await runtime.stop()


@pytest.mark.asyncio
async def test_current_credentials_and_disconnect_revoke_retained_tools(store):
    spec = definition()
    store.save(spec, "test-secret-key")
    received = []

    def handle(request):
        received.append(request.headers["xi-api-key"])
        return httpx.Response(200, json={"ok": True})

    runtime = CustomApiRuntime(store, transport=httpx.MockTransport(handle))
    await runtime.refresh()
    tool = next(iter(runtime.tools.values()))
    store.save(spec, "rotated-secret-key")
    assert (await tool.execute(args(), context())).success
    assert received == ["rotated-secret-key"]
    store.delete(spec.id)
    assert not (await tool.execute(args(), context())).success
    assert len(received) == 1
    await runtime.refresh()
    assert not runtime.tools
    await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 429, 500])
async def test_http_failures_are_not_retried_and_never_echo_provider_body(store, status):
    store.save(definition(), "test-secret-key")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, text="test-secret-key", headers={"Location": "https://evil.example"}
        )

    runtime = CustomApiRuntime(store, transport=httpx.MockTransport(handle))
    await runtime.refresh()
    result = await next(iter(runtime.tools.values())).execute(args(), context())
    assert not result.success
    assert result.error == f"API returned HTTP {status}"
    assert len(calls) == 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_private_hosts_and_invalid_arguments_never_reach_transport(store):
    store.save(definition(base_url="https://127.0.0.1"), "test-secret-key")

    def forbidden(request):
        pytest.fail("The request must be blocked before network access")

    runtime = CustomApiRuntime(store, transport=httpx.MockTransport(forbidden))
    await runtime.refresh()
    tool = next(iter(runtime.tools.values()))
    assert not (await tool.execute(args(), context())).success
    assert not (await tool.execute({"body": {}}, context())).success
    await runtime.stop()


@pytest.mark.asyncio
async def test_shared_registry_loader_and_society_grants(store):
    from jarvis.marketplace.plugin_loader import PluginToolLoader
    from jarvis.marketplace.plugin_shared import set_active_plugin_registry
    from jarvis.society.capabilities import build_catalog, select_tools

    store.save(definition(), "test-secret-key")
    runtime = CustomApiRuntime(store)
    registry = PluginToolRegistry(
        catalog=PluginCatalog(version=1, schema_version="1", plugins=[]),
        custom_api_runtime=runtime,
    )
    await registry.bootstrap()
    set_active_plugin_registry(registry)
    try:
        tools = {t.name: t for t in PluginToolLoader().expand()}
        assert len(tools) == 1
        row = build_catalog(tools)[0]
        assert str(row.kind) == "plugin"
        assert row.label.startswith("Audio service:")
        assert select_tools(tools, grant_mode="all", grants=[], focus=[], denies=[]) == tools
        assert select_tools(tools, grant_mode="all", grants=[], focus=[], denies=[row.id]) == {}
    finally:
        set_active_plugin_registry(None)
        await registry.stop()


@pytest.mark.asyncio
async def test_composer_groups_api_actions_and_missions_receive_relevant_tools(store):
    from types import SimpleNamespace

    from jarvis.agent_chat.tool_catalog import build_catalog, resolve_choices, selection_tools
    from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
    from jarvis.core import runtime_refs
    from jarvis.missions.init import _custom_api_worker_tools
    from jarvis.missions.workers.worker_tool_broker import worker_tool_name_allowed

    store.save(definition(name="ElevenLabs"), "test-secret-key")
    runtime = CustomApiRuntime(store)
    await runtime.refresh()
    rows = build_catalog(runtime.tools)
    assert len(rows) == 1
    assert rows[0].label == "ElevenLabs"
    assert rows[0].category == "plugins"
    choices = resolve_choices([rows[0].id], rows)
    assert selection_tools(choices, runtime.tools) == runtime.tools
    previous = runtime_refs.get_supervisor_tool_gateway()
    runtime_refs.set_supervisor_tool_gateway(
        BrainSupervisorToolGateway(
            SimpleNamespace(_tools=runtime.tools),
        )
    )
    try:
        names = _custom_api_worker_tools("Create a narration with ElevenLabs")
        assert set(names) == set(runtime.tools)
        assert all(worker_tool_name_allowed(name) for name in names)
        assert _custom_api_worker_tools("Calculate two plus two") == ()
        assert "test-secret-key" not in str(names)
    finally:
        runtime_refs.set_supervisor_tool_gateway(previous)
        await runtime.stop()
