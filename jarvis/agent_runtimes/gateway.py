"""Jarvis' model gateway: Hermes / OpenClaw agents on a ChatGPT subscription.

Handing the person's subscription login to Hermes or OpenClaw would put a
second program in charge of refreshing it, and OAuth refresh tokens are
single-use: whichever program refreshed first would break the other (and the
person's own Codex login with it). Instead the runtime talks to Jarvis. This
module answers an OpenAI Responses request on the app's own loopback server
(``/api/runtime-gateway/v1``) with Jarvis' subscription client
(``jarvis.live.subscription_reasoning``) on the agent's Codex account, whose
login refresh is coordinated in one place (``jarvis.live.subscription_auth``).
The runtime gets a per-agent token Jarvis minted, never the login, and both
runtimes only need the long-stable OpenAI Responses shape.

Only function tools pass through: Jarvis' subscription client accepts nothing
else, and the runtime executes its own tools. Requests are rebuilt field by
field, so a runtime option the subscription endpoint does not accept never
reaches it.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Final

log = logging.getLogger(__name__)

#: Every gateway route lives under this path (``surface_security`` accepts a
#: gateway token there and nowhere else).
PATH_PREFIX: Final[str] = "/api/runtime-gateway"
BASE_PATH: Final[str] = f"{PATH_PREFIX}/v1"

#: The Jarvis provider whose agents run through this gateway.
SUBSCRIPTION_PROVIDER: Final[str] = "openai-codex"


class GatewayError(Exception):
    """A request the gateway cannot answer; ``status`` is the HTTP code."""

    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class Grant:
    """Who a gateway token speaks for."""

    agent_id: str
    #: The Codex account (``jarvis.agent_accounts``); "" = the active one.
    account_id: str


_LOCK = threading.Lock()
_GRANTS: dict[str, Grant] = {}
_TOKENS: dict[Grant, str] = {}
_CLIENTS: dict[str, Any] = {}


def grant_token(agent_id: str, account_id: str = "") -> str:
    """The token one agent's runtime presents (stable for the app's lifetime,
    so a runtime that keeps a process between turns is not restarted)."""
    grant = Grant(agent_id, account_id)
    with _LOCK:
        token = _TOKENS.get(grant)
        if token is None:
            token = f"jrg_{secrets.token_urlsafe(32)}"
            _TOKENS[grant] = token
            _GRANTS[token] = grant
        return token


def verify(token: str) -> Grant | None:
    """The grant behind ``token``; ``None`` for anything Jarvis did not mint."""
    if not token.startswith("jrg_"):
        return None
    with _LOCK:
        return _GRANTS.get(token)


def base_url() -> str | None:
    """The gateway's URL on this app's server, or ``None`` before it bound."""
    from jarvis.core import runtime_refs

    base = runtime_refs.get_api_base_url()
    return f"{base.rstrip('/')}{BASE_PATH}" if base else None


def subscription_ready(account_id: str = "") -> bool:
    """Whether the Codex login can answer (local metadata only, no network)."""
    from jarvis.live.subscription_auth import SubscriptionAuth

    return bool(SubscriptionAuth(account_id).status_snapshot()["connected"])


def _client(account_id: str) -> Any:
    """One subscription client per Codex account, kept for the app's lifetime."""
    with _LOCK:
        found = _CLIENTS.get(account_id)
        if found is None:
            from jarvis.live.subscription_auth import SubscriptionAuth
            from jarvis.live.subscription_reasoning import SubscriptionReasoning

            auth = SubscriptionAuth(account_id)
            found = SubscriptionReasoning(credentials=auth.credentials)
            _CLIENTS[account_id] = found
        return found


# ---------------------------------------------------------------- requests


