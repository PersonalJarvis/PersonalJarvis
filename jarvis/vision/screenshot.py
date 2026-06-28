"""ScreenshotSource — Primary-Monitor-Screenshot via mss.

Liefert eine `Observation` mit `source="screenshot_only"` (keine UIA-Nodes,
die Source ist reiner Bildzug). Falls gewuenscht wird der PNG-Blob unter
`data/flight_recorder/blobs/<sha256>.png` abgelegt, damit der Flight-Recorder
die Roh-Observation replayen kann.

Wichtige Windows-Spezifika (ADR-0002 plus Erfahrung aus Phase 1c):

- `SetProcessDpiAwareness(2)` muss einmal beim Init aufgerufen werden.
  Ohne das liefert Windows auf 125 %/150 %-Scaling-Setups verzerrte
  Koordinaten (virtuelle statt physische Pixel), was spaeter beim
  `pyautogui.click(x, y)` zu systematischem Miss fuehrt.
- mss liefert BGRA; Pillow schreibt PNG aus einem RGB/RGBA-Array. Wir
  konvertieren explizit, damit der Hash stabil ist (BGRA-Rohbytes sind
  nicht portabel).

Der Screenshot ist synchron-blockierend; wir wrappen ihn in
`asyncio.to_thread` damit der Event-Loop responsive bleibt.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from jarvis.core.protocols import CancelToken, Observation

logger = logging.getLogger(__name__)

# Default-Blob-Verzeichnis — liegt unter Repo-Root/data. Kann ueber den
# Konstruktor ueberschrieben werden (z.B. fuer Tests).
_DEFAULT_BLOB_DIR = Path("data") / "flight_recorder" / "blobs"

# H1 (DEEP-DIVE-AUDIT-2026-06-19): on macOS, screen capture is gated behind a
# TCC "Screen Recording" grant. Without it mss returns ONLY the desktop
# wallpaper with no error, so Computer-Use would click blind. Surface a clear
# onboarding message at the capture site instead of failing silently.
_SCREEN_RECORDING_MSG = (
    "macOS Screen Recording permission not granted — grant it in System "
    "Settings > Privacy & Security > Screen Recording so Jarvis can see the "
    "screen; without it screenshots capture only the desktop wallpaper and "
    "Computer-Use would click blind."
)
_screen_recording_warned = False


def warn_if_screen_recording_denied() -> bool:
    """Log ``_SCREEN_RECORDING_MSG`` once per process if the macOS Screen
    Recording grant is missing.

    Returns ``True`` iff capture is expected to be blank (macOS + the grant is
    explicitly denied). No-op returning ``False`` on Windows/Linux, when the
    grant is present, or when it is unknown (``None`` — pyobjc absent): we never
    nag on a state we cannot prove (AD-13 detect-and-degrade).

    One-shot: once a denial has been seen the probe is not re-run, so the
    TCC round-trip never happens at the CU frame rate (1-2 Hz) — subsequent
    calls are a single boolean read.
    """
    global _screen_recording_warned
    if _screen_recording_warned:  # already warned once → still denied; skip the per-frame TCC probe
        return True
    from jarvis.platform.probes import screen_recording_granted  # noqa: PLC0415

    if screen_recording_granted() is False:
        _screen_recording_warned = True
        logger.warning(_SCREEN_RECORDING_MSG)
        return True
    return False


# ---------------------------------------------------------------------------
# DPI-Awareness — extrahiert nach jarvis/core/win32_dpi.py (Phase A1).
# Re-Export hier damit alter Code (Tests, Vision-Engine) ohne Aenderung
# weiterlaeuft.
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
    """Waehlt den Monitor, von dem ein Screenshot gegrabbt werden soll.

    ``mss.monitors`` ist 1-indexiert fuer physische Bildschirme; ``[0]`` ist
    die virtuelle Bounding-Box ueber alle. Auf Multi-Monitor-Setups ist
    hardcoded ``[1]`` falsch, sobald der User auf einem anderen Display
    arbeitet — Jarvis wuerde sonst einen "leeren" Monitor sehen, waehrend
    der User auf einem anderen aktiv ist. Die Default-Strategie ``foreground``
    folgt deshalb dem aktiven Fenster.

    Strategien:

    - ``"foreground"`` — Foreground-Window-Center -> Monitor-Lookup.
      Fallback bei minimiertem/unauffindbarem Fenster: Primary.
    - ``"primary"`` — explizit der Primaer-Monitor (mss-typisch ``[1]``).
    - ``"all"`` — virtuelle Bounding-Box ueber alle Monitore (``[0]``).
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

    if os.name != "nt":
        return primary

    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            logger.debug(
                "select_capture_monitor: kein Foreground-Window — fallback auf primary",
            )
            return primary

        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            logger.debug(
                "select_capture_monitor: GetWindowRect fehlgeschlagen — fallback auf primary",
            )
            return primary

        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2

        for m in physical:
            left, top = m["left"], m["top"]
            right = left + m["width"]
            bottom = top + m["height"]
            if left <= cx < right and top <= cy < bottom:
                if m is not primary:
                    logger.info(
                        "select_capture_monitor: Foreground auf %s (left=%d top=%d %dx%d) — capture dort statt Primary",
                        m.get("name"),
                        left,
                        top,
                        m["width"],
                        m["height"],
                    )
                else:
                    logger.debug(
                        "select_capture_monitor: Foreground auf Primary %s",
                        m.get("name"),
                    )
                return m

        # Foreground-Center liegt ausserhalb aller physischen Monitore
        # (z.B. Fenster minimiert -> rect = -32000/-32000).
        logger.debug(
            "select_capture_monitor: Foreground-Center (%d,%d) auf keinem Monitor — fallback auf primary",
            cx,
            cy,
        )
        return primary
    except Exception:  # noqa: BLE001
        logger.warning(
            "select_capture_monitor: Foreground-Detection fehlgeschlagen, nutze Primary",
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
    """Nimmt Screenshots vom richtigen Monitor via mss auf.

    Erfuellt `jarvis.core.protocols.VisionSource` strukturell — kein
    `isinstance`-Import noetig.

    Monitor-Strategie (Default: ``"foreground"``):

    - ``"foreground"`` — folgt dem aktiven Fenster (GetForegroundWindow +
      GetWindowRect → Monitor-Lookup ueber den Window-Mittelpunkt). Damit
      sieht Jarvis das, was der User gerade vor sich hat, auch auf
      Multi-Monitor-Setups. Fallback bei minimiertem/unauffindbarem
      Fenster: Primary-Monitor.
    - ``"primary"`` — alter Hardcode (mss.monitors[1]). Nur fuer
      Regression-Tests / explizite Einzel-Monitor-Setups.
    - ``"all"`` — virtuelle Bounding-Box ueber alle Monitore
      (mss.monitors[0]). Token-teuer, aber maximaler Kontext.
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
        window_title_filter: str | None = None,  # noqa: ARG002 — fuer Protocol-Signatur
    ) -> Observation | None:
        """Nimmt einen Primary-Monitor-Screenshot auf.

        `window_title_filter` wird hier ignoriert — ein reiner Screenshot
        kann nicht pro-Fenster gefiltert werden. Der Parameter bleibt aber
        in der Signatur wegen dem Protocol.

        Returns None when the GDI/BitBlt grab fails transiently (display
        asleep, locked workstation, resolution change). The caller (engine /
        context_provider) must treat None as "skip this frame and reuse the
        last good observation". This avoids spamming the log and keeps the
        refresh loop alive during monitor power-save / lock-screen events.
        """
        if self._closed:
            raise RuntimeError("ScreenshotSource ist geschlossen")
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        # Screenshot synchron — Thread-Pool wegen blockierender GDI-Calls.
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
                # Write-Fehler (Permission denied, Disk full, Antivirus-Block)
                # duerfen die Observation nicht canceln — aber wir muessen es
                # laut loggen, sonst kriegt der Router spaeter screenshot_path
                # =None und der Vision-Inject-Pfad wirft ValueError silent.
                logger.error(
                    "ScreenshotSource: Blob-Write nach %s fehlgeschlagen: %s "
                    "— Observation wird ohne Disk-Pfad zurueckgegeben.",
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
        )

    async def close(self) -> None:
        self._closed = True

    # ---- Internals ---------------------------------------------------------

    def _capture_image(self) -> bytes | None:
        """Blocking: nimmt Primary-Monitor auf und gibt Bild-Bytes zurueck.

        Format ist `self._image_format` — JPEG q85 default (8x kleiner als PNG
        bei identischen Token-Kosten, da Claude/GPT/Gemini in Pixel-Area
        rechnen, nicht in Bytes). PNG nur fuer Tests/Screenshots wo Pixel-
        perfekte Reproduktion gebraucht wird.

        Returns None on transient GDI/BitBlt failure (display asleep, workstation
        locked, resolution change, disconnected monitor). The caller must treat
        None as "skip this frame" — the loop keeps running and recovers on the
        next successful grab.  Only one WARNING is logged per error episode
        (state-change logging: silent while the error persists, INFO on recovery).
        """
        # Late-Import, damit das Modul auch ohne mss importierbar bleibt
        # (Contract-Tests laufen so, selbst wenn die Dep fehlt).
        try:
            import mss  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "mss ist nicht installiert — Dependency aus pyproject.toml fehlt"
            ) from exc
        try:
            from PIL import Image  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("pillow ist nicht installiert") from exc

        # Lazy import of the exception class — same pattern as the mss import
        # above; keeps this module importable without mss installed.
        try:
            from mss.exception import ScreenShotError  # noqa: PLC0415
        except ImportError:
            # mss not installed — ImportError already raised above, unreachable.
            ScreenShotError = Exception  # type: ignore[assignment,misc]

        # H1: on macOS without the Screen-Recording grant the grab below returns
        # only the wallpaper with no error — tell the user once instead of
        # letting Computer-Use click blind.
        warn_if_screen_recording_denied()

        monitor_id: str = "unknown"
        try:
            with mss.mss() as sct:
                target = self._select_capture_monitor(sct.monitors)
                # Keep a human-readable monitor identity for the warning message.
                monitor_id = (
                    f"left={target.get('left', '?')},top={target.get('top', '?')},"
                    f"{target.get('width', '?')}x{target.get('height', '?')}"
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
        """Delegiert an die Modul-Funktion, damit andere Pfade
        (z.B. das ``screenshot``-Router-Tool) dieselbe Logik teilen koennen.
        """
        return select_capture_monitor(monitors, strategy=self._monitor_strategy)

    def _write_blob(self, sha: str, image_bytes: bytes) -> str:
        """Speichert den Bild-Blob unter `<blob_dir>/<sha><ext>`."""
        self._blob_dir.mkdir(parents=True, exist_ok=True)
        target = self._blob_dir / f"{sha}{self.file_extension}"
        if not target.exists():
            # Atomares Write ist hier uebertrieben; sha im Namen ist Idempotenz.
            target.write_bytes(image_bytes)
        return str(target)
