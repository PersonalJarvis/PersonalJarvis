"""Sidecar entry: ``python -m jarvis.cu.indicator``.

Guards the PySide6 import so a host without the GUI stack (headless VPS,
base install) exits with ``protocol.EXIT_NO_GUI`` and one English line on
stderr instead of a traceback. The controller treats that exit code as an
expected degradation.
"""

from __future__ import annotations

import sys

from jarvis.cu.indicator.protocol import EXIT_NO_GUI


def main() -> int:
    try:
        from jarvis.cu.indicator.renderer import run  # noqa: PLC0415
    except ImportError as exc:
        sys.stderr.write(
            f"cu-indicator: PySide6 unavailable ({exc}) — indicator disabled. "
            "Install the [desktop] extra to enable the Computer-Use screen "
            "indicator.\n"
        )
        return EXIT_NO_GUI
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
