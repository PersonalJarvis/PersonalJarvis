"""Unit-Tests für den StreamingAggregate-Akkumulator."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from jarvis.brain import aggregate, tee_text
from jarvis.core.protocols import BrainDelta


async def _iter(deltas: list[BrainDelta]) -> AsyncIterator[BrainDelta]:
    for d in deltas:
        yield d


@pytest.mark.asyncio
async def test_aggregate_concatenates_text():
    stream = _iter([
        BrainDelta(content="Hallo "),
        BrainDelta(content="Welt"),
        BrainDelta(finish_reason="stop"),
    ])
    agg = await aggregate(stream)
    assert agg.text == "Hallo Welt"
    assert agg.finish_reason == "stop"


@pytest.mark.asyncio
async def test_aggregate_collects_tool_calls():
    stream = _iter([
        BrainDelta(tool_call={"id": "c1", "name": "t", "input": {"x": 1}}),
        BrainDelta(tool_call={"id": "c2", "name": "t", "input": {"x": 2}}),
    ])
    agg = await aggregate(stream)
    assert len(agg.tool_calls) == 2
    assert agg.tool_calls[0]["id"] == "c1"


@pytest.mark.asyncio
async def test_aggregate_sums_usage():
    stream = _iter([
        BrainDelta(usage={"input_tokens": 10, "output_tokens": 5}),
        BrainDelta(usage={"input_tokens": 0, "output_tokens": 3}),
    ])
    agg = await aggregate(stream)
    assert agg.usage["input_tokens"] == 10
    assert agg.usage["output_tokens"] == 8


@pytest.mark.asyncio
async def test_tee_text_only_yields_content():
    stream = _iter([
        BrainDelta(content="A"),
        BrainDelta(tool_call={"id": "c", "name": "t", "input": {}}),
        BrainDelta(content="B"),
    ])
    out = [t async for t in tee_text(stream)]
    assert out == ["A", "B"]
