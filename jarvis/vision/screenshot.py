"""ScreenshotSource — primary-monitor screenshot via mss.

Returns an `Observation` with `source="screenshot_only"` (no UIA nodes,
the source is pure image capture). If desired, the PNG blob is stored under
`data/flight_recorder/blobs/<sha256>.png` so the flight recorder can replay
the raw observation.

Important Windows specifics (ADR-0002 plus experience from Phase 1c):

- `SetProcessDpiAwareness(2)` must be called once at init. Without it,
  Windows returns distorted coordinates (virtual instead of physical
  pixels) on 125%/150% scaling setups, which later causes systematic
  misses in `pyautogui.click(x, y)`.
- mss delivers BGRA; Pillow writes PNG from an RGB/RGBA array. We
  convert explicitly so the hash is stable (raw BGRA bytes are not
  portable).

The screenshot is a synchronous, blocking call; we wrap it in
`asyncio.to_thread` so the event loop stays responsive.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from jarvis.core.protocols import CancelToken, Observation

logger = logging.getLogger(__name__)

# Default blob directory — lives under repo-root/data. Can be overridden via
# the constructor (e.g. for tests).
_DEFAULT_BLOB_DIR = Path("data") / "flight_recorder" / "blobs"

# H1 (DEEP-DIVE-AUDIT-2026-06-19): on macOS, screen capture is gated behind a
# TCC "Screen Recording" grant. Without it mss returns ONLY the desktop
# wallpaper with no error, so Computer-Use would click blind. Surface a clear
# onboarding message at the capture site instead of failing silently.
_SCREEN_RECORDING_MSG = (
    "macOS Screen Recording permission not granted — grant it in Personal "
    "Jarvis > Settings > Permissions or System Settings > Privacy & Security "
    "> Screen Recording; without it screenshots can contain only the desktop "
    "wallpaper and Computer-Use would click blind."
)
_screen_recording_warned = False


def warn_if_screen_recording_denied() -> bool:
    """Probe Screen Recording now and log once per blocked-state episode.

    The native result is deliberately uncached: macOS can revoke a TCC grant
    while Jarvis is running. The boolean flag suppresses repeated log lines
    only; it never suppresses a permission probe. Unknown/unavailable native
    state fails closed on macOS and non-macOS reports ``NOT_REQUIRED``.
    """
    global _screen_recording_warned
    from jarvis.platform.permissions import (  # noqa: PLC0415
        PermissionId,
        get_system_permission_port,
    )

    port = get_system_permission_port()
    blocked = not port.runtime_access_granted(PermissionId.SCREEN_RECORDING)
    if blocked:
        state = port.state(PermissionId.SCREEN_RECORDING)
        if not _screen_recording_warned:
            logger.warning("%s Native state: %s.", _SCREEN_RECORDING_MSG, state.value)
        _screen_recording_warned = True
        return True
    if _screen_recording_warned:
        logger.info("macOS Screen Recording permission is available again.")
    _screen_recording_warned = False
    return False


# ---------------------------------------------------------------------------
# DPI awareness — extracted to jarvis/core/win32_dpi.py (Phase A1).
# Re-exported here so old code (tests, vision engine) keeps working
# without changes.
# ---------------------------------------------------------------------------

from jarvis.core.win32_dpi import ensure_dpi_awareness as _ensure_dpi_awareness  # noqa: E402

# ---------------------------------------------------------------------------
# ScreenshotSource
# ---------------------------------------------------------------------------

MonitorStrategy = Literal["foreground", "primary", "all"]


def cu_capture_strategy(monitor_policy: str) -> MonitorStrategy:
    """Map the ``[computer_use].monitor`` policy to the screenshot CAPTURE
    strategy (Problem 1, 2026-06-28).

    Both ``"primary"`` and ``"foreground"`` FOLLOW the foreground/target window,
    so the screenshot is never a pinned EMPTY monitor while the target sits on
    another screen — consistent with ``_capture_monitor_geometry`` (the click
    resolver), which already follows the foreground window. The difference is the
    *move*: the ``"primary"`` policy additionally brings the target onto the main
    monitor via the G8 ensure-on-primary hook (so the normal case lands on the
    main screen and the user sees it there), while a window that genuinely cannot
    be moved is still captured + clicked where it is instead of CU freezing on an
    empty primary. ``"all"`` captures the whole virtual desktop."""
    return "all" if monitor_policy == "all" else "foreground"


def select_capture_monitor(
    monitors: list[dict],
    *,
    strategy: MonitorStrategy = "foreground",
    primary_override: str = "primary",
) -> dict:
    """Selects the monitor a screenshot should be grabbed from.

    ``mss.monitors`` is 1-indexed for physical screens; ``[0]`` is
    the virtual bounding box over all of them. On multi-monitor setups,
    the hardcoded ``[1]`` is wrong as soon as the user works on a different
    display — Jarvis would otherwise see an "empty" monitor while the user
    is active on another one. The default strategy ``foreground`` therefore
    follows the active window.

    Strategies:

    - ``"foreground"`` — display with the largest foreground-window overlap.
      Fallback for a minimized/unfindable window: primary.
    - ``"primary"`` — explicitly the primary monitor (mss-typical ``[1]``).
    - ``"all"`` — virtual bounding box over all monitors (``[0]``).
    """
    if len(monitors) <= 1:
        return monitors[0]

    if strategy == "all":
        logger.debug("select_capture_monitor: strategy=all (virtual desktop)")
        return monitors[0]

    physical = monitors[1:]
    # Identify the primary ROBUSTLY (audit G8a) -- not by assuming origin (0,0).
    # mss dicts carry no ``is_primary`` flag and do NOT order the primary first
    # (a screen LEFT of primary is listed as physical[0] with negative X), so the
    # old ``physical[0]`` fallback acted on the wrong screen (the "CU works on my
    # non-main monitor" bug). ``resolve_primary_monitor`` asks the OS natively
    # (Win MONITORINFOF_PRIMARY / macOS CGMainDisplayID / X11 XRRGetOutputPrimary)
    # and honours the ``main_monitor`` override (primary | largest | explicit id).
    from jarvis.platform.monitors import resolve_primary_monitor  # noqa: PLC0415

    primary = resolve_primary_monitor(monitors, override=primary_override)

    if strategy == "primary":
        logger.debug("select_capture_monitor: strategy=primary -> %s", primary.get("name"))
        return primary

    try:
        # Cross-platform foreground follow (every OS is first-class): the
        # window identity + frame rect come from the one platform seam
        # (Win32 hwnd/DWM, macOS Quartz points, X11 xdotool root pixels) —
        # the same units the mss monitor rects use on each platform.
        from jarvis.platform import window_state as ws  # noqa: PLC0415

        win = ws.foreground_window()
        if win is None:
            logger.debug(
                "select_capture_monitor: no foreground window — falling back to primary",
            )
            return primary

        rect = ws.window_frame_rect(win) or ws.window_rect(win)
        if rect is None:
            logger.debug(
                "select_capture_monitor: foreground rect unreadable — falling back to primary",
            )
            return primary

        wl, wt, ww, wh = rect

        def overlap_area(m: dict) -> int:
            left = max(wl, int(m["left"]))
            top = max(wt, int(m["top"]))
            right = min(wl + ww, int(m["left"]) + int(m["width"]))
            bottom = min(wt + wh, int(m["top"]) + int(m["height"]))
            return max(0, right - left) * max(0, bottom - top)

        target = max(physical, key=overlap_area)
        target_overlap = overlap_area(target)
        if target_overlap > 0:
            if target is not primary:
                logger.info(
                    "select_capture_monitor: largest foreground overlap is on %s "
                    "(left=%d top=%d %dx%d) — capturing there instead of primary",
                    target.get("name"),
                    target["left"],
                    target["top"],
                    target["width"],
                    target["height"],
                )
            else:
                logger.debug(
                    "select_capture_monitor: largest foreground overlap is primary %s",
                    target.get("name"),
                )
            return target

        # Foreground rectangle intersects no physical monitor (for example a
        # minimized window at -32000/-32000 or a stale window geometry read).
        logger.debug(
            "select_capture_monitor: foreground rect %s is on no monitor — "
            "falling back to primary",
            rect,
        )
        return primary
    except Exception:  # noqa: BLE001
        logger.warning(
            "select_capture_monitor: foreground detection failed, using primary",
            exc_info=True,
        )
        return primary


# ---------------------------------------------------------------------------
# Region-of-interest crop around a screen point (AI Pointer step 4).
#
# A tight crop centered on the cursor — the scoped fallback the AI Pointer uses
# when the accessibility element under the cursor carries no label (a raster
# graphic). It is never a full-screen dump; the radius bounds the token cost.
# ---------------------------------------------------------------------------

def region_bbox_around(
    x: int,
    y: int,
    radius: int,
    *,
    virtual_bounds: tuple[int, int, int, int] | None = None,
) -> dict[str, int]:
    """An mss-style bbox dict centered on ``(x, y)`` with side ``2 * radius``.

    ``(x, y)`` and the returned coords are physical-pixel virtual-desktop
    coordinates (negative on a secondary monitor left of the primary). When
    ``virtual_bounds = (left, top, width, height)`` is given the crop is clamped
    so it never extends past the desktop.
    """
    r = max(1, int(radius))
    side = r * 2
    left = int(x) - r
    top = int(y) - r
    width = side
    height = side
    if virtual_bounds is not None:
        vl, vt, vw, vh = (int(v) for v in virtual_bounds)
        left = max(vl, min(left, vl + vw - 1))
        top = max(vt, min(top, vt + vh - 1))
        width = max(1, min(width, vl + vw - left))
        height = max(1, min(height, vt + vh - top))
    return {"left": left, "top": top, "width": width, "height": height}


def _mss_grab(bbox: dict[str, int]) -> tuple[tuple[int, int], bytes]:
    """Default grabber: capture an arbitrary screen rectangle via mss."""
    import mss  # type: ignore[import-not-found]  # noqa: PLC0415

    with mss.mss() as sct:
        raw = sct.grab(bbox)
    return (tuple(raw.size), raw.rgb)


def capture_region(
    bbox: dict[str, int],
    *,
    image_format: Literal["jpeg", "png"] = "jpeg",
    jpeg_quality: int = 85,
    grab=None,
) -> bytes:
    """Capture the screen rectangle ``bbox`` and return encoded image bytes.

    ``grab`` is injectable for tests: a callable ``(bbox) -> ((w, h), rgb_bytes)``.
    Defaults to :func:`_mss_grab`. JPEG by default (token-cheap; the model bills
    by pixel area, not bytes).
    """
    from PIL import Image  # noqa: PLC0415

    if warn_if_screen_recording_denied():
        raise RuntimeError(_SCREEN_RECORDING_MSG)
    grabber = grab or _mss_grab
    size, rgb = grabber(bbox)
    img = Image.frombytes("RGB", size, rgb)
    buf = io.BytesIO()
    if image_format == "jpeg":
        img.save(buf, format="JPEG", quality=jpeg_quality, optimize=False)
    else:
        img.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()


class ScreenshotSource:
    """Takes screenshots from the correct monitor via mss.

    Structurally satisfies `jarvis.core.protocols.VisionSource` — no
    `isinstance` import needed.

    Monitor strategy (default: ``"foreground"``):

    - ``"foreground"`` — follows the active window and selects the display
      with its largest window overlap. This remains correct for windows that
      straddle displays or whose center lies in an L-shaped layout gap.
      Fallback for a minimized/unfindable window: the primary monitor.
    - ``"primary"`` — the old hardcode (mss.monitors[1]). Only for
      regression tests / explicit single-monitor setups.
    - ``"all"`` — virtual bounding box over all monitors
      (mss.monitors[0]). Token-expensive, but maximum context.
    """

    name: str = "screenshot"
    kind: Literal["screenshot", "ui_tree", "composite"] = "screenshot"

    def __init__(
        self,
        *,
        save_blob: bool = True,
        blob_dir: Path | None = None,
        image_format: Literal["jpeg", "png"] = "jpeg",
        jpeg_quality: int = 85,
        monitor_strategy: MonitorStrategy = "foreground",
    ) -> None:
        _ensure_dpi_awareness()
        self._save_blob = save_blob
        self._blob_dir = blob_dir or _DEFAULT_BLOB_DIR
        self._image_format = image_format
        self._jpeg_quality = jpeg_quality
        self._monitor_strategy: MonitorStrategy = monitor_strategy
        self._closed = False
        # State-change flag for BitBlt / GDI transient errors (BUG-BitBlt):
        # True while the last grab failed; cleared on the next successful grab.
        # Used to emit exactly ONE warning log per error episode instead of
        # spamming the log every refresh cycle.
        self._bitblt_error_active: bool = False

    @property
    def mime_type(self) -> str:
        return "image/jpeg" if self._image_format == "jpeg" else "image/png"

    @property
    def file_extension(self) -> str:
        return ".jpg" if self._image_format == "jpeg" else ".png"

    # ---- Public API --------------------------------------------------------

    async def observe(
        self,
        *,
        cancel_token: CancelToken | None = None,
        window_title_filter: str | None = None,  # noqa: ARG002 — for the protocol signature
    ) -> Observation | None:
        """Takes a primary-monitor screenshot.

        `window_title_filter` is ignored here — a plain screenshot can't be
        filtered per window. The parameter still stays in the signature
        because of the protocol.

        Returns None when the GDI/BitBlt grab fails transiently (display
        asleep, locked workstation, resolution change). The caller (engine /
        context_provider) must treat None as "skip this frame and reuse the
        last good observation". This avoids spamming the log and keeps the
        refresh loop alive during monitor power-save / lock-screen events.
        """
        if self._closed:
            raise RuntimeError("ScreenshotSource is closed")
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        # Screenshot is synchronous — thread pool because of blocking GDI calls.
        image_bytes = await asyncio.to_thread(self._capture_image)

        # _capture_image returns None on a transient BitBlt / GDI error.
        # Propagate None upward so the engine/context_provider can skip the
        # frame gracefully without a traceback.
        if image_bytes is None:
            return None

        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        sha = hashlib.sha256(image_bytes).hexdigest()
        blob_path: str | None = None
        if self._save_blob:
            try:
                blob_path = await asyncio.to_thread(self._write_blob, sha, image_bytes)
            except OSError as exc:
                # A write failure (permission denied, disk full, antivirus block)
                # must not cancel the observation — but we do need to log it
                # loudly, otherwise the router later gets screenshot_path=None
                # and the vision-inject path silently raises a ValueError.
                logger.error(
                    "ScreenshotSource: blob write to %s failed: %s "
                    "— observation is returned without a disk path.",
                    self._blob_dir,
                    exc,
                    exc_info=True,
                )

        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=blob_path,
            screenshot_hash=sha,
            nodes=(),
            window_title="",
            active_pid=0,
            source="screenshot_only",
            pruning_stats={"nodes_before": 0, "nodes_after": 0, "depth_used": 0},
            # Thread the EXACT captured monitor so clicks map back to THIS screen
            # (mixed-DPI / multi-monitor consistency, live bug 2026-06-28).
            monitor_geom=getattr(self, "_last_capture_monitor", (0, 0, 0, 0)),
        )

    async def close(self) -> None:
        self._closed = True

    # ---- Internals ---------------------------------------------------------

    def _capture_image(self) -> bytes | None:
        """Blocking: takes a primary-monitor capture and returns image bytes.

        Format is `self._image_format` — JPEG q85 default (8x smaller than PNG
        at identical token cost, since Claude/GPT/Gemini bill by pixel area,
        not bytes). PNG only for tests/screenshots where pixel-perfect
        reproduction is needed.

        Returns None on transient GDI/BitBlt failure (display asleep, workstation
        locked, resolution change, disconnected monitor). The caller must treat
        None as "skip this frame" — the loop keeps running and recovers on the
        next successful grab.  Only one WARNING is logged per error episode
        (state-change logging: silent while the error persists, INFO on recovery).
        """
        # Late import, so the module stays importable even without mss
        # (contract tests run this way even when the dep is missing).
        try:
            import mss  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "mss is not installed — dependency from pyproject.toml is missing"
            ) from exc
        try:
            from PIL import Image  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("pillow is not installed") from exc

        # Lazy import of the exception class — same pattern as the mss import
        # above; keeps this module importable without mss installed.
        try:
            from mss.exception import ScreenShotError  # noqa: PLC0415
        except ImportError:
            # mss not installed — ImportError already raised above, unreachable.
            ScreenShotError = Exception  # type: ignore[assignment,misc]

        # H1: macOS can return only wallpaper without an error when Screen
        # Recording is missing. Probe on every capture and fail closed.
        if warn_if_screen_recording_denied():
            raise RuntimeError(_SCREEN_RECORDING_MSG)

        monitor_id: str = "unknown"
        try:
            with mss.mss() as sct:
                target = self._select_capture_monitor(sct.monitors)
                # Keep a human-readable monitor identity for the warning message.
                monitor_id = (
                    f"left={target.get('left', '?')},top={target.get('top', '?')},"
                    f"{target.get('width', '?')}x{target.get('height', '?')}"
                )
                # Record the EXACT monitor this frame was captured from so the
                # click-coordinate resolver maps the model's 0-1000 coords back to
                # THIS screen — not a separately-derived monitor that can diverge
                # on a mixed-DPI / multi-monitor desktop (live bug 2026-06-28).
                self._last_capture_monitor = (
                    int(target.get("left", 0)), int(target.get("top", 0)),
                    int(target.get("width", 0)), int(target.get("height", 0)),
                )
                raw = sct.grab(target)

        except ScreenShotError as exc:
            # Transient Windows GDI failure (BitBlt, display asleep, locked screen,
            # resolution change).  Log ONCE when the error state begins; stay silent
            # on subsequent failures in the same uninterrupted error run.
            if not self._bitblt_error_active:
                self._bitblt_error_active = True
                logger.warning(
                    "ScreenshotSource: BitBlt failed for monitor [%s] — "
                    "skipping frame (will retry; logged once per error episode): %s",
                    monitor_id,
                    exc,
                )
            return None

        # --- Successful grab: clear the error-state flag and log recovery. ---
        if self._bitblt_error_active:
            self._bitblt_error_active = False
            logger.info(
                "ScreenshotSource: BitBlt recovered for monitor [%s].", monitor_id
            )

        img = Image.frombytes("RGB", raw.size, raw.rgb)
        buf = io.BytesIO()
        if self._image_format == "jpeg":
            img.save(buf, format="JPEG", quality=self._jpeg_quality, optimize=False)
        else:
            img.save(buf, format="PNG", optimize=False, compress_level=1)
        return buf.getvalue()

    def _select_capture_monitor(self, monitors: list[dict]) -> dict:
        """Delegates to the module function, so other paths
        (e.g. the ``screenshot`` router tool) can share the same logic.
        """
        return select_capture_monitor(monitors, strategy=self._monitor_strategy)

    def _write_blob(self, sha: str, image_bytes: bytes) -> str:
        """Stores the image blob under `<blob_dir>/<sha><ext>`."""
        self._blob_dir.mkdir(parents=True, exist_ok=True)
        target = self._blob_dir / f"{sha}{self.file_extension}"
        if not target.exists():
            # An atomic write is overkill here; the sha in the name gives idempotency.
            target.write_bytes(image_bytes)
        return str(target)
