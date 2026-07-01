"""vercel tool — inspect Vercel projects and deployments via the REST API.

Wired Node-free, mirroring ``gmail_rest`` (the catalog's other REST-backed
plugin): the marketplace's PAT-paste flow stores the user's Vercel token in the
credential store (key ``vercel``); this tool reads that access token and calls
the Vercel REST API directly. Vercel ships no usable MCP server for our
in-process bridge (its catalog entry used the never-implemented ``rest_wrapper``
transport, which produced zero tools), so a native router tool is what makes a
connected Vercel actually reachable by voice/chat.

Router-tier tool, risk_tier ``monitor`` — read-focused (list projects and
deployments). It does not trigger deployments or mutate state.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

_VERCEL_BASE = "https://api.vercel.com"


def _default_token_provider() -> str | None:
    from jarvis.marketplace.token_store import TokenStore

    tokens = TokenStore().load("vercel")
    return tokens.access if tokens is not None else None


class VercelRestTool:
    name: str = "vercel"
    risk_tier: str = "monitor"
    description: str = (
        "Inspect the user's connected Vercel account: list projects and recent "
        "deployments, or read one deployment's status. Use for 'show my Vercel "
        "projects', 'what are my latest deployments', 'is my deployment ready'. "
        "Actions: list_projects, list_deployments, get_deployment (read-only). "
        "Requires the Vercel plugin to be connected in the Plugins view."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_projects", "list_deployments", "get_deployment"],
                "default": "list_projects",
            },
            "limit": {"type": "integer", "default": 10},
            "project_id": {
                "type": "string",
                "description": "filter deployments by project id/name (list_deployments)",
            },
            "deployment_id": {"type": "string", "description": "deployment id (get_deployment)"},
        },
        "required": ["action"],
    }

    def __init__(
        self,
        access_token_provider: Callable[[], str | None] | None = None,
        transport: Any | None = None,
    ) -> None:
        from ._http_pool import HttpClientPool

        self._token_provider = access_token_provider or _default_token_provider
        self._transport = transport
        # Keep-alive pool: reuse one warm connection across list/get instead of
        # a fresh TLS handshake per request (mirrors gmail_rest).
        self._pool = HttpClientPool(transport=transport)

    # -- internal helpers ---------------------------------------------------

    def _bearer(self) -> dict[str, str] | None:
        token = self._token_provider()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}", "User-Agent": "Personal-Jarvis/1.0"}

    async def _get(self, path: str, params: dict[str, Any], headers: dict[str, str]):
        client = self._pool.client()
        resp = await client.get(f"{_VERCEL_BASE}{path}", params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # -- public actions (also directly unit-testable) -----------------------

    async def list_projects(self, *, limit: int = 10) -> dict[str, Any]:
        headers = self._bearer()
        if headers is None:
            return {"error": "Vercel is not connected — connect it in the Plugins view."}
        return await self._get("/v9/projects", {"limit": limit}, headers)

    async def list_deployments(
        self, *, limit: int = 10, project_id: str | None = None
    ) -> dict[str, Any]:
        headers = self._bearer()
        if headers is None:
            return {"error": "Vercel is not connected — connect it in the Plugins view."}
        params: dict[str, Any] = {"limit": limit}
        if project_id:
            params["projectId"] = project_id
        return await self._get("/v6/deployments", params, headers)

    async def get_deployment(self, *, deployment_id: str) -> dict[str, Any]:
        headers = self._bearer()
        if headers is None:
            return {"error": "Vercel is not connected — connect it in the Plugins view."}
        return await self._get(f"/v13/deployments/{deployment_id}", {}, headers)

    # -- Tool protocol ------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = (args.get("action") or "list_projects").strip()
        try:
            if action == "list_projects":
                out = await self.list_projects(limit=int(args.get("limit", 10)))
            elif action == "list_deployments":
                out = await self.list_deployments(
                    limit=int(args.get("limit", 10)),
                    project_id=args.get("project_id"),
                )
            elif action == "get_deployment":
                did = args.get("deployment_id")
                if not did:
                    return ToolResult(success=False, output=None, error="deployment_id missing")
                out = await self.get_deployment(deployment_id=did)
            else:
                return ToolResult(success=False, output=None, error=f"unknown action {action!r}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))

        if isinstance(out, dict) and out.get("error"):
            return ToolResult(success=False, output=None, error=out["error"])
        return ToolResult(success=True, output=out)
