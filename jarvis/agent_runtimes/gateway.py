"""Jarvis' model gateway: the one model provider Hermes / OpenClaw agents see.

A Hermes or OpenClaw agent never talks to a model vendor itself. Its runtime
is configured with exactly one provider — Jarvis, on the app's own loopback
server (``/api/runtime-gateway/v1``) — and a per-agent token Jarvis minted.
Jarvis answers with the same provider plugins its own agents use
(``jarvis.plugins.brain.*``), so every provider connected in Jarvis works on
every runtime, keys never leave the Jarvis process, every call lands in the
cost ledger, and a runtime update can only ever meet the long-stable OpenAI
wire shapes:

* ``POST /chat/completions`` — every API-key and local provider. The request
  is translated into a ``BrainRequest`` (system text, messages, function
  tools, effort) and the plugin's stream back into Chat Completions chunks.
* ``POST /responses`` — the ChatGPT subscription. Handing the login to the
  runtime would make a second program refresh it, and OAuth refresh tokens are
  single-use (whichever refreshed first would break the other, and the
  person's own Codex login with it). Jarvis' subscription client
  (``jarvis.live.subscription_reasoning``) answers on the agent's Codex
  account, whose refresh is coordinated in one place.
* ``GET /models`` — the models the agent's provider offers.

Only function tools pass through, and the runtime executes its own tools.
Requests are rebuilt field by field, so a runtime option a provider does not
accept never reaches it. Provider failures reach the runtime as a plain
message and an HTTP status it can back off on, never as a provider's body.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import threading
import time
import uuid
from collections import OrderedDict
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


def _subscription_message(exc: Exception) -> str:
    """Fixed wording for a failed subscription call; no exception text leaves."""
    from jarvis.live.subscription_auth import SubscriptionAuthError

    if isinstance(exc, SubscriptionAuthError) or getattr(exc, "code", "") == "login":
        return "The ChatGPT subscription needs a new sign-in in Jarvis."
    return "The ChatGPT subscription could not answer this turn."


@dataclass(frozen=True, slots=True)
class Grant:
    """Who a gateway token speaks for, and on which Jarvis provider."""

    agent_id: str
    provider: str
    #: The provider account (``jarvis.agent_accounts``); "" = the active one.
    account_id: str = ""


_LOCK = threading.Lock()
_GRANTS: dict[str, Grant] = {}
_TOKENS: dict[Grant, str] = {}
_CLIENTS: dict[str, Any] = {}

#: Gemini's thought signature per tool call id: the OpenAI wire shape has no
#: field for it, and the next request must carry it back with the call.
_SIGNATURES: OrderedDict[str, str] = OrderedDict()
_SIGNATURES_MAX: Final[int] = 4096

#: The finish reasons a Chat Completions client knows.
_FINISH: Final[dict[str, str]] = {"max_tokens": "length", "length": "length"}

_EFFORTS: Final[frozenset[str]] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)


def grant_token(agent_id: str, provider: str, account_id: str = "") -> str:
    """The token one agent's runtime presents (stable for the app's lifetime,
    so a runtime that keeps a process between turns is not restarted)."""
    grant = Grant(agent_id, provider, account_id)
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
        code = getattr(exc, "code", "subscription_unavailable")
        yield _sse(_failed_event(_subscription_message(exc), code))


async def complete_response(grant: Grant, args: dict[str, Any]) -> dict[str, Any]:
    """The finished Responses object, for a runtime that did not ask to stream."""
    from jarvis.live.subscription_auth import SubscriptionAuthError
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    try:
        items: list[dict[str, Any]] = []
        async for event in _client(grant.account_id).stream(**args):
            finished = event.get("response")
            if event.get("type") == "response.output_item.done" and isinstance(
                event.get("item"), dict
            ):
                items.append(event["item"])
            if event.get("type") == "response.completed" and isinstance(finished, dict):
                # ChatGPT's backend streams the output items and sends the
                # completed response with an empty ``output``.
                return {**finished, "output": finished.get("output") or items}
    except (SubscriptionReasoningError, SubscriptionAuthError) as exc:
        log.info("runtime gateway: %s call failed (%s)", grant.agent_id, type(exc).__name__)
        raise GatewayError(
            _subscription_message(exc), status=502, code="subscription_unavailable"
        ) from exc
    raise GatewayError("ChatGPT ended without an answer.", status=502, code="incomplete")


async def list_models(grant: Grant) -> list[dict[str, Any]]:
    """The models the grant's provider offers, in the OpenAI ``/models`` shape.

    The subscription asks its account; every other provider answers from
    Jarvis' own catalog, so listing never spends a call.
    """
    if grant.provider != SUBSCRIPTION_PROVIDER:
        from jarvis.agent_chat.catalog import provider_row

        row = provider_row(grant.provider)
        models = [model.id for model in row.curated_models] if row is not None else []
        return [{"id": model, "object": "model", "owned_by": grant.provider} for model in models]
    from jarvis.live.subscription_auth import SubscriptionAuthError
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    try:
        rows = await _client(grant.account_id).list_models()
    except (SubscriptionReasoningError, SubscriptionAuthError) as exc:
        log.info("runtime gateway: %s call failed (%s)", grant.agent_id, type(exc).__name__)
        raise GatewayError(
            _subscription_message(exc), status=502, code="subscription_unavailable"
        ) from exc
    return [
        {"id": row["id"], "object": "model", "owned_by": "openai"}
        for row in rows
        if isinstance(row.get("id"), str)
    ]


# ------------------------------------------------- chat completions (API keys)


def _parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [part for part in content or [] if isinstance(part, dict)]


def _text(content: Any) -> str:
    return "".join(
        str(part.get("text") or "")
        for part in _parts(content)
        if part.get("type") in ("text", "input_text", "output_text")
    )


def _images(content: Any) -> tuple[Any, ...]:
    """Inline images (``data:`` URLs); a remote URL is not fetched."""
    from jarvis.core.protocols import ImageBlock

    out: list[ImageBlock] = []
    for part in _parts(content):
        if part.get("type") != "image_url":
            continue
        url = part.get("image_url")
        url = url.get("url") if isinstance(url, dict) else url
        if isinstance(url, str) and url.startswith("data:") and ";base64," in url:
            mime, _, data = url[5:].partition(";base64,")
            out.append(ImageBlock(mime=mime or "image/png", data_b64=data))
    return tuple(out)


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:  # unparsable tool arguments become an empty object
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _remember_signature(call_id: str, signature: str) -> None:
    with _LOCK:
        _SIGNATURES[call_id] = signature
        _SIGNATURES.move_to_end(call_id)
        while len(_SIGNATURES) > _SIGNATURES_MAX:
            _SIGNATURES.popitem(last=False)


def _signature(call_id: str) -> str:
    with _LOCK:
        return _SIGNATURES.get(call_id, "")


def chat_request(body: Any) -> tuple[str, Any]:
    """``(model, BrainRequest)`` for one Chat Completions request body."""
    from jarvis.core.protocols import BrainMessage, BrainRequest

    if not isinstance(body, dict):
        raise GatewayError("The request body must be a JSON object.")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise GatewayError("The request names no model.")
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise GatewayError("The request has no messages.")
    system: list[str] = []
    messages: list[BrainMessage] = []
    for message in raw_messages:
        if not isinstance(message, dict):
            raise GatewayError("Every message must be a JSON object.")
        role = message.get("role")
        content = message.get("content")
        if role in ("system", "developer"):
            if text := _text(content):
                system.append(text)
        elif role == "user":
            messages.append(BrainMessage("user", _text(content), images=_images(content)))
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if text := _text(content):
                blocks.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                call_id = str(call.get("id") or f"call_{uuid.uuid4().hex[:8]}")
                block: dict[str, Any] = {
                    "type": "tool_use",
                    "id": call_id,
                    "name": str(function.get("name") or ""),
                    "input": _arguments(function.get("arguments")),
                }
                if signature := _signature(call_id):
                    block["thought_signature"] = signature
                blocks.append(block)
            if blocks:
                only_text = all(block["type"] == "text" for block in blocks)
                folded = "".join(b["text"] for b in blocks) if only_text else blocks
                messages.append(BrainMessage("assistant", folded))
        elif role == "tool":
            messages.append(
                BrainMessage(
                    "tool",
                    _text(content) if not isinstance(content, str) else content,
                    tool_call_id=str(message.get("tool_call_id") or ""),
                    name=message.get("name") if isinstance(message.get("name"), str) else None,
                )
            )
    tools: list[dict[str, Any]] = []
    for tool in body.get("tools") or []:
        found = _function_tool(tool)
        if found is not None:
            tools.append(
                {
                    "name": found["name"],
                    "description": found.get("description", ""),
                    "input_schema": found["parameters"],
                }
            )
    limit = body.get("max_completion_tokens") or body.get("max_tokens") or 8192
    temperature = body.get("temperature")
    reasoning = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
    effort = body.get("reasoning_effort") or reasoning.get("effort")
    return model.strip(), BrainRequest(
        messages=tuple(messages),
        tools=tuple(tools),
        system="\n\n".join(system) or None,
        temperature=float(temperature) if isinstance(temperature, int | float) else 0.7,
        max_tokens=max(1, min(int(limit) if isinstance(limit, int) else 8192, 128_000)),
        stream=True,
        reasoning_effort=effort if effort in _EFFORTS else None,
    )


def _failure(provider: str, exc: Exception) -> GatewayError:
    """A provider error as a status the runtime can back off on, and a plain
    message — never the provider's own response body."""
    from jarvis.agent_runtimes.provider_errors import classify

    if (refusal := classify(provider, exc)) is not None:
        # No credits, provider unreachable, Claude Extra Usage off: a retry
        # cannot help, so the runtime must not read it as a rate limit.
        failure = GatewayError(refusal.message, status=refusal.status, code=refusal.code)
        failure.retry_after = refusal.retry_after
        return failure
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if not isinstance(status, int):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status == 429:
        return GatewayError(
            f"{provider} is rate-limiting this key; try again shortly.",
            status=429,
            code="rate_limited",
        )
    if status in (401, 403):
        return GatewayError(
            f"{provider} refused the saved key. Check it in Settings → API keys.",
            status=401,
            code="provider_auth",
        )
    return GatewayError(
        f"{provider} could not answer ({type(exc).__name__}).",
        status=502,
        code="provider_error",
    )


