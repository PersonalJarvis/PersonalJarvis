"""``find-app-action`` / ``run-app-action`` — any action of the Jarvis app.

The Command Registry gives the brain ~50 curated, hand-described commands.
Everything else the app can do (765 REST operations) used to be reachable
only by clicking. These two router tools open the whole surface without
declaring hundreds of schemas every turn: the brain searches the catalog
(``jarvis.app_actions.catalog``) and runs one operation in-process, exactly
like the UI would. Each call's risk tier comes from the person's policy on
the Jarvis-actions page (allow / ask / block, else the action's default), so
ToolExecutor still decides and confirms (AP-3). Secrets, sign-ins and the
policy itself are never in the catalog.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)

_MAX_RESULTS = 6
_MAX_RESPONSE_CHARS = 12_000


def _catalog() -> dict[str, Any]:
    from jarvis.app_actions.catalog import live_catalog

    return live_catalog()


class FindAppActionTool:
    """Search every action of the Jarvis app by keywords."""

    name: str = "find-app-action"
    risk_tier: str = "safe"
    description: str = (
        "Search ALL actions of the Jarvis desktop app (settings, IDE panes and "
        "workspaces, skills, local models, computers, marketplace, MCP servers, "
        "dictation, chats, agents, automations …) when no other tool does what the "
        "user asked. Pass English keywords; returns action ids, what each does, its "
        "parameters and whether it runs freely, asks first or is blocked. Then call "
        "run-app-action."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "English keywords, e.g. 'pane rename'."},
            "area": {"type": "string", "description": "Optional area filter, e.g. 'skills'."},
        },
        "required": ["query"],
    }

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        from jarvis.app_actions.policy import effective_tier, load_policy
        from jarvis.core.protocols import SupervisorToolDescriptor
        from jarvis.live.discovery import discover

        catalog = _catalog()
        if not catalog:
            return ToolResult(
                success=False, output=None, error="The app's actions are not available here."
            )
        area = str(args.get("area") or "").strip().lower()
        entries = [e for e in catalog.values() if not area or e.area.lower() == area]
        policy = load_policy()
        descriptors = [
            SupervisorToolDescriptor(
                name=e.id,
                description=f"{e.title}. {e.description} {e.area} {e.path}",
                input_schema=e.parameters,
                risk_tier=effective_tier(e, policy),  # type: ignore[arg-type]
            )
            for e in entries
        ]
        found = discover(descriptors, str(args.get("query") or ""))
        actions = []
        for item in found["tools"][:_MAX_RESULTS]:
            entry = catalog[item["name"]]
            tier = effective_tier(entry, policy)
            actions.append(
                {
                    "action_id": entry.id,
                    "title": entry.title,
                    "does": entry.description,
                    "area": entry.area,
                    "runs": {"safe": "freely", "monitor": "freely", "ask": "asks first"}.get(
                        tier, "blocked by the user"
                    ),
                    "parameters": entry.parameters,
                }
            )
        return ToolResult(
            success=True,
            output={"actions": actions, "total_matches": found["total"]},
        )


class RunAppActionTool:
    """Run one app action by id, under the person's per-action policy."""

    name: str = "run-app-action"
    risk_tier: str = "monitor"
    is_action_tool: bool = True
    description: str = (
        "Run one Jarvis app action found with find-app-action. Pass its action_id and "
        "params: path and query parameters by name, the request body under 'body'. "
        "Report the result the tool returns, never your assumption; a blocked action "
        "stays blocked — tell the user it is off in Settings > Jarvis actions."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action_id": {"type": "string", "description": "Id from find-app-action."},
            "params": {"type": "object", "description": "Parameters as listed by the search."},
        },
        "required": ["action_id"],
    }

    def __init__(self, runtime: Any = None) -> None:
        if runtime is None:
            from jarvis.plugins.tool.app_command import _Runtime

            runtime = _Runtime()
        self._runtime = runtime

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        from jarvis.app_actions import history
        from jarvis.app_actions.policy import effective_tier

        entry = _catalog().get(str((args or {}).get("action_id") or ""))
        if entry is None:
            return "safe"  # execute refuses an unknown id without side effects
        tier = effective_tier(entry)
        if tier == "block":
            history.record(entry.id, "blocked", "Blocked in Jarvis actions", via=self.name)
        return tier

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        from jarvis.app_actions import history
        from jarvis.app_actions.policy import effective_tier

        action_id = str((args or {}).get("action_id") or "")
        entry = _catalog().get(action_id)
        if entry is None:
            return ToolResult(
                success=False,
                output=None,
                error=f"Unknown action {action_id!r}. Search with find-app-action first.",
            )
        if effective_tier(entry) == "block":
            # The executor already refuses a blocked tier; this keeps a direct
            # call honest too.
            history.record(entry.id, "blocked", "Blocked in Jarvis actions", via=self.name)
            return ToolResult(
                success=False, output=None, error="The user blocked this action for Jarvis."
            )
        params = dict((args or {}).get("params") or {})
        path = entry.path
        for name in entry.path_params:
            if name not in params:
                return ToolResult(
                    success=False, output=None, error=f"Missing path parameter {name!r}."
                )
            path = path.replace("{" + name + "}", quote(str(params.pop(name)), safe=""))
        query = {k: params.pop(k) for k in list(params) if k in entry.query_params}
        body = params.pop("body", None)
        if body is None and entry.has_body and params:
            body = params  # a model that flattened the body still gets it through
        status, data = await self._request(entry.method, path, query, body)
        if status is None:
            history.record(entry.id, "failed", str(data), via=self.name)
            return ToolResult(success=False, output=None, error=str(data))
        if status >= 400:
            detail = data.get("detail", data) if isinstance(data, dict) else data
            history.record(entry.id, "failed", f"HTTP {status}: {detail}", via=self.name)
            log.info("app action %s refused: HTTP %s %s", entry.id, status, str(detail)[:500])
            return ToolResult(
                success=False,
                output={"action_id": entry.id, "status": status},
                error=f"{entry.title} failed: HTTP {status}: {detail}",
            )
        history.record(entry.id, "ran", entry.title, via=self.name)
        return ToolResult(success=True, output={"action_id": entry.id, "response": _trim(data)})

    async def _request(
        self, method: str, path: str, query: dict[str, Any], body: Any
    ) -> tuple[int | None, Any]:
        import httpx

        from jarvis.agent_chat.jarvis_harness import HEADER_NAME
        from jarvis.society.inherit import caller_session_id
        from jarvis.tasks.context import CLIENT_TIMEZONE_HEADER, turn_timezone

        transport = self._runtime.resolve_transport()
        if transport is None:
            return None, "The app server is not available in this runtime."
        headers: dict[str, str] = {}
        key = self._runtime.control_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        zone = turn_timezone()
        if zone:
            headers[CLIENT_TIMEZONE_HEADER] = zone
        session_id = caller_session_id()
        if session_id:
            headers[HEADER_NAME] = session_id
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1", headers=headers, timeout=60.0
            ) as client:
                resp = await client.request(
                    method,
                    path,
                    params=query or None,
                    json=body if method != "GET" and body is not None else None,
                )
        except httpx.HTTPError as exc:
            # The transport error is returned to the model as the tool result.
            return None, f"transport error: {exc}"
        try:
            data = resp.json() if resp.content else None
        except ValueError:
            # A non-JSON body is still a valid answer; hand back the raw text.
            data = resp.text
        return resp.status_code, data


def _trim(data: Any) -> Any:
    from jarvis.plugins.tool.app_command import _without_snapshots

    data = _without_snapshots(data)
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= _MAX_RESPONSE_CHARS:
        return data
    return {"truncated": True, "preview": text[:_MAX_RESPONSE_CHARS]}
