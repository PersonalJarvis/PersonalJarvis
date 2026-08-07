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


# ---------------------------------------------------------------------------
# Responses-API compatibility (rejection-driven, like the param retries below)
# ---------------------------------------------------------------------------

# OpenAI serves its "pro" deep-reasoning class (gpt-5.5-pro, ...) ONLY via the
# Responses API: a Chat-Completions call returns 404 "This is not a chat
# model...". Live 2026-08-06 20:19: the screen-context vision turn crossed
# families onto openai(gpt-5.5-pro), took that 404, and the user heard the
# dishonest "network or provider issue" apology. Detection is by the server's
# EXPLICIT rejection, never by model-name pinning (AP-21), and the verdict is
# cached per (base_url, model) so later turns skip the failed round-trip.
_RESPONSES_ONLY_MARKERS = (
    "not a chat model",
    "not supported in the v1/chat/completions",
    "only supported in v1/responses",
    "use the responses api",
)

#: (base_url, model) pairs the server has declared Responses-only.
_RESPONSES_ONLY_CACHE: set[tuple[str, str]] = set()


def _is_responses_only_rejection(exc: Exception) -> bool:
    """Whether the server explicitly refused the model on Chat-Completions."""
    message = str(exc).lower()
    return any(marker in message for marker in _RESPONSES_ONLY_MARKERS)


def _client_supports_responses(client: Any) -> bool:
    """Whether the installed SDK exposes the Responses API at all."""
    return callable(getattr(getattr(client, "responses", None), "create", None))


def _chat_messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Translate an already-built Chat-Completions array to Responses input.

    system → ``instructions``; user text/image blocks → ``input_text`` /
    ``input_image``; assistant ``tool_calls`` → ``function_call`` items; tool
    results → ``function_call_output`` items. Translating the FINISHED chat
    array (not the BrainMessages) keeps one message builder as the single
    source of truth for history reconstruction and name sanitizing.
    """
    instruction_parts: list[str] = []
    items: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if isinstance(content, str) and content:
                instruction_parts.append(content)
            continue
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id") or "",
                "output": (
                    content
                    if isinstance(content, str)
                    else json.dumps(content, default=str)
                ),
            })
            continue
        if role == "assistant":
            if isinstance(content, str) and content:
                items.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content}],
                })
            for tc in m.get("tool_calls") or ():
                fn = tc.get("function") or {}
                items.append({
                    "type": "function_call",
                    "call_id": tc.get("id") or "",
                    "name": fn.get("name") or "",
                    "arguments": fn.get("arguments") or "{}",
                })
            continue
        # user
        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    blocks.append({
                        "type": "input_text", "text": block.get("text", "")
                    })
                elif btype == "image_url":
                    url = (block.get("image_url") or {}).get("url") or ""
                    if url:
                        blocks.append({"type": "input_image", "image_url": url})
            items.append({"role": "user", "content": blocks})
        else:
            items.append({
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": (
                        content
                        if isinstance(content, str)
                        else json.dumps(content, default=str)
                    ),
                }],
            })
    return ("\n\n".join(instruction_parts) or None), items


def _tools_responses_format(
    chat_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Chat-Completions nested function tools → flat Responses function tools."""
    out: list[dict[str, Any]] = []
    for t in chat_tools:
        fn = t.get("function") or {}
        out.append({
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters")
            or {"type": "object", "properties": {}},
        })
    return out


# Reasoning tokens count against ``max_output_tokens`` on the Responses API,
# and the models that land on this transport reason MANDATORILY. A small
# chat-era budget (the CU tool step sends 256) would be consumed by hidden
# thought before the first visible token, returning an empty "incomplete"
# answer — so the budget is floored here.
_RESPONSES_MIN_OUTPUT_TOKENS = 4096


