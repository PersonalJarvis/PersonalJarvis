"""Provider-neutral recovery for function calls emitted as response text.

Some model transports occasionally serialize a function call into the text
stream instead of returning a structured tool-call delta. This module parses
only explicit tool-call envelopes; arbitrary JSON is never treated as an
action. Recovered calls still pass through ToolUseLoop and ToolExecutor.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

_CALL_MARKERS = ("tool_use", "function_call", "tool_calls", '"function"')
_MISSING = object()


def _strip_json_fence(text: str) -> str | None:
    """Return an entire JSON response, optionally inside one strict fence."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return None
    if lines[0].strip().lower() not in {"```", "```json"}:
        return None
    return "\n".join(lines[1:-1]).strip()


def _decode_arguments(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
        return dict(decoded) if isinstance(decoded, dict) else None
    return None


def _normalize_call(block: Any) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None

    call: Any = block
    if isinstance(block.get("function_call"), dict):
        call = block["function_call"]
    elif isinstance(block.get("function"), dict):
        call = block["function"]

    if not isinstance(call, dict):
        return None
    call_type = str(block.get("type") or call.get("type") or "")
    explicitly_wrapped = (
        call is not block
        or call_type in {"tool_use", "function_call", "function"}
    )
    if not explicitly_wrapped:
        return None

    name = call.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    arguments = call.get("input", _MISSING)
    if arguments is _MISSING:
        arguments = call.get("arguments", call.get("args", _MISSING))
    decoded_arguments = (
        {} if arguments is _MISSING else _decode_arguments(arguments)
    )
    if decoded_arguments is None:
        return None
    fingerprint = json.dumps(
        [name.strip(), decoded_arguments],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return {
        "id": str(
            block.get("id")
            or call.get("id")
            or f"leaked_{sha256(fingerprint).hexdigest()[:12]}"
        ),
        "name": name.strip(),
        "input": decoded_arguments,
    }


def _calls_from_candidate(candidate: Any) -> list[dict[str, Any]]:
    blocks = candidate if isinstance(candidate, list) else [candidate]
    calls: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, dict) and isinstance(block.get("tool_calls"), list):
            for nested in block["tool_calls"]:
                normalized = _normalize_call(nested)
                if normalized is not None:
                    calls.append(normalized)
            continue
        normalized = _normalize_call(block)
        if normalized is not None:
            calls.append(normalized)
    return calls


def extract_leaked_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse explicit text-serialized tool calls into normal loop call records."""
    if not text or not any(marker in text.lower() for marker in _CALL_MARKERS):
        return []

    stripped = _strip_json_fence(text)
    if stripped is None:
        return []
    try:
        candidate = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        # Never execute quoted examples or JSON embedded in ordinary prose.
        return []
    return _calls_from_candidate(candidate)


__all__ = ["extract_leaked_tool_calls"]
