"""Unit-Tests für den StreamingAggregate-Akkumulator."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from jarvis.brain import aggregate, tee_text
from jarvis.brain.streaming import aggregate_first_json
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


# ---------------------------------------------------------------------------
# aggregate_first_json — early-stop the moment a complete JSON action is seen.
# Computer-Use action calls return a small JSON object/array; waiting for the
# whole stream (finish_reason) wastes the tail latency on the #1 cost path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_first_json_early_stops_on_complete_object():
    """Closes the stream the instant a complete top-level object is parseable."""
    consumed: list[str] = []

    async def _src() -> AsyncIterator[BrainDelta]:
        consumed.append("a"); yield BrainDelta(content='{"action": ')
        consumed.append("b"); yield BrainDelta(content='"done"}')
        consumed.append("c"); yield BrainDelta(content=" extra tokens the model rambled on")
        consumed.append("d"); yield BrainDelta(finish_reason="stop")

    agg = await aggregate_first_json(_src())
    assert agg.text == '{"action": "done"}'
    assert consumed == ["a", "b"]  # closed right after the complete object


@pytest.mark.asyncio
async def test_aggregate_first_json_early_stops_on_complete_array():
    """A batch (JSON array) closes only at its terminal ']'."""
    consumed: list[str] = []

    async def _src() -> AsyncIterator[BrainDelta]:
        consumed.append("a"); yield BrainDelta(content='[{"action":"click","x":1,"y":2},')
        consumed.append("b"); yield BrainDelta(content='{"action":"wait","ms":100}]')
        consumed.append("c"); yield BrainDelta(content="\n```")

    agg = await aggregate_first_json(_src())
    assert agg.text.endswith("]")
    assert consumed == ["a", "b"]


@pytest.mark.asyncio
async def test_aggregate_first_json_ignores_brackets_inside_strings():
    """A '}' or ']' inside a string value must not be read as the end."""
    consumed: list[str] = []

    async def _src() -> AsyncIterator[BrainDelta]:
        consumed.append("a"); yield BrainDelta(content='{"text": "a}b]c')
        consumed.append("b"); yield BrainDelta(content='d", "action": "type"}')
        consumed.append("c"); yield BrainDelta(content=" trailing")

    agg = await aggregate_first_json(_src())
    assert agg.text == '{"text": "a}b]cd", "action": "type"}'
    assert consumed == ["a", "b"]


@pytest.mark.asyncio
async def test_aggregate_first_json_handles_fence_prefix():
    """A leading ```json fence before the object is tolerated (scan starts at '{')."""
    consumed: list[str] = []

    async def _src() -> AsyncIterator[BrainDelta]:
        consumed.append("a"); yield BrainDelta(content='```json\n{"action":"wait",')
        consumed.append("b"); yield BrainDelta(content='"ms":50}\n```')
        consumed.append("c"); yield BrainDelta(content="should not be read")

    agg = await aggregate_first_json(_src())
    assert '"ms":50}' in agg.text
    assert consumed == ["a", "b"]


@pytest.mark.asyncio
async def test_aggregate_first_json_falls_back_to_full_stream_for_prose():
    """No complete JSON -> behaves exactly like aggregate (reads the whole stream)."""
    consumed: list[str] = []

    async def _src() -> AsyncIterator[BrainDelta]:
        consumed.append("a"); yield BrainDelta(content="I cannot ")
        consumed.append("b"); yield BrainDelta(content="do that.")
        consumed.append("c"); yield BrainDelta(finish_reason="stop")

    agg = await aggregate_first_json(_src())
    assert agg.text == "I cannot do that."
    assert agg.finish_reason == "stop"
    assert consumed == ["a", "b", "c"]


def test_has_complete_json_action_caps_scan_on_oversized_text():
    """Defensive bound: above a generous size cap (far over the action/planner
    max_tokens), the scan is skipped (returns False -> the full aggregate takes
    over) so a misbehaving provider streaming prose before the JSON cannot make
    the per-delta scan quadratic. A normal-size action still early-stops."""
    from jarvis.brain.streaming import _has_complete_json_action

    assert _has_complete_json_action('{"action": "done"}') is True
    # A *valid* but absurdly large object (>> any real 256/512-token response):
    # not early-stopped, degrades safely to the full-stream aggregate.
    oversized = '{"action":"type","text":"' + ("x" * 20000) + '"}'
    assert _has_complete_json_action(oversized) is False


@pytest.mark.asyncio
async def test_aggregate_first_json_does_not_stop_on_empty_object_braces():
    """'{}' is balanced but not a usable action; an empty {} alone is still
    returned (parseable JSON) — the action parser rejects it downstream. This
    documents that the early-stop fires on the first *parseable* top-level JSON."""
    consumed: list[str] = []

    async def _src() -> AsyncIterator[BrainDelta]:
        consumed.append("a"); yield BrainDelta(content="{}")
        consumed.append("b"); yield BrainDelta(content="more")

    agg = await aggregate_first_json(_src())
    assert agg.text == "{}"
    assert consumed == ["a"]
