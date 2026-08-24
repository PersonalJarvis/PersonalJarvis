"""search_web's answer instruction follows the turn's delivery.

The spoken instruction ("one or two SPOKEN sentences, never name a
title/url/source/date") is right for voice and wrong for a scheduled written
digest — every search-based automation fought it. A turn that declares
``ExecutionContext.config["delivery"] == "written"`` gets the written
variant; every other context (including one without the key) keeps the
spoken one. Also pins that the dispatcher → loop path actually delivers the
``tool_context`` keys into the ExecutionContext.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool import search_web as sw


def _ctx(config: dict[str, Any] | None) -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(), user_utterance="q", config=config or {}, memory_read=None,
    )


def test_spoken_by_default() -> None:
    assert sw._answer_instruction_for(_ctx({})) is sw._ANSWER_INSTRUCTION
    assert sw._answer_instruction_for(_ctx({"delivery": "spoken"})) is sw._ANSWER_INSTRUCTION


def test_written_delivery_gets_written_instruction() -> None:
    text = sw._answer_instruction_for(_ctx({"delivery": "written"}))
    assert text is sw._WRITTEN_ANSWER_INSTRUCTION
    assert "SPOKEN" not in text
    assert "source" in text.lower()


def test_foreign_context_shape_degrades_to_spoken() -> None:
    class _Odd:
        config = None

    assert sw._answer_instruction_for(_Odd()) is sw._ANSWER_INSTRUCTION  # type: ignore[arg-type]


def test_tool_context_reaches_execution_context() -> None:
    """ToolUseLoop merges the dispatcher's tool_context under the per-turn keys."""
    from jarvis.brain.tool_use_loop import ToolUseLoop

    loop = ToolUseLoop(
        object(), {}, object(),  # type: ignore[arg-type]
        tool_context={"delivery": "written", "output_language": "must-lose"},
    )
    merged = {**loop._tool_context, "output_language": "de", "voice_confirm": False}
    assert merged == {"delivery": "written", "output_language": "de", "voice_confirm": False}
