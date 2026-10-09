"""Bounded, single-thread-owned video encoding with wall-clock timestamps."""

from __future__ import annotations

import logging
import queue
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from jarvis.appshot.recording_codec import VIDEO_TIME_BASE
from jarvis.appshot.recording_options import RecordingOptions, output_size

log = logging.getLogger(__name__)
FPS = 60


class FirstFramePoster:
    """Keep a small immutable copy of the first submitted frame for the video card."""

    def __init__(self) -> None:
        self.image: Any = None

    def observe(self, image: Any) -> None:
        if self.image is not None:
            return
        from PySide6.QtCore import Qt

        self.image = image.scaled(
            560, 560, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )


class VideoEncoder:
    """Native encoder calls stay off the UI thread and in a disposable process."""

    def __init__(
        self, path: Path, *, options: RecordingOptions | None = None,
        fps: int | None = None, epoch: float | None = None, hdr: bool = False,
    ) -> None:
        self.path = path
        #: HDR frames arrive as finished P010 planes ``(y, uv)`` at the output
        #: size (:class:`jarvis.appshot.hdr_recording.HdrCapture`), not QImages.
        self.hdr = hdr
        self.options = options or RecordingOptions()
        self.fps = fps or self.options.fps
        self.epoch = epoch if epoch is not None else time.perf_counter()
        self.dropped_frames = 0
        self._audio: Any = None
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
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QImage

            if self.options.system_audio:
                from jarvis.appshot.recording_audio import SystemAudioCapture

                self._audio = SystemAudioCapture(self.epoch)
                self._audio.start()
                if not self._audio.ready.wait(timeout=5) or self._audio.error:
                    raise RuntimeError(self._audio.error or "System audio startup timed out.")
            stream = None
            audio_stream = None
            source_size = None
            last_pts = -1
            encoded_frames = 0
            last_array = None
            while not self.stopping.is_set() or not self.frames.empty():
                if self._audio is not None and self._audio.error:
                    raise RuntimeError(self._audio.error)
                if audio_stream is not None:
                    self._drain_audio(av, container, audio_stream)
                try:
                    image, elapsed = self.frames.get(timeout=0.1)
                except queue.Empty:  # Normal idle tick: recheck stop without blocking shutdown.
                    continue
                if stream is None:
                    width, height = self._source_size(image)
                    source_size = (width, height)
                    # Pad to even encoder dimensions instead of cropping user pixels.
                    self.size = (
                        (width, height) if self.hdr
                        else output_size(width, height, self.options.resolution)
                    )
                    from jarvis.appshot.recording_codec import open_video

                    container, stream = open_video(
                        av, self.partial, self.size, self.fps,
                        self.options.bitrate_mbps * 1_000_000, hdr=self.hdr,
                    )
                    if self._audio is not None:
                        audio_stream = container.add_stream("aac", rate=48000)
                        audio_stream.layout = "stereo"
                        audio_stream.bit_rate = 192000
                if self._source_size(image) != source_size:
                    raise ValueError("The selected display changed size during recording.")
                # Hold the first usable picture from time zero instead of an
                # empty MP4 edit-list interval during capture/driver startup.
                pts = 0 if last_pts < 0 else round(elapsed / VIDEO_TIME_BASE)
                if pts <= last_pts:
                    continue  # Never stretch wall-clock time when a timer fires early.
                if self.hdr:
                    last_array = image
                    encoded = self._encode(av, container, stream, image, pts)
                    encoded_frames += 1
                    last_pts = pts
                    if encoded:
                        self.ready.set()
                    continue
                if self.size[0] < image.width() or self.size[1] < image.height():
                    # Scaling and color conversion belong off the capture/UI thread.
                    # Resize before copying into PyAV to avoid converting 4K RGBA
                    # buffers when the selected output is only Full HD.
                    image = image.scaled(*self.size, Qt.AspectRatioMode.IgnoreAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                image = image.convertToFormat(QImage.Format.Format_RGBA8888)
                # QImage RGBA rows can include alignment padding.
                array = (
                    np.frombuffer(image.constBits(), dtype=np.uint8)
                    .reshape(image.height(), image.bytesPerLine())[:, : image.width() * 4]
                    .reshape(image.height(), image.width(), 4)
                )
                width, height = image.width(), image.height()
                if width % 2 or height % 2:
                    padded = np.zeros((height + height % 2, width + width % 2, 4), dtype=np.uint8)
                    padded[:height, :width] = array
                else:
                    padded = np.ascontiguousarray(array)
                last_array = padded
                encoded = self._encode(av, container, stream, padded, pts)
                encoded_frames += 1
                last_pts = pts
                if encoded:
                    self.ready.set()
            if stream is not None and container is not None:
                final_pts = max(0, round((self.duration - 1 / self.fps) / VIDEO_TIME_BASE))
                if last_array is not None and final_pts > last_pts:
                    self._encode(av, container, stream, last_array, final_pts)
                    encoded_frames += 1
                self.dropped_frames = max(0, round(self.duration * self.fps) - encoded_frames)
                for packet in stream.encode(None):
                    container.mux(packet)
                if self._audio is not None:
                    self._audio.stop()
                    if self._audio.error:
                        raise RuntimeError(self._audio.error)
                    self._drain_audio(av, container, audio_stream)
                    for packet in audio_stream.encode(None):
                        container.mux(packet)
                container.close()
                container = None
                self.partial.replace(self.path)
        except Exception:
            log.exception("appshot: video encoding failed")
            self.error = (
                self._audio.error if self._audio is not None and self._audio.error else
                "The video could not be saved. Check free disk space and video support."
            )
        finally:
            if self._audio is not None and not self._audio.stopping.is_set():
                self._audio.stop()
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

    def _drain_audio(self, av: Any, container: Any, stream: Any) -> None:
        while True:
            try:
                samples, pts = self._audio.chunks.get_nowait()
            except queue.Empty:  # All currently queued audio has been encoded.
                return
            if self.stopping.is_set():
                remaining = max(0, round(self.duration * 48000) - pts)
                samples = samples[:, :remaining]
            if not samples.shape[1]:
                continue
            import numpy as np

            frame = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(samples), format="fltp", layout="stereo"
            )
            frame.sample_rate = 48000
            frame.pts, frame.time_base = pts, Fraction(1, 48000)
            for packet in stream.encode(frame):
                container.mux(packet)

    def _source_size(self, image: Any) -> tuple[int, int]:
        if self.hdr:
            height, width = image[0].shape
            return width, height
        return image.width(), image.height()

    def _encode(self, av: Any, container: Any, stream: Any, array: Any, pts: int) -> bool:
        if self.hdr:
            from jarvis.appshot.hdr_recording import p010_frame

            frame = p010_frame(av, stream.pix_fmt, *array)
        else:
            frame = av.VideoFrame.from_ndarray(array, format="rgba")
            frame = frame.reformat(
                width=self.size[0], height=self.size[1], format=stream.pix_fmt
            )
        frame.pts = pts
        frame.time_base = VIDEO_TIME_BASE
        written = False
        for packet in stream.encode(frame):
            container.mux(packet)
            written = True
        return written
