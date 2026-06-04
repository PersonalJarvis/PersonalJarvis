"""Python-Script-Harness: führt Python-Code im Subprocess aus.

`task.prompt` wird als `-c "<code>"` an `python` übergeben (inline). Für
File-Execution kann der Prompt mit `@path/to/file.py` beginnen.

Kein LLM-Loop — einfach deterministisches Script-Execution. Nützlich als
schneller "Calculator"- oder "Datenverarbeitungs"-Harness, und als Test-Harness.
"""
from __future__ import annotations

import os
import shutil

from jarvis.core.protocols import HarnessTask
from jarvis.harness.base import SubprocessHarness, python_executable


class PythonScriptHarness(SubprocessHarness):
    name: str = "python-script"
    version: str = "0.1"
    supports_versions: str = ">=3.11"

    def build_command(self, task: HarnessTask) -> list[str]:
        py = python_executable()
        prompt = (task.prompt or "").strip()
        if prompt.startswith("@"):
            script_path = prompt[1:].strip()
            return [py, script_path]
        # Inline-Code
        return [py, "-X", "utf8", "-c", prompt]

    async def health(self) -> bool:
        return shutil.which(python_executable()) is not None or os.path.isfile(python_executable())
