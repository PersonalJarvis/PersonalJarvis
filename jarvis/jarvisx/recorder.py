"""Screen recording for Jarvis X: grab frames, encode a video, stop cleanly.

The recorder is two seams and a loop:

- a :class:`FrameSource` hands out BGRA frames of one fixed screen area
  (``mss`` in production);
- an :class:`Encoder` turns them into a file (PyAV in production);
- :class:`Recorder` runs the loop on its own thread at up to ``fps`` frames a
  second. Timestamps come from the wall clock, so a slow machine produces
  fewer frames of the right duration, never a sped-up video.

Encoder choice (:func:`probe_encoder`): PyAV is already installed with the
base install on every platform that ships wheels for it (it is the WebRTC
dependency of the voice stack), so recording needs nothing extra. The probe
tries H.264 encoders in order of quality — ``libx264``, then the OS encoders
(Media Foundation on Windows, VideoToolbox on macOS), then ``libopenh264`` —
and falls back to MPEG-4 Part 2 in an ``.mp4``, then VP9 in a ``.webm``. The
first one that really encodes a test frame wins, and the settings report which
one it is. Without PyAV (Windows on ARM) recording is reported unavailable
with the reason; screenshots keep working.

Audio is not recorded, and the mouse pointer is not drawn into the frames.
"""

from __future__ import annotations

import io
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jarvis.jarvisx.geometry import Rect, even_size

log = logging.getLogger(__name__)

#: Frames per second the recorder aims for.
DEFAULT_FPS = 30

#: Consecutive failed grabs after which a recording gives up (display gone).
_MAX_GRAB_FAILURES = 60

#: Frames waiting for the encoder before new ones are dropped.
_QUEUE_DEPTH = 3


# --------------------------------------------------------------------------
# Seams
# --------------------------------------------------------------------------


class FrameSource(Protocol):
    def grab(self) -> tuple[int, int, bytes]:
        """``(width, height, bgra_bytes)`` of the recorded area, now."""
        ...

    def close(self) -> None: ...


class Encoder(Protocol):
    name: str
    extension: str

    def open(self, path: Path, width: int, height: int, fps: int) -> None: ...

    def write(self, bgra: bytes, width: int, height: int, pts: int) -> None:
        """One frame; ``pts`` counts in units of ``1 / fps`` seconds."""
        ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class EncoderChoice:
    """The encoder the probe settled on (or why there is none)."""

    available: bool
    codec: str = ""
    extension: str = "mp4"
    pix_fmt: str = "yuv420p"
    detail: str = ""


# --------------------------------------------------------------------------
# Production seams
# --------------------------------------------------------------------------


class MssFrameSource:
    """Grabs one fixed capture-space rectangle with ``mss``.

    Created on the recording thread: an ``mss`` handle belongs to the thread
    that opened it, and the per-monitor DPI pinning is thread-local too.
    """

    def __init__(self, bbox: Rect) -> None:
        import mss  # type: ignore[import-not-found]  # noqa: PLC0415

        from jarvis.jarvisx.capture import input_space  # noqa: PLC0415

        left, top, width, height = (int(v) for v in bbox)
        self._area = {"left": left, "top": top, "width": width, "height": height}
        self._space = input_space()
        self._space.__enter__()
        factory = getattr(mss, "MSS", None) or mss.mss
        self._sct = factory()

    def grab(self) -> tuple[int, int, bytes]:
        shot = self._sct.grab(self._area)
        # ``raw`` is already a fresh BGRA buffer; copying 33 MB per 4K frame
        # would cost a frame of its own.
        return int(shot.size[0]), int(shot.size[1]), shot.raw

    def close(self) -> None:
        try:
            self._sct.close()
        finally:
            self._space.__exit__(None, None, None)


_CANDIDATES: tuple[tuple[str, str, str, dict[str, str]], ...] = (
    # (codec, container extension, pixel format, codec options)
    ("libx264", "mp4", "yuv420p", {"preset": "superfast", "tune": "zerolatency", "crf": "23"}),
    ("h264_mf", "mp4", "nv12", {}),
    ("h264_videotoolbox", "mp4", "nv12", {"realtime": "1"}),
    ("libopenh264", "mp4", "yuv420p", {}),
    ("mpeg4", "mp4", "yuv420p", {"qscale": "3"}),
    ("libvpx-vp9", "webm", "yuv420p", {"deadline": "realtime", "cpu-used": "8"}),
)