_DONE: Final = object()


async def _deltas(grant: Grant, model: str, request: Any) -> AsyncIterator[Any]:
    """The provider plugin's stream, with the Agents-tier key and cost caller.

    The plugin runs in a task of its own and hands its deltas over a queue:
    the key override and the cost caller are context variables, and a
    streamed response is read by a different task than the one that opened
    it — a context variable set there could not be reset (``ValueError``).
    """
    from jarvis.agent_chat.runner_api import build_brain
    from jarvis.agent_runtimes.model_map import login_token_for
    from jarvis.core.config import get_jarvis_agent_secret, override_provider_secrets
    from jarvis.costs.ledger import usage_context

    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)

    async def pump() -> None:
        login: str | None = None
        try:
            secret = get_jarvis_agent_secret(grant.provider)
            overrides = {grant.provider: secret} if secret else {}
            with override_provider_secrets(overrides), usage_context("agent-runtime"):
                login = await asyncio.to_thread(login_token_for, grant.provider, grant.account_id)
                if login:
                    # No API key: the person's Claude Code login answers,
                    # which Anthropic bills as extra usage.
                    from jarvis.plugins.brain.claude_api import ClaudeAPIBrain

                    brain = ClaudeAPIBrain(model=model or None, auth_token=login)
                else:
                    brain = build_brain(grant.provider, model)
                async for delta in brain.complete(request):
                    await queue.put(delta)
            await queue.put(_DONE)
        except Exception as exc:  # noqa: BLE001 — handed to the reader, which reports it
            if login:
                # Anthropic answers a Claude login it will not serve with a
                # bare 429; the account's usage report says why.
                from jarvis.agent_runtimes.provider_errors import explain_login_refusal

                exc = await explain_login_refusal(exc, login, model)
            await queue.put(exc)

    task = asyncio.get_running_loop().create_task(pump())
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def _chunk(chat_id: str, model: str, delta: dict[str, Any], **extra: Any) -> bytes:
    body: dict[str, Any] = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": extra.pop("finish", None)}],
        **extra,
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n".encode()


