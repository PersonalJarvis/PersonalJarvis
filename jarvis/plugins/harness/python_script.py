"""Python-script harness: runs Python code in a subprocess.

`task.prompt` is passed to `python` as `-c "<code>"` (inline). For file
execution, the prompt can start with `@path/to/file.py`.

No LLM loop — just deterministic script execution. Useful as a quick
"calculator" or "data-processing" harness, and as a test harness.
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
        # Inline code
        return [py, "-X", "utf8", "-c", prompt]

    async def health(self) -> bool:
        return shutil.which(python_executable()) is not None or os.path.isfile(python_executable())
