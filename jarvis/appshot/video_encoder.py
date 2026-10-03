"""Bounded, single-thread-owned video encoding with wall-clock timestamps."""

from __future__ import annotations

import logging
import queue
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
FPS = 30


class VideoEncoder:
    """Native encoder calls stay off the UI thread and in a disposable process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.partial = path.with_suffix(".partial")
        self.frames: queue.Queue[tuple[Any, float]] = queue.Queue(maxsize=2)
        self.stopping = threading.Event()
        self.ready = threading.Event()
        self.done = threading.Event()
        self.error = ""
        self.size = (0, 0)
        self.duration = 0.0
        self._thread = threading.Thread(target=self._run, name="appshot-video", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, image: Any, elapsed: float) -> None:
        if self.stopping.is_set():
            return
        try:
            self.frames.put_nowait((image, elapsed))
        except queue.Full:
            # Bounded backlog: drop frames, keeping real elapsed timestamps.
            pass

    def stop(self, elapsed: float) -> None:
        self.duration = elapsed
        self.stopping.set()

    def _run(self) -> None:
        container = None
        try:
            import av
            import numpy as np

            stream = None
            last_pts = -1
            last_array = None
            while not self.stopping.is_set() or not self.frames.empty():
                try:
                    image, elapsed = self.frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                if stream is None:
                    width, height = image.width(), image.height()
                    # Pad to even encoder dimensions instead of cropping user pixels.
                    self.size = (width + width % 2, height + height % 2)
                    container = av.open(str(self.partial), "w", format="mp4")
                    codec = "libx264" if "libx264" in av.codecs_available else "mpeg4"
                    options = (
                        {"preset": "ultrafast", "crf": "23", "tune": "zerolatency"}
                        if codec == "libx264"
                        else {}
                    )
                    stream = container.add_stream(codec, rate=FPS, options=options)
                    stream.width, stream.height = self.size
                    stream.pix_fmt = "yuv420p"
                    stream.codec_context.thread_count = 2
                    stream.codec_context.max_b_frames = 0
                if image.width() > self.size[0] or image.height() > self.size[1]:
                    raise ValueError("The selected display changed size during recording.")
                pts = max(last_pts + 1, round(elapsed * FPS))
                # QImage RGBA rows can include alignment padding.
                array = (
                    np.frombuffer(image.constBits(), dtype=np.uint8)
                    .reshape(image.height(), image.bytesPerLine())[:, : image.width() * 4]
                    .reshape(image.height(), image.width(), 4)
                )
                padded = np.zeros((self.size[1], self.size[0], 4), dtype=np.uint8)
                padded[: image.height(), : image.width()] = array
                last_array = padded
                encoded = self._encode(av, container, stream, padded, pts)
                last_pts = pts
                if encoded:
                    self.ready.set()
            if stream is not None and container is not None:
                final_pts = round(self.duration * FPS)
                if last_array is not None and final_pts > last_pts:
                    self._encode(av, container, stream, last_array, final_pts)
                for packet in stream.encode(None):
                    container.mux(packet)
                container.close()
                container = None
                self.partial.replace(self.path)
        except Exception:
            log.exception("appshot: video encoding failed")
            self.error = "The video could not be saved. Check free disk space and video support."
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    log.debug("appshot: failed encoder cleanup", exc_info=True)
            try:
                self.partial.unlink(missing_ok=True)
            except OSError:
                log.warning("appshot: partial video cleanup failed", exc_info=True)
            self.done.set()

    @staticmethod
    def _encode(av: Any, container: Any, stream: Any, array: Any, pts: int) -> bool:
        frame = av.VideoFrame.from_ndarray(array, format="rgba")
        frame.pts = pts
        frame.time_base = Fraction(1, FPS)
        written = False
        for packet in stream.encode(frame):
            container.mux(packet)
            written = True
        return written
