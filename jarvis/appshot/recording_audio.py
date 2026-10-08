"""Opt-in output-loopback capture owned by the disposable recording process.

No microphone fallback: only the default playback device's loopback is opened.
The device, recorder and native backend are confined to one capture thread.
"""

from __future__ import annotations

import importlib.util
import logging
import queue
import sys
import threading
import time
from typing import Any

log = logging.getLogger(__name__)
SAMPLE_RATE = 48000
BLOCK_FRAMES = 960


def capability() -> dict[str, Any]:
    """Package/platform readiness only; never open a device during a health poll."""
    supported = sys.platform in {"win32", "linux"}
    installed = importlib.util.find_spec("soundcard") is not None
    reason = "" if supported and installed else (
        "System audio recording is not available on this operating system."
        if not supported else "System audio recording requires the desktop audio package."
    )
    return {"available": supported and installed, "detail": reason}


class SystemAudioCapture:
    """Bounded PCM queue; startup and shutdown are bounded by the sidecar owner."""

    def __init__(self, epoch: float) -> None:
        self.epoch = epoch
        self.chunks: queue.Queue[tuple[Any, int]] = queue.Queue(maxsize=128)
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.done = threading.Event()
        self.error = ""
        self._thread = threading.Thread(target=self._run, name="appshot-audio", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.stopping.set()
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            self.error = "System audio did not stop in time. The recording could not be saved."
            log.warning("appshot: loopback capture shutdown timed out")

    def _run(self) -> None:
        try:
            import numpy as np
            import soundcard as sc

            speaker = sc.default_speaker()
            if speaker is None:
                raise RuntimeError("No default playback device.")
            # Exact identity matters: fuzzy matching must never select a microphone.
            loopbacks = sc.all_microphones(include_loopback=True)
            source = next((item for item in loopbacks
                           if item.isloopback and item.id == speaker.id), None)
            if source is None and sys.platform == "linux":
                source = next((item for item in loopbacks
                               if item.isloopback and item.id == f"{speaker.id}.monitor"), None)
            if source is None:
                raise RuntimeError("The default output has no loopback source.")
            with source.recorder(samplerate=SAMPLE_RATE, channels=2, blocksize=BLOCK_FRAMES) as mic:
                pts = max(0, round((time.perf_counter() - self.epoch) * SAMPLE_RATE))
                self.ready.set()
                while not self.stopping.is_set():
                    data = mic.record(numframes=BLOCK_FRAMES)
                    samples = np.ascontiguousarray(data.T, dtype=np.float32)
                    try:
                        self.chunks.put_nowait((samples, pts))
                    except queue.Full:
                        raise RuntimeError("The audio encoder cannot keep up.") from None
                    pts += samples.shape[1]
        except Exception:
            log.exception("appshot: system audio capture failed")
            self.error = (
                "System audio could not be recorded. Check the default output device "
                "or switch off system audio and try again."
            )
        finally:
            self.ready.set()
            self.done.set()
