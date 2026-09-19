"""Open-palm emergency stop from the webcam (opt-in ``[trigger].palm_stop_enabled``).

The MediaPipe hand landmarker runs on about 8 frames per second from the
default camera. An open palm (all five fingers extended) held for ``HOLD_S``
fires the callback once; the hand must close or leave the frame before it can
fire again. Frames live only in memory: nothing is written, nothing is sent.

``mediapipe`` and ``opencv`` are an optional install and the landmark model is
downloaded by ``scripts/install_hand_tracking.py``; without either, the watcher
logs why and does not start.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from jarvis.core.paths import user_data_dir

log = logging.getLogger(__name__)

HOLD_S = 1.0
FRAME_INTERVAL_S = 0.12
MODEL_NAME = "hand_landmarker.task"

# MediaPipe hand landmark indices.
_WRIST = 0
_FINGERS = ((8, 6), (12, 10), (16, 14), (20, 18))  # (tip, pip)
_THUMB_TIP, _THUMB_IP, _INDEX_MCP, _PINKY_MCP = 4, 3, 5, 17


class _Point(Protocol):
    x: float
    y: float


def model_path() -> Path:
    return user_data_dir() / "models" / MODEL_NAME


def _dist2(a: _Point, b: _Point) -> float:
    return (a.x - b.x) ** 2 + (a.y - b.y) ** 2


def is_open_palm(landmarks: Sequence[_Point]) -> bool:
    """All five fingers extended, independent of hand rotation.

    Each fingertip is clearly farther from the wrist than its middle joint,
    and the thumb tip stands away from the palm.
    """
    if len(landmarks) < 21:
        return False
    wrist = landmarks[_WRIST]
    for tip, pip in _FINGERS:
        if _dist2(landmarks[tip], wrist) <= _dist2(landmarks[pip], wrist) * 1.15:
            return False
    palm_width = _dist2(landmarks[_INDEX_MCP], landmarks[_PINKY_MCP])
    thumb_out = _dist2(landmarks[_THUMB_TIP], landmarks[_PINKY_MCP])
    return thumb_out > palm_width * 1.4 and _dist2(landmarks[_THUMB_TIP], wrist) > _dist2(
        landmarks[_THUMB_IP], wrist
    )


class PalmHold:
    """Fires once per continuous open-palm hold of ``hold_s`` seconds."""

    def __init__(self, hold_s: float = HOLD_S) -> None:
        self._hold_s = hold_s
        self._since: float | None = None
        self._fired = False

    def feed(self, palm: bool, now: float) -> bool:
        if not palm:
            self._since, self._fired = None, False
            return False
        if self._since is None:
            self._since = now
        if not self._fired and now - self._since >= self._hold_s:
            self._fired = True
            return True
        return False


def unavailable_reason() -> str | None:
    """Why the watcher cannot run here, or None when it can."""
    try:
        import cv2  # noqa: F401, PLC0415
        import mediapipe  # noqa: F401, PLC0415
    except ImportError:
        return "mediapipe/opencv not installed (python scripts/install_hand_tracking.py)"
    if not model_path().is_file():
        return "hand landmark model missing (python scripts/install_hand_tracking.py)"
    return None


def _build_landmarker() -> Any:
    from mediapipe.tasks.python import BaseOptions, vision  # noqa: PLC0415

    options = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path())),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1,
    )
    return vision.HandLandmarker.create_from_options(options)


def start_palm_watcher(
    on_palm: Callable[[], None], stop: threading.Event | None = None
) -> threading.Thread | None:
    """Start the camera watcher thread; None (logged) when it cannot run."""
    reason = unavailable_reason()
    if reason is not None:
        log.info("Palm stop unavailable: %s", reason)
        return None
    stop_event = stop or threading.Event()

    def _run() -> None:
        import cv2  # noqa: PLC0415
        import mediapipe as mp  # noqa: PLC0415

        try:
            landmarker = _build_landmarker()
        except Exception:  # noqa: BLE001 - optional feature; the app keeps running
            log.warning("Palm stop: landmarker failed to load", exc_info=True)
            return
        backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else 0
        cap = cv2.VideoCapture(0, backend)
        if not cap.isOpened():
            log.warning("Palm stop: no camera could be opened")
            landmarker.close()
            return
        hold = PalmHold()
        start = time.monotonic()
        log.info("Palm stop is watching the camera")
        try:
            while not stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.5)
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                stamp_ms = int((time.monotonic() - start) * 1000)
                result = landmarker.detect_for_video(image, stamp_ms)
                palm = bool(result.hand_landmarks) and is_open_palm(result.hand_landmarks[0])
                if hold.feed(palm, time.monotonic()):
                    log.info("Open palm held %.1fs: emergency stop", HOLD_S)
                    on_palm()
                time.sleep(FRAME_INTERVAL_S)
        except Exception:  # noqa: BLE001 - a camera fault ends the optional watcher, logged
            log.warning("Palm stop watcher stopped after an error", exc_info=True)
        finally:
            cap.release()
            landmarker.close()

    thread = threading.Thread(target=_run, name="jarvis-palm-stop", daemon=True)
    thread.start()
    return thread


__all__ = [
    "HOLD_S",
    "PalmHold",
    "is_open_palm",
    "model_path",
    "start_palm_watcher",
    "unavailable_reason",
]
