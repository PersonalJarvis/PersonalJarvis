"""Mascot Position-Persistence + Monitor-Recovery. Plan §13.4 + §20.4.

Persistiert ``[overlay.mascot] position_monitor / x_relative / y_relative``
nach ``jarvis.toml`` via Atomic-Write (AD-11). Beim Boot:
  - Wenn ``position_monitor`` in EnumDisplayMonitors gefunden:
    Mascot dort an (rcWork.left + x_rel, rcWork.top + y_rel).
  - Wenn nicht (Laptop undocked, port-swap, monitor unplugged):
    deterministischer Fallback auf primary monitor + default (200, 80).

Wir umgehen ctypes hier — Qt's QGuiApplication.screens() liefert
``screen.name()`` was unter Win32 dem Device-Name (``\\.\DISPLAY1``)
entspricht. Das hat den Vorteil dass Tests headless laufen koennen
ohne Win32-Mock.

Atomic-Write nutzt tomllib + manuelles Schreiben (kein tomli_w noetig
weil wir nur eine bekannte Section editieren).
"""

from __future__ import annotations

import logging
import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MascotPosition:
    """Persistierte Position. ``monitor`` = Win32 device name (``\\\\.\\DISPLAY1``)."""

    monitor: str
    x_relative: int
    y_relative: int


@dataclass(frozen=True)
class ResolvedPlacement:
    """Ergebnis von ``resolve_placement()`` — wo das Window tatsaechlich hin soll."""

    abs_x: int
    abs_y: int
    monitor: str
    recovered: bool  # True wenn primary-fallback gegriffen hat


# Plan §13.4: defaults wenn die persistierte Position nicht restorbar ist.
DEFAULT_X_RELATIVE: int = 200
DEFAULT_Y_RELATIVE: int = 80
DEFAULT_MARGIN_PX: int = 16  # rcWork-Clamp + Snap-Toleranz


@dataclass(frozen=True)
class _ScreenSnapshot:
    """Plattform-frei. Aus ``QGuiApplication.screens()`` befuellt."""

    name: str  # Win32 device name unter Windows; auf Linux/macOS sym. id
    geometry: tuple[int, int, int, int]  # (x, y, w, h) work-area in logical px
    is_primary: bool


def screens_from_qt() -> list[_ScreenSnapshot]:
    """Qt-screens snapshot fuer resolve_placement(). Lazy-import damit
    der Loader auch headless ohne PySide6 importierbar bleibt."""
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        return []
    primary = QGuiApplication.primaryScreen()
    out: list[_ScreenSnapshot] = []
    for screen in QGuiApplication.screens():
        # availableGeometry = work-area (ohne Taskbar). Plan §13.4 nutzt
        # rcWork — Qt's availableGeometry mappt 1:1.
        geo = screen.availableGeometry()
        out.append(
            _ScreenSnapshot(
                name=screen.name(),
                geometry=(geo.x(), geo.y(), geo.width(), geo.height()),
                is_primary=(screen is primary),
            )
        )
    return out


