"""Shared logic for OpenAI-compatible APIs (openai / openrouter / grok).

All three use the Chat-Completions format. Differences:
- Base URL (api.openai.com / openrouter.ai / api.x.ai)
- Model-name namespace
- Default headers (OpenRouter wants X-Title, HTTP-Referer)
"""
from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest

log = logging.getLogger(__name__)

#: Shared HTTP timeout for every openai-SDK-based brain (openai / grok /
#: openrouter). The SDK default read timeout is 600 s — a hung backup provider
#: on the fallback chain could otherwise hold the brain coroutine far longer
#: than the voice path tolerates. Read is capped to 30 s (well under the brain
#: stall guard) while connect stays at 5 s so a dead endpoint fast-fails and the
#: chain moves on (Wave-3 latency fix).
CLIENT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0)


def _stream_options_supported() -> bool:
    """One-shot detection: does the installed openai SDK know ``stream_options``?

    `stream_options` was introduced in openai>=1.30 (June 2024) — older
    versions raise ``TypeError: got unexpected keyword argument`` directly
    on the call. We check the signature once at module-load time and cache
    the result. For future API changes, the re-try path in
    ``run_openai_chat`` is the safety net.
    """
    try:
        from openai.resources.chat.completions import AsyncCompletions

        sig = inspect.signature(AsyncCompletions.create)
        return "stream_options" in sig.parameters
    except Exception:  # noqa: BLE001 — detection must never kill the import
        return False


_STREAM_OPTIONS_SUPPORTED = _stream_options_supported()
if not _STREAM_OPTIONS_SUPPORTED:
    log.warning(
        "openai SDK does not know 'stream_options' — likely openai<1.30. "
        "Provider runs without inline usage tracking. Recommendation: pip install -U openai."
    )


def _to_openai_messages(
    messages: tuple[BrainMessage, ...],
    system_extra: str | None,
    *,
    supports_vision: bool = True,
    tool_name_map: dict[str, str] | None = None,
    assistant_tool_call_extra_content: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """BrainMessages → OpenAI Chat-Completions array.

    Multimodal: `BrainMessage.images` is encoded as a Data-URI in the
    `image_url` content block for user messages. If the target provider has
    no vision support (`supports_vision=False`), images are dropped and
    logged once per call.
    Backwards-compat: without images, the content stays a plain string.
    """
    out: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for m in messages:
        if m.role == "system" and isinstance(m.content, str):
            system_parts.append(m.content)
    if system_extra:
        system_parts.append(system_extra)
    if system_parts:
        out.append({"role": "system", "content": "\n\n".join(system_parts)})

    vision_drop_warned = False
    for m in messages:
        if m.role == "system":
            continue

        if m.role == "tool":
            out.append({
                "role": "tool",
                "content": (
                    m.content
                    if isinstance(m.content, str)
                    else json.dumps(m.content, default=str)
                ),
                "tool_call_id": m.tool_call_id or "",
            })
            continue

        if m.role == "assistant" and isinstance(m.content, list):
            # Assistant with tool calls
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in m.content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    original_name = block.get("name", "")
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": (tool_name_map or {}).get(
                                original_name,
                                original_name,
                            ),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    })
            if tool_calls and assistant_tool_call_extra_content:
                # Gemini 3 validates a thought signature on the first call in
                # each reconstructed assistant tool step. Other compatible
                # providers leave this unset. The caller owns the provider-
                # specific payload; this shared adapter only preserves it.
                tool_calls[0]["extra_content"] = assistant_tool_call_extra_content
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
            continue

        # user | assistant (with string content)
        # `getattr` for backwards-compat (Protocol pre-Wave-1-B1 had no images).
        images = getattr(m, "images", ()) or ()
        has_images = m.role == "user" and bool(images)
        if has_images and supports_vision:
            text_content = (
                m.content
                if isinstance(m.content, str)
                else json.dumps(m.content, default=str)
            )
            content_blocks: list[dict[str, Any]] = []
            if text_content:
                content_blocks.append({"type": "text", "text": text_content})
            for img in images:
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{img.mime};base64,{img.data_b64}",
                    },
                })
            out.append({"role": m.role, "content": content_blocks})
            continue

        if has_images and not supports_vision:
            if not vision_drop_warned:
                log.warning(
                    "Provider without vision support — dropping %d image(s).",
                    len(images),
                )
                vision_drop_warned = True
            # Fall through to the plain-text path (images dropped).

        text_content = (
            m.content
            if isinstance(m.content, str)
            else json.dumps(m.content, default=str)
        )
        out.append({"role": m.role, "content": text_content})
    return out


