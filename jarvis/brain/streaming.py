"""Streaming utilities: accumulator for BrainDelta streams.

Brain responses arrive as AsyncIterator[BrainDelta]. For
(a) downstream logging and (b) the "simple" `__call__(text)->str` adapter
we need an accumulator that collects text + tool-calls + usage.
"""
from __future__ import annotations

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


async def aggregate_with_consumer(
    stream: AsyncIterator[BrainDelta],
    text_consumer: Callable[[str], None] | None,
) -> StreamingAggregate:
    """Like ``aggregate`` — but emits every text chunk live to ``text_consumer``.

    Latency-sprint-1: enables sentence-streaming TTS in the speech pipeline.
    The aggregate is still collected in full (for history, cost-tracking,
    tool-use loop). ``text_consumer`` is synchronous — for long-running consumers
    please use ``asyncio.Queue.put_nowait`` instead.
    """
    agg = StreamingAggregate()
    async for delta in stream:
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
    "aggregate_with_consumer",
    "tee_text",
    "is_length_truncated",
]
