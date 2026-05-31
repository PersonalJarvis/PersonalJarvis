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


def select_capture_monitor(
    monitors: list[dict],
    *,
    strategy: MonitorStrategy = "foreground",
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
    primary = next((m for m in physical if m.get("is_primary")), physical[0])

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
    ) -> Observation:
        """Nimmt einen Primary-Monitor-Screenshot auf.

        `window_title_filter` wird hier ignoriert — ein reiner Screenshot
        kann nicht pro-Fenster gefiltert werden. Der Parameter bleibt aber
        in der Signatur wegen dem Protocol.
        """
        if self._closed:
            raise RuntimeError("ScreenshotSource ist geschlossen")
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        # Screenshot synchron — Thread-Pool wegen blockierender GDI-Calls.
        image_bytes = await asyncio.to_thread(self._capture_image)

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

    def _capture_image(self) -> bytes:
        """Blocking: nimmt Primary-Monitor auf und gibt Bild-Bytes zurueck.

        Format ist `self._image_format` — JPEG q85 default (8x kleiner als PNG
        bei identischen Token-Kosten, da Claude/GPT/Gemini in Pixel-Area
        rechnen, nicht in Bytes). PNG nur fuer Tests/Screenshots wo Pixel-
        perfekte Reproduktion gebraucht wird.
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

        with mss.mss() as sct:
            target = self._select_capture_monitor(sct.monitors)
            raw = sct.grab(target)

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
