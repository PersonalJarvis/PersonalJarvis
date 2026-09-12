"""Provider-family contracts without accessing a user's accounts or credentials."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from jarvis.marketplace.bundled_rest_client import BundledRestMcpClient
from jarvis.marketplace.catalog_data import _PACKAGE_SEED_PATH, load_catalog
from jarvis.marketplace.token_store import Tokens
from jarvis.plugins.tool.connected_operations import OPERATIONS
from jarvis.plugins.tool.connected_server import ConnectedRestClient

CASES = [
    (
        "outlook",
        "read_message",
        {"message_id": "a/b"},
        "GET",
        "graph.microsoft.com",
        "/v1.0/me/messages/a%2Fb",
    ),
    (
        "onedrive",
        "search_files",
        {"query": "O'Brien"},
        "GET",
        "graph.microsoft.com",
        "/v1.0/me/drive/root/search(q='O%27%27Brien')",
    ),
    (
        "teams",
        "send_message",
        {"chat_id": "chat", "body": {"body": {"content": "Hello"}}},
        "POST",
        "graph.microsoft.com",
        "/v1.0/chats/chat/messages",
    ),
    (
        "sharepoint",
        "list_lists",
        {"site_id": "site"},
        "GET",
        "graph.microsoft.com",
        "/v1.0/sites/site/lists",
    ),
    (
        "onenote",
        "create_page",
        {"section_id": "s", "body": "<html><title>Note</title><body>Hello</body></html>"},
        "POST",
        "graph.microsoft.com",
        "/v1.0/me/onenote/sections/s/pages",
    ),
    (
        "microsoft_todo",
        "update_task",
        {"list_id": "l", "task_id": "t", "body": {"status": "completed"}},
        "PATCH",
        "graph.microsoft.com",
        "/v1.0/me/todo/lists/l/tasks/t",
    ),
    (
        "azure",
        "query_costs",
        {"subscription_id": "sub", "body": {"type": "ActualCost"}},
        "POST",
        "management.azure.com",
        "/subscriptions/sub/providers/Microsoft.CostManagement/query",
    ),
    (
        "google_cloud",
        "list_buckets",
        {"query_params": {"project": "demo"}},
        "GET",
        "storage.googleapis.com",
        "/storage/v1/b",
    ),
    (
        "gitlab",
        "list_issues",
        {"project_id": "team/repo"},
        "GET",
        "gitlab.com",
        "/api/v4/projects/team%2Frepo/issues",
    ),
    ("x", "create_post", {"body": {"text": "Hello"}}, "POST", "api.x.com", "/2/tweets"),
    ("linkedin", "read_profile", {}, "GET", "api.linkedin.com", "/v2/userinfo"),
    ("meta", "list_pages", {}, "GET", "graph.facebook.com", "/v23.0/me/accounts"),
    ("youtube_studio", "read_channel", {}, "GET", "www.googleapis.com", "/youtube/v3/channels"),
    ("hubspot", "list_contacts", {}, "GET", "api.hubapi.com", "/crm/v3/objects/contacts"),
    (
        "figma",
        "list_comments",
        {"file_key": "key"},
        "GET",
        "api.figma.com",
        "/v1/files/key/comments",
    ),
    (
        "zoom",
        "create_meeting",
        {"body": {"topic": "Demo", "type": 1}},
        "POST",
        "api.zoom.us",
        "/v2/users/me/meetings",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("pid,name,args,method,host,path", CASES)
async def test_provider_requests(pid, name, args, method, host, path):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == method
        assert request.url.host == host
        assert request.url.raw_path.decode().split("?")[0] == path
        if pid == "figma":
            assert request.headers["X-Figma-Token"] == "fixture-token"
            assert "Authorization" not in request.headers
        else:
            assert request.headers["Authorization"] == "Bearer fixture-token"
        if "body" in args:
            if pid == "onenote":
                assert request.content.decode() == args["body"]
                assert request.headers["content-type"].startswith("text/html")
            else:
                assert json.loads(request.content) == args["body"]
        return httpx.Response(200, json={"id": "result"})

    client = ConnectedRestClient(pid, "fixture-token", transport=httpx.MockTransport(respond))
    try:
        assert await client.call(name, args) == {"id": "result"}
        assert len(requests) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
async def test_provider_errors_do_not_leak_secrets_or_retry(status):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            status,
            text="private-token-and-message",
            headers={"Location": "https://untrusted.example/"},
        )

    client = ConnectedRestClient("outlook", "fixture", transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(RuntimeError) as error:
            await client.call("send_mail", {"body": {"message": {}}})
        assert "private-token" not in str(error.value)
        assert len(requests) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"message_id": ".."},
        {"message_id": "ok", "url": "https://evil.example"},
        {"message_id": "ok", "query_params": {"access_token": "evil"}},
        {},
    ],
)
async def test_unsupported_inputs_never_reach_network(args):
    def fail(request):
        pytest.fail("Invalid input reached the network")

    client = ConnectedRestClient("outlook", "fixture", transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(ValueError):
            await client.call("read_message", args)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rotation_and_disconnect_apply_to_retained_tools():
    token = Tokens(access="old")
    seen = []

    def respond(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"value": []})

    client = BundledRestMcpClient(
        SimpleNamespace(name="outlook"), transport=httpx.MockTransport(respond)
    )
    client.set_token_provider(lambda: token)
    try:
        await client.start()
        await client.call_tool("list_messages", {})
        token = Tokens(access="new")
        await client.call_tool("list_messages", {})
        token = None
        with pytest.raises(RuntimeError, match="disconnected"):
            await client.call_tool("list_messages", {})
        assert seen == ["Bearer old", "Bearer new"]
    finally:
        await client.stop()


def test_all_families_are_exercised():
    assert set(OPERATIONS) == {case[0] for case in CASES}


def test_new_packages_and_seed_match():
    catalog = load_catalog(_PACKAGE_SEED_PATH)
    root = _PACKAGE_SEED_PATH.parent / "plugins"
    for path in root.glob("*/plugin.json"):
        package = json.loads(path.read_text())
        pid = package["name"].replace("-", "_")
        spec = catalog.by_id(pid)
        assert spec is not None, pid
        extension = package["extensions"]["io.github.personaljarvis"]
        assert type(spec.auth).model_validate(extension["auth"]) == spec.auth
        assert extension["mcp_server"] == spec.mcp_server
        mcp = json.loads(path.with_name("mcp.json").read_text())
        assert "access_token" not in json.dumps(mcp)
        assert package["name"] in mcp["mcpServers"]
        assert (
            Path(__file__).parents[2] / "jarvis/skills/builtin" / f"plugin-{pid}" / "SKILL.md"
        ).exists()


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_amd_absence_degrades_without_importing_native_libraries(monkeypatch, platform):
    import sys

    from jarvis.marketplace import amd_mcp

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(amd_mcp.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        amd_mcp.read_amd_status()


def test_amd_reads_only_fixed_json_commands(monkeypatch):
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
    from jarvis.marketplace import amd_mcp

    calls = []
    monkeypatch.setattr(amd_mcp.sys, "platform", "linux")
    monkeypatch.setattr(amd_mcp.shutil, "which", lambda _: "/opt/rocm/bin/amd-smi")

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["creationflags"] == NO_WINDOW_CREATIONFLAGS
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["timeout"] == 10
        return SimpleNamespace(returncode=0, stdout='{"gpu_data":[{"temperature":45}]}')

    monkeypatch.setattr(amd_mcp.subprocess, "run", run)
    assert amd_mcp.read_amd_status()["metric"]["gpu_data"][0]["temperature"] == 45
    assert [args[1:] for args in calls] == [["static", "--json"], ["metric", "--json"]]
