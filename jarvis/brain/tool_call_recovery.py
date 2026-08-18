"""Provider-neutral recovery for function calls emitted as response text.

Some model transports occasionally serialize a function call into the text
stream instead of returning a structured tool-call delta. This module parses
only explicit tool-call envelopes; arbitrary JSON is never treated as an
action. Recovered calls still pass through ToolUseLoop and ToolExecutor.

The envelope does not have to be the whole response. A model that writes
"I'm opening Spotify. {"type":"tool_use",...}" used to execute NOTHING: the
whole-text ``json.loads`` failed, the call was dropped, and the turn ended as
plain text — the maintainer's "nothing happens" (audit GT-13). Envelopes are
now located by balanced-brace scanning, so prose before, after, or on both
sides of the call no longer defeats the recovery.

Widening WHERE we look must not widen WHAT counts as a call. An answer that
merely QUOTES a tool call ("For example, a provider might emit {...}") must
never fire it: a missed recovery costs one answer, a wrongly executed call is
a real side effect. Four gates keep the two apart — see
:func:`extract_leaked_tool_calls`.
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any

_CALL_MARKERS = ("tool_use", "function_call", "tool_calls", '"function"')
_MISSING = object()

# A fence marker (``` optionally followed by a language tag). Masked out before
# the prose is judged, so a fenced envelope is not mistaken for a quoted one.
_FENCE_RE = re.compile(r"```[A-Za-z0-9_+-]*")
# Only a plain or explicitly JSON fence may carry an executable envelope. A
# ```python / ```tool_code block is documentation about a call, not the call.
_ALLOWED_FENCE_TAGS = frozenset({"", "json"})
# Quote characters around an envelope mark it as CITED text, not an action.
_QUOTE_CHARS = ("`", '"', "'", "„", "“", "”", "‚", "‘", "’", "«", "»")

# Prose that DESCRIBES a call instead of performing one. Deliberately broad:
# every marker here costs at most one missed recovery, while a miss in the
# other direction executes something the user never asked for. Covers the
# three product locales plus the vocabulary of an answer *about* tool calls.
_EXAMPLE_MARKER_RE = re.compile(
    r"(?:"
    r"\be\.\s?g\.|\bi\.\s?e\.|\bz\.\s?b\.|\bp\.\s?ej\.|"
    r"\b(?:"
    # EN
    r"example|examples|sample|samples|for instance|such as|pseudo|"
    r"looks?\s+like|would\s+look|might|would|template|schema|format|syntax|"
    r"illustrat\w*|json|tool[\s_-]?use|tool\s+calls?|function\s+calls?|"
    # DE
    r"beispiel\w*|etwa\s+so|so\s+aus|vorlage|schema|format|syntax|"
    r"werkzeugaufruf\w*|"
    # ES
    r"ejemplo\w*|formato|esquema|plantilla|sintaxis"
    r")\b"
    r")",
    re.IGNORECASE,
)
# A leak announces an action in a sentence or two ("Ich öffne Spotify.").
# Paragraphs of prose around an envelope are an explanation that happens to
# contain JSON, so the recovery stands down.
_MAX_SURROUNDING_PROSE_CHARS = 240


def _mask_fences(text: str) -> str:
    """Blank out fence markers, preserving every character offset.

    The masked copy is what the quote and prose gates read: a fenced envelope
    must not look "quoted" just because a fence ends in a backtick.
    """
    return _FENCE_RE.sub(lambda m: " " * len(m.group(0)), text)


def _fence_tags(text: str) -> list[str]:
    return [match.group(0).lstrip("`").lower() for match in _FENCE_RE.finditer(text)]


def _json_spans(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` of every balanced top-level JSON object/array.

    Brace counting, not a parser: string literals and backslash escapes are
    tracked so a ``}`` inside a JSON string never closes a span. An opener
    that is never closed (a truncated payload) yields no span at all.
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if depth == 0:
            if char in "{[":
                depth = 1
                start = index
                in_string = False
                escaped = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                spans.append((start, index + 1))
    return spans


def _is_quoted(masked: str, start: int, end: int) -> bool:
    """True when the span sits inside quotation marks or an inline code span."""
    before = masked[:start].rstrip()
    after = masked[end:].lstrip()
    return before.endswith(_QUOTE_CHARS) or after.startswith(_QUOTE_CHARS)


def _surrounding_prose(masked: str, spans: list[tuple[int, int]]) -> str:
    """Everything outside the executed envelopes, whitespace-collapsed."""
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(masked[cursor:start])
        cursor = end
    parts.append(masked[cursor:])
    return " ".join("".join(parts).split())


def _decode_arguments(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            # Silence is the decision: arguments we cannot decode mean we do
            # not know what the call would do, so the envelope is refused
            # rather than guessed at. The caller sees "no call found".
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


def _call_identity(call: dict[str, Any]) -> str:
    """Name + arguments — the same action, however the provider labelled it."""
    return json.dumps(
        [call["name"], call["input"]],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def extract_leaked_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse explicit text-serialized tool calls into normal loop call records.

    An envelope may sit anywhere in the response — alone, after prose, before
    prose, between two sentences, or inside a ```json fence. Every envelope
    found in the text is returned, in document order, with identical calls
    collapsed to one.

    Four gates separate an intended call from a quoted sample. All of them
    fail closed, i.e. they return ``[]`` for the WHOLE text rather than
    executing a subset:

    1. Shape — unchanged: only an explicit ``tool_use`` / ``function_call`` /
       ``function`` envelope with a non-empty name and object arguments.
    2. Fences — an unbalanced ``` count means a truncated payload, and a
       language tag other than ``json`` means documentation about a call.
    3. Quoting — an envelope wrapped in quotation marks or inline backticks is
       being cited, not invoked.
    4. Prose — the text outside the envelopes must be short and free of
       example vocabulary ("for example", "z. B.", "schema", "json", …).
    """
    if not text or not any(marker in text.lower() for marker in _CALL_MARKERS):
        return []

    masked = _mask_fences(text)
    tags = _fence_tags(text)
    if len(tags) % 2 or any(tag not in _ALLOWED_FENCE_TAGS for tag in tags):
        return []

    calls: list[dict[str, Any]] = []
    consumed: list[tuple[int, int]] = []
    for start, end in _json_spans(masked):
        try:
            candidate = json.loads(text[start:end])
        except (json.JSONDecodeError, ValueError):
            # Expected on ordinary prose: balanced braces are not proof of
            # JSON (a smiley, a Python repr with single quotes). Nothing to
            # report — this span is simply not an envelope.
            continue
        found = _calls_from_candidate(candidate)
        if not found:
            continue
        if _is_quoted(masked, start, end):
            return []
        calls.extend(found)
        consumed.append((start, end))

    if not calls:
        return []

    prose = _surrounding_prose(masked, consumed)
    if len(prose) > _MAX_SURROUNDING_PROSE_CHARS:
        return []
    if _EXAMPLE_MARKER_RE.search(prose):
        return []

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for call in calls:
        identity = _call_identity(call)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(call)
    return unique


__all__ = ["extract_leaked_tool_calls"]