def _responses_kwargs_from_chat(chat_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Build ``responses.create`` kwargs from prepared Chat-Completions kwargs.

    Deliberately NOT carried over: ``temperature`` and ``reasoning_effort``
    (the deep-reasoning models this transport exists for reject sampling
    knobs and refuse to switch reasoning off) and ``stream_options`` (usage
    arrives on the terminal ``response.completed`` event instead).
    """
    instructions, input_items = _chat_messages_to_responses_input(
        list(chat_kwargs.get("messages") or ())
    )
    out: dict[str, Any] = {
        "model": chat_kwargs.get("model"),
        "input": input_items,
        "stream": True,
    }
    if instructions:
        out["instructions"] = instructions
    max_out = chat_kwargs.get("max_completion_tokens") or chat_kwargs.get(
        "max_tokens"
    )
    if max_out:
        out["max_output_tokens"] = max(int(max_out), _RESPONSES_MIN_OUTPUT_TOKENS)
    if chat_kwargs.get("tools"):
        out["tools"] = _tools_responses_format(list(chat_kwargs["tools"]))
    return out


async def _stream_via_responses(
    client: Any,
    chat_kwargs: dict[str, Any],
    reverse_name_map: dict[str, str],
) -> AsyncIterator[BrainDelta]:
    """Stream one turn over the Responses API, emitting the same BrainDeltas.

    Delta parity with the chat path: text deltas while streaming, tool calls
    finalized before the ``finish_reason``, usage last.
    """
    stream = await client.responses.create(
        **_responses_kwargs_from_chat(chat_kwargs)
    )
    tool_calls: list[dict[str, Any]] = []
    usage_payload: dict[str, int] | None = None
    failure: str | None = None
    async for event in stream:
        etype = str(getattr(event, "type", "") or "")
        if etype == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if delta:
                yield BrainDelta(content=delta)
        elif etype == "response.output_item.done":
            item = getattr(event, "item", None)
            if str(getattr(item, "type", "") or "") == "function_call":
                name = getattr(item, "name", "") or ""
                try:
                    parsed = json.loads(getattr(item, "arguments", "") or "{}")
                except json.JSONDecodeError:
                    log.warning(
                        "Responses stream: tool call %r arrived with "
                        "unparseable arguments; executing with empty input.",
                        name,
                    )
                    parsed = {}
                tool_calls.append({
                    "id": (
                        getattr(item, "call_id", None)
                        or getattr(item, "id", None)
                        or f"call_{len(tool_calls)}"
                    ),
                    "name": reverse_name_map.get(name, name),
                    "input": parsed,
                })
        elif etype in ("response.completed", "response.incomplete"):
            usage = getattr(getattr(event, "response", None), "usage", None)
            if usage is not None:
                usage_payload = {
                    "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                    "output_tokens": int(
                        getattr(usage, "output_tokens", 0) or 0
                    ),
                }
                details = getattr(usage, "input_tokens_details", None)
                cached = int(getattr(details, "cached_tokens", 0) or 0)
                if cached > 0:
                    usage_payload["cache_hit_tokens"] = cached
        elif etype in ("response.failed", "error"):
            err = getattr(getattr(event, "response", None), "error", None)
            if err is None:
                err = getattr(event, "error", None)
            failure = str(
                getattr(err, "message", None) or err or "response failed"
            )
    if failure:
        raise RuntimeError(f"Responses API stream failed: {failure}")
    for tc in tool_calls:
        yield BrainDelta(tool_call=tc)
    yield BrainDelta(finish_reason="tool_calls" if tool_calls else "stop")
    if usage_payload is not None:
        yield BrainDelta(usage=usage_payload)


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

    Models the server declares Responses-only (404 "not a chat model") are
    transparently served over the Responses API — see
    ``_is_responses_only_rejection``.
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
    # Reasoning-by-default models (GPT-5.x class) otherwise burn the small
    # deterministic tool-step budget (CU: 256 tokens) on hidden thought and
    # stream back empty/truncated JSON — "OpenAI never works as the Tool
    # Model". Skipped when the caller's extra_body already carries a gateway
    # reasoning directive (OpenRouter's unified ``reasoning`` object), so the
    # two knobs never conflict. Models/SDKs that reject the parameter are
    # recovered by the graded retry in _compatible_retry_kwargs and the
    # TypeError strip below.
    effort = getattr(req, "reasoning_effort", None)
    if effort is not None and not (extra_body and "reasoning" in extra_body):
        kwargs["reasoning_effort"] = effort
    if extra_body:
        # As ``extra_body`` — merged into the request JSON by the SDK. A
        # top-level ``kwargs.update(extra_body)`` raises TypeError on every
        # modern SDK (create() takes no **kwargs), which killed each
        # OpenRouter call that carried the reasoning opt-out.
        kwargs["extra_body"] = dict(extra_body)

    # Accumulator for tool-call partials (OpenAI streams per tool_call index)
    tool_buffer: dict[int, dict[str, Any]] = {}

    # A model the server already declared Responses-only skips the doomed
    # Chat-Completions round-trip for the rest of the process lifetime.
    responses_cache_key = (str(getattr(client, "base_url", "") or ""), model)
    if responses_cache_key in _RESPONSES_ONLY_CACHE and _client_supports_responses(
        client
    ):
        async for delta in _stream_via_responses(client, kwargs, reverse_name_map):
            yield delta
        return

    # Belt-and-suspenders for SDK-level kwarg gaps: old SDKs know neither
    # ``stream_options`` (added ~1.30) nor ``reasoning_effort`` (added ~1.58)
    # and raise TypeError before any request is sent. Strip exactly the kwarg
    # the SDK named and retry — an ancient SDK may need both stripped.
    _sdk_optional_kwargs = ("stream_options", "reasoning_effort")
    stream = None
    use_responses = False
    for _ in range(len(_sdk_optional_kwargs) + 1):
        try:
            stream = await _create_with_token_param_retry(client, kwargs)
            break
        except TypeError as exc:
            offender = next(
                (k for k in _sdk_optional_kwargs if k in kwargs and k in str(exc)),
                None,
            )
            if offender is None:
                raise
            log.warning(
                "openai SDK rejected '%s' (%s) — retrying without the kwarg.",
                offender,
                exc,
            )
            kwargs.pop(offender, None)
        except Exception as exc:  # noqa: BLE001 — inspect for the transport verdict
            if _is_responses_only_rejection(exc) and _client_supports_responses(
                client
            ):
                _RESPONSES_ONLY_CACHE.add(responses_cache_key)
                log.info(
                    "model %s is Responses-API-only per the server's rejection "
                    "— switching transport (cached for this endpoint).",
                    model,
                )
                use_responses = True
                break
            raise
    if use_responses:
        async for delta in _stream_via_responses(client, kwargs, reverse_name_map):
            yield delta
        return
    if stream is None:  # pragma: no cover -- loop always breaks or raises
        raise RuntimeError("openai stream creation retry loop exhausted")
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
            usage_payload = {
                "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            }
            # Cache hits were invisible on this path (2026-07-28 cost audit:
            # 84% of tool-loop tokens ran through OpenAI-protocol gateways) —
            # without the protocol's cache_hit_tokens key nobody can measure
            # whether a prompt-cache change works or what a turn really cost.
            details = getattr(usage, "prompt_tokens_details", None)
            cached = int(getattr(details, "cached_tokens", 0) or 0)
            if cached > 0:
                usage_payload["cache_hit_tokens"] = cached
            yield BrainDelta(usage=usage_payload)


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


def _rejection_confidence(exc: Exception, parameter: str) -> str | None:
    """How confidently an API error rejects one request parameter.

    ``"structured"`` — the error's own ``param``/``code`` metadata names the
    parameter (safe to remember for the endpoint). ``"message"`` — only the
    narrow substring fallback matched: field name plus an unsupported marker
    somewhere in the text. A gateway that concatenates several validation
    complaints into one message can satisfy that accidentally, so a
    message-level match may drive ONE retry but must never poison the
    process-lifetime adaptation cache. ``None`` — no explicit rejection.
    """
    rejected_parameter, code, message = _error_metadata(exc)
    unsupported = code in {"unsupported_parameter", "unsupported_value"} or any(
        marker in message for marker in _UNSUPPORTED_ERROR_MARKERS
    )
    if not unsupported:
        return None
    if rejected_parameter:
        return "structured" if rejected_parameter == parameter else None
    return "message" if parameter in message else None


#: An endpoint may REQUIRE internal reasoning and refuse every attempt to turn
#: it off. That is a different complaint from "unsupported parameter": the knob
#: is understood, its OFF value is refused — so it carries none of the markers
#: above, names no parameter, and every degradation path here is blind to it by
#: construction. Field-found on OpenRouter's ``google/gemini-3.5-flash``
#: (2026-07-26): "Reasoning is mandatory for this endpoint and cannot be
#: disabled" dropped the first provider out of every delegated voice turn.
_REASONING_MANDATORY_MARKERS = (
    "cannot be disabled",
    "can not be disabled",
    "cannot be turned off",
    "is mandatory",
    "is required",
)


def _rejects_disabling_reasoning(exc: Exception) -> bool:
    """Whether an API error refuses to let reasoning be switched OFF."""
    _, _, message = _error_metadata(exc)
    if "reasoning" not in message:
        return False
    return any(marker in message for marker in _REASONING_MANDATORY_MARKERS)


def _without_reasoning_opt_out(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """*kwargs* with every "turn reasoning off" directive removed.

    Both spellings go in ONE step — the native ``reasoning_effort="none"`` and
    a gateway's ``reasoning={"enabled": False}`` object. Dropping one and
    retrying re-sends the other and earns the identical rejection, which is
    what made a live turn pay two HTTP 400s before moving on. Unrelated
    ``extra_body`` entries (a gateway's own routing knobs) are preserved.
    Returns ``None`` when the request asks for no opt-out at all — a graded
    effort like ``"medium"`` is not an opt-out and has nothing to give up.
    """
    adapted = dict(kwargs)
    changed = False
    if adapted.get("reasoning_effort") == "none":
        adapted.pop("reasoning_effort", None)
        changed = True
    extra_body = adapted.get("extra_body")
    if isinstance(extra_body, dict):
        directive = extra_body.get("reasoning")
        if isinstance(directive, dict) and directive.get("enabled") is False:
            trimmed = {k: v for k, v in extra_body.items() if k != "reasoning"}
            if trimmed:
                adapted["extra_body"] = trimmed
            else:
                adapted.pop("extra_body", None)
            changed = True
    return adapted if changed else None


def _explicitly_rejects_parameter(exc: Exception, parameter: str) -> bool:
    """Whether an API error explicitly rejects one request parameter.

    OpenAI SDK errors expose ``param``/``code`` on some versions and only an
    embedded response body on others. OpenAI-compatible gateways often expose
    neither, so the message fallback remains intentionally narrow: the field
    name and an explicit unsupported marker must both be present.
    """
    return _rejection_confidence(exc, parameter) is not None


# (base_url, model) -> optional-param adaptations that endpoint has already
# explicitly rejected in this process. Providers/models are few, so this
# stays tiny; process-lifetime staleness is fine — the worst case after a
# server-side change is one conservative omission, never a failure.
_PARAM_ADAPTATION_CACHE: dict[tuple[str, str], set[str]] = {}


def _apply_adaptation(kwargs: dict[str, Any], field: str) -> dict[str, Any] | None:
    """Apply one remembered adaptation to *kwargs*; ``None`` when moot."""
    if field == "max_tokens":
        if "max_tokens" not in kwargs:
            return None
        adapted = dict(kwargs)
        adapted["max_completion_tokens"] = adapted.pop("max_tokens")
        return adapted
    if field == "reasoning_effort:minimal":
        if kwargs.get("reasoning_effort") != "none":
            return None
        adapted = dict(kwargs)
        adapted["reasoning_effort"] = "minimal"
        return adapted
    if field == "reasoning:mandatory":
        return _without_reasoning_opt_out(kwargs)
    if field in kwargs:
        adapted = dict(kwargs)
        adapted.pop(field, None)
        return adapted
    return None


def _compatible_retry_kwargs(
    exc: Exception,
    kwargs: dict[str, Any],
    adaptations: set[str],
) -> tuple[dict[str, Any], str, bool] | None:
    """Build one safe retry after an explicit model/API capability rejection.

    Returns ``(retry_kwargs, field, cacheable)``. ``cacheable`` marks
    adaptations safe to remember per (endpoint, model) for the process:
    only STRUCTURED rejections qualify — a substring-matched message may
    drive this one retry but never the cache — and a value-level
    ``temperature`` complaint qualifies only when the model declares itself
    default-only ("Only the default (1) value is supported"); an arbitrary
    out-of-range value must not evict the knob for every later call.
    """
    message = str(exc).lower()
    if "max_tokens" in kwargs and "max_tokens" not in adaptations:
        confidence = _rejection_confidence(exc, "max_tokens")
        if "max_completion_tokens" in message or confidence is not None:
            retry_kwargs = dict(kwargs)
            retry_kwargs["max_completion_tokens"] = retry_kwargs.pop("max_tokens")
            # The rename is also cacheable on the server's own "use
            # max_completion_tokens" hint — that wording is unambiguous.
            cacheable = confidence == "structured" or "max_completion_tokens" in message
            return retry_kwargs, "max_tokens", cacheable

    # Sampling is optional. If a model accepts only its own default, omission
    # is the capability-safe fallback and preserves the provider's chosen value.
    if "temperature" in kwargs and "temperature" not in adaptations:
        confidence = _rejection_confidence(exc, "temperature")
        if confidence is not None:
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("temperature", None)
            _, code, _ = _error_metadata(exc)
            default_only = "only the default" in message
            cacheable = confidence == "structured" and (
                code == "unsupported_parameter" or default_only
            )
            return retry_kwargs, "temperature", cacheable

    # An endpoint that REQUIRES reasoning refuses the OFF value without ever
    # calling any parameter "unsupported", so the graded degradation below
    # cannot see it. Checked first, and it clears BOTH spellings of "off" at
    # once: recovering one at a time re-sends the other and earns the same
    # rejection. Cacheable on the message alone — "reasoning ... cannot be
    # disabled" is an unambiguous statement about the endpoint, in the same
    # class as the server's own "use max_completion_tokens" hint above.
    if "reasoning:mandatory" not in adaptations and _rejects_disabling_reasoning(exc):
        retry_kwargs = _without_reasoning_opt_out(kwargs)
        if retry_kwargs is not None:
            return retry_kwargs, "reasoning:mandatory", True

    # Reasoning effort degrades in two steps: a model that knows the knob but
    # not the value "none" (o-series, gpt-5 base) still honors "minimal" —
    # keeping the thought budget capped, which is the caller's whole intent —
    # and only a model that rejects the parameter itself loses the cap.
    if kwargs.get("reasoning_effort") is not None:
        confidence = _rejection_confidence(exc, "reasoning_effort")
        if confidence is not None:
            cacheable = confidence == "structured"
            if (
                kwargs["reasoning_effort"] == "none"
                and "reasoning_effort:minimal" not in adaptations
            ):
                retry_kwargs = dict(kwargs)
                retry_kwargs["reasoning_effort"] = "minimal"
                return retry_kwargs, "reasoning_effort:minimal", cacheable
            if "reasoning_effort" not in adaptations:
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop("reasoning_effort", None)
                return retry_kwargs, "reasoning_effort", cacheable

    # Inline usage accounting is optional and not implemented by every
    # OpenAI-compatible server even when the installed SDK accepts the kwarg.
    if "stream_options" in kwargs and "stream_options" not in adaptations:
        confidence = _rejection_confidence(exc, "stream_options")
        if confidence is not None:
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("stream_options", None)
            return retry_kwargs, "stream_options", confidence == "structured"
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
    # Remember which optional params one (endpoint, model) pair has already
    # rejected, so a tool loop does not pay the same rejection round-trip on
    # every single step (the CU path calls this dozens of times per mission;
    # an xAI/NIM model that rejects ``reasoning_effort`` would otherwise eat
    # up to two extra HTTP 400s per step, and ``max_tokens``-renaming models
    # one). Keyed by base_url+model — never by provider name (AP-21).
    cache_key = (
        str(getattr(client, "base_url", "") or ""),
        str(kwargs.get("model", "") or ""),
    )
    current_kwargs = dict(kwargs)
    adaptations: set[str] = set()
    for field in _PARAM_ADAPTATION_CACHE.get(cache_key, ()):  # noqa: B007
        adapted = _apply_adaptation(current_kwargs, field)
        if adapted is not None:
            current_kwargs = adapted
            adaptations.add(field)
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
            current_kwargs, field, cacheable = retry
            adaptations.add(field)
            if cacheable:
                _PARAM_ADAPTATION_CACHE.setdefault(cache_key, set()).add(field)
            if field == "max_tokens":
                log.info(
                    "provider rejected 'max_tokens' — retrying with "
                    "'max_completion_tokens'."
                )
            elif field == "reasoning_effort:minimal":
                log.info(
                    "provider rejected reasoning_effort='none' — retrying "
                    "with the lowest supported effort 'minimal'."
                )
            elif field == "reasoning:mandatory":
                log.info(
                    "endpoint requires internal reasoning — retrying without "
                    "the opt-out; this model cannot serve the low-latency hint."
                )
            else:
                log.info(
                    "provider rejected optional '%s' — retrying without it.",
                    field,
                )