class PyAvEncoder:
    """Encodes BGRA frames into a video file with PyAV (FFmpeg)."""

    def __init__(self, choice: EncoderChoice) -> None:
        self.name = choice.codec
        self.extension = choice.extension
        self._pix_fmt = choice.pix_fmt
        self._options = next((c[3] for c in _CANDIDATES if c[0] == choice.codec), {})
        self._container: Any = None
        self._stream: Any = None
        self._size = (0, 0)

    def open(self, path: Path, width: int, height: int, fps: int) -> None:
        import av  # noqa: PLC0415

        container_options = {"movflags": "+faststart"} if self.extension == "mp4" else {}
        self._container = av.open(str(path), mode="w", container_options=container_options)
        stream = self._container.add_stream(self.name, rate=fps, options=dict(self._options))
        self._size = even_size(width, height)
        stream.width, stream.height = self._size
        stream.pix_fmt = self._pix_fmt
        self._stream = stream

    def write(self, bgra: bytes, width: int, height: int, pts: int) -> None:
        import av  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415

        w, h = self._size
        array = np.frombuffer(bgra, dtype=np.uint8).reshape(height, width, 4)[:h, :w]
        frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(array), format="bgra")
        frame = frame.reformat(format=self._pix_fmt)
        frame.pts = pts
        for packet in self._stream.encode(frame):
            self._container.mux(packet)

    def close(self) -> None:
        if self._container is None:
            return
        try:
            for packet in self._stream.encode(None):
                self._container.mux(packet)
        finally:
            self._container.close()
            self._container = None


_probe_lock = threading.Lock()
_probe_result: EncoderChoice | None = None


def probe_encoder(*, refresh: bool = False) -> EncoderChoice:
    """The best video encoder that really works here (cached per process)."""
    global _probe_result
    with _probe_lock:
        if _probe_result is not None and not refresh:
            return _probe_result
        _probe_result = _probe()
        return _probe_result


def _probe() -> EncoderChoice:
    try:
        import av  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - ImportError or a broken native build
        log.info("jarvisx: recording unavailable, PyAV missing (%s)", exc)
        return EncoderChoice(
            available=False,
            detail=(
                "Screen recording needs the PyAV package, which is not installed on this "
                "computer (pip install av). Screenshots work without it."
            ),
        )
    available = set(getattr(av, "codecs_available", ()) or ())
    for codec, extension, pix_fmt, options in _CANDIDATES:
        if available and codec not in available:
            continue
        if _encodes(av, codec, extension, pix_fmt, options):
            label = "H.264" if "264" in codec else ("MPEG-4" if codec == "mpeg4" else "VP9")
            return EncoderChoice(
                available=True,
                codec=codec,
                extension=extension,
                pix_fmt=pix_fmt,
                detail=f"Recordings are saved as {extension.upper()} ({label}, {codec}).",
            )
    return EncoderChoice(
        available=False,
        detail="No working video encoder was found in this PyAV build, so recording is off.",
    )


def _encodes(av: Any, codec: str, extension: str, pix_fmt: str, options: dict[str, str]) -> bool:
    """Encode one tiny frame into memory; any failure means "not usable"."""
    try:
        import numpy as np  # noqa: PLC0415

        buffer = io.BytesIO()
        container = av.open(buffer, mode="w", format=extension)
        try:
            stream = container.add_stream(codec, rate=DEFAULT_FPS, options=dict(options))
            stream.width, stream.height = 64, 64
            stream.pix_fmt = pix_fmt
            array = np.zeros((64, 64, 4), dtype=np.uint8)
            for pts in range(2):
                frame = av.VideoFrame.from_ndarray(array, format="bgra").reformat(format=pix_fmt)
                frame.pts = pts
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        finally:
            container.close()
        return buffer.tell() > 0 or len(buffer.getvalue()) > 0
    except Exception:  # noqa: BLE001 - probing: an encoder that fails is skipped
        log.debug("jarvisx: encoder %s is not usable here", codec, exc_info=True)
        return False


# --------------------------------------------------------------------------
# The recording loop
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecordingResult:
    path: Path
    width: int
    height: int
    duration_s: float
    frames: int
    #: The first frame as ``(width, height, bgra)``, for the thumbnail.
    first_frame: tuple[int, int, bytes] | None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.frames > 0


