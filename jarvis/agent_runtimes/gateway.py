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
import math
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from concurrent.futures import Future
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from typing import Any, Final

from jarvis.agent_runtimes.model_limits import ModelLimits, resolve_limits

log = logging.getLogger(__name__)

#: Every gateway route lives under this path (``surface_security`` accepts a
#: gateway token there and nowhere else).
PATH_PREFIX: Final[str] = "/api/runtime-gateway"
BASE_PATH: Final[str] = f"{PATH_PREFIX}/v1"

#: The Jarvis provider whose agents run through this gateway.
SUBSCRIPTION_PROVIDER: Final[str] = "openai-codex"


class GatewayError(Exception):
    """A request the gateway cannot answer; ``status`` is the HTTP code."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 400,
        code: str = "invalid_request",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Grant:
    """Who a gateway token speaks for, and on which Jarvis provider."""

    agent_id: str
    provider: str
    #: The provider account (``jarvis.agent_accounts``); "" = the active one.
    account_id: str = ""
    #: The runtime profile: direct chat and serialized scheduled runs differ.
    scope: str = ""


_LOCK = threading.Lock()
_GRANTS: dict[str, Grant] = {}
_TOKENS: dict[Grant, str] = {}
_CLIENTS: dict[str, Any] = {}
_MODEL_LIMITS: dict[Grant, dict[str, ModelLimits]] = {}
_CATALOG: Any = None
_FAILURES: dict[Grant, Future[str]] = {}
_COOLDOWNS: OrderedDict[tuple[str, str, str], float] = OrderedDict()
_DEFAULT_COOLDOWN_S: Final = 30.0

#: Gemini's thought signature per tool call id: the OpenAI wire shape has no
#: field for it, and the next request must carry it back with the call.
_SIGNATURES: OrderedDict[str, str] = OrderedDict()
_SIGNATURES_MAX: Final[int] = 4096

#: The finish reasons a Chat Completions client knows.
_FINISH: Final[dict[str, str]] = {"max_tokens": "length", "length": "length"}

_EFFORTS: Final[frozenset[str]] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)


def grant_token(agent_id: str, provider: str, account_id: str = "", *, scope: str = "") -> str:
    """The token one agent's runtime presents (stable for the app's lifetime,
    so a runtime that keeps a process between turns is not restarted)."""
    grant = Grant(agent_id, provider, account_id, scope)
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


def register_model(token: str, model: str, limits: ModelLimits) -> None:
    """Keep the selected model discoverable, including arbitrary local model ids."""
    with _LOCK:
        grant = _GRANTS[token]
        _MODEL_LIMITS.setdefault(grant, {})[model] = limits


def model_limits(grant: Grant, model: str) -> ModelLimits:
    with _LOCK:
        found = _MODEL_LIMITS.get(grant, {}).get(model)
    if found is not None:
        return found
    from jarvis.brain.model_catalog import ModelCatalog
    from jarvis.core.config import load_config

    return resolve_limits(
        load_config(), grant.provider, model, ModelCatalog().cached_model(grant.provider, model)
    )


async def refresh_model_limits(grant: Grant, model: str, config: Any) -> ModelLimits:
    """Read catalog facts only; unavailable metadata keeps the conservative budget."""
    global _CATALOG  # one lazy catalog cache; no work on the boot path
    from types import SimpleNamespace

    from jarvis.brain.model_catalog import ModelCatalog
    from jarvis.live.subscription_auth import SubscriptionAuthError
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    try:
        if grant.provider == SUBSCRIPTION_PROVIDER:
            rows = await asyncio.wait_for(_client(grant.account_id).list_models(), timeout=5)
            row = next((row for row in rows if row.get("id") == model), {})
            metadata = SimpleNamespace(**row)
        else:
            if _CATALOG is None:
                _CATALOG = await asyncio.to_thread(ModelCatalog)
            catalog = await asyncio.wait_for(_CATALOG.list_models(grant.provider), timeout=5)
            aliases = {model, f"{model}:latest"} if grant.provider == "ollama" else {model}
            metadata = next((row for row in catalog.models if row.id in aliases), None)
    except (TimeoutError, SubscriptionAuthError, SubscriptionReasoningError):
        log.info("runtime gateway: model metadata unavailable for %s", grant.provider)
        return await asyncio.to_thread(model_limits, grant, model)
    if metadata is None or (
        not getattr(metadata, "context_length", None)
        and not getattr(metadata, "max_output_tokens", None)
    ):
        return await asyncio.to_thread(model_limits, grant, model)
    return resolve_limits(config, grant.provider, model, metadata)


def watch_failure(token: str) -> Future[str]:
    """Observe this session's current turn, across the HTTP and runner loops."""
    with _LOCK:
        grant = _GRANTS[token]
        if grant in _FAILURES:
            raise RuntimeError("A runtime turn already owns this gateway session.")
        signal: Future[str] = Future()
        _FAILURES[grant] = signal
        return signal


