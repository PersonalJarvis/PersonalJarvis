"""Fake brain for tests: yields scripted BrainDeltas.

Two modes:
- `text_response`: text only, no tool call
- `tool_then_text`: tool call first, then text after the tool result

Implements the Brain protocol structurally (runtime_checkable isinstance checks
name/context_window/supports_tools/supports_vision/complete/estimate_cost).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core.protocols import BrainDelta, BrainRequest


class FakeBrain:
    """Fake brain provider for tests."""

    name: str = "fake-brain"
    context_window: int = 8192
    supports_tools: bool = True
    supports_vision: bool = False

    def __init__(
        self,
        script: list[list[BrainDelta]] | None = None,
        text_response: str = "Hallo Welt",
        fail_on_call: int = -1,
    ) -> None:
        """
        Args:
            script: List of turn responses. Each turn is a list of BrainDeltas.
                    For N tool-use rounds, the script needs N+1 turns.
            text_response: Default text when no script is given.
            fail_on_call: If >=0, raises RuntimeError on the Nth complete() call.
        """
        self._script = script or [[BrainDelta(content=text_response, finish_reason="stop")]]
        self._call_index = 0
        self._fail_on_call = fail_on_call
        self.calls: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls.append(req)
        if self._fail_on_call == self._call_index:
            self._call_index += 1
            raise RuntimeError("FakeBrain-scripted failure")
        idx = min(self._call_index, len(self._script) - 1)
        deltas = self._script[idx]
        self._call_index += 1
        for d in deltas:
            yield d

    def estimate_cost(self, req: BrainRequest) -> float:
        return 0.0


def tool_call_delta(
    name: str, input_dict: dict[str, Any], call_id: str = "call_test"
) -> BrainDelta:
    """Helper constructor for a tool-call delta."""
    return BrainDelta(tool_call={"id": call_id, "name": name, "input": input_dict})
