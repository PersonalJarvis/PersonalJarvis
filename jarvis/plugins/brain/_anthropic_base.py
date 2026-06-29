"""Gemeinsame Anthropic-Logik für claude-api & claude-api.

Die beiden Provider unterscheiden sich fast nur in den Key-Quellen. Alles
Streaming + Tool-Use ist identisch (Anthropic-API-Format).
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest

# Reuse the tested tool-name sanitizer/map (regex [^A-Za-z0-9_-] + dedup). Its
# 64-char cap is stricter than Anthropic's 128 but still valid, so a slash/dot/
# colon MCP name (jarvis/mcp/adapter.py) no longer trips Anthropic's
# ``tools.N.custom.name`` 400 on the direct claude-api path.
from ._openai_base import _openai_tool_name_map

# Latenz-Sprint-2: Beta-Header fuer 1h-Cache-TTL. Der Default ist 5 min;
# 1h verlaengert die effektive Cache-Dauer und schluckt mehr Voice-Sessions.
# Konstante zentral, damit beide Provider-Klassen denselben Header setzen.
_ANTHROPIC_CACHE_TTL_BETA = "extended-cache-ttl-2025-04-11"

# ENV-Switch fuer Sprint-2 Caching. Wird vom BrainManager gesetzt, wenn
# ``[performance].anthropic_prompt_cache = true``. Bei "1" wird ``cache_control``
# auf System-Prompt + letztem Tool-Schema gesetzt + Beta-Header angefordert.
_ENV_PROMPT_CACHE = "JARVIS_ANTHROPIC_PROMPT_CACHE"


def _to_anthropic_messages(messages: tuple[BrainMessage, ...]) -> list[dict[str, Any]]:
    """BrainMessages → Anthropic API-Messages-Array.

    Anthropic unterstützt Rollen: "user", "assistant". "system" geht separat,
    "tool" wird als "user"-Message mit tool_result-Block.

    Multimodal: `BrainMessage.images` wird für user-Messages als
    `{"type": "image", "source": {"type": "base64", ...}}`-Blöcke angehängt.
    Backwards-Compat: Ohne images bleibt String-content ein String.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.role
        content = m.content

        if role == "system":
            continue  # system wird extern als `system`-Parameter übergeben

        if role == "tool":
            # Tool-Result wird als user-Message mit tool_result-Content
            out.append({
                "role": "user",
                "content": content if isinstance(content, list) else [
                    {"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": str(content)}
                ],
            })
            continue

        # role: user | assistant — Multimodal nur für user (Anthropic akzeptiert
        # images nur dort; assistant-images sind nicht Teil der public API).
        # `getattr`-Fallback für Backwards-Compat falls BrainMessage noch kein
        # `images`-Attribut hat (Protocol-Version pre-Wave-1-B1).
        images = getattr(m, "images", ()) or ()
        has_images = role == "user" and bool(images)
        if has_images:
            content_blocks: list[dict[str, Any]] = []
            if isinstance(content, str):
                if content:
                    content_blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                # Bereits Blocks (z.B. tool_result-Passthrough) — übernehmen.
                content_blocks.extend(content)
            for img in images:
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.mime,
                        "data": img.data_b64,
                    },
                })
            out.append({"role": role, "content": content_blocks})
            continue

        # Kein Image — Legacy-Pfad 1:1 erhalten.
        if isinstance(content, str):
            out.append({"role": role, "content": content})
        else:
            out.append({"role": role, "content": content})
    return out


def _extract_system(messages: tuple[BrainMessage, ...], extra_system: str | None) -> str | None:
    """Sammelt alle role=system-Messages + zusätzlichen extra_system."""
    parts: list[str] = []
    for m in messages:
        if m.role == "system" and isinstance(m.content, str):
            parts.append(m.content)
    if extra_system:
        parts.append(extra_system)
    return "\n\n".join(parts) if parts else None


