"""Pytest setup for the web-search skill tests.

The skill lives under the repo-level ``src/`` layout rather than the in-tree
``jarvis/`` package, so this conftest adds ``<repo>/src`` to ``sys.path`` for
test collection. It is scoped to ``tests/skills/`` so the surrounding suite
is unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