def resolve_placement(
    persisted: Optional[MascotPosition],
    screens: Sequence[_ScreenSnapshot],
    *,
    mascot_size_px: int = 160,
) -> ResolvedPlacement:
    """Plan §13.4 — die kanonische 5-Schritt-Recovery.

    1. Wenn ``persisted.monitor`` in screens enthalten:
       Restore an (rcWork.left + x_rel, rcWork.top + y_rel) mit Clamp
       in rcWork minus 16-px-Margin minus mascot_size.
    2. Wenn nicht:
       Pick primary monitor, default (200, 80) Position.
       ``recovered=True`` damit Caller das loggen kann.
    3. Wenn keine Screens (Headless / Tests): default (0, 0)
       relative + leerer Monitor-Name; Caller muss damit umgehen.
    """
    if not screens:
        # Tests / headless ohne Qt-App.
        return ResolvedPlacement(
            abs_x=DEFAULT_X_RELATIVE,
            abs_y=DEFAULT_Y_RELATIVE,
            monitor="",
            recovered=True,
        )

    by_name = {s.name: s for s in screens}

    if persisted is not None and persisted.monitor in by_name:
        screen = by_name[persisted.monitor]
        sx, sy, sw, sh = screen.geometry
        # Clamp der relativen Coords in rcWork minus Margin minus
        # mascot_size — verhindert dass Mascot beim Restore von einem
        # Resolution-Change off-screen landet.
        max_rel_x = max(0, sw - mascot_size_px - DEFAULT_MARGIN_PX)
        max_rel_y = max(0, sh - mascot_size_px - DEFAULT_MARGIN_PX)
        rel_x = max(DEFAULT_MARGIN_PX, min(persisted.x_relative, max_rel_x))
        rel_y = max(DEFAULT_MARGIN_PX, min(persisted.y_relative, max_rel_y))
        return ResolvedPlacement(
            abs_x=sx + rel_x,
            abs_y=sy + rel_y,
            monitor=screen.name,
            recovered=False,
        )

    # Persisted-Monitor nicht da — primary fallback.
    primary = next((s for s in screens if s.is_primary), screens[0])
    sx, sy, _sw, _sh = primary.geometry
    return ResolvedPlacement(
        abs_x=sx + DEFAULT_X_RELATIVE,
        abs_y=sy + DEFAULT_Y_RELATIVE,
        monitor=primary.name,
        recovered=True,
    )


def snap_to_edges(
    abs_x: int,
    abs_y: int,
    monitor_geometry: tuple[int, int, int, int],
    *,
    mascot_size_px: int = 160,
    snap_tolerance_px: int = 16,
) -> tuple[int, int]:
    """Plan §13.3 — Edge-Snap innerhalb 16 px Toleranz.

    Berechnet die nearest snap fuer top/bottom/left/right Kanten und
    snapt wenn innerhalb Toleranz. Returnt (snapped_x, snapped_y).
    """
    sx, sy, sw, sh = monitor_geometry
    snapped_x, snapped_y = abs_x, abs_y

    # Distanz zur linken / rechten Kante.
    dist_left = abs_x - sx
    dist_right = (sx + sw) - (abs_x + mascot_size_px)
    if 0 <= dist_left <= snap_tolerance_px:
        snapped_x = sx
    elif 0 <= dist_right <= snap_tolerance_px:
        snapped_x = sx + sw - mascot_size_px

    # Distanz zur top / bottom Kante.
    dist_top = abs_y - sy
    dist_bottom = (sy + sh) - (abs_y + mascot_size_px)
    if 0 <= dist_top <= snap_tolerance_px:
        snapped_y = sy
    elif 0 <= dist_bottom <= snap_tolerance_px:
        snapped_y = sy + sh - mascot_size_px

    return snapped_x, snapped_y


def clamp_to_work_area(
    abs_x: int,
    abs_y: int,
    monitor_geometry: tuple[int, int, int, int],
    *,
    mascot_size_px: int = 160,
    margin_px: int = DEFAULT_MARGIN_PX,
) -> tuple[int, int]:
    """Verhindert Off-Screen-Drag. Plan §13.3 + §20.4.

    Mascot bleibt komplett innerhalb rcWork minus Margin.
    """
    sx, sy, sw, sh = monitor_geometry
    min_x = sx + margin_px
    min_y = sy + margin_px
    max_x = sx + sw - mascot_size_px - margin_px
    max_y = sy + sh - mascot_size_px - margin_px
    return (max(min_x, min(abs_x, max_x)), max(min_y, min(abs_y, max_y)))


# -------------------------------------------------------------------------
# TOML Read/Write — Plan §21.1 + §21.3 Atomic Pipeline (light variant).
# -------------------------------------------------------------------------


def load_position_from_toml(path: Path) -> Optional[MascotPosition]:
    """Liest ``[overlay.mascot]``-Felder aus ``jarvis.toml``. None
    wenn File fehlt oder Section/Felder nicht da."""
    if not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning("load_position_from_toml: %s", exc)
        return None
    overlay = data.get("overlay") or {}
    section = overlay.get("mascot") or {}
    monitor = section.get("position_monitor", "")
    if not isinstance(monitor, str):
        monitor = ""
    x_rel = int(section.get("position_x_relative", DEFAULT_X_RELATIVE))
    y_rel = int(section.get("position_y_relative", DEFAULT_Y_RELATIVE))
    return MascotPosition(monitor=monitor, x_relative=x_rel, y_relative=y_rel)


