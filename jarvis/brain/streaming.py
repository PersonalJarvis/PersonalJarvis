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
