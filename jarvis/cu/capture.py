"""Stable-frame capture for Computer-Use v2.

Two structural fixes over the legacy engine live here:

1. **UI-idle instead of fixed sleeps.** The legacy loop slept fixed settle
   times (0.6 s and friends) and still acted on half-rendered UIs — timing
   errors are ~15 % of GUI-agent failures in the literature. Here a frame is
   only handed to the model once two consecutive grabs are visually stable
   (thumbnail diff below threshold) or a bounded timeout passed; the common
   case returns after one cheap re-grab (~150 ms), the worst case is capped.
2. **One CoordinateMapper per frame.** The mapper is built from the exact
   capture rect and the exact downscaled image size of THIS frame — the only
   object action coordinates may resolve through.

Frames are downscaled to a model-friendly size (Anthropic guidance: do not
send screenshots much above XGA/WXGA; own downscaling beats provider-side
resizing for grounding accuracy) and encoded as JPEG (providers bill by
pixel area, not bytes).

All functions are synchronous and thread-safe; the engine calls them via
``asyncio.to_thread``. Grabs run inside :func:`jarvis.cu.geometry.input_space`
so rects stay in input units on mixed-DPI Windows.
"""
from __future__ import annotations

import hashlib
import io
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jarvis.cu.geometry import CoordinateMapper, MonitorInfo, input_space

logger = logging.getLogger(__name__)

#: Longest image side sent to the model. ~1.3k keeps small controls legible
#: while staying in the resolution band vision models ground reliably.
DEFAULT_MAX_DIMENSION = 1366
DEFAULT_JPEG_QUALITY = 85

#: Stability probe: re-grab interval and total budget. The budget bounds the
#: worst case (video playing => never stable) — we then act on the freshest
#: frame and mark it unstable so the loop can be more careful.
DEFAULT_STABILITY_INTERVAL_S = 0.15
DEFAULT_STABILITY_TIMEOUT_S = 1.2

#: Mean absolute thumbnail difference (0..255) below which two grabs count as
#: "the same screen". Blinking cursors / clock seconds stay well below this;
#: page loads, dialogs and animations exceed it clearly.
STABILITY_DIFF_THRESHOLD = 2.0

#: Thumbnail size used for the stability diff — cheap and cursor-blind enough.
_THUMB_SIZE = (96, 54)


class Grabber(Protocol):
    """Injectable screen grabber: ``bbox -> ((width, height), rgb_bytes)``."""

    def __call__(self, bbox: dict[str, int]) -> tuple[tuple[int, int], bytes]: ...


def mss_grab(bbox: dict[str, int]) -> tuple[tuple[int, int], bytes]:
    """Default grabber via mss, inside the thread DPI pin."""
    import mss  # noqa: PLC0415

    with input_space(), mss.mss() as sct:
        raw = sct.grab(bbox)
    return (tuple(raw.size), raw.rgb)


@dataclass(frozen=True)
class Frame:
    """One perception frame: the image the model sees + its mapper.

    ``sha256`` identifies the exact encoded image (blob dedup, events).
    ``thumb`` is the PERCEPTUAL identity (see :func:`screen_thumb`): the
    key the idempotency ledger and the no-progress guard compare on via
    :func:`thumbs_similar`. An exact hash flips on every caret blink, which
    let duplicate actions through as "the screen changed" (live rig run
    2026-07-02).
    """

    jpeg: bytes
    image_width: int
    image_height: int
    mapper: CoordinateMapper
    sha256: str
    thumb: bytes
    captured_at_ns: int
    stable: bool
    blob_path: str | None = None


def screen_thumb(raw: tuple[tuple[int, int], bytes]) -> bytes:
    """Grayscale 96x54 thumbnail bytes — the frame's perceptual identity.

    Compared with :func:`thumbs_similar` (mean-abs-diff threshold), NOT by
    equality/hash: any hash quantization has boundary artifacts where a
    one-gray-level caret blink flips the identity and lets a duplicate
    action through.
    """
    from PIL import Image  # noqa: PLC0415

    return Image.frombytes("RGB", raw[0], raw[1]).convert("L").resize(_THUMB_SIZE).tobytes()


