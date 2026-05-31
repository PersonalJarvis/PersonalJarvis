"""Fake-Harness: scripted Ergebnisse ohne echten Subprocess."""
from __future__ import annotations

from collections.abc import AsyncIterator

from jarvis.core.protocols import HarnessResult, HarnessTask


class FakeHarness:
    """Fake-Harness für Tests. Liefert scripted Results."""

    name: str = "fake-harness"
    version: str = "0.0.1"
    supports_versions: str = ">=0"

    def __init__(
        self,
        *,
        scripted_output: str = "fake output\n",
        exit_code: int = 0,
        fail: bool = False,
    ) -> None:
        self._scripted_output = scripted_output
        self._exit_code = exit_code
        self._fail = fail
        self.invocations: list[HarnessTask] = []

    async def health(self) -> bool:
        return not self._fail

    async def invoke(self, task: HarnessTask) -> AsyncIterator[HarnessResult]:
        self.invocations.append(task)
        if self._fail:
            yield HarnessResult(stderr="scripted failure\n", exit_code=1, is_final=True)
            return
        # Split output in 2 chunks to simulate streaming
        mid = len(self._scripted_output) // 2 or 1
        yield HarnessResult(stdout=self._scripted_output[:mid], is_final=False)
        yield HarnessResult(stdout=self._scripted_output[mid:], is_final=False)
        yield HarnessResult(exit_code=self._exit_code, duration_ms=10, is_final=True)

    async def cancel(self) -> None:
        pass
