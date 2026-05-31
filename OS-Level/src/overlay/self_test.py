"""Self-Test ohne GUI-Dependencies.

Importiert NUR Foundation-Module (config, state, schema). Beruehrt KEIN Qt,
damit headless-CI-Runs ohne Display funktionieren.
"""

from __future__ import annotations

import sys


def run() -> int:
    from . import __version__
    from .config import OverlayConfig
    from .state import OverlayState

    # Pydantic-Default-Roundtrip als Sanity-Check.
    cfg = OverlayConfig()
    assert cfg.enabled is True
    assert OverlayState.IDLE.value == "idle"
    assert len(list(OverlayState)) == 8

    print(f"OK overlay=={__version__}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
