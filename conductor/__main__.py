"""``python -m conductor`` — CLI entry point.

Delegates to ``conductor.cli.main``. The pattern is deliberately thin:
the actual logic lives in ``cli.py``, here it's only the dispatch.
"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
