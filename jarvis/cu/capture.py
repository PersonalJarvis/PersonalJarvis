"""Stable-frame capture for Computer-Use v2.

Two structural fixes over the legacy engine live here:

1. **UI-idle instead of fixed sleeps.** The legacy loop slept fixed settle
   times (0.6 s and friends) and still acted on half-rendered UIs — timing
   errors are ~15 % of GUI-agent failures in the literature. Here a frame is
   only handed to the model once two consecutive grabs are visually stable
   (thumbnail diff below threshold) or a bounded timeout passed; the common
   case returns after one frame-paced re-grab, the worst case is capped.
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
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jarvis.cu.geometry import CoordinateMapper, MonitorInfo, input_space

logger = logging.getLogger(__name__)

#: Longest image side sent to the model. ~1.3k keeps small controls legible
#: while staying in the resolution band vision models ground reliably.
DEFAULT_MAX_DIMENSION = 1366
DEFAULT_JPEG_QUALITY = 85

#: Stability probe: re-grab interval and total budget. Two captures already
#: consume measurable wall time on every supported backend, so an additional
#: 150 ms blind sleep made the common stable-screen path pay three separate
#: latency costs (grab + sleep + grab). A 20 ms floor spans at least one frame
#: on ordinary 60 Hz desktops while the two real grabs add their own capture
#: time. Changing/animated surfaces still keep polling up to the unchanged
#: timeout below; only the already-stable path exits earlier.
DEFAULT_STABILITY_INTERVAL_S = 0.02
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


@dataclass(frozen=True)
class _CapturedPixels:
    """Internal zero-copy-ish MSS pixels kept in native BGRX order.

    Public/injected grabbers retain the long-standing RGB tuple contract. The
    call-scoped MSS path delays its one color conversion until Pillow encodes
    the final model frame instead of materializing a 3-byte RGB desktop after
    every stability grab.
    """

    size: tuple[int, int]
    data: bytes | bytearray | memoryview


_RawCapture = tuple[tuple[int, int], bytes] | _CapturedPixels


def _capture_size(raw: _RawCapture) -> tuple[int, int]:
    return raw.size if isinstance(raw, _CapturedPixels) else raw[0]


def _capture_image(raw: _RawCapture):
    """Build a Pillow RGB image from public RGB or internal MSS BGRX pixels."""
    from PIL import Image  # noqa: PLC0415

    if isinstance(raw, _CapturedPixels):
        return Image.frombytes("RGB", raw.size, raw.data, "raw", "BGRX")
    return Image.frombytes("RGB", raw[0], raw[1])


def _capture_thumb(raw: _RawCapture) -> bytes:
    """Area-filtered perceptual identity shared by stability and ledger."""
    from PIL import Image  # noqa: PLC0415

    return (
        _capture_image(raw)
        .convert("L")
        .resize(
            _THUMB_SIZE,
            Image.Resampling.BICUBIC,
            reducing_gap=2.0,
        )
        .tobytes()
    )


def _require_macos_screen_recording_permission() -> None:
    """Fail closed unless macOS currently permits screen capture.

    The native state is probed before every grab, not only at mission start,
    because a user can revoke Screen Recording while Jarvis is running.
    """
    from jarvis.platform import detect_platform  # noqa: PLC0415

    if detect_platform() != "darwin":
        return

    from jarvis.platform.permissions import (  # noqa: PLC0415
        PermissionId,
        PermissionState,
        get_system_permission_port,
    )

    port = get_system_permission_port()
    if not port.runtime_access_granted(PermissionId.SCREEN_RECORDING):
        state = port.state(PermissionId.SCREEN_RECORDING)
        detail = (
            state.value
            if state is not PermissionState.GRANTED
            else "grant belongs to an unstable app identity or needs restart"
        )
        raise RuntimeError(
            "Cannot capture the screen on macOS because Screen Recording "
            f"permission is not ready ({detail}). Open Personal Jarvis "
            "> Settings > Permissions (or System Settings > Privacy & "
            "Security > Screen Recording), grant access, then retry."
        )


def mss_grab(bbox: dict[str, int]) -> tuple[tuple[int, int], bytes]:
    """Default grabber via mss, inside the thread DPI pin.

    Wrapped in the indicator capture guard: on hosts without a native
    capture-exclusion API (non-Windows) the yellow CU screen border is
    blanked for the duration of the grab so the model never sees it.
    """
    import mss  # noqa: PLC0415

    from jarvis.cu.indicator.capture_guard import (  # noqa: PLC0415
        indicator_suppressed,
    )

    with indicator_suppressed(), input_space(), mss.mss() as sct:
        raw = sct.grab(bbox)
    return (tuple(raw.size), raw.rgb)


def grabber_for(
    target: MonitorInfo,
    *,
    _rect_grab: Grabber | None = None,
) -> Grabber:
    """The grabber for a capture target.

    A window target (``window_handle`` set) prefers the platform's NATIVE
    per-window capture (:func:`jarvis.platform.window_capture.grab_window`,
    e.g. ScreenCaptureKit by CGWindowID on macOS) and falls back to the rect
    grab of the window's frame — pixel-identical for the raised foreground
    window. The native-unavailable verdict sticks for the grabber's lifetime
    (one perception frame) so stability re-grabs don't re-probe a dead path.
    Plain monitor rects grab via mss directly.
    """
    rect_grab = _rect_grab or mss_grab
    if target.window_handle is None:
        return rect_grab
    handle = int(target.window_handle)
    native_alive = True

    def grab(bbox: dict[str, int]) -> tuple[tuple[int, int], bytes]:
        nonlocal native_alive
        if native_alive:
            from jarvis.cu.indicator.capture_guard import (  # noqa: PLC0415
                indicator_suppressed,
            )
            from jarvis.platform import window_capture  # noqa: PLC0415

            # Native per-window capture can still include an overlapping
            # topmost overlay on some platforms — same guard as mss_grab.
            with indicator_suppressed():
                raw = window_capture.grab_window(handle, bbox)
            if raw is not None:
                return raw
            native_alive = False
        return rect_grab(bbox)

    return grab


@contextmanager
def _frame_grabber(
    target: MonitorInfo,
    injected: Grabber | None,
):
    """Yield one grabber whose MSS resources live for one perception frame.

    A stable frame needs at least two consecutive grabs. Opening and closing
    the OS capture backend for each one paid setup/teardown twice and, on
    Windows, rebuilt the same GDI resources. The owner stays call-local (never
    global or thread-local), closes before image encoding, and is created
    lazily so a successful native window capture never opens MSS at all.
    """
    if injected is not None:
        yield injected
        return

    with ExitStack() as stack:
        # On Windows the MSS handles inherit this thread's physical-pixel DPI
        # context. Keeping it for construction, grabs, and close prevents a
        # mixed-DPI session from changing coordinate spaces mid-frame; it is a
        # no-op on macOS/Linux.
        stack.enter_context(input_space())
        session = None

        def rect_grab(bbox: dict[str, int]) -> _CapturedPixels:
            nonlocal session
            if session is None:
                import mss  # noqa: PLC0415

                # ``mss.mss`` is the public constructor available across our
                # supported mss>=10.0 floor (the ``MSS`` alias arrived later).
                session = stack.enter_context(mss.mss())
            from jarvis.cu.indicator.capture_guard import (  # noqa: PLC0415
                indicator_suppressed,
            )

            # Suppress only while pixels are copied, not during the stability
            # interval, so the user's active-CU border does not flicker away.
            with indicator_suppressed():
                raw = session.grab(bbox)
            return _CapturedPixels(tuple(raw.size), raw.raw)

        # Private frame acquisition accepts the internal BGRX representation;
        # the public grabber_for/mss_grab API remains RGB tuple-compatible.
        yield grabber_for(target, _rect_grab=rect_grab)  # type: ignore[arg-type]


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


@dataclass(frozen=True)
class VisualProbe:
    """Compact pre/post evidence for action-effect verification.

    ``global_thumb`` carries the same area-filtered identity used by the
    idempotency ledger. ``local`` is a tight RGB crop around the target; it is
    captured separately so happy-path confirmation never has to copy/convert
    the full monitor again. Normalizing the crop to RGB discards MSS's unused
    fourth byte so backend padding cannot masquerade as a visual effect.
    """

    global_thumb: bytes | None
    local: tuple[tuple[int, int], bytes] | None


def screen_thumb(raw: tuple[tuple[int, int], bytes]) -> bytes:
    """Grayscale 96x54 thumbnail bytes — the frame's perceptual identity.

    Compared with :func:`thumbs_similar` (mean-abs-diff threshold), NOT by
    equality/hash: any hash quantization has boundary artifacts where a
    one-gray-level caret blink flips the identity and lets a duplicate
    action through.
    """
    return _capture_thumb(raw)


def thumbs_similar(
    a: bytes | str,
    b: bytes | str,
    *,
    threshold: float = STABILITY_DIFF_THRESHOLD,
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
    return not thumbs_similar(
        screen_thumb(a),
        screen_thumb(b),
        threshold=threshold,
    )


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
            monitors,
            strategy=strategy,
            primary_override=main_monitor,
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
    # A native per-window grab captures the FULL window — only wire the
    # window id through when the rect survived unclamped, so the mapper's
    # capture rect always matches the grabbed image (aspect guard).
    unclamped = (clamped_left, clamped_top, clamped_w, clamped_h) == rect
    logger.debug(
        "[cu] window-scoped capture: '%s' rect=(%d,%d %dx%d)",
        (win.title or "")[:60],
        clamped_left,
        clamped_top,
        clamped_w,
        clamped_h,
    )
    return MonitorInfo(
        left=clamped_left,
        top=clamped_top,
        width=clamped_w,
        height=clamped_h,
        name=f"window:{(win.title or '')[:48]}",
        window_handle=int(win.handle) if (win.handle and unclamped) else None,
    )


def _downscale_and_encode(
    raw: _RawCapture,
    *,
    max_dimension: int,
    jpeg_quality: int,
) -> tuple[bytes, int, int]:
    """Uniformly downscale a raw grab and JPEG-encode it."""
    from PIL import Image  # noqa: PLC0415

    w, h = _capture_size(raw)
    img = _capture_image(raw)
    longest = max(w, h)
    if max_dimension > 0 and longest > max_dimension:
        scale = max_dimension / longest
        new_w = max(1, round(w * scale))
        new_h = max(1, round(h * scale))
        # Pillow's reducing-gap path performs an integer box reduction before
        # the final Lanczos pass when a large virtual desktop is collapsed to
        # model size. It preserves the Lanczos final filter and coordinate
        # geometry while avoiding a full high-cost convolution over every
        # source pixel (about 3x faster for a 6400px desktop in the CU rig).
        img = img.resize(
            (new_w, new_h),
            Image.LANCZOS,
            reducing_gap=2.0,
        )
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
    capture_guard: Callable[[], bool] | None = None,
) -> Frame:
    """Capture the monitor, waiting briefly for the UI to settle.

    Grabs, re-grabs after ``stability_interval_s`` and keeps re-grabbing while
    the screen is still changing, up to ``stability_timeout_s``. Returns the
    freshest grab either way; ``Frame.stable`` records whether idle was
    reached. Never raises on a merely-unstable screen — only on a failed grab.

    The grabber defaults to :func:`grabber_for` — native per-window capture
    for window targets, the mss rect grab otherwise. Whatever grabbed the
    pixels, the frame's mapper is built from the TARGET rect in input units
    plus the encoded image size: the one central translation.
    """
    deadline = time.monotonic() + max(0.0, stability_timeout_s)
    # One permission probe per FRAME, not per re-grab: the stability loop can
    # re-grab repeatedly inside 1.2 s, and a grant cannot plausibly be revoked
    # and matter within that window — the engine independently re-probes
    # before every dispatched action, which is the check that prevents blind
    # input after a revocation.
    _require_macos_screen_recording_permission()
    stable = False
    with _frame_grabber(monitor, grab) as grabber:

        def guarded_grab() -> _RawCapture:
            if capture_guard is not None and not capture_guard():
                raise RuntimeError(
                    "foreground window changed before screen capture",
                )
            raw = grabber(monitor.bbox)
            if capture_guard is not None and not capture_guard():
                raise RuntimeError(
                    "foreground window changed during screen capture",
                )
            return raw

        current = guarded_grab()
        current_thumb = _capture_thumb(current)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sleep(min(max(0.01, stability_interval_s), remaining))
            nxt = guarded_grab()
            nxt_thumb = _capture_thumb(nxt)
            if thumbs_similar(current_thumb, nxt_thumb):
                current = nxt
                current_thumb = nxt_thumb
                stable = True
                break
            current = nxt
            current_thumb = nxt_thumb

    jpeg, iw, ih = _downscale_and_encode(
        current,
        max_dimension=max_dimension,
        jpeg_quality=jpeg_quality,
    )
    thumb = current_thumb
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
                blob_dir,
                exc_info=True,
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
    bbox: dict[str, int],
    *,
    grab: Grabber | None = None,
) -> tuple[tuple[int, int], bytes] | None:
    """One raw region grab for pre/post verification diffs.

    Returns ``None`` on capture failures (headless, transient GDI error, or a
    revoked macOS grant) so pixel-effect verification can report "cannot
    tell". The Computer-Use dispatcher independently rechecks Screen Recording
    immediately before every action, so this degradation cannot permit blind
    input after a revoked grant.
    """
    grabber = grab or mss_grab
    try:
        _require_macos_screen_recording_permission()
        return grabber(bbox)
    except Exception:  # noqa: BLE001
        logger.debug("[cu] region grab failed (non-fatal)", exc_info=True)
        return None


def _local_probe_bbox(
    bbox: dict[str, int],
    point: tuple[int, int],
    radius: int,
) -> dict[str, int] | None:
    left = int(bbox["left"])
    top = int(bbox["top"])
    right = left + int(bbox["width"])
    bottom = top + int(bbox["height"])
    x, y = (int(point[0]), int(point[1]))
    if not (left <= x < right and top <= y < bottom):
        return None
    return {
        "left": max(left, x - radius),
        "top": max(top, y - radius),
        "width": min(right, x + radius) - max(left, x - radius),
        "height": min(bottom, y + radius) - max(top, y - radius),
    }


def grab_visual_probe(
    bbox: dict[str, int],
    *,
    point: tuple[int, int] | None = None,
    radius: int = 110,
    local_only: bool = False,
) -> VisualProbe | None:
    """Capture compact visual-effect evidence directly from MSS BGRX pixels.

    A point probe takes one full-frame thumbnail for the remote-effect
    fallback plus an exact tight crop. Confirmation calls use
    ``local_only=True`` and copy only that crop. Missing capture remains
    tri-state ``None``; it never becomes proof of success or failure.
    """
    try:
        _require_macos_screen_recording_permission()
        import mss  # noqa: PLC0415

        from jarvis.cu.indicator.capture_guard import (  # noqa: PLC0415
            indicator_suppressed,
        )

        local_bbox = (
            _local_probe_bbox(bbox, point, max(1, int(radius))) if point is not None else None
        )
        with input_space(), mss.mss() as session:

            def shot(rect: dict[str, int]):
                with indicator_suppressed():
                    return session.grab(rect)

            global_thumb: bytes | None = None
            if not local_only:
                full = shot(bbox)
                global_thumb = _capture_thumb(
                    _CapturedPixels(tuple(full.size), full.raw),
                )
            local = None
            if local_bbox is not None:
                crop = shot(local_bbox)
                local = (tuple(crop.size), bytes(crop.rgb))
        return VisualProbe(global_thumb=global_thumb, local=local)
    except Exception:  # noqa: BLE001 — effect evidence is best-effort
        logger.debug("[cu] visual probe failed (non-fatal)", exc_info=True)
        return None
