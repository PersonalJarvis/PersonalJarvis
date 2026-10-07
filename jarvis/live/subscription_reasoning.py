"""Direct Codex Responses streaming; Jarvis alone executes declared tools.

The account's Codex OAuth grant targets the Codex endpoint. It is deliberately
not sent to public Responses: that official SIWC flow needs a different grant.
There is no CLI turn, autonomous harness, hosted tool, or API-key fallback here.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import uuid4

from jarvis.core.http_pool import HttpClientPool
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.live.subscription_auth import SubscriptionCredentials

_BASE_URL = "https://chatgpt.com/backend-api/codex"
_MAX_EVENT_CHARS = 2 * 1024 * 1024
_MAX_CATALOG_BYTES = 4 * 1024 * 1024
_MAX_ERROR_BYTES = 64 * 1024
_REQUEST_FAILURES = {
    "invalid_function_parameters": "ChatGPT rejected a tool schema in this request.",
    "invalid_tool_schema": "ChatGPT rejected a tool schema in this request.",
    "context_length_exceeded": "This request exceeds the selected ChatGPT model's context limit.",
    "model_not_found": "The selected model is unavailable on this ChatGPT subscription.",
    "invalid_request_error": "ChatGPT rejected this request.",
}
log = logging.getLogger(__name__)
_SAFE_CODE = re.compile(r"^[a-zA-Z0-9_.-]{1,96}$")
_SAFE_EFFORT = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_KNOWN_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
# "No thinking" requests (small structured calls) map to the model's lightest
# advertised level: a Codex model without "none" answers 400 to it (live
# 2026-10-03, gpt-6.1-sol: every computer-use step failed before acting).
_LIGHTEST_FIRST = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


class SubscriptionReasoningError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "subscription_unavailable",
        status: int = 0,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after


def _failure(
    status: int = 0,
    payload: Any = None,
    *,
    retry_after: str | None = None,
) -> SubscriptionReasoningError:
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {}
    code = str(error.get("code") or "")
    if not _SAFE_CODE.fullmatch(code):
        code = "subscription_unavailable"
    if status == 429 or code in {
        "usage_limit_reached",
        "rate_limit_exceeded",
        "subscription_sharing_usage_limit_exceeded",
    }:
        status = 429
        message = (
            "The selected ChatGPT subscription reached a usage limit. "
            "Try again after its allowance resets."
        )
    elif status in (401, 403):
        message = (
            "ChatGPT subscription access was rejected. "
            "Check the selected account and sign in again if needed."
        )
    elif status in (400, 404, 413, 422):
        if code not in _REQUEST_FAILURES:
            code = "invalid_request_error"
        message = (
            f"{_REQUEST_FAILURES[code]} (HTTP {status}). "
            "Check the model, conversation and tool settings before trying again."
        )
    else:
        message = (
            "ChatGPT subscription reasoning failed. "
            "Try again later; no API billing fallback was used."
        )
    return SubscriptionReasoningError(message, code=code, status=status, retry_after=retry_after)


def _invalid_request(message: str) -> SubscriptionReasoningError:
    """A request this client refuses before sending it: never worth a retry."""
    return SubscriptionReasoningError(message, code="invalid_request_error", status=400)


async def _http_failure(response: Any) -> SubscriptionReasoningError:
    """Read bounded error metadata, never expose provider text or request content."""
    import httpx

    raw = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            if len(raw) + len(chunk) > _MAX_ERROR_BYTES:
                raw.clear()
                break
            raw.extend(chunk)
    except httpx.HTTPError:
        # Headers already established rejection; a broken error body must not
        # turn a permanent 400 into a retryable network failure.
        raw.clear()
    payload: Any = None
    if raw:
        try:
            payload = json.loads(raw)
        except ValueError:
            pass  # HTML, malformed JSON and oversized bodies use the HTTP status alone.
    failure = _failure(response.status_code, payload)
    # Preserve throttling instructions for gateway backoff without copying any
    # other headers. Existing consumers may read this optional exception field.
    failure.retry_after = response.headers.get("retry-after")
    log.info(
        "ChatGPT subscription request rejected: status=%s category=%s",
        failure.status,
        failure.code if failure.code in _REQUEST_FAILURES else "subscription_unavailable",
    )
    return failure


class SubscriptionReasoning:
    """Streaming text/image inference and function calls, without side effects."""

    def __init__(
        self,
        credentials: Callable[..., Awaitable[SubscriptionCredentials]],
        *,
        http_pool: Any = None,
        session_id: str = "",
    ) -> None:
        self._credentials = credentials
        self._pool = http_pool or HttpClientPool(timeout_s=60.0)
        self._session_id = session_id or str(uuid4())
        self._model_efforts: dict[str, tuple[str, ...]] = {}

    def _headers(self, credentials: SubscriptionCredentials) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {credentials.access_token}",
            "ChatGPT-Account-Id": credentials.account_id,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "originator": "personal_jarvis",
            "session_id": self._session_id,
        }

    async def list_models(self) -> list[dict[str, Any]]:
        import httpx

        from jarvis import __version__

        for attempt in range(2):
            credentials = await self._credentials(force_refresh=bool(attempt))
            try:
                response = await self._pool.client().get(
                    f"{_BASE_URL}/models",
                    params={"client_version": __version__},
                    headers={**self._headers(credentials), "Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise _failure() from exc
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code != 200:
                raise await _http_failure(response)
            if len(response.content) > _MAX_CATALOG_BYTES:
                raise SubscriptionReasoningError("The ChatGPT model catalog is too large.")
            try:
                payload = response.json()
            except ValueError as exc:
                raise SubscriptionReasoningError("The ChatGPT model catalog is invalid.") from exc
            rows = payload.get("models") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise SubscriptionReasoningError("The ChatGPT model catalog is unavailable.")
            models: list[dict[str, Any]] = []
            model_efforts: dict[str, tuple[str, ...]] = {}
            for row in rows:
                if not isinstance(row, dict) or row.get("visibility", "list") != "list":
                    continue
                slug = row.get("slug")
                if not isinstance(slug, str) or not slug.strip():
                    continue
                model: dict[str, Any] = {
                    "id": slug,
                    "label": str(row.get("display_name") or slug),
                }
                for source, target in (
                    ("context_window", "context_length"),
                    ("max_output_tokens", "max_output_tokens"),
                ):
                    value = row.get(source)
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        model[target] = value
                levels = row.get("supported_reasoning_levels")
                if isinstance(levels, list):
                    efforts: list[str] = []
                    for level in levels:
                        effort = level.get("effort") if isinstance(level, dict) else level
                        if (
                            isinstance(effort, str)
                            and _SAFE_EFFORT.fullmatch(effort)
                            and effort not in efforts
                        ):
                            efforts.append(effort)
                    model["efforts"] = efforts
                    model_efforts[slug] = tuple(efforts)
                default = row.get("default_reasoning_level")
                if (
                    isinstance(default, str)
                    and _SAFE_EFFORT.fullmatch(default)
                    and ("efforts" not in model or default in model["efforts"])
                ):
                    model["default_effort"] = default
                models.append(model)
            self._model_efforts = model_efforts
            return models
        raise _failure(401)

    async def stream(
        self,
        *,
        model: str,
        input: list[dict[str, Any]],  # noqa: A002 - matches the Responses wire field
        instructions: str,
        tools: list[dict[str, Any]],
        reasoning_effort: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        import httpx

        if not model.strip():
            raise _invalid_request(
                "Select a ChatGPT subscription thinking model before starting voice."
            )
        if any(tool.get("type") != "function" for tool in tools):
            raise _invalid_request("Subscription reasoning accepts only Jarvis function tools.")
        if reasoning_effort and not _SAFE_EFFORT.fullmatch(reasoning_effort):
            raise _invalid_request("The subscription thinking effort is invalid.")
        if reasoning_effort in {"none", "minimal"}:
            reasoning_effort = await self._lightest_effort(model, reasoning_effort)
        if reasoning_effort and reasoning_effort not in _KNOWN_EFFORTS:
            # New efforts are capability-driven. A model catalog request is free
            # of inference and happens only when an unknown effort needs checking.
            if model not in self._model_efforts:
                await self.list_models()
            if reasoning_effort not in self._model_efforts.get(model, ()):
                raise _invalid_request(
                    "The selected ChatGPT model does not advertise this thinking effort."
                )
        supported = self._model_efforts.get(model)
        if reasoning_effort and supported is not None and reasoning_effort not in supported:
            raise _invalid_request(
                "The selected ChatGPT model does not support this thinking effort."
            )
        reasoning = {"summary": "auto"}
        if reasoning_effort:
            reasoning["effort"] = reasoning_effort
        body = {
            "model": model,
            "input": input,
            "instructions": instructions,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "store": False,
            "stream": True,
            "include": ["reasoning.encrypted_content"],
            "reasoning": reasoning,
        }
        for attempt in range(2):
            credentials = await self._credentials(force_refresh=bool(attempt))
            try:
                async with self._pool.client().stream(
                    "POST",
                    f"{_BASE_URL}/responses",
                    json=body,
                    headers=self._headers(credentials),
                ) as response:
                    if response.status_code == 401 and attempt == 0:
                        continue
                    if response.status_code != 200:
                        raise await _http_failure(response)
                    async for event in self._events(response):
                        yield event
                    return
            except httpx.HTTPError as exc:
                # Once a stream has started, never replay the request automatically.
                raise _failure() from exc
        raise _failure(401)

    async def _lightest_effort(self, model: str, requested: str) -> str:
        """``requested`` when the model offers it, else its lightest level ("" = default)."""
        if model not in self._model_efforts:
            try:
                await self.list_models()
            except SubscriptionReasoningError:
                # Unknown capabilities: the model's own default is always valid.
                return ""
        supported = self._model_efforts.get(model, ())
        if requested in supported:
            return requested
        start = _LIGHTEST_FIRST.index(requested)
        return next((e for e in _LIGHTEST_FIRST[start:] if e in supported), "")

    async def _events(self, response: Any) -> AsyncIterator[dict[str, Any]]:
        parts: list[str] = []
        size = 0
        completed = False
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                chunk = line[5:].lstrip()
                size += len(chunk)
                if size > _MAX_EVENT_CHARS:
                    raise SubscriptionReasoningError(
                        "ChatGPT returned an oversized reasoning event."
                    )
                parts.append(chunk)
            elif not line and parts:
                raw = "\n".join(parts)
                parts, size = [], 0
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except ValueError as exc:
                    raise SubscriptionReasoningError(
                        "ChatGPT returned an invalid reasoning event."
                    ) from exc
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise SubscriptionReasoningError("ChatGPT returned an invalid reasoning event.")
                kind = event["type"]
                if kind in {"error", "response.failed", "response.incomplete"}:
                    raise _failure(payload=event.get("response", event))
                if kind == "response.completed":
                    result = event.get("response", {})
                    if (
                        not isinstance(result, dict)
                        or result.get("status", "completed") != "completed"
                    ):
                        raise _failure(payload=result)
                    completed = True
                yield event
                if completed:
                    return
        if not completed:
            raise SubscriptionReasoningError(
                "ChatGPT reasoning ended before completion. The request was not retried."
            )

    async def aclose(self) -> None:
        await self._pool.aclose()


def _request_input(request: BrainRequest) -> tuple[str, list[dict[str, Any]]]:
    instructions = [request.system] if request.system else []
    items: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role == "system":
            if isinstance(message.content, str):
                instructions.append(message.content)
            continue
        if message.role == "tool":
            if not message.tool_call_id:
                raise SubscriptionReasoningError("A tool result is missing its call identity.")
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content
                    if isinstance(message.content, str)
                    else json.dumps(message.content),
                }
            )
            continue
        blocks: list[dict[str, Any]] = []
        text_type = "output_text" if message.role == "assistant" else "input_text"
        if isinstance(message.content, str):
            if message.content:
                blocks.append({"type": text_type, "text": message.content})
        else:
            for block in message.content:
                kind = block.get("type")
                if kind in {"text", "input_text", "output_text"}:
                    blocks.append({"type": text_type, "text": block.get("text", "")})
                elif kind in {"input_image", "image_url"} and message.role == "user":
                    image = block.get("image_url", "")
                    if isinstance(image, dict):
                        image = image.get("url", "")
                    if image:
                        blocks.append({"type": "input_image", "image_url": image})
                elif kind == "tool_use":
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        }
                    )
                elif kind in {"reasoning", "function_call"}:
                    items.append(dict(block))
                else:
                    raise SubscriptionReasoningError(
                        "This message contains an unsupported subscription input."
                    )
        for image in message.images:
            if message.role != "user":
                raise SubscriptionReasoningError(
                    "Subscription images must belong to a user observation."
                )
            blocks.append(
                {"type": "input_image", "image_url": f"data:{image.mime};base64,{image.data_b64}"}
            )
        if blocks:
            items.append({"role": message.role, "content": blocks})
    return "\n\n".join(instructions), items


class SubscriptionReasoningBrain:
    """Per-operation Brain adapter for image analysis and Jarvis computer tools."""

    supports_vision = True
    supports_tools = True
    context_window = 128_000
    name = "openai-chatgpt-subscription"

    def __init__(self, reasoning: SubscriptionReasoning, model: str) -> None:
        self._reasoning = reasoning
        self._model = model

    def estimate_cost(self, req: BrainRequest) -> float:
        """No metered API-key billing; subscription allowance is reported separately."""
        return 0.0

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        instructions, items = _request_input(req)
        tools: list[dict[str, Any]] = []
        for declaration in req.tools:
            if "function" in declaration:
                tools.append({"type": "function", **declaration["function"]})
            elif declaration.get("type") == "function":
                tools.append(dict(declaration))
            elif "name" in declaration and "input_schema" in declaration:
                tools.append(
                    {
                        "type": "function",
                        "name": declaration["name"],
                        "description": declaration.get("description", ""),
                        "parameters": declaration["input_schema"],
                    }
                )
            else:
                raise SubscriptionReasoningError(
                    "Subscription reasoning accepts only Jarvis function tools."
                )
        calls: list[dict[str, Any]] = []
        seen_calls: set[str] = set()
        usage: dict[str, int] | None = None
        async for event in self._reasoning.stream(
            model=self._model,
            input=items,
            instructions=instructions,
            tools=tools,
            reasoning_effort=req.reasoning_effort or "",
        ):
            kind = event["type"]
            if kind == "response.output_text.delta" and event.get("delta"):
                yield BrainDelta(content=str(event["delta"]))
            elif kind == "response.output_item.done":
                item = event.get("item", {})
                if item.get("type") != "function_call":
                    continue
                call_id = item.get("call_id")
                if not isinstance(call_id, str) or not call_id:
                    raise SubscriptionReasoningError(
                        "ChatGPT returned a tool call without an identity."
                    )
                if call_id in seen_calls:
                    continue
                try:
                    arguments = json.loads(item.get("arguments") or "{}")
                except ValueError as exc:
                    raise SubscriptionReasoningError(
                        "ChatGPT returned invalid tool arguments."
                    ) from exc
                if not isinstance(arguments, dict):
                    raise SubscriptionReasoningError("ChatGPT returned invalid tool arguments.")
                seen_calls.add(call_id)
                calls.append({"id": call_id, "name": item.get("name", ""), "input": arguments})
            elif kind == "response.completed":
                raw = event.get("response", {}).get("usage")
                if isinstance(raw, dict):
                    cached = int((raw.get("input_tokens_details") or {}).get("cached_tokens", 0))
                    usage = {
                        "input_tokens": max(0, int(raw.get("input_tokens", 0)) - cached),
                        "output_tokens": int(raw.get("output_tokens", 0)),
                        "cache_hit_tokens": cached,
                    }
        # BrainDelta has no reasoning-summary field: raw stream consumers receive
        # those events, while Brain callers receive only answer text and tools.
        for call in calls:
            yield BrainDelta(tool_call=call)
        yield BrainDelta(finish_reason="tool_calls" if calls else "stop")
        if usage is not None:
            yield BrainDelta(usage=usage)
