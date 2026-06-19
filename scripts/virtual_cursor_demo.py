"""Visual demo of the Jarvis virtual mouse — run it to *see* the overlay.

    python scripts/virtual_cursor_demo.py

It starts the same overlay Computer-Use uses, then glides the real cursor to a
handful of points on the primary monitor, pulsing a gold ring at each — exactly
what you would see while the agent clicks, but WITHOUT issuing real clicks
(nothing on your desktop is actually pressed).

This is the interactive check the unit tests cannot do (Tk needs a real
desktop). If you see the cursor travel with a gold halo and a click pulse, the
feature works; alignment of the pulse with the cursor confirms DPI mapping.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Allow running straight from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.control.cursor_motion import glide_os_cursor, set_glide_ms  # noqa: E402
from jarvis.overlay.virtual_cursor import get_virtual_cursor  # noqa: E402
from ui.orb.virtual_cursor_window import TkVirtualCursor  # noqa: E402


def _primary_size() -> tuple[int, int]:
    try:
        import ctypes

        u = ctypes.windll.user32
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:  # noqa: BLE001
        return 1920, 1080


def main() -> int:
    cursor = TkVirtualCursor()
    if not cursor.start(timeout_s=8.0):
        print("Virtual cursor window could not start (no interactive desktop?).")
        return 1
    print("Overlay up. Watch the cursor glide + pulse (no real clicks fired)...")

    set_glide_ms(280)
    w, h = _primary_size()
    targets = [
        (int(w * 0.20), int(h * 0.25)),
        (int(w * 0.75), int(h * 0.30)),
        (int(w * 0.50), int(h * 0.55)),
        (int(w * 0.30), int(h * 0.75)),
        (int(w * 0.82), int(h * 0.78)),
        (int(w * 0.50), int(h * 0.50)),
    ]
    for (x, y) in targets:
        glide_os_cursor(x, y)  # real cursor travels; overlay halo tracks it
        get_virtual_cursor().show_click(x, y, button="left")  # pulse only
        time.sleep(0.8)

    time.sleep(0.6)
    cursor.shutdown()
    print("Done.")
    # Hard-exit so the Tcl interpreter is not finalised from the main thread
    # (the "async handler deleted by the wrong thread" panic).
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