def thumbs_similar(
    a: bytes | str, b: bytes | str, *, threshold: float = STABILITY_DIFF_THRESHOLD,
) -> bool:
    """Are two screen identities visually the same screen?

    Fast-path equality; a real thumbnail pair is compared by mean absolute
    difference (caret blinks / antialiasing noise stay below the threshold).
    Opaque non-thumbnail keys (tests, foreign callers) fall back to equality.
    """
    if a == b:
        return True
    expected = _THUMB_SIZE[0] * _THUMB_SIZE[1]
    if (
        not isinstance(a, (bytes, bytearray))
        or not isinstance(b, (bytes, bytearray))
        or len(a) != expected
        or len(b) != expected
    ):
        return False
    from PIL import Image, ImageChops, ImageStat  # noqa: PLC0415

    img_a = Image.frombytes("L", _THUMB_SIZE, bytes(a))
    img_b = Image.frombytes("L", _THUMB_SIZE, bytes(b))
    mean = ImageStat.Stat(ImageChops.difference(img_a, img_b)).mean[0]
    return mean <= threshold


def frames_differ(
    a: tuple[tuple[int, int], bytes],
    b: tuple[tuple[int, int], bytes],
    *,
    threshold: float = STABILITY_DIFF_THRESHOLD,
) -> bool:
    """True when two raw grabs are visually different.

    Byte equality would flag every blinking cursor; instead both frames are
    reduced to small grayscale thumbnails and compared by mean absolute
    difference. Differing sizes (resolution change mid-capture) always count
    as different.
    """
    if a[0] != b[0]:
        return True
    if a[1] == b[1]:
        return False
    from PIL import Image, ImageChops, ImageStat  # noqa: PLC0415

    def thumb(raw: tuple[tuple[int, int], bytes]):
        img = Image.frombytes("RGB", raw[0], raw[1])
        return img.convert("L").resize(_THUMB_SIZE)

    diff = ImageChops.difference(thumb(a), thumb(b))
    mean = ImageStat.Stat(diff).mean[0]
    return mean > threshold


def select_monitor(policy: str, *, main_monitor: str = "primary") -> MonitorInfo:
    """Resolve which screen rect to capture, per ``[computer_use].monitor``.

    Reuses the proven selector from the vision layer (foreground-window
    lookup, robust primary resolution) and returns the rect as a
    :class:`MonitorInfo` in input units. Raises ``RuntimeError`` on a
    headless host — the engine turns that into an honest mission failure.
    """
    import mss  # noqa: PLC0415

    from jarvis.vision.screenshot import (  # noqa: PLC0415
        cu_capture_strategy,
        select_capture_monitor,
    )

    with input_space(), mss.mss() as sct:
        monitors = sct.monitors
        if not monitors:
            raise RuntimeError("no display present — cannot capture the screen")
        strategy = cu_capture_strategy(policy)
        target = select_capture_monitor(
            monitors, strategy=strategy, primary_override=main_monitor,
        )
        return MonitorInfo(
            left=int(target["left"]),
            top=int(target["top"]),
            width=int(target["width"]),
            height=int(target["height"]),
            name=str(target.get("name", "") or ""),
        )


#: A window-scoped capture below this size (input units) is useless to a
#: vision model — fall back to the whole monitor instead.
_MIN_WINDOW_CAPTURE_W = 160
_MIN_WINDOW_CAPTURE_H = 120


def select_capture_target(
    policy: str,
    *,
    main_monitor: str = "primary",
    scope: str = "window",
) -> MonitorInfo:
    """Resolve the rect Computer-Use captures AND acts on.

    ``scope="window"`` (default) applies the industry-standard framing for
    pixel-grounded GUI agents: the model sees the TARGET WINDOW, not a whole
    monitor (OpenAI CUA: fixed viewport; Anthropic reference: one small
    display the app fills; Microsoft UFO: per-application screenshots). A
    small window floating on a large desktop otherwise shrinks to stamp size
    in the downscaled frame and surrounds itself with wallpaper — grounding
    errors then land OUTSIDE the app and steal its focus (live incident
    2026-07-02, three desktop clicks in a row next to a restored Chrome).

    Cropping the capture to the window also makes stray clicks structurally
    impossible: the :class:`CoordinateMapper` clamps every model coordinate
    into the capture rect, so the click cannot leave the window.

    Falls back to the ``policy`` monitor (previous behaviour) when the
    foreground window is the shell, has no readable rect (macOS/Linux today,
    or a headless probe), or is too small to be a real work surface.
    ``scope="monitor"`` restores the previous behaviour entirely.
    """
    monitor = select_monitor(policy, main_monitor=main_monitor)
    if scope != "window" or policy == "all":
        return monitor

    from jarvis.platform import window_state as ws  # noqa: PLC0415

    with input_space():
        win = ws.foreground_window()
        if win is None or ws.is_shell_window(win):
            return monitor
        rect = ws.window_frame_rect(win)
    if rect is None:
        return monitor
    left, top, width, height = rect
    # Clamp to the selected monitor: a window straddling a mixed-DPI boundary
    # must not produce a grab that crosses coordinate spaces.
    clamped_left = max(left, monitor.left)
    clamped_top = max(top, monitor.top)
    clamped_right = min(left + width, monitor.left + monitor.width)
    clamped_bottom = min(top + height, monitor.top + monitor.height)
    clamped_w = clamped_right - clamped_left
    clamped_h = clamped_bottom - clamped_top
    if clamped_w < _MIN_WINDOW_CAPTURE_W or clamped_h < _MIN_WINDOW_CAPTURE_H:
        return monitor
    logger.debug(
        "[cu] window-scoped capture: '%s' rect=(%d,%d %dx%d)",
        (win.title or "")[:60], clamped_left, clamped_top, clamped_w, clamped_h,
    )
    return MonitorInfo(
        left=clamped_left,
        top=clamped_top,
        width=clamped_w,
        height=clamped_h,
        name=f"window:{(win.title or '')[:48]}",
    )