def save_position_to_toml(path: Path, position: MascotPosition) -> None:
    """Atomic-Write der drei Position-Felder. Plan §21.3 light:
       1. read existing TOML als Text
       2. ersetze die drei Felder via line-based regex
       3. tempfile + os.replace()

    Wir schreiben TOML manuell (text-based) statt mit tomli_w weil das
    Format dann den User-Whitespace und Comments beibehalt — wichtig
    weil ``jarvis.toml`` user-editable ist und wir keine
    Comments fressen wollen.
    """
    import re

    if not path.is_file():
        # Fresh File — minimal initial section schreiben.
        new_text = (
            "[overlay.mascot]\n"
            f'position_monitor = "{_escape_toml_str(position.monitor)}"\n'
            f"position_x_relative = {position.x_relative}\n"
            f"position_y_relative = {position.y_relative}\n"
        )
        _atomic_write_text(path, new_text)
        return

    text = path.read_text(encoding="utf-8")

    # Section [overlay.mascot] muss existieren oder wir appenden.
    section_re = re.compile(r"^\[overlay\.mascot\]\s*$", re.MULTILINE)
    if not section_re.search(text):
        if not text.endswith("\n"):
            text += "\n"
        text += (
            "\n[overlay.mascot]\n"
            f'position_monitor = "{_escape_toml_str(position.monitor)}"\n'
            f"position_x_relative = {position.x_relative}\n"
            f"position_y_relative = {position.y_relative}\n"
        )
        _atomic_write_text(path, text)
        return

    # Section da. Ersetze die drei Felder, oder appendiere wenn fehlen.
    text = _replace_or_append_field(
        text,
        section_header="[overlay.mascot]",
        field="position_monitor",
        value=f'"{_escape_toml_str(position.monitor)}"',
    )
    text = _replace_or_append_field(
        text,
        section_header="[overlay.mascot]",
        field="position_x_relative",
        value=str(position.x_relative),
    )
    text = _replace_or_append_field(
        text,
        section_header="[overlay.mascot]",
        field="position_y_relative",
        value=str(position.y_relative),
    )

    _atomic_write_text(path, text)


def _atomic_write_text(path: Path, text: str) -> None:
    """tempfile + os.replace() (atomic auf Win32)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _escape_toml_str(s: str) -> str:
    """Minimal TOML-string-escape: backslashes + double-quotes."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _replace_or_append_field(
    text: str, *, section_header: str, field: str, value: str
) -> str:
    """Ersetzt ``field = ...`` innerhalb ``[section]``. Wenn das Feld
    nicht existiert, wird es direkt unter dem Section-Header eingefuegt.

    Region-grenze: bis zum naechsten ``\\n[`` (= naechster Section)
    oder Ende-of-File.
    """
    import re

    section_pattern = re.escape(section_header)
    region_re = re.compile(
        rf"(^{section_pattern}\s*\n)(.*?)(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    region_m = region_re.search(text)
    if region_m is None:
        return text  # caller stellte sicher dass section da ist

    region_text = region_m.group(2)

    field_re = re.compile(
        rf"^(?P<lead>\s*){re.escape(field)}\s*=.*?$",
        re.MULTILINE,
    )
    if field_re.search(region_text):
        new_region = field_re.sub(
            lambda m: f"{m.group('lead')}{field} = {value}",
            region_text,
            count=1,
        )
    else:
        # Feld am Section-Anfang einfuegen.
        new_region = f"{field} = {value}\n" + region_text

    return text[: region_m.start(2)] + new_region + text[region_m.end(2) :]


__all__ = [
    "DEFAULT_MARGIN_PX",
    "DEFAULT_X_RELATIVE",
    "DEFAULT_Y_RELATIVE",
    "MascotPosition",
    "ResolvedPlacement",
    "_ScreenSnapshot",
    "clamp_to_work_area",
    "load_position_from_toml",
    "resolve_placement",
    "save_position_to_toml",
    "screens_from_qt",
    "snap_to_edges",
]
