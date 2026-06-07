"""Unit tests for the Vercel REST tool (mirrors the Gmail tool tests)."""
from __future__ import annotations

import httpx

from jarvis.plugins.tool.vercel_rest import VercelRestTool


def _tool(token, handler):
    return VercelRestTool(
        access_token_provider=lambda: token,
        transport=httpx.MockTransport(handler),
    )


async def test_not_connected_without_token():
    tool = VercelRestTool(access_token_provider=lambda: None)
    out = await tool.list_projects()
    assert "not connected" in out["error"].lower()


async def test_list_projects_calls_api_with_bearer():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"projects": [{"name": "myapp"}]})

    out = await _tool("tok123", handler).list_projects(limit=5)
    assert out["projects"][0]["name"] == "myapp"
    assert "api.vercel.com/v9/projects" in seen["url"]
    assert seen["auth"] == "Bearer tok123"


async def test_list_deployments_passes_project_filter():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"deployments": []})

    await _tool("t", handler).list_deployments(limit=3, project_id="prj_1")
    assert "v6/deployments" in seen["url"] and "projectId=prj_1" in seen["url"]


async def test_execute_unknown_action_fails():
    res = await VercelRestTool(access_token_provider=lambda: "x").execute(
        {"action": "delete_everything"}, ctx=None  # type: ignore[arg-type]
    )
    assert not res.success


async def test_execute_get_deployment_requires_id():
    res = await VercelRestTool(access_token_provider=lambda: "x").execute(
        {"action": "get_deployment"}, ctx=None  # type: ignore[arg-type]
    )
    assert not res.success and "deployment_id" in (res.error or "")