def _function_tool(tool: Any) -> dict[str, Any] | None:
    """A Responses function tool, from either the Responses or the Chat shape."""
    if not isinstance(tool, dict) or tool.get("type") != "function":
        return None
    spec = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        return None
    out: dict[str, Any] = {"type": "function", "name": name}
    if isinstance(spec.get("description"), str):
        out["description"] = spec["description"]
    out["parameters"] = (
        spec["parameters"] if isinstance(spec.get("parameters"), dict) else {"type": "object"}
    )
    if isinstance(spec.get("strict"), bool):
        out["strict"] = spec["strict"]
    return out


def request_args(body: Any) -> dict[str, Any]:
    """The subscription client's arguments for one Responses request body."""
    if not isinstance(body, dict):
        raise GatewayError("The request body must be a JSON object.")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise GatewayError("The request names no model.")
    raw_input = body.get("input", [])
    if isinstance(raw_input, str):
        items: list[dict[str, Any]] = [
            {"role": "user", "content": [{"type": "input_text", "text": raw_input}]}
        ]
    elif isinstance(raw_input, list) and all(isinstance(item, dict) for item in raw_input):
        items = list(raw_input)
    else:
        raise GatewayError("The request input must be text or a list of items.")
    instructions = body.get("instructions")
    tools = [found for tool in body.get("tools") or [] if (found := _function_tool(tool))]
    reasoning = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
    effort = reasoning.get("effort") if isinstance(reasoning.get("effort"), str) else ""
    return {
        "model": model.strip(),
        "input": items,
        "instructions": instructions if isinstance(instructions, str) else "",
        "tools": tools,
        "reasoning_effort": effort,
    }


def _sse(event: dict[str, Any]) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def _failed_event(message: str, code: str) -> dict[str, Any]:
    return {
        "type": "response.failed",
        "response": {"status": "failed", "error": {"code": code, "message": message}},
    }


async def stream_response(grant: Grant, args: dict[str, Any]) -> AsyncIterator[bytes]:
    """The Responses event stream as server-sent events.

    Once streaming has begun a failure can only travel as an event, so it
    becomes ``response.failed`` with the plain message (never a provider
    body: the subscription client words its own errors).
    """
    from jarvis.live.subscription_auth import SubscriptionAuthError
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    try:
        async for event in _client(grant.account_id).stream(**args):
            yield _sse(event)
    except (SubscriptionReasoningError, SubscriptionAuthError) as exc:
        log.info("runtime gateway: %s turn failed (%s)", grant.agent_id, type(exc).__name__)
        yield _sse(_failed_event(str(exc), getattr(exc, "code", "subscription_unavailable")))


async def complete_response(grant: Grant, args: dict[str, Any]) -> dict[str, Any]:
    """The finished Responses object, for a runtime that did not ask to stream."""
    from jarvis.live.subscription_auth import SubscriptionAuthError
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    try:
        async for event in _client(grant.account_id).stream(**args):
            finished = event.get("response")
            if event.get("type") == "response.completed" and isinstance(finished, dict):
                return dict(finished)
    except (SubscriptionReasoningError, SubscriptionAuthError) as exc:
        raise GatewayError(str(exc), status=502, code="subscription_unavailable") from exc
    raise GatewayError("ChatGPT ended without an answer.", status=502, code="incomplete")


async def list_models(grant: Grant) -> list[dict[str, Any]]:
    """The account's ChatGPT models, in the OpenAI ``/models`` shape."""
    from jarvis.live.subscription_auth import SubscriptionAuthError
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    try:
        rows = await _client(grant.account_id).list_models()
    except (SubscriptionReasoningError, SubscriptionAuthError) as exc:
        raise GatewayError(str(exc), status=502, code="subscription_unavailable") from exc
    return [
        {"id": row["id"], "object": "model", "owned_by": "openai"}
        for row in rows
        if isinstance(row.get("id"), str)
    ]


def reset() -> None:
    """Forget every token and client (tests)."""
    with _LOCK:
        _GRANTS.clear()
        _TOKENS.clear()
        _CLIENTS.clear()