def _tools_anthropic_format(
    tools: tuple[dict[str, Any], ...],
    name_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Normalisiert Tool-Schemas auf Anthropic-Format (Namen sanitisiert)."""
    name_map = name_map if name_map is not None else _openai_tool_name_map(tools)
    out: list[dict[str, Any]] = []
    for t in tools:
        schema = t.get("input_schema") or t.get("parameters") or t.get("schema") or {}
        original = t.get("name", "")
        out.append({
            "name": name_map.get(original, original),
            "description": t.get("description", ""),
            "input_schema": schema if schema else {"type": "object", "properties": {}},
        })
    return out


def _is_reasoning_model(model: str) -> bool:
    """Claude Opus-4.x und Sonnet-4.x akzeptieren `temperature` nicht mehr."""
    m = (model or "").lower()
    return "opus-4" in m or "sonnet-4" in m or "haiku-4" in m


async def stream_complete(
    client: Any,
    model: str,
    req: BrainRequest,
) -> AsyncIterator[BrainDelta]:
    """Führt ein streamendes messages.create aus und yielded BrainDeltas."""
    messages = _to_anthropic_messages(req.messages)
    system = _extract_system(req.messages, req.system)
    # Sanitize tool names + keep a reverse map so the inbound tool_use name maps
    # back to the ORIGINAL tool the executor knows (e.g. the "server/tool" MCP name).
    name_map = _openai_tool_name_map(req.tools) if req.tools else {}
    reverse_name_map = {safe: original for original, safe in name_map.items()}
    tools_payload = _tools_anthropic_format(req.tools, name_map) if req.tools else None

    # Latenz-Sprint-2: Prompt-Caching wenn aktiviert. Wandelt System-Prompt
    # in einen Block-Array mit ``cache_control`` und markiert das letzte
    # Tool-Schema als Cache-Boundary (Anthropic cached alles bis zum
    # markierten Block einschliesslich).
    prompt_cache_enabled = os.environ.get(_ENV_PROMPT_CACHE) == "1"
    extra_headers: dict[str, str] = {}
    system_payload: Any = system
    if prompt_cache_enabled and system:
        # System wird zu Block-Array, damit ``cache_control`` greift.
        system_payload = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ]
        extra_headers["anthropic-beta"] = _ANTHROPIC_CACHE_TTL_BETA
    if prompt_cache_enabled and tools_payload:
        # Letztes Tool als Cache-Boundary: alles davor (System + Tools)
        # wird gemeinsam gecached. Keine Aenderung am Tool-Inhalt.
        cached_tools = [dict(t) for t in tools_payload]
        cached_tools[-1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        tools_payload = cached_tools
        extra_headers.setdefault("anthropic-beta", _ANTHROPIC_CACHE_TTL_BETA)

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": req.max_tokens,
        "messages": messages,
    }
    # `temperature` ist auf Reasoning-Modellen (opus-4.x, sonnet-4.x) deprecated.
    # Nur für explizit-klassische Modelle senden; bei den neuen Defaults ist
    # temperature=1 ohnehin hardcoded im Backend.
    if not _is_reasoning_model(model):
        kwargs["temperature"] = req.temperature
    if system_payload:
        kwargs["system"] = system_payload
    if tools_payload:
        kwargs["tools"] = tools_payload
    if extra_headers:
        kwargs["extra_headers"] = extra_headers

    async with client.messages.stream(**kwargs) as stream:
        # Tool-Call-Akkumulator (Anthropic streamt tool_use als separate blocks)
        current_tool: dict[str, Any] | None = None
        current_tool_json = ""

        async for event in stream:
            etype = getattr(event, "type", None) or getattr(event, "event", None)

            # Text-Delta
            if etype == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta is None:
                    continue
                dtype = getattr(delta, "type", None)
                if dtype == "text_delta":
                    text = getattr(delta, "text", "") or ""
                    if text:
                        yield BrainDelta(content=text)
                elif dtype == "input_json_delta":
                    if current_tool is not None:
                        partial = getattr(delta, "partial_json", "") or ""
                        current_tool_json += partial

            # Tool-Use-Block start
            elif etype == "content_block_start":
                block = getattr(event, "content_block", None)
                if block is not None and getattr(block, "type", None) == "tool_use":
                    _raw_name = getattr(block, "name", "")
                    current_tool = {
                        "id": getattr(block, "id", ""),
                        "name": reverse_name_map.get(_raw_name, _raw_name),
                    }
                    current_tool_json = ""

            # Tool-Use-Block ende
            elif etype == "content_block_stop":
                if current_tool is not None:
                    try:
                        parsed = json.loads(current_tool_json) if current_tool_json else {}
                    except json.JSONDecodeError:
                        parsed = {}
                    current_tool["input"] = parsed
                    yield BrainDelta(tool_call=current_tool)
                    current_tool = None
                    current_tool_json = ""

            # Message-Ende mit Usage
            elif etype == "message_delta":
                delta = getattr(event, "delta", None)
                finish = getattr(delta, "stop_reason", None) if delta else None
                usage = getattr(event, "usage", None)
                usage_d: dict[str, int] = {}
                if usage is not None:
                    usage_d = {
                        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                        "cache_read_input_tokens": int(
                            getattr(usage, "cache_read_input_tokens", 0) or 0),
                    }
                yield BrainDelta(finish_reason=finish, usage=usage_d or None)
