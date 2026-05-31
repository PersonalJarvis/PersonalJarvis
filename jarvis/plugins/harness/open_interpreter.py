"""Open-Interpreter-Harness (Stub).

Open-Interpreter hat keine Subprocess-CLI, es ist ein Python-Package. Für
sinnvolle Integration müssen wir `from interpreter import interpreter`
in-process laden und `interpreter.chat(prompt, stream=True, display=False)`
iterieren. Das Package ist dependency-heavy (OpenAI, torch, ...) und nicht
Teil der Jarvis-Requirements.

Dieser Stub failed `health()` solange das Package fehlt — so bleibt die
Discovery ohne Crash und der User bekommt eine klare Meldung.
"""
from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator

from jarvis.core.protocols import HarnessResult, HarnessTask


class OpenInterpreterHarness:
    name: str = "open-interpreter"
    version: str = "0.1"
    supports_versions: str = ">=0.3"

    async def health(self) -> bool:
        return importlib.util.find_spec("interpreter") is not None

    async def invoke(self, task: HarnessTask) -> AsyncIterator[HarnessResult]:
        if not await self.health():
            yield HarnessResult(
                stderr=(
                    "Open-Interpreter nicht installiert. "
                    "`pip install open-interpreter` dann Harness erneut aufrufen.\n"
                ),
                exit_code=127,
                is_final=True,
            )
            return
        # In-Process-Mode — future work.
        yield HarnessResult(
            stderr="Open-Interpreter In-Process-Integration noch nicht implementiert.\n",
            exit_code=1,
            is_final=True,
        )

    async def cancel(self) -> None:
        return
