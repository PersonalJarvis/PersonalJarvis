"""Resource verification rejects unusable grants without saving credentials."""

import asyncio

import httpx
import pytest

from jarvis.marketplace import connection_verification as verification
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.token_store import Tokens


def plugin(name):
    return load_catalog().by_id(name)


class FakeMcp:
    def __init__(self, *, tools=None, fail=False, hang=False):
        self.tools = tools if tools is not None else [{"name": "read_resource"}]
        self.fail = fail
        self.hang = hang
        self.tasks = []
        self.stopped = False

    async def start(self):
        self.tasks.append(asyncio.current_task())
        if self.fail:
            raise ValueError("private-provider-token")
        if self.hang:
            await asyncio.Event().wait()

    async def list_tools(self):
        self.tasks.append(asyncio.current_task())
        return self.tools

    async def stop(self):
        self.tasks.append(asyncio.current_task())
        self.stopped = True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "empty", "failure", "timeout"])
async def test_mcp_verification_owns_lifecycle_and_closes(monkeypatch, mode):
    from jarvis.marketplace import plugin_registry

    client = FakeMcp(
        tools=[] if mode == "empty" else None,
        fail=mode == "failure",
        hang=mode == "timeout",
    )
    monkeypatch.setattr(plugin_registry, "_default_client_factory", lambda *a, **k: client)
    monkeypatch.setattr(verification, "VERIFY_TIMEOUT_SECONDS", 0.02)
    if mode == "success":
        await verification.verify_connection(plugin("notion"), Tokens(access="test"))
    else:
        with pytest.raises(verification.ConnectionVerificationError) as caught:
            await verification.verify_connection(plugin("notion"), Tokens(access="test"))
        assert "private-provider-token" not in str(caught.value)
    assert client.stopped
    assert all(task is asyncio.current_task() for task in client.tasks)


@pytest.mark.asyncio
async def test_cancellation_closes_and_propagates(monkeypatch):
    from jarvis.marketplace import plugin_registry

    client = FakeMcp(hang=True)
    monkeypatch.setattr(plugin_registry, "_default_client_factory", lambda *a, **k: client)
    task = asyncio.create_task(verification.verify_connection(plugin("notion"), Tokens(access="t")))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.stopped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload", [(200, {"files": []}), (403, {"error": "secret"}), (200, {})]
)
async def test_native_uses_authenticated_resource_read(monkeypatch, status, payload):
    original = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(
        verification.httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle)),
    )
    if status == 200 and "files" in payload:
        await verification.verify_connection(plugin("google_drive"), Tokens(access="t"))
    else:
        with pytest.raises(verification.ConnectionVerificationError):
            await verification.verify_connection(plugin("google_drive"), Tokens(access="t"))
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/drive/v3/files"
    assert requests[0].headers["Authorization"] == "Bearer t"


@pytest.mark.asyncio
async def test_unknown_verifier_fails_closed():
    spec = plugin("gmail").model_copy(update={"id": "unknown", "native_tool": "unknown"})
    with pytest.raises(verification.ConnectionVerificationError, match="no supported"):
        await verification.verify_connection(spec, Tokens(access="t"))


@pytest.mark.asyncio
async def test_unknown_declared_hook_cannot_bypass_verification():
    spec = plugin("gmail").model_copy(update={"verification_hook": "unimplemented"})
    with pytest.raises(verification.ConnectionVerificationError, match="unsupported"):
        await verification.verify_connection(spec, Tokens(access="t"))


@pytest.mark.asyncio
async def test_rest_probe_really_calls_provider_and_rejects_denial(monkeypatch):
    from jarvis.plugins.tool import connected_server

    original = connected_server.ConnectedRestClient
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(403, json={"error": "private-token"})

    monkeypatch.setattr(
        connected_server,
        "ConnectedRestClient",
        lambda *a, **k: original(*a, **k, transport=httpx.MockTransport(handle)),
    )
    with pytest.raises(verification.ConnectionVerificationError) as caught:
        await verification.verify_connection(plugin("outlook"), Tokens(access="t"))
    assert "private-token" not in str(caught.value)
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.path == "/v1.0/me/messages"


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", ["pat", "oauth"])
async def test_figma_token_modes(auth_type):
    from jarvis.plugins.tool.connected_server import ConnectedRestClient

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "test-user"})

    client = ConnectedRestClient(
        "figma", "token", auth_type=auth_type, transport=httpx.MockTransport(handle)
    )
    try:
        await client.call("read_profile", {})
    finally:
        await client.close()
    expected = "authorization" if auth_type == "oauth" else "x-figma-token"
    unexpected = "x-figma-token" if auth_type == "oauth" else "authorization"
    assert expected in requests[0].headers
    assert unexpected not in requests[0].headers


@pytest.mark.parametrize("oauth", [True, False])
def test_both_mcp_bridges_carry_figma_auth_mode(oauth):
    from jarvis.marketplace.catalog import PluginCatalog
    from jarvis.marketplace.mcp_bridge import assemble_claude_mcp_servers
    from jarvis.marketplace.plugin_mcp import plugin_to_mcp_server_spec

    tokens = Tokens(access="t", extra={"client_id": "app"} if oauth else {})
    spec = plugin("figma")

    class Store:
        def load(self, plugin_id):
            return tokens

    expected = "oauth" if oauth else "pat"
    resolved = plugin_to_mcp_server_spec(spec, tokens)
    assert resolved[1]["JARVIS_CONNECTOR_AUTH_TYPE"] == expected
    catalog = PluginCatalog(version=1, schema_version="1", plugins=[spec])
    entry = assemble_claude_mcp_servers(catalog, Store())["figma"]
    assert entry["env"]["JARVIS_CONNECTOR_AUTH_TYPE"] == expected
