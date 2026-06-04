"""Fake-Brain für Tests: yielded scripted BrainDeltas.

Zwei Modi:
- `text_response`: nur Text, kein Tool-Call
- `tool_then_text`: erst Tool-Call, dann nach Tool-Result Text

Implementiert das Brain-Protocol strukturell (runtime_checkable isinstance prüft
name/context_window/supports_tools/supports_vision/complete/estimate_cost).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.core.protocols import BrainDelta, BrainRequest


class FakeBrain:
    """Fake-Brain-Provider für Tests."""

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
            script: Liste von Turn-Responses. Jeder Turn ist eine Liste von BrainDeltas.
                    Bei N Tool-Use-Rounds müssen N+1 Turns im Script liegen.
            text_response: Default-Text wenn kein Script.
            fail_on_call: Wenn >=0, wirft RuntimeError beim N-ten complete()-Call.
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
    """Hilfs-Konstruktor für einen Tool-Call-Delta."""
    return BrainDelta(tool_call={"id": call_id, "name": name, "input": input_dict})
