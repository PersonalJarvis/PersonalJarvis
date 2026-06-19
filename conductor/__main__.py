"""``python -m conductor`` — CLI-Entry.

Delegiert an ``conductor.cli.main``. Pattern ist bewusst schlank: die
eigentliche Logik liegt in ``cli.py``, hier nur der Dispatch.
"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