def unwatch_failure(token: str, signal: Future[str]) -> None:
    with _LOCK:
        grant = _GRANTS.get(token)
        if _FAILURES.get(grant) is signal:
            del _FAILURES[grant]


def _watch_for(grant: Grant) -> Future[str] | None:
    with _LOCK:
        return _FAILURES.get(grant)


def _retry_after(exc: Exception) -> float | None:
    """Read only Retry-After, never copy a provider's body or other headers."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None) or {}
    raw = next(
        (v for k, v in headers.items() if str(k).lower() == "retry-after"),
        getattr(exc, "retry_after", None),
    )
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(str(raw)).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            log.debug("runtime gateway: invalid Retry-After ignored; using the default cooldown")
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _limited(provider: str, seconds: float) -> GatewayError:
    return GatewayError(
        f"{provider} returned HTTP 429 (rate limit). Try again in {math.ceil(seconds)} s. "
        "Your message is saved; use /continue after the wait, or choose another "
        "connected model in the agent's settings. No fallback provider was called.",
        status=429,
        code="rate_limited",
        retry_after=seconds,
    )


def check_cooldown(provider: str, model: str, account_id: str = "") -> None:
    """Reject early, without spawning a runtime or spending another model call."""
    key = (provider, account_id, model)
    with _LOCK:
        remaining = _COOLDOWNS.get(key, 0.0) - time.monotonic()
        if remaining <= 0:
            _COOLDOWNS.pop(key, None)
    if remaining > 0:
        raise _limited(provider, remaining)


def _report_failure(
    grant: Grant,
    model: str,
    failure: GatewayError,
    signal: Future[str] | None,
) -> GatewayError:
    if failure.status == 429:
        seconds = failure.retry_after if failure.retry_after is not None else _DEFAULT_COOLDOWN_S
        key = (grant.provider, grant.account_id, model)
        with _LOCK:
            now = time.monotonic()
            until = max(_COOLDOWNS.get(key, 0.0), now + seconds)
            seconds = until - now
            _COOLDOWNS[key] = until
            _COOLDOWNS.move_to_end(key)
            while len(_COOLDOWNS) > 512:
                _COOLDOWNS.popitem(last=False)
        failure = _limited(grant.provider, seconds)
    from jarvis.agent_runtimes.provider_errors import is_terminal

    # Only a failure no runtime can recover from ends the turn here. A
    # timeout, an overloaded provider or a context overflow goes back to the
    # runtime, whose own retry and conversation compression handle it.
    if signal is not None and is_terminal(failure.code):
        # A request captures its turn's signal before awaiting the provider.
        # Late failures therefore cannot stop a later turn on the same session.
        from concurrent.futures import InvalidStateError

        try:
            signal.set_result(str(failure))
        except InvalidStateError:
            # A previous failure or runner cleanup already settled this signal.
            log.debug("runtime gateway: failure signal was already settled")
    return failure


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


def _is_system(item: dict[str, Any]) -> bool:
    return item.get("role") == "system" and item.get("type", "message") == "message"


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
    # ChatGPT's backend refuses a system message in the input ("System
    # messages are not allowed", live 2026-10-07: every OpenClaw turn on the
    # subscription). Its place is the instructions, which OpenClaw leaves empty.
    system = [_text(item.get("content")) for item in items if _is_system(item)]
    items = [item for item in items if not _is_system(item)]
    parts = [instructions if isinstance(instructions, str) else "", *system]
    tools = [found for tool in body.get("tools") or [] if (found := _function_tool(tool))]
    reasoning = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
    effort = reasoning.get("effort") if isinstance(reasoning.get("effort"), str) else ""
    return {
        "model": model.strip(),
        "input": items,
        "instructions": "\n\n".join(part for part in parts if part.strip()),
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

    model = str(args.get("model") or "")
    signal = _watch_for(grant)
    started = False
    try:
        check_cooldown(grant.provider, model, grant.account_id)
        async with contextlib.aclosing(_client(grant.account_id).stream(**args)) as upstream:
            async for event in upstream:
                started = True
                yield _sse(event)
    except (SubscriptionReasoningError, SubscriptionAuthError, GatewayError) as exc:
        failure = _report_failure(grant, model, _subscription_failure(grant, exc), signal)
        if not started:
            raise failure from exc
        yield _sse(_failed_event(str(failure), failure.code))


async def open_response_stream(grant: Grant, args: dict[str, Any]) -> AsyncIterator[bytes]:
    """Check upstream acceptance before committing an HTTP 200 SSE response."""
    events = stream_response(grant, args)
    try:
        first = await anext(events)
    except StopAsyncIteration as exc:
        raise GatewayError("ChatGPT ended without an answer.", status=502) from exc
    except BaseException:
        await events.aclose()
        raise

    async def accepted() -> AsyncIterator[bytes]:
        try:
            yield first
            async for event in events:
                yield event
        finally:
            await events.aclose()

    return accepted()


async def complete_response(grant: Grant, args: dict[str, Any]) -> dict[str, Any]:
    """The finished Responses object, for a runtime that did not ask to stream."""
    from jarvis.live.subscription_auth import SubscriptionAuthError
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    model = str(args.get("model") or "")
    signal = _watch_for(grant)
    try:
        check_cooldown(grant.provider, model, grant.account_id)
        items: list[dict[str, Any]] = []
        async with contextlib.aclosing(_client(grant.account_id).stream(**args)) as upstream:
            async for event in upstream:
                finished = event.get("response")
                if event.get("type") == "response.output_item.done" and isinstance(
                    event.get("item"), dict
                ):
                    items.append(event["item"])
                if event.get("type") == "response.completed" and isinstance(finished, dict):
                    # ChatGPT's backend streams the output items and sends the
                    # completed response with an empty ``output``.
                    return {**finished, "output": finished.get("output") or items}
    except (SubscriptionReasoningError, SubscriptionAuthError, GatewayError) as exc:
        raise _report_failure(grant, model, _subscription_failure(grant, exc), signal) from exc
    raise _report_failure(
        grant,
        model,
        GatewayError("ChatGPT ended without an answer.", status=502, code="incomplete"),
        signal,
    )


#: The subscription client's request-failure codes, as the gateway's codes.
_SUBSCRIPTION_CODES: Final[dict[str, str]] = {
    "invalid_function_parameters": "invalid_tool_schema",
    "invalid_tool_schema": "invalid_tool_schema",
    "model_not_found": "model_not_found",
}


def _subscription_failure(grant: Grant, exc: Exception) -> GatewayError:
    """A subscription failure as a status and code the runtime can act on.

    The subscription client already words its errors without provider text,
    so its message is kept; the code decides whether the turn ends (a
    terminal code) or the runtime handles it (overflow: compress).
    """
    from jarvis.live.subscription_auth import SubscriptionAuthError

    if isinstance(exc, GatewayError) or getattr(exc, "status", 0) == 429:
        return _failure(grant.provider, exc)
    log.warning(
        "runtime gateway: %s subscription call failed: %s", grant.agent_id, _describe(exc)
    )
    if isinstance(exc, SubscriptionAuthError):
        return GatewayError(str(exc), status=401, code="provider_auth")
    status = getattr(exc, "status", 0)
    code = str(getattr(exc, "code", "") or "")
    if code == "context_length_exceeded":
        return GatewayError(
            f"Context length exceeded: {exc} Compress or shorten the conversation and "
            "send it again.",
            status=400,
            code="context_length_exceeded",
        )
    if status in (401, 403):
        return GatewayError(str(exc), status=401, code="provider_auth")
    if status == 404 or code == "model_not_found":
        return GatewayError(str(exc), status=404, code="model_not_found")
    if status in (400, 413, 422):
        return GatewayError(
            str(exc), status=400, code=_SUBSCRIPTION_CODES.get(code, "invalid_request")
        )
    return GatewayError(str(exc), status=502, code="subscription_unavailable")


def _describe(exc: BaseException) -> str:
    from jarvis.agent_runtimes.provider_errors import describe

    return describe(exc)


async def list_models(grant: Grant) -> list[dict[str, Any]]:
    """The models the grant's provider offers, in the OpenAI ``/models`` shape.

    The subscription asks its account; every other provider answers from
    Jarvis' own catalog, so listing never spends a call.
    """
    if grant.provider != SUBSCRIPTION_PROVIDER:
        from jarvis.agent_chat.catalog import provider_row

        row = provider_row(grant.provider)
        models = [model.id for model in row.curated_models] if row is not None else []
        with _LOCK:
            models = list(dict.fromkeys([*models, *_MODEL_LIMITS.get(grant, {})]))
        return [
            {"id": model, "object": "model", "owned_by": grant.provider,
             **(await asyncio.to_thread(model_limits, grant, model)).wire()}
            for model in models
        ]
    from jarvis.live.subscription_auth import SubscriptionAuthError
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    try:
        rows = await _client(grant.account_id).list_models()
    except (SubscriptionReasoningError, SubscriptionAuthError) as exc:
        raise GatewayError(str(exc), status=502, code="subscription_unavailable") from exc
    from types import SimpleNamespace

    result = []
    for row in rows:
        model = row.get("id")
        if not isinstance(model, str):
            continue
        with _LOCK:
            limits = _MODEL_LIMITS.get(grant, {}).get(model)
        if limits is None:
            limits = resolve_limits(None, grant.provider, model, SimpleNamespace(**row))
        result.append({"id": model, "object": "model", "owned_by": "openai", **limits.wire()})
    return result


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
    except ValueError:
        log.info("runtime gateway: malformed tool arguments replaced with an empty object")
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
    limit = body.get("max_completion_tokens", body.get("max_tokens"))
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise GatewayError("The output token limit must be a positive integer.")
    temperature = body.get("temperature")
    reasoning = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
    effort = body.get("reasoning_effort") or reasoning.get("effort")
    return model.strip(), BrainRequest(
        messages=tuple(messages),
        tools=tuple(tools),
        system="\n\n".join(system) or None,
        temperature=float(temperature) if isinstance(temperature, int | float) else 0.7,
        # Zero is internal only: resolve an omitted limit from this model's
        # actual capacity before handing the request to a provider.
        max_tokens=limit if limit is not None else 0,
        stream=True,
        reasoning_effort=effort if effort in _EFFORTS else None,
    )


def _failure(provider: str, exc: Exception, model: str = "") -> GatewayError:
    """A provider error as a status and code the runtime acts on, and a plain
    message — never the provider's own response body (that goes to the log,
    bounded and redacted)."""
    from jarvis.agent_runtimes.provider_errors import gateway_refusal

    if isinstance(exc, GatewayError):
        return exc
    log.warning("runtime gateway: %s call failed: %s", provider, _describe(exc))
    refusal = gateway_refusal(provider, exc, model)
    return GatewayError(
        refusal.message,
        status=refusal.status,
        code=refusal.code,
        retry_after=_retry_after(exc) if refusal.code == "rate_limited" else None,
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
    from jarvis.agent_runtimes.model_map import login_route
    from jarvis.agent_runtimes.provider_errors import login_expired
    from jarvis.core.config import get_jarvis_agent_secret, override_provider_secrets
    from jarvis.costs.ledger import usage_context

    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)

    async def pump() -> None:
        login: str | None = None
        try:
            secret = get_jarvis_agent_secret(grant.provider)
            overrides = {grant.provider: secret} if secret else {}
            with override_provider_secrets(overrides), usage_context("agent-runtime"):
                on_login, login = await asyncio.to_thread(
                    login_route, grant.provider, grant.account_id
                )
                if on_login and not login:
                    # Only the Claude CLI renews the login; without it the
                    # API-key path would only say "no key".
                    raise login_expired()
                if login:
                    # No API key: the person's Claude Code login answers,
                    # which Anthropic bills as extra usage.
                    from jarvis.plugins.brain.claude_api import ClaudeAPIBrain

                    brain = ClaudeAPIBrain(model=model or None, auth_token=login)
                else:
                    brain = build_brain(grant.provider, model)
                configure_context = getattr(brain, "set_context_window", None)
                if callable(configure_context):
                    limits = await asyncio.to_thread(model_limits, grant, model)
                    configure_context(limits.context_window)
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


def _merge_usage(total: dict[str, int], usage: dict[str, Any]) -> None:
    """Fold one usage block in. A plugin reports a call once, but a split or
    cumulative report must not count the same tokens twice: keep the highest
    figure per key."""
    for key, value in usage.items():
        try:
            total[key] = max(total.get(key, 0), int(value or 0))
        except (TypeError, ValueError):
            log.debug("runtime gateway: non-numeric usage %r ignored", key)


def _openai_usage(usage: dict[str, int]) -> dict[str, Any]:
    """The plugin's usage in the Chat Completions shape.

    The plugins report ``input_tokens`` as the UNCACHED share
    (``jarvis.brain.usage_meter``); Chat Completions' ``prompt_tokens``
    counts the whole prompt, cache reads and writes included. Hermes and
    OpenClaw size the conversation from it and compress when it nears the
    context window, so the cached share must be in it.
    """
    cached = int(usage.get("cache_hit_tokens", 0))
    written = int(usage.get("cache_write_tokens", 0))
    prompt = int(usage.get("input_tokens", 0)) + cached + written
    completion = int(usage.get("output_tokens", 0))
    details: dict[str, int] = {"cached_tokens": cached}
    if written:
        details["cache_write_tokens"] = written
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": details,
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
    limits = await asyncio.to_thread(model_limits, grant, model)
    request = _with_output_capacity(request, limits)
    stream = _deltas(grant, model, request)
    started = time.monotonic()
    signal = _watch_for(grant)
    try:
        check_cooldown(grant.provider, model, grant.account_id)
        first = await anext(stream, None)
    except Exception as exc:  # noqa: BLE001 — becomes the runtime's HTTP error; _failure logs it
        raise _report_failure(grant, model, _failure(grant.provider, exc, model), signal) from exc
    return _chat_chunks(grant, model, stream, first, started, signal)


async def _chat_chunks(
    grant: Grant,
    model: str,
    stream: AsyncIterator[Any],
    first: Any,
    started: float,
    signal: Future[str] | None = None,
) -> AsyncIterator[bytes]:
    chat_id = f"chatcmpl-{uuid.uuid4().hex}"
    usage: dict[str, int] = {}
    calls = 0
    finish = "stop"
    delta = first
    try:
        yield _chunk(chat_id, model, {"role": "assistant", "content": ""})
        while delta is not None:
            if delta.content:
                yield _chunk(chat_id, model, {"content": delta.content})
            if delta.tool_call:
                yield _chunk(chat_id, model, {"tool_calls": [_tool_call(calls, delta.tool_call)]})
                calls += 1
            if delta.usage:
                _merge_usage(usage, delta.usage)
            if delta.finish_reason:
                finish = _FINISH.get(delta.finish_reason, "stop")
            delta = await anext(stream, None)
    except Exception as exc:  # noqa: BLE001 — the stream already began: an error chunk tells the runtime
        failure = _report_failure(grant, model, _failure(grant.provider, exc, model), signal)
        payload = {"error": {"message": str(failure), "type": failure.code, "code": failure.code}}
        yield f"data: {json.dumps(payload)}\n\n".encode()
        return
    finally:
        # The runner stops on a provider failure; a closed HTTP client must
        # also release the plugin task if it was suspended between deltas.
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
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
        grant.agent_id,
        grant.provider,
        model,
        time.monotonic() - started,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        calls,
    )


async def complete_chat(grant: Grant, model: str, request: Any) -> dict[str, Any]:
    """The finished ``chat.completion`` for a runtime that did not ask to stream."""
    limits = await asyncio.to_thread(model_limits, grant, model)
    request = _with_output_capacity(request, limits)
    stream = _deltas(grant, model, request)
    text: list[str] = []
    calls: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    finish = "stop"
    signal = _watch_for(grant)
    try:
        check_cooldown(grant.provider, model, grant.account_id)
        async for delta in stream:
            if delta.content:
                text.append(delta.content)
            if delta.tool_call:
                calls.append(_tool_call(len(calls), delta.tool_call))
            if delta.usage:
                _merge_usage(usage, delta.usage)
            if delta.finish_reason:
                finish = _FINISH.get(delta.finish_reason, "stop")
    except Exception as exc:  # noqa: BLE001 — becomes the runtime's HTTP error; _failure logs it
        raise _report_failure(grant, model, _failure(grant.provider, exc, model), signal) from exc
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


def _with_output_capacity(request: Any, limits: ModelLimits) -> Any:
    from jarvis.core.protocols import BrainRequest

    maximum = limits.max_output_tokens
    default = BrainRequest.__dataclass_fields__["max_tokens"].default
    requested = request.max_tokens or maximum or default
    return replace(
        request, max_tokens=min(requested, maximum) if maximum is not None else requested
    )


def reset() -> None:
    """Forget every token, client and remembered signature (tests)."""
    global _CATALOG
    _CATALOG = None
    with _LOCK:
        _GRANTS.clear()
        _TOKENS.clear()
        _CLIENTS.clear()
        _SIGNATURES.clear()
        _MODEL_LIMITS.clear()
        _FAILURES.clear()
        _COOLDOWNS.clear()
