"""Streaming utilities: accumulator for BrainDelta streams.

Brain responses arrive as AsyncIterator[BrainDelta]. For
(a) downstream logging and (b) the "simple" `__call__(text)->str` adapter
we need an accumulator that collects text + tool-calls + usage.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.protocols import BrainDelta


@dataclass
class StreamingAggregate:
    """Accumulated brain stream."""
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    #: Names of tools that ACTUALLY EXECUTED successfully this turn (populated by
    #: the tool-use loop only inside its execute branch, i.e. NOT for tool calls
    #: blocked by a guard or for an unknown tool). Distinct from ``tool_calls``,
    #: which holds every tool the model REQUESTED. Consumers that need to know a
    #: side effect really landed (e.g. the voice pipeline's "speak a confirmation
    #: instead of a clarifying question after a wordless desktop action" net)
    #: must read this, never ``tool_calls`` (2026-06-09).
    executed_tool_names: set[str] = field(default_factory=set)
    #: Set when a consequential tool was DEFERRED for two-turn voice/chat
    #: confirmation (the executor returned ``VOICE_CONFIRM_SENTINEL``). Carries
    #: ``{"trace_id": str, "tool_name": str}`` so the BrainManager can resume the
    #: stashed action on the user's next "ja". ``None`` on every normal turn.
    voice_confirm: dict[str, Any] | None = None


async def aggregate(stream: AsyncIterator[BrainDelta]) -> StreamingAggregate:
    """Consumes the complete stream and returns the aggregated result."""
    agg = StreamingAggregate()
    async for delta in stream:
        if delta.content:
            agg.text += delta.content
        if delta.tool_call:
            agg.tool_calls.append(dict(delta.tool_call))
        if delta.finish_reason:
            agg.finish_reason = delta.finish_reason
        if delta.usage:
            for k, v in delta.usage.items():
                agg.usage[k] = agg.usage.get(k, 0) + int(v)
    return agg


#: Defensive ceiling on the early-stop scan. A CU action / planner response is
#: bounded by max_tokens (256 / 512 -> a few KB), so this is far above any real
#: payload; above it we skip the scan (degrade to the full aggregate) so a
#: misbehaving provider streaming prose before the JSON cannot make the
#: per-delta scan quadratic.
_MAX_JSON_SCAN_CHARS = 16_384


def _has_complete_json_action(text: str) -> bool:
    """True when ``text`` already contains a complete, parseable top-level JSON
    object or array (an early-stop boundary for a Computer-Use action call).

    Scans from the first structural opener (``{`` or ``[``), tolerating any
    leading ```` ```json ```` fence or prose, and tracks bracket depth while
    respecting string literals and escapes — so a ``}`` or ``]`` *inside* a
    string value is never mistaken for the end. The candidate span is then
    verified with ``json.loads`` so a balanced-but-invalid prefix can never
    trigger a premature stop. Deterministic, no LLM call.

    Returns ``False`` (no early stop) for text above ``_MAX_JSON_SCAN_CHARS`` —
    the full aggregate then completes the read. Safe because that ceiling is far
    above any real action/planner payload.
    """
    if len(text) > _MAX_JSON_SCAN_CHARS:
        return False
    start = -1
    for i, ch in enumerate(text):
        if ch == "{" or ch == "[":
            start = i
            break
    if start < 0:
        return False
    depth = 0
    in_str = False
    escape = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{" or ch == "[":
            depth += 1
        elif ch == "}" or ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return False
    try:
        json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return False
    return True


# Public name: the CU dispatch uses this to decide whether a length-capped
# reply still holds a usable JSON payload (truncation-retry gate).
def has_complete_json_action(text: str) -> bool:
    """Public wrapper around :func:`_has_complete_json_action`."""
    return _has_complete_json_action(text)


async def _aclose_quietly(stream: AsyncIterator[BrainDelta]) -> None:
    """Best-effort close of an async stream after an early stop, so the
    provider connection/generator is released. Never raises."""
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:  # noqa: BLE001, S110 — closing must not surface upstream
        pass


async def aggregate_first_json(
    stream: AsyncIterator[BrainDelta],
) -> StreamingAggregate:
    """Like :func:`aggregate`, but stops the moment the accumulated text holds a
    complete top-level JSON object/array — then closes the stream.

    Computer-Use action/plan calls return a small JSON action; waiting for the
    provider's terminal ``finish_reason`` (or a rambling tail up to ``max_tokens``)
    is pure latency on the loop's #1 cost path (THINK). This consumes deltas
    identically to ``aggregate`` until the first *parseable* JSON boundary, so the
    returned ``text`` is byte-identical to what ``aggregate`` would have collected
    up to that point, and the downstream action parser sees the same payload.

    Provider-agnostic (every provider yields ``BrainDelta``), deterministic, no
    LLM call. If the stream never produces a complete JSON (e.g. a prose refusal),
    it falls back to draining the whole stream exactly like ``aggregate``.

    Note: a terminal ``finish_reason``/``usage`` delta that arrives *after* the
    JSON is intentionally not awaited — callers that need usage/cost telemetry on
    the full generation must use ``aggregate`` instead. The CU action path reads
    only ``text``.
    """
    agg = StreamingAggregate()
    async for delta in stream:
        if delta.content:
            agg.text += delta.content
        if delta.tool_call:
            agg.tool_calls.append(dict(delta.tool_call))
        if delta.finish_reason:
            agg.finish_reason = delta.finish_reason
        if delta.usage:
            for k, v in delta.usage.items():
                agg.usage[k] = agg.usage.get(k, 0) + int(v)
        # Only run the O(n) scan when this chunk could have *closed* a structure.
        if delta.content and ("}" in delta.content or "]" in delta.content):
            if _has_complete_json_action(agg.text):
                await _aclose_quietly(stream)
                break
    return agg


async def aggregate_with_consumer(
    stream: AsyncIterator[BrainDelta],
    text_consumer: Callable[[str], None] | None,
    *,
    delta_consumer: Callable[[BrainDelta], None] | None = None,
) -> StreamingAggregate:
    """Like ``aggregate`` — but emits every text chunk live to ``text_consumer``.

    Latency-sprint-1: enables sentence-streaming TTS in the speech pipeline.
    The aggregate is still collected in full (for history, cost-tracking,
    tool-use loop). ``text_consumer`` is synchronous — for long-running consumers
    please use ``asyncio.Queue.put_nowait`` instead.
    """
    agg = StreamingAggregate()
    async for delta in stream:
        if delta_consumer is not None:
            try:
                delta_consumer(delta)
            except Exception:  # noqa: BLE001
                pass
        if delta.content:
            agg.text += delta.content
            if text_consumer is not None:
                try:
                    text_consumer(delta.content)
                except Exception:  # noqa: BLE001 — do not propagate consumer errors
                    pass
        if delta.tool_call:
            agg.tool_calls.append(dict(delta.tool_call))
        if delta.finish_reason:
            agg.finish_reason = delta.finish_reason
        if delta.usage:
            for k, v in delta.usage.items():
                agg.usage[k] = agg.usage.get(k, 0) + int(v)
    return agg


async def tee_text(stream: AsyncIterator[BrainDelta]) -> AsyncIterator[str]:
    """Yields each text chunk as it arrives.

    Useful for UI streaming (token-by-token rendering).
    """
    async for delta in stream:
        if delta.content:
            yield delta.content


# Provider-specific finish/stop-reason markers that mean "output was cut off
# because it hit the max-token cap" — NOT a natural stop. aggregate() does not
# normalise these, so we match every dialect by case-insensitive substring:
#   - Anthropic  stop_reason == "max_tokens"      (_anthropic_base.py)
#   - OpenAI/OpenRouter/Grok finish_reason == "length" (_openai_base.py)
#   - Gemini     str(finish_reason) in {"MAX_TOKENS", "FinishReason.MAX_TOKENS"} (gemini.py)
_LENGTH_FINISH_MARKERS: tuple[str, ...] = ("length", "max_tokens", "max-tokens")

# Characters a complete sentence/JSON payload may legitimately end on. Used only
# as a fallback when the provider surfaced no finish_reason at all (e.g. Codex,
# which hardcodes "stop", or a test/mock that omits the terminal delta).
_SENTENCE_FINAL = frozenset('.!?…")]}』」”’')


def is_length_truncated(finish_reason: str | None, text: str) -> bool:
    """Return True when a brain generation was cut off at the output-token cap.

    Two signals, primary then fallback:

    1. ``finish_reason`` matches a known max-token marker (any provider dialect,
       case-insensitive substring). This is authoritative when present.
    2. When ``finish_reason`` is falsy (provider did not surface one), fall back
       to a heuristic: non-empty prose that does NOT end on sentence-final
       punctuation is treated as truncated. Empty text is NOT truncated here —
       the caller handles "empty" separately.

    Deterministic, no LLM call (mirrors the scrub_for_voice latency mandate).
    """
    if finish_reason:
        lowered = finish_reason.lower()
        if any(marker in lowered for marker in _LENGTH_FINISH_MARKERS):
            return True
        # A real, non-length reason ("stop", "end_turn", "tool_use",
        # "stop_sequence", "STOP") means the model finished on its own terms.
        return False
    stripped = (text or "").strip()
    if not stripped:
        return False
    return stripped[-1] not in _SENTENCE_FINAL


__all__ = [
    "StreamingAggregate",
    "aggregate",
    "aggregate_first_json",
    "aggregate_with_consumer",
    "has_complete_json_action",
    "tee_text",
    "is_length_truncated",
]
