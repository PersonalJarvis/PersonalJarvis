"""Capture the bottom-right corner of every monitor to prove the orb is
visible. Run AFTER verify_orb_appears.py spawns the orb (or while the
real Jarvis is running with the orb shown).

Output files land in ``data/orb-snapshots/`` so they don't pollute the
repo root.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import mss
from PIL import Image


def main() -> int:
    out_dir = Path("data/orb-snapshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    with mss.mss() as sct:
        for idx, monitor in enumerate(sct.monitors[1:], start=1):
            img = sct.grab(monitor)
            pil = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            path = out_dir / f"monitor-{idx}-{ts}.png"
            pil.save(path)
            print(f"saved {path} (size={img.size}, geom={monitor})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