# Function/tool names sent to OpenAI- and Anthropic-family models must match
# ``^[A-Za-z0-9_-]{1,N}`` (OpenAI N=64, Anthropic N=128). MCP tools are namespaced
# ``"<server>/<tool>"`` (jarvis/mcp/adapter.py) and some servers use ``.``/``:`` —
# all rejected. Gemini sanitizes separately; this is the OpenAI/Anthropic-family
# equivalent. Sanitize to the stricter cap (<=64) so BOTH accept it. Forensic
# 2026-06-29: a single slash-named MCP tool made Anthropic reject the WHOLE request
# (``tools.N.custom.name``) and bricked every tool turn → "can't reach my model".
_OAI_NAME_FORBIDDEN_RE = re.compile(r"[^A-Za-z0-9_-]")
_OAI_NAME_MAXLEN = 64


def _sanitize_openai_function_name(name: str, taken: set[str]) -> str:
    """Coerce ``name`` to ``^[A-Za-z0-9_-]{1,64}``, unique vs ``taken``.

    Identity-preserving for already-valid names (the router tools round-trip for
    free). Collisions get a numeric suffix so the original→safe map stays bijective
    — tool-call resolution depends on that round-trip.
    """
    cleaned = _OAI_NAME_FORBIDDEN_RE.sub("_", name or "")
    if not cleaned:
        cleaned = "_"
    if len(cleaned) > _OAI_NAME_MAXLEN:
        cleaned = cleaned[:_OAI_NAME_MAXLEN]
    if cleaned not in taken:
        return cleaned
    base = cleaned
    i = 1
    while cleaned in taken:
        suffix = f"_{i}"
        cleaned = base[: _OAI_NAME_MAXLEN - len(suffix)] + suffix
        i += 1
    return cleaned


def _openai_tool_name_map(tools: tuple[dict[str, Any], ...]) -> dict[str, str]:
    """Deterministic original→safe tool-name map — the single source of truth for
    the outbound tool defs AND the inbound tool_call back-translation."""
    taken: set[str] = set()
    mapping: dict[str, str] = {}
    for t in tools or ():
        original = t.get("name", "")
        safe = _sanitize_openai_function_name(original, taken)
        taken.add(safe)
        mapping[original] = safe
    return mapping


