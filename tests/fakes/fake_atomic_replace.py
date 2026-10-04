"""Deterministic transient or permanent failures for one atomic file replacement."""

from __future__ import annotations

import os
from pathlib import Path


class FailingReplace:
    def __init__(self, target: Path, failures: int, error: OSError | None = None) -> None:
        self.target = target
        self.failures = failures
        self.error = error or PermissionError(13, "Simulated external file lock")
        self.sources: list[str] = []
        self._replace = os.replace

    def __call__(self, source: str, destination: Path) -> None:
        if Path(destination) == self.target:
            self.sources.append(str(source))
            if len(self.sources) <= self.failures:
                raise self.error
        self._replace(source, destination)
