"""Install the open-palm stop's dependencies: mediapipe (with opencv) and the
official MediaPipe hand landmark model (about 8 MB, Apache-2.0).

Usage::

    python scripts/install_hand_tracking.py

Then turn on ``[trigger] palm_stop_enabled``.
"""

from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: E402
from jarvis.vision.hand_gesture import model_path  # noqa: E402

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)


def main() -> int:
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "mediapipe"],
            check=True,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    target = model_path()
    if target.is_file():
        print(f"model present: {target}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    print(f"downloading {MODEL_URL}")
    with urllib.request.urlopen(MODEL_URL, timeout=120) as resp, tmp.open("wb") as fh:  # noqa: S310
        fh.write(resp.read())
    tmp.replace(target)
    print(f"model saved: {target} ({target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
