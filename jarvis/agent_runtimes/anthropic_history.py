"""Preserve signed Claude tool continuations across an OpenAI-shaped runtime.

Entries are scoped to the runtime grant and model, and replayed only against
the unchanged request prefix and assistant content that produced them.
Nothing is persisted or sent as visible reasoning text to the runtime.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import asdict, replace
from typing import Any

_LOCK = threading.Lock()
_HISTORY: OrderedDict[
    tuple[Any, str, str], tuple[str, str, str, list[dict[str, Any]]]
] = OrderedDict()
_MAX_ENTRIES = 128


def _prefix(request: Any, messages: Any) -> str:
    encoded = json.dumps({"system": request.system, "tools": request.tools,
                          "messages": [asdict(m) for m in messages]},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _visible(blocks: list[dict[str, Any]]) -> str:
    # Chat Completions folds text and tools into separate fields.
    return json.dumps({
        "text": "".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text"),
        "tools": [{k: b.get(k) for k in ("id", "name", "input")}
                  for b in blocks if b.get("type") == "tool_use"],
    }, sort_keys=True, separators=(",", ":"))


def remember(
    grant: Any, model: str, request: Any, call: dict[str, Any], *, provider_request: Any,
) -> None:
    blocks = call.get("_anthropic_blocks")
    if not isinstance(blocks, list) or not call.get("id"):
        return
    key = (grant, model, str(call["id"]))
    # Copy the provider envelope so later mutation cannot change signed data.
    copied = json.loads(json.dumps(blocks))
    entry = (
        _prefix(request, request.messages),
        _prefix(provider_request, provider_request.messages),
        _visible(copied), copied,
    )
    with _LOCK:
        _HISTORY[key] = entry
        _HISTORY.move_to_end(key)
        while len(_HISTORY) > _MAX_ENTRIES:
            _HISTORY.popitem(last=False)


def restore(grant: Any, model: str, request: Any) -> Any:
    messages = list(request.messages)
    for index, message in enumerate(request.messages):
        if message.role != "assistant" or not isinstance(message.content, list):
            continue
        ids = [str(b.get("id")) for b in message.content if b.get("type") == "tool_use"]
        with _LOCK:
            entry = next((_HISTORY[(grant, model, call_id)] for call_id in ids
                          if (grant, model, call_id) in _HISTORY), None)
        if entry is None:
            continue
        prefix, provider_prefix, visible, blocks = entry
        if (
            prefix != _prefix(request, request.messages[:index])
            or provider_prefix != _prefix(request, messages[:index])
            or visible != _visible(message.content)
        ):
            # Edited/compacted history must never receive another prefix's
            # signature. Start a fresh Claude turn using the runtime's history.
            continue
        messages[index] = replace(message, content=json.loads(json.dumps(blocks)))
    return replace(request, messages=tuple(messages))


def reset() -> None:
    with _LOCK:
        _HISTORY.clear()
