"""home_assistant tool — control the user's smart home over the REST API.

Home Assistant is the one connector where "assistant" is the literal use case:
lights, heating, doors, scenes. It is wired as a NATIVE tool rather than through
its MCP integration on purpose. Home Assistant has explicitly declined dynamic
client registration, and its MCP server is scoped to its own Assist pipeline —
so it exposes conversation, not the entity and service surface a voice assistant
actually needs. The documented REST API does.

Two properties shape everything here:

* **The address is user data.** Home Assistant runs on the user's own network,
  so the base URL is stored per connection (``Tokens.extra['instance_url']``)
  rather than hardcoded. Never assume the ``.local`` hostname — mDNS does not
  resolve inside Docker or over a VPN.
* **The token never expires but carries full rights.** A Long-Lived Access Token
  is valid for ten years with no refresh, which is why this is the most durable
  connection in the catalog; it also inherits every permission of the account
  that created it, which is why the setup text asks for a dedicated non-admin
  user.

Risk tier ``ask``: unlike the read-only REST plugins, calling a service here
physically changes something in the user's home. Turning off the wrong light is
recoverable; unlocking a door is not, so the safety layer gets to weigh in
rather than this tool deciding on its own.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

_NOT_CONNECTED = (
    "Home Assistant is not connected — connect it in the Plugins view."
)
# Entity attributes worth speaking. The raw state object carries dozens of
# fields (icons, ids, timestamps) that would bury the answer.
_SPOKEN_ATTRS = (
    "friendly_name",
    "brightness",
    "current_temperature",
    "temperature",
    "battery_level",
    "unit_of_measurement",
)


def _default_connection() -> tuple[str | None, str | None]:
    """Return ``(base_url, token)`` for the connected Home Assistant."""
    from jarvis.marketplace.token_store import TokenStore

    tokens = TokenStore().load("home_assistant")
    if tokens is None:
        return None, None
    return tokens.extra.get("instance_url"), tokens.access


def _summarize(state: dict[str, Any]) -> dict[str, Any]:
    """Reduce a raw entity state to what is worth reading aloud."""
    attrs = state.get("attributes") or {}
    out: dict[str, Any] = {
        "entity_id": state.get("entity_id"),
        "state": state.get("state"),
    }
    for key in _SPOKEN_ATTRS:
        if key in attrs:
            out[key] = attrs[key]
    return out


class HomeAssistantRestTool:
    name: str = "home_assistant"
    risk_tier: str = "ask"
    description: str = (
        "Control and read the user's Home Assistant smart home: list devices, "
        "read the state of one device, or call a service to act on it. Use for "
        "'turn the living room lights off', 'is the garage door still open', "
        "'set the heating to 21 degrees', 'what is the temperature in the "
        "bedroom'. Actions: list_entities, get_state, call_service. Requires "
        "the Home Assistant plugin to be connected in the Plugins view."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_entities", "get_state", "call_service"],
                "default": "list_entities",
            },
            "domain_filter": {
                "type": "string",
                "description": (
                    "restrict list_entities to one domain, e.g. light, switch, "
                    "climate, cover, sensor, lock"
                ),
            },
            "entity_id": {
                "type": "string",
                "description": "e.g. light.living_room (get_state, call_service)",
            },
            "domain": {
                "type": "string",
                "description": "service domain for call_service, e.g. light",
            },
            "service": {
                "type": "string",
                "description": "service name for call_service, e.g. turn_on",
            },
            "data": {
                "type": "object",
                "description": (
                    "extra service data, e.g. {'brightness_pct': 30} or "
                    "{'temperature': 21}"
                ),
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        connection_provider: Callable[[], tuple[str | None, str | None]] | None = None,
        transport: Any | None = None,
    ) -> None:
        from ._http_pool import HttpClientPool

        self._connection = connection_provider or _default_connection
        self._pool = HttpClientPool(transport=transport)

    # -- internal helpers ---------------------------------------------------

    def _resolve(self) -> tuple[str, dict[str, str]] | None:
        base, token = self._connection()
        if not base or not token:
            return None
        return base, {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Personal-Jarvis/1.0",
        }

    # -- public actions (also directly unit-testable) -----------------------

    async def list_entities(
        self, *, domain_filter: str | None = None, limit: int = 60
    ) -> dict[str, Any]:
        resolved = self._resolve()
        if resolved is None:
            return {"error": _NOT_CONNECTED}
        base, headers = resolved
        client = self._pool.client()
        resp = await client.get(f"{base}/api/states", headers=headers)
        resp.raise_for_status()
        states = resp.json()
        if not isinstance(states, list):
            return {"error": "unexpected response from Home Assistant"}
        if domain_filter:
            prefix = f"{domain_filter.strip().rstrip('.')}."
            states = [s for s in states if str(s.get("entity_id", "")).startswith(prefix)]
        # A real home has hundreds of entities. Sending them all would blow the
        # prompt budget and bury the ones the user meant.
        return {
            "total": len(states),
            "returned": min(len(states), limit),
            "entities": [_summarize(s) for s in states[:limit]],
        }

    async def get_state(self, *, entity_id: str) -> dict[str, Any]:
        resolved = self._resolve()
        if resolved is None:
            return {"error": _NOT_CONNECTED}
        base, headers = resolved
        client = self._pool.client()
        resp = await client.get(f"{base}/api/states/{entity_id}", headers=headers)
        if resp.status_code == 404:
            return {"error": f"no entity called {entity_id!r} in Home Assistant"}
        resp.raise_for_status()
        return _summarize(resp.json())

    async def call_service(
        self,
        *,
        domain: str,
        service: str,
        entity_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = self._resolve()
        if resolved is None:
            return {"error": _NOT_CONNECTED}
        base, headers = resolved
        payload: dict[str, Any] = dict(data or {})
        if entity_id:
            payload["entity_id"] = entity_id
        client = self._pool.client()
        resp = await client.post(
            f"{base}/api/services/{domain}/{service}",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        changed = resp.json()
        # Home Assistant answers with the entities whose state changed. Reading
        # that back is what lets the assistant say what actually happened rather
        # than "done" — the difference between a report and a guess.
        return {
            "called": f"{domain}.{service}",
            "changed": [_summarize(s) for s in changed] if isinstance(changed, list) else [],
        }

    # -- Tool protocol ------------------------------------------------------

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        action = (args.get("action") or "list_entities").strip()
        try:
            if action == "list_entities":
                out = await self.list_entities(domain_filter=args.get("domain_filter"))
            elif action == "get_state":
                entity_id = args.get("entity_id")
                if not entity_id:
                    return ToolResult(success=False, output=None, error="entity_id missing")
                out = await self.get_state(entity_id=entity_id)
            elif action == "call_service":
                domain = args.get("domain")
                service = args.get("service")
                if not domain or not service:
                    return ToolResult(
                        success=False,
                        output=None,
                        error="call_service needs both domain and service",
                    )
                out = await self.call_service(
                    domain=domain,
                    service=service,
                    entity_id=args.get("entity_id"),
                    data=args.get("data") if isinstance(args.get("data"), dict) else None,
                )
            else:
                return ToolResult(success=False, output=None, error=f"unknown action {action!r}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=self._explain(exc))

        if isinstance(out, dict) and out.get("error"):
            return ToolResult(success=False, output=None, error=out["error"])
        return ToolResult(success=True, output=out)

    @staticmethod
    def _explain(exc: Exception) -> str:
        """Turn a transport failure into something a person can act on.

        Home Assistant lives on the home network, so the common failure is not
        a bad token but an unreachable server — Jarvis running on a different
        machine, a VPN, or a container. "ConnectError" tells the user nothing;
        naming the likely cause tells them what to check.
        """
        import httpx

        if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
            return (
                "Could not reach Home Assistant. It runs on your home network, "
                "so check that this machine can reach that address (a VPN, a "
                "container or a remote server will not see it)."
            )
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
            return (
                "Home Assistant rejected the access token. Create a new "
                "long-lived token in your Home Assistant profile and reconnect "
                "the plugin."
            )
        return str(exc)
