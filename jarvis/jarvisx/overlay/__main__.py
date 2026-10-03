"""Sidecar entry: ``python -m jarvis.jarvisx.overlay``.

Guards the PySide6 import so a host without the GUI stack exits with
``protocol.EXIT_NO_GUI`` and one English line on stderr, never a traceback.
"""

from __future__ import annotations

import sys

from jarvis.jarvisx.overlay.protocol import EXIT_NO_GUI


def main() -> int:
    try:
        from jarvis.jarvisx.overlay.renderer import run  # noqa: PLC0415
    except ImportError as exc:
        sys.stderr.write(
            f"jarvisx-overlay: PySide6 unavailable ({exc}); install the [desktop] extra "
            "for the Jarvis X selection overlay and thumbnails.\n"
        )
        return EXIT_NO_GUI
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