def _openai_usage(usage: dict[str, int]) -> dict[str, int]:
    prompt = int(usage.get("input_tokens", 0))
    completion = int(usage.get("output_tokens", 0))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _tool_call(index: int, call: dict[str, Any]) -> dict[str, Any]:
    call_id = str(call.get("id") or f"call_{uuid.uuid4().hex[:12]}")
    if call.get("thought_signature"):
        _remember_signature(call_id, str(call["thought_signature"]))
    raw = call.get("input")
    return {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {
            "name": str(call.get("name") or ""),
            "arguments": raw if isinstance(raw, str) else json.dumps(raw or {}),
        },
    }


async def open_chat_stream(grant: Grant, model: str, request: Any) -> AsyncIterator[bytes]:
    """The answer as Chat Completions chunks.

    The provider's first delta is awaited before this returns, so a failure
    up front (bad key, rate limit) becomes an HTTP status the runtime can act
    on; a failure after streaming began arrives as an ``error`` chunk.
    """
    stream = _deltas(grant, model, request)
    started = time.monotonic()
    try:
        first = await anext(stream, None)
    except Exception as exc:  # noqa: BLE001 — becomes the runtime's HTTP error, logged below
        log.info("runtime gateway: %s on %s failed up front (%s)", grant.agent_id,
                 grant.provider, type(exc).__name__)
        raise _failure(grant.provider, exc) from exc
    return _chat_chunks(grant, model, stream, first, started)


