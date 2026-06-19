"""Gemeinsame Logik für OpenAI-kompatible APIs (openai / openrouter / grok).

Alle drei nutzen das Chat-Completions-Format. Unterschiede:
- Base-URL (api.openai.com / openrouter.ai / api.x.ai)
- Model-Namen-Namensraum
- Default-Headers (OpenRouter will X-Title, HTTP-Referer)
"""
from __future__ import annotations

import inspect
import json
import logging
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
    """One-shot Detection: kennt das installierte openai-SDK ``stream_options``?

    `stream_options` wurde in openai>=1.30 (Juni 2024) eingefuehrt — aeltere
    Versionen werfen ``TypeError: got unexpected keyword argument`` direkt
    beim Aufruf. Wir pruefen die Signatur einmalig zur Modul-Ladezeit und
    cachen das Ergebnis. Bei zukuenftigen API-Aenderungen ist der Re-Try-Pfad
    in ``run_openai_chat`` der Notanker.
    """
    try:
        from openai.resources.chat.completions import AsyncCompletions

        sig = inspect.signature(AsyncCompletions.create)
        return "stream_options" in sig.parameters
    except Exception:  # noqa: BLE001 — Detection darf den Import nicht killen
        return False


_STREAM_OPTIONS_SUPPORTED = _stream_options_supported()
if not _STREAM_OPTIONS_SUPPORTED:
    log.warning(
        "openai-SDK kennt 'stream_options' nicht — vermutlich openai<1.30. "
        "Provider laufen ohne Inline-Usage-Tracking. Empfehlung: pip install -U openai."
    )


def _to_openai_messages(
    messages: tuple[BrainMessage, ...],
    system_extra: str | None,
    *,
    supports_vision: bool = True,
) -> list[dict[str, Any]]:
    """BrainMessages → OpenAI-Chat-Completions-Array.

    Multimodal: `BrainMessage.images` wird für user-Messages als Data-URI
    im `image_url`-Content-Block enkodiert. Wenn der Ziel-Provider kein
    Vision-Support hat (`supports_vision=False`), werden images verworfen
    und einmalig pro Call geloggt.
    Backwards-Compat: Ohne images bleibt der Content ein plain String.
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
                "content": m.content if isinstance(m.content, str) else json.dumps(m.content, default=str),
                "tool_call_id": m.tool_call_id or "",
            })
            continue

        if m.role == "assistant" and isinstance(m.content, list):
            # Assistant mit Tool-Calls
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in m.content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    })
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
            continue

        # user | assistant (mit string content)
        # `getattr` für Backwards-Compat (Protocol pre-Wave-1-B1 hat kein images).
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
                    "Provider ohne Vision-Support — %d Image(s) werden verworfen.",
                    len(images),
                )
                vision_drop_warned = True
            # Fall through zum plain-text-Pfad (images gedroppt).

        text_content = (
            m.content
            if isinstance(m.content, str)
            else json.dumps(m.content, default=str)
        )
        out.append({"role": m.role, "content": text_content})
    return out


def _tools_openai_format(tools: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tools:
        schema = t.get("input_schema") or t.get("parameters") or t.get("schema") or {}
        if not schema:
            schema = {"type": "object", "properties": {}}
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
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
) -> AsyncIterator[BrainDelta]:
    """Streaming-Run gegen OpenAI-kompatible Chat-Completions.

    `supports_vision` wird an den Message-Builder durchgereicht — bei `False`
    werden `BrainMessage.images` verworfen + eine WARN geloggt.
    """
    messages = _to_openai_messages(req.messages, req.system, supports_vision=supports_vision)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
        "stream": True,
    }
    # stream_options gibts erst seit openai>=1.30. Auf alten SDKs (z.B. 1.10)
    # wuerde der unconditional-Aufruf einen TypeError werfen und die Plugin-
    # Kette mit "AsyncCompletions.create() got an unexpected keyword argument"
    # crashen — User hoert dann statt einer Antwort die "unerreichbar"-Diag.
    if _STREAM_OPTIONS_SUPPORTED:
        kwargs["stream_options"] = {"include_usage": True}
    if req.tools:
        kwargs["tools"] = _tools_openai_format(req.tools)
    if extra_body:
        kwargs.update(extra_body)

    # Akkumulator für Tool-Call-Partials (OpenAI streamt pro tool_call index)
    tool_buffer: dict[int, dict[str, Any]] = {}

    try:
        stream = await client.chat.completions.create(**kwargs)
    except TypeError as exc:
        # Belt-and-Suspenders: falls die Detection oben aus irgendeinem Grund
        # falsch lag (gemockte Tests, monkey-patched SDK, exotische Forks),
        # versuchen wir's nochmal ohne stream_options. Erspart einen harten
        # Fail wenn der Live-API einen unerwarteten Kwarg ablehnt.
        if "stream_options" not in kwargs or "stream_options" not in str(exc):
            raise
        log.warning(
            "openai-SDK lehnte 'stream_options' ab (%s) — Re-Try ohne Kwarg.",
            exc,
        )
        kwargs.pop("stream_options", None)
        stream = await client.chat.completions.create(**kwargs)
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
                # Tool-Calls abschließen wenn vorhanden
                for idx, buf in sorted(tool_buffer.items()):
                    try:
                        parsed = json.loads(buf["arguments"]) if buf["arguments"] else {}
                    except json.JSONDecodeError:
                        parsed = {}
                    yield BrainDelta(tool_call={
                        "id": buf["id"] or f"call_{idx}",
                        "name": buf["name"],
                        "input": parsed,
                    })
                tool_buffer.clear()
                yield BrainDelta(finish_reason=finish)

        # Usage-Info (OpenAI liefert das im letzten Chunk)
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            yield BrainDelta(usage={
                "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            })