def _downscale_and_encode(
    raw: tuple[tuple[int, int], bytes],
    *,
    max_dimension: int,
    jpeg_quality: int,
) -> tuple[bytes, int, int]:
    """Uniformly downscale a raw grab and JPEG-encode it."""
    from PIL import Image  # noqa: PLC0415

    (w, h), rgb = raw
    img = Image.frombytes("RGB", (w, h), rgb)
    longest = max(w, h)
    if max_dimension > 0 and longest > max_dimension:
        scale = max_dimension / longest
        new_w = max(1, round(w * scale))
        new_h = max(1, round(h * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality, optimize=False)
    return buf.getvalue(), img.width, img.height


def capture_stable_frame(
    monitor: MonitorInfo,
    *,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    stability_interval_s: float = DEFAULT_STABILITY_INTERVAL_S,
    stability_timeout_s: float = DEFAULT_STABILITY_TIMEOUT_S,
    grab: Grabber | None = None,
    blob_dir: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Frame:
    """Capture the monitor, waiting briefly for the UI to settle.

    Grabs, re-grabs after ``stability_interval_s`` and keeps re-grabbing while
    the screen is still changing, up to ``stability_timeout_s``. Returns the
    freshest grab either way; ``Frame.stable`` records whether idle was
    reached. Never raises on a merely-unstable screen — only on a failed grab.
    """
    grabber = grab or mss_grab
    deadline = time.monotonic() + max(0.0, stability_timeout_s)
    current = grabber(monitor.bbox)
    stable = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sleep(min(max(0.01, stability_interval_s), remaining))
        nxt = grabber(monitor.bbox)
        if not frames_differ(current, nxt):
            current = nxt
            stable = True
            break
        current = nxt

    jpeg, iw, ih = _downscale_and_encode(
        current, max_dimension=max_dimension, jpeg_quality=jpeg_quality,
    )
    thumb = screen_thumb(current)
    mapper = CoordinateMapper(
        capture_left=monitor.left,
        capture_top=monitor.top,
        capture_width=monitor.width,
        capture_height=monitor.height,
        image_width=iw,
        image_height=ih,
    )
    sha = hashlib.sha256(jpeg).hexdigest()
    blob_path: str | None = None
    if blob_dir is not None:
        try:
            blob_dir.mkdir(parents=True, exist_ok=True)
            target = blob_dir / f"{sha}.jpg"
            if not target.exists():
                target.write_bytes(jpeg)
            blob_path = str(target)
        except OSError:
            logger.warning(
                "[cu] frame blob write to %s failed — frame kept in memory only",
                blob_dir, exc_info=True,
            )
    return Frame(
        jpeg=jpeg,
        image_width=iw,
        image_height=ih,
        mapper=mapper,
        sha256=sha,
        thumb=thumb,
        captured_at_ns=time.time_ns(),
        stable=stable,
        blob_path=blob_path,
    )


def grab_region(
    bbox: dict[str, int], *, grab: Grabber | None = None,
) -> tuple[tuple[int, int], bytes] | None:
    """One raw region grab for pre/post verification diffs.

    Returns ``None`` on any failure (headless, transient GDI error) so
    verification degrades to "cannot tell" instead of killing the action.
    """
    grabber = grab or mss_grab
    try:
        return grabber(bbox)
    except Exception:  # noqa: BLE001
        logger.debug("[cu] region grab failed (non-fatal)", exc_info=True)
        return None
