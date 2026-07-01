"""Open-Interpreter harness (stub).

Open-Interpreter has no subprocess CLI, it's a Python package. For a
meaningful integration we'd need to load `from interpreter import interpreter`
in-process and iterate `interpreter.chat(prompt, stream=True, display=False)`.
The package is dependency-heavy (OpenAI, torch, ...) and not part of the
Jarvis requirements.

This stub fails `health()` as long as the package is missing — that keeps
discovery crash-free and gives the user a clear message.
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
                    "Open-Interpreter not installed. "
                    "`pip install open-interpreter` then call the harness again.\n"
                ),
                exit_code=127,
                is_final=True,
            )
            return
        # In-process mode — future work.
        yield HarnessResult(
            stderr="Open-Interpreter in-process integration not implemented yet.\n",
            exit_code=1,
            is_final=True,
        )

    async def cancel(self) -> None:
        return
