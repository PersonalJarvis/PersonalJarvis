"""One-shot verification that OrbOverlay starts and shows at the persisted
position (regression guard for the UnboundLocalError fix in
``ui/orb/overlay.py`` 2026-05-17). Prints geometry + state, then exits.

Run from the repo root::

    python scripts/verify_orb_appears.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.orb.drag_persistence import load_position_from_toml
from ui.orb.overlay import OrbOverlay


def main() -> int:
    persisted = load_position_from_toml(Path("jarvis.toml"))
    print(f"persisted position: {persisted}")

    orb = OrbOverlay(sticky=False, mic_reactive=False, style="mascot")
    orb.start_in_thread(timeout=5.0)
    print("orb thread initialised — no UnboundLocalError")

    orb.show(mode="think")
    orb.show_comment("denke nach …", duration_ms=6000)
    time.sleep(2.5)

    root = orb._root
    if root is None:
        print("FAIL: orb root is None")
        return 1
    try:
        geo = root.winfo_geometry()
        x = root.winfo_x()
        y = root.winfo_y()
        viewable = root.winfo_viewable()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: cannot read geometry: {exc!r}")
        return 1

    print(f"geometry={geo}  x={x}  y={y}  viewable={viewable}")
    print(f"_mascot_x={orb._mascot_x}  _mascot_y={orb._mascot_y}")
    print(f"_manual_pinned={orb._manual_pinned}")

    time.sleep(1.0)
    # No explicit stop — orb runs in a daemon thread, exits with us.
    return 0


if __name__ == "__main__":
    sys.exit(main())
