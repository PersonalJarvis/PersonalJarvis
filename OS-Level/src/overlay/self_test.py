"""Self-test without GUI dependencies.

Imports ONLY foundation modules (config, state, schema). Touches NO Qt,
so headless CI runs without a display work.
"""

from __future__ import annotations

import sys


def run() -> int:
    from . import __version__
    from .config import OverlayConfig
    from .state import OverlayState

    # Pydantic default roundtrip as a sanity check.
    cfg = OverlayConfig()
    assert cfg.enabled is True
    assert OverlayState.IDLE.value == "idle"
    assert len(list(OverlayState)) == 8

    print(f"OK overlay=={__version__}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