def _tools_openai_format(
    tools: tuple[dict[str, Any], ...],
    name_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    name_map = name_map if name_map is not None else _openai_tool_name_map(tools)
    out: list[dict[str, Any]] = []
    for t in tools:
        schema = t.get("input_schema") or t.get("parameters") or t.get("schema") or {}
        if not schema:
            schema = {"type": "object", "properties": {}}
        original = t.get("name", "")
        out.append({
            "type": "function",
            "function": {
                "name": name_map.get(original, original),
                "description": t.get("description", ""),
                "parameters": schema,
            },
        })
    return out


async def stream_complete(
    client: Any,
    model: str,
    req: BrainRequest,
    *,
    extra_body: dict[str, Any] | None = None,
    supports_vision: bool = True,
    assistant_tool_call_extra_content: dict[str, Any] | None = None,
) -> AsyncIterator[BrainDelta]:
    """Streaming run against OpenAI-compatible Chat-Completions.

    `supports_vision` is passed through to the message builder — when `False`,
    `BrainMessage.images` are dropped and a WARN is logged.
    """
    # The same map must sanitize both declarations and reconstructed assistant
    # tool history. Otherwise an MCP name such as ``github/search`` succeeds in
    # the first round, then makes the provider reject the second-round history.
    # (Token-limit param note: see _create_with_token_param_retry below.)
    name_map = _openai_tool_name_map(req.tools) if req.tools else {}
    messages = _to_openai_messages(
        req.messages,
        req.system,
        supports_vision=supports_vision,
        tool_name_map=name_map,
        assistant_tool_call_extra_content=assistant_tool_call_extra_content,
    )
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
        "stream": True,
    }
    # stream_options only exists since openai>=1.30. On old SDKs (e.g. 1.10)
    # the unconditional call would raise a TypeError and crash the plugin
    # chain with "AsyncCompletions.create() got an unexpected keyword argument"
    # — the user then hears the "unreachable" diagnostic instead of an answer.
    if _STREAM_OPTIONS_SUPPORTED:
        kwargs["stream_options"] = {"include_usage": True}
    # Sanitize tool names to the OpenAI/Anthropic rule and keep a reverse map so
    # the model's tool_call resolves back to the ORIGINAL tool name (e.g. the
    # ``server/tool`` MCP name) that the executor knows.
    reverse_name_map = {safe: original for original, safe in name_map.items()}
    if req.tools:
        kwargs["tools"] = _tools_openai_format(req.tools, name_map)
    if extra_body:
        kwargs.update(extra_body)

    # Accumulator for tool-call partials (OpenAI streams per tool_call index)
    tool_buffer: dict[int, dict[str, Any]] = {}

    try:
        stream = await _create_with_token_param_retry(client, kwargs)
    except TypeError as exc:
        # Belt-and-suspenders: if the detection above was wrong for whatever
        # reason (mocked tests, monkey-patched SDK, exotic forks), retry once
        # without stream_options. Saves a hard fail when the live API rejects
        # an unexpected kwarg.
        if "stream_options" not in kwargs or "stream_options" not in str(exc):
            raise
        log.warning(
            "openai SDK rejected 'stream_options' (%s) — retrying without the kwarg.",
            exc,
        )
        kwargs.pop("stream_options", None)
        stream = await _create_with_token_param_retry(client, kwargs)
    async for chunk in stream:
        # Text-Content
        choices = getattr(chunk, "choices", None) or []
        for choice in choices:
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            content = getattr(delta, "content", None)
            if content:
                yield BrainDelta(content=content)

            tool_calls = getattr(delta, "tool_calls", None) or []
            for tc in tool_calls:
                idx = getattr(tc, "index", 0) or 0
                slot = tool_buffer.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["arguments"] += fn.arguments

            finish = getattr(choice, "finish_reason", None)
            if finish:
                # Finalize tool calls if present
                for idx, buf in sorted(tool_buffer.items()):
                    try:
                        parsed = json.loads(buf["arguments"]) if buf["arguments"] else {}
                    except json.JSONDecodeError:
                        parsed = {}
                    yield BrainDelta(tool_call={
                        "id": buf["id"] or f"call_{idx}",
                        "name": reverse_name_map.get(buf["name"], buf["name"]),
                        "input": parsed,
                    })
                tool_buffer.clear()
                yield BrainDelta(finish_reason=finish)

        # Usage info (OpenAI delivers this in the last chunk)
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            yield BrainDelta(usage={
                "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            })


_UNSUPPORTED_ERROR_MARKERS = (
    "unsupported_parameter",
    "unsupported_value",
    "unsupported parameter",
    "unsupported value",
    "does not support",
    "not supported",
)


def _error_metadata(exc: Exception) -> tuple[str, str, str]:
    """Return normalized ``(parameter, code, message)`` without SDK coupling."""
    parameter = str(getattr(exc, "param", "") or "").strip().lower()
    code = str(getattr(exc, "code", "") or "").strip().lower()
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            parameter = str(error.get("param") or parameter).strip().lower()
            code = str(error.get("code") or code).strip().lower()
    return parameter, code, str(exc).lower()


def _explicitly_rejects_parameter(exc: Exception, parameter: str) -> bool:
    """Whether an API error explicitly rejects one request parameter.

    OpenAI SDK errors expose ``param``/``code`` on some versions and only an
    embedded response body on others. OpenAI-compatible gateways often expose
    neither, so the message fallback remains intentionally narrow: the field
    name and an explicit unsupported marker must both be present.
    """
    rejected_parameter, code, message = _error_metadata(exc)
    unsupported = code in {"unsupported_parameter", "unsupported_value"} or any(
        marker in message for marker in _UNSUPPORTED_ERROR_MARKERS
    )
    if not unsupported:
        return False
    if rejected_parameter:
        return rejected_parameter == parameter
    return parameter in message


def _compatible_retry_kwargs(
    exc: Exception,
    kwargs: dict[str, Any],
    adaptations: set[str],
) -> tuple[dict[str, Any], str] | None:
    """Build one safe retry after an explicit model/API capability rejection."""
    message = str(exc).lower()
    if "max_tokens" in kwargs and "max_tokens" not in adaptations:
        rejected_max_tokens = (
            "max_completion_tokens" in message
            or _explicitly_rejects_parameter(exc, "max_tokens")
        )
        if rejected_max_tokens:
            retry_kwargs = dict(kwargs)
            retry_kwargs["max_completion_tokens"] = retry_kwargs.pop("max_tokens")
            return retry_kwargs, "max_tokens"

    # Sampling is optional. If a model accepts only its own default, omission
    # is the capability-safe fallback and preserves the provider's chosen value.
    if (
        "temperature" in kwargs
        and "temperature" not in adaptations
        and _explicitly_rejects_parameter(exc, "temperature")
    ):
        retry_kwargs = dict(kwargs)
        retry_kwargs.pop("temperature", None)
        return retry_kwargs, "temperature"

    # Inline usage accounting is optional and not implemented by every
    # OpenAI-compatible server even when the installed SDK accepts the kwarg.
    if (
        "stream_options" in kwargs
        and "stream_options" not in adaptations
        and _explicitly_rejects_parameter(exc, "stream_options")
    ):
        retry_kwargs = dict(kwargs)
        retry_kwargs.pop("stream_options", None)
        return retry_kwargs, "stream_options"
    return None


async def _create_with_token_param_retry(client: Any, kwargs: dict[str, Any]) -> Any:
    """Create a chat stream with bounded, rejection-driven compatibility retries.

    Newer OpenAI models reject the legacy ``max_tokens`` with a 400
    ``unsupported_parameter`` error ("Use 'max_completion_tokens' instead"),
    while many OpenAI-COMPATIBLE servers (local runtimes, gateways) still only
    accept ``max_tokens``. Sending the legacy name first and switching only on
    the server's EXPLICIT rejection keeps both families working without
    pinning model names (AP-21). Field-found: a valid OpenAI key read as
    "Not working" in the provider test because of this 400.

    The same capability negotiation applies to optional ``temperature`` and
    ``stream_options`` fields. Each field is adapted at most once and only
    after the API explicitly rejects it, so authentication, billing, model,
    tool-schema, and network failures are never hidden or retried.
    """
    current_kwargs = kwargs
    adaptations: set[str] = set()
    while True:
        try:
            return await client.chat.completions.create(**current_kwargs)
        except TypeError:
            # SDK-level kwarg problems belong to the caller's stream_options
            # handling — never ours.
            raise
        except Exception as exc:  # noqa: BLE001 — inspect, adapt, or re-raise
            retry = _compatible_retry_kwargs(exc, current_kwargs, adaptations)
            if retry is None:
                raise
            current_kwargs, field = retry
            adaptations.add(field)
            if field == "max_tokens":
                log.info(
                    "provider rejected 'max_tokens' — retrying with "
                    "'max_completion_tokens'."
                )
            else:
                log.info(
                    "provider rejected optional '%s' — retrying without it.",
                    field,
                )