class Recorder:
    """Records one area until :meth:`stop`. One use per instance."""

    def __init__(
        self,
        *,
        source_factory: Callable[[], FrameSource],
        encoder: Encoder,
        path: Path,
        fps: int = DEFAULT_FPS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._source_factory = source_factory
        self._encoder = encoder
        self.path = path
        self.fps = max(1, int(fps))
        self._clock = clock
        self._sleep = sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self._frames = 0
        self._size = (0, 0)
        self._first: tuple[int, int, bytes] | None = None
        self._error = ""
        self._dropped = 0
        self._opened = threading.Event()

    @property
    def elapsed_s(self) -> float:
        if self._started_at is None:
            return 0.0
        end = self._stopped_at if self._stopped_at is not None else self._clock()
        return max(0.0, end - self._started_at)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, wait_s: float = 5.0) -> str:
        """Start the loop; returns ``""`` or the reason it could not start."""
        self._thread = threading.Thread(target=self._run, name="jarvisx-recorder", daemon=True)
        self._thread.start()
        self._opened.wait(timeout=wait_s)
        return self._error

    def stop(self, *, timeout_s: float = 30.0) -> RecordingResult:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_s)
            if thread.is_alive():
                self._error = self._error or "The recording did not finish writing in time."
        width, height = self._size
        return RecordingResult(
            path=self.path,
            width=width,
            height=height,
            duration_s=round(self.elapsed_s, 2),
            frames=self._frames,
            first_frame=self._first,
            error=self._error,
        )

    def _run(self) -> None:
        source: FrameSource | None = None
        opened = False
        frames: queue.Queue[tuple[int, bytes] | None] = queue.Queue(maxsize=_QUEUE_DEPTH)
        writer: threading.Thread | None = None
        try:
            source = self._source_factory()
            width, height, first = source.grab()
            self._size = even_size(width, height)
            self._encoder.open(self.path, width, height, self.fps)
            opened = True
            self._first = (width, height, first)
            self._started_at = self._clock()
            writer = threading.Thread(
                target=self._write_frames,
                args=(frames, width, height),
                name="jarvisx-encoder",
                daemon=True,
            )
            writer.start()
            self._opened.set()
            self._grab_frames(source, frames, width, height, first)
        except Exception as exc:  # noqa: BLE001 - reported through the result
            log.warning("jarvisx: recording failed", exc_info=True)
            self._error = self._error or f"The recording failed ({type(exc).__name__}: {exc})."
        finally:
            self._stopped_at = self._clock() if self._started_at is not None else None
            self._opened.set()
            if writer is not None:
                frames.put(None)
                writer.join()
            if opened:
                try:
                    self._encoder.close()
                except Exception as exc:  # noqa: BLE001 - reported through the result
                    log.warning("jarvisx: finishing the video failed", exc_info=True)
                    self._error = self._error or f"The video could not be finished ({exc})."
            if source is not None:
                try:
                    source.close()
                except Exception:  # noqa: BLE001 - the video is already written
                    log.debug("jarvisx: frame source close failed", exc_info=True)

    def _grab_frames(
        self,
        source: FrameSource,
        frames: queue.Queue[tuple[int, bytes] | None],
        width: int,
        height: int,
        first: bytes,
    ) -> None:
        """Grab on this thread, encode on the writer thread.

        The two run in parallel (both native calls release the GIL), which
        roughly doubles the frame rate of a large screen. When the encoder
        falls behind, a frame is dropped rather than queued: the timestamps
        keep the video's duration true.
        """
        assert self._started_at is not None
        interval = 1.0 / self.fps
        last_pts = -1
        failures = 0
        pending: bytes | None = first
        while True:
            pts = int((self._clock() - self._started_at) * self.fps)
            if pending is not None and pts > last_pts:
                try:
                    frames.put_nowait((pts, pending))
                    last_pts = pts
                except queue.Full:
                    self._dropped += 1
            if self._stop.is_set() or self._error:
                return
            next_due = self._started_at + (max(last_pts, pts) + 1) * interval
            delay = next_due - self._clock()
            if delay > 0:
                if self._sleep is time.sleep:
                    # Wakes early on stop(), so stopping never waits a frame.
                    self._stop.wait(delay)
                else:
                    self._sleep(delay)
            try:
                w, h, pending = source.grab()
                failures = 0
                if (w, h) != (width, height):
                    # The screen changed resolution mid-recording; frames of
                    # another size cannot join this video.
                    pending = None
            except Exception:  # noqa: BLE001 - a transient grab failure skips a frame
                failures += 1
                pending = None
                log.debug("jarvisx: frame grab failed", exc_info=True)
                if failures >= _MAX_GRAB_FAILURES:
                    self._error = "The screen stopped delivering frames, so the recording ended."
                    return

    def _write_frames(
        self, frames: queue.Queue[tuple[int, bytes] | None], width: int, height: int
    ) -> None:
        while True:
            item = frames.get()
            if item is None:
                return
            if self._error:
                continue
            pts, bgra = item
            try:
                self._encoder.write(bgra, width, height, pts)
                self._frames += 1
            except Exception as exc:  # noqa: BLE001 - ends the recording, reported
                log.warning("jarvisx: encoding a frame failed", exc_info=True)
                self._error = f"The video encoder failed ({type(exc).__name__}: {exc})."
                self._stop.set()


__all__ = [
    "DEFAULT_FPS",
    "Encoder",
    "EncoderChoice",
    "FrameSource",
    "MssFrameSource",
    "PyAvEncoder",
    "Recorder",
    "RecordingResult",
    "probe_encoder",
]
