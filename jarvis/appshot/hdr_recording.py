"""HDR screen recording: full-depth frames in, 10-bit BT.2020 PQ video out.

Used by the recording sidecar when the selected monitor runs HDR or a wide
colour gamut (:func:`jarvis.platform.display_color.display_color`). The
capture thread reads the framebuffer through FP16 Desktop Duplication; the GPU
crops, scales and converts each frame to P010 (BT.2020, PQ, limited range), so
the encoder receives exactly the light the monitor emitted. The video carries
BT.2020 / SMPTE ST 2084 / BT.2020-NCL colour tags in the bitstream and the MP4
``colr`` box, so players show it as the monitor did.

Encoders, best first: AV1 Main 10 in hardware (plays in the app's own editor
and on Windows without extra codecs), HEVC Main 10 in hardware, then the
software AV1 and HEVC encoders. Windows only today; elsewhere
:func:`hdr_capture_available` says no and the SDR path records.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

#: (codec, pixel format, options) — tried in order, first that opens wins.
HDR_CANDIDATES: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("av1_nvenc", "p010le", {"preset": "p4", "tune": "ll", "rc": "vbr"}),
    ("hevc_nvenc", "p010le", {"preset": "p4", "tune": "ll", "rc": "vbr", "profile": "main10"}),
    ("av1_amf", "p010le", {"usage": "lowlatency"}),
    ("hevc_amf", "p010le", {"usage": "lowlatency", "profile": "main10"}),
    ("av1_qsv", "p010le", {"preset": "veryfast"}),
    ("hevc_qsv", "p010le", {"preset": "veryfast", "profile": "main10"}),
    ("libsvtav1", "yuv420p10le", {"preset": "11"}),
    ("libx265", "yuv420p10le", {"preset": "ultrafast", "tune": "zerolatency"}),
)
#: AVCOL_PRI_BT2020, AVCOL_TRC_SMPTE2084, AVCOL_SPC_BT2020_NCL, AVCOL_RANGE_MPEG.
HDR10_TAGS = (9, 16, 9, 1)
#: Stop after this many lost-and-reopened desktops in a row.
_MAX_REOPENS = 5


def hdr_capture_available() -> bool:
    from jarvis.platform import win_duplication  # noqa: PLC0415

    return win_duplication.available()


def tag_hdr10(context: Any) -> None:
    """Write the HDR10 colour description into an encoder (or frame)."""
    primaries, transfer, matrix, colour_range = HDR10_TAGS
    context.color_primaries = primaries
    context.color_trc = transfer
    context.colorspace = matrix
    context.color_range = colour_range


def p010_frame(av: Any, pixel_format: str, y: Any, uv: Any) -> Any:
    """A PyAV frame from P010 planes, for either P010 or planar 10-bit encoders."""
    import numpy as np  # noqa: PLC0415

    height, width = y.shape
    frame = av.VideoFrame(width, height, pixel_format)

    def fill(plane: Any, rows: Any) -> None:
        line = plane.line_size // 2
        target = np.frombuffer(memoryview(plane), dtype=np.uint16).reshape(-1, line)
        target[: rows.shape[0], : rows.shape[1]] = rows

    if pixel_format == "p010le":
        fill(frame.planes[0], y)
        fill(frame.planes[1], uv.reshape(uv.shape[0], -1))
    else:  # yuv420p10le: the 10-bit codes in the low bits, U and V apart
        fill(frame.planes[0], y >> 6)
        fill(frame.planes[1], uv[..., 0] >> 6)
        fill(frame.planes[2], uv[..., 1] >> 6)
    tag_hdr10(frame)
    return frame


class HdrCapture:
    """A thread that feeds one encoder with converted frames at a steady rate.

    ``frac`` is the selection as fractions of the monitor; ``resolution`` the
    user's output preset. :attr:`out_size` is known once :meth:`open`
    returned. Errors end up in :attr:`error` for the sidecar to report.
    """

    def __init__(
        self,
        device_name: str,
        frac: tuple[float, float, float, float],
        resolution: str,
        fps: int,
        sdr_white_nits: float,
    ) -> None:
        self.device_name = device_name
        self.frac = frac
        self.resolution = resolution
        self.fps = max(1, int(fps))
        self.sdr_white = max(1.0, float(sdr_white_nits)) / 80.0
        self.sdr_white_nits = float(sdr_white_nits)
        self.error = ""
        self.out_size = (0, 0)
        self.crop = (0, 0, 0, 0)
        self.poster: Any = None
        self.frames = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dup: Any = None

    def open(self) -> None:
        """Open the duplication now, so a failure can still fall back to SDR."""
        from jarvis.appshot.recording_options import output_size  # noqa: PLC0415
        from jarvis.platform.win_duplication import DesktopDuplication  # noqa: PLC0415

        self._dup = DesktopDuplication(self.device_name)
        self._dup.wait_first_frame(1500)
        width, height = self._dup.size
        x, y, w, h = self.frac
        left, top = round(x * width), round(y * height)
        right = min(width, left + max(2, round(w * width)))
        bottom = min(height, top + max(2, round(h * height)))
        self.crop = (left, top, right - left, bottom - top)
        self.out_size = output_size(self.crop[2], self.crop[3], self.resolution)

    def start(self, encoder: Any, epoch: float) -> None:
        self._thread = threading.Thread(
            target=self._run, args=(encoder, epoch), name="appshot-hdr-capture", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)

    def close(self) -> None:
        """Release the duplication when the capture never started."""
        if self._thread is None and self._dup is not None:
            self._dup.close()
            self._dup = None

    def _run(self, encoder: Any, epoch: float) -> None:
        from jarvis.platform.win_duplication import (  # noqa: PLC0415
            AccessLost,
            DesktopDuplication,
        )

        interval = 1.0 / self.fps
        due = time.perf_counter()
        reopens = 0
        try:
            while not self._stop.is_set():
                try:
                    wait = due - time.perf_counter()
                    self._dup.acquire(max(0, int(wait * 1000)))
                    now = time.perf_counter()
                    if now < due:
                        continue
                    y, uv = self._dup.read_p010(self.crop, self.out_size, sdr_white=self.sdr_white)
                    encoder.submit((y, uv), now - epoch)
                    self.frames += 1
                    reopens = 0
                    due += interval
                    if due < now:  # fell behind: skip, keep real timestamps
                        due = now + interval
                    if self.poster is None:
                        self.poster = self._make_poster()
                except AccessLost:
                    reopens += 1
                    if reopens > _MAX_REOPENS:
                        raise
                    self._dup.close()
                    time.sleep(0.2)  # the desktop is switching; let it settle
                    self._dup = DesktopDuplication(self.device_name)
        except Exception as exc:  # noqa: BLE001 - reported to the user via the sidecar
            log.exception("appshot: HDR capture failed")
            self.error = (
                "The HDR screen capture stopped. Start a new recording."
                if not isinstance(exc, MemoryError)
                else "Not enough memory for HDR recording."
            )
        finally:
            if self._dup is not None:
                self._dup.close()
                self._dup = None

    def _make_poster(self) -> Any:
        """A small SDR picture of the first frame for the corner card."""
        try:
            from PySide6.QtGui import QImage  # noqa: PLC0415

            from jarvis.appshot.hdr_image import scrgb_to_srgb8  # noqa: PLC0415

            frame = self._dup.read_linear()
            x, y, w, h = self.crop
            step = max(1, max(w, h) // 560)
            rgb = scrgb_to_srgb8(frame[y : y + h : step, x : x + w : step], self.sdr_white_nits)
            height, width = rgb.shape[:2]
            image = QImage(rgb.tobytes(), width, height, width * 3, QImage.Format.Format_RGB888)
            return image.copy()
        except Exception:  # noqa: BLE001 - the card falls back to no picture
            log.debug("appshot: HDR poster unavailable", exc_info=True)
            return False


def choose_hdr_capture(
    window_handle: int | None,
    frac: tuple[float, float, float, float],
    resolution: str,
    fps: int,
) -> HdrCapture | None:
    """An opened :class:`HdrCapture` when the monitor needs one, else ``None``.

    ``window_handle`` is any window on the selected monitor (the recording
    frame). Plain SDR monitors, other platforms and any setup failure return
    ``None``: the 8-bit path then records, exactly as before.
    """
    if sys.platform != "win32" or not window_handle or not hdr_capture_available():
        return None
    from jarvis.platform.display_color import device_of_window, display_color  # noqa: PLC0415

    device = device_of_window(window_handle)
    colour = display_color(device)
    if not device or not colour.extended:
        return None
    capture = HdrCapture(device, frac, resolution, fps, colour.sdr_white_nits)
    try:
        capture.open()
    except Exception:  # noqa: BLE001 - SDR recording is the honest fallback
        log.warning("appshot: HDR capture unavailable; recording in SDR", exc_info=True)
        capture.close()
        return None
    log.info(
        "appshot: recording %s in HDR (%s, SDR white %.0f nits)",
        device, colour.mode, colour.sdr_white_nits,
    )
    return capture