async def _chat_chunks(
    grant: Grant, model: str, stream: AsyncIterator[Any], first: Any, started: float
) -> AsyncIterator[bytes]:
    chat_id = f"chatcmpl-{uuid.uuid4().hex}"
    usage: dict[str, int] = {}
    calls = 0
    finish = "stop"
    yield _chunk(chat_id, model, {"role": "assistant", "content": ""})
    delta = first
    try:
        while delta is not None:
            if delta.content:
                yield _chunk(chat_id, model, {"content": delta.content})
            if delta.tool_call:
                yield _chunk(chat_id, model, {"tool_calls": [_tool_call(calls, delta.tool_call)]})
                calls += 1
            if delta.usage:
                for key, value in delta.usage.items():
                    usage[key] = usage.get(key, 0) + int(value or 0)
            if delta.finish_reason:
                finish = _FINISH.get(delta.finish_reason, "stop")
            delta = await anext(stream, None)
    except Exception as exc:  # noqa: BLE001 — the stream already began: an error chunk tells the runtime
        failure = _failure(grant.provider, exc)
        log.info("runtime gateway: %s on %s failed mid-stream (%s)", grant.agent_id,
                 grant.provider, type(exc).__name__)
        payload = {"error": {"message": str(failure), "type": failure.code, "code": failure.code}}
        yield f"data: {json.dumps(payload)}\n\n".encode()
        return
    yield _chunk(
        chat_id,
        model,
        {},
        finish="tool_calls" if calls else finish,
        usage=_openai_usage(usage),
    )
    yield b"data: [DONE]\n\n"
    log.info(
        "runtime gateway: %s on %s/%s answered in %.1f s (%s in, %s out, %d tool calls)",
        grant.agent_id, grant.provider, model, time.monotonic() - started,
        usage.get("input_tokens", 0), usage.get("output_tokens", 0), calls,
    )


async def complete_chat(grant: Grant, model: str, request: Any) -> dict[str, Any]:
    """The finished ``chat.completion`` for a runtime that did not ask to stream."""
    stream = _deltas(grant, model, request)
    text: list[str] = []
    calls: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    finish = "stop"
    try:
        async for delta in stream:
            if delta.content:
                text.append(delta.content)
            if delta.tool_call:
                calls.append(_tool_call(len(calls), delta.tool_call))
            if delta.usage:
                for key, value in delta.usage.items():
                    usage[key] = usage.get(key, 0) + int(value or 0)
            if delta.finish_reason:
                finish = _FINISH.get(delta.finish_reason, "stop")
    except Exception as exc:  # noqa: BLE001 — becomes the runtime's HTTP error
        raise _failure(grant.provider, exc) from exc
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        message["tool_calls"] = [{k: v for k, v in call.items() if k != "index"} for call in calls]
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {"index": 0, "message": message, "finish_reason": "tool_calls" if calls else finish}
        ],
        "usage": _openai_usage(usage),
    }


def reset() -> None:
    """Forget every token, client and remembered signature (tests)."""
    with _LOCK:
        _GRANTS.clear()
        _TOKENS.clear()
        _CLIENTS.clear()
        _SIGNATURES.clear()
