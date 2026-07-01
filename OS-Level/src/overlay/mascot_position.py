"""Mascot position persistence + monitor recovery. Plan §13.4 + §20.4.

Persists ``[overlay.mascot] position_monitor / x_relative / y_relative``
to ``jarvis.toml`` via atomic write (AD-11). On boot:
  - If ``position_monitor`` is found in EnumDisplayMonitors:
    place the mascot there at (rcWork.left + x_rel, rcWork.top + y_rel).
  - If not (laptop undocked, port swap, monitor unplugged):
    deterministic fallback to the primary monitor + default (200, 80).

We avoid ctypes here — Qt's QGuiApplication.screens() returns
``screen.name()``, which matches the device name (``\\.\DISPLAY1``)
under Win32. This has the advantage that tests can run headless
without a Win32 mock.

Atomic write uses tomllib + manual writing (no tomli_w needed
since we only edit one known section).
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
    """Persisted position. ``monitor`` = Win32 device name (``\\\\.\\DISPLAY1``)."""

    monitor: str
    x_relative: int
    y_relative: int


@dataclass(frozen=True)
class ResolvedPlacement:
    """Result of ``resolve_placement()`` — where the window should actually go."""

    abs_x: int
    abs_y: int
    monitor: str
    recovered: bool  # True if the primary fallback kicked in


# Plan §13.4: defaults when the persisted position cannot be restored.
DEFAULT_X_RELATIVE: int = 200
DEFAULT_Y_RELATIVE: int = 80
DEFAULT_MARGIN_PX: int = 16  # rcWork clamp + snap tolerance


@dataclass(frozen=True)
class _ScreenSnapshot:
    """Platform-agnostic. Populated from ``QGuiApplication.screens()``."""

    name: str  # Win32 device name on Windows; symbolic id on Linux/macOS
    geometry: tuple[int, int, int, int]  # (x, y, w, h) work-area in logical px
    is_primary: bool


def screens_from_qt() -> list[_ScreenSnapshot]:
    """Qt screens snapshot for resolve_placement(). Lazy import so
    the loader stays importable headless without PySide6."""
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        return []
    primary = QGuiApplication.primaryScreen()
    out: list[_ScreenSnapshot] = []
    for screen in QGuiApplication.screens():
        # availableGeometry = work area (without taskbar). Plan §13.4 uses
        # rcWork — Qt's availableGeometry maps 1:1.
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
    """Plan §13.4 — the canonical 5-step recovery.

    1. If ``persisted.monitor`` is present in screens:
       restore at (rcWork.left + x_rel, rcWork.top + y_rel) clamped
       into rcWork minus a 16 px margin minus mascot_size.
    2. If not:
       pick the primary monitor, default (200, 80) position.
       ``recovered=True`` so the caller can log it.
    3. If there are no screens (headless / tests): default (0, 0)
       relative + empty monitor name; the caller must handle that.
    """
    if not screens:
        # Tests / headless without a Qt app.
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
        # Clamp the relative coords into rcWork minus margin minus
        # mascot_size — prevents the mascot from landing off-screen
        # on restore after a resolution change.
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

    # Persisted monitor not present — primary fallback.
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
    """Plan §13.3 — edge snap within a 16 px tolerance.

    Computes the nearest snap for the top/bottom/left/right edges and
    snaps if within tolerance. Returns (snapped_x, snapped_y).
    """
    sx, sy, sw, sh = monitor_geometry
    snapped_x, snapped_y = abs_x, abs_y

    # Distance to the left / right edge.
    dist_left = abs_x - sx
    dist_right = (sx + sw) - (abs_x + mascot_size_px)
    if 0 <= dist_left <= snap_tolerance_px:
        snapped_x = sx
    elif 0 <= dist_right <= snap_tolerance_px:
        snapped_x = sx + sw - mascot_size_px

    # Distance to the top / bottom edge.
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
    """Prevents off-screen drag. Plan §13.3 + §20.4.

    Mascot stays fully inside rcWork minus the margin.
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
    """Reads the ``[overlay.mascot]`` fields from ``jarvis.toml``. None
    if the file is missing or the section/fields aren't there."""
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
    """Atomic write of the three position fields. Plan §21.3 light:
       1. read existing TOML as text
       2. replace the three fields via line-based regex
       3. tempfile + os.replace()

    We write TOML manually (text-based) instead of using tomli_w
    because that preserves the user's whitespace and comments —
    important because ``jarvis.toml`` is user-editable and we don't
    want to eat comments.
    """
    import re

    if not path.is_file():
        # Fresh file — write a minimal initial section.
        new_text = (
            "[overlay.mascot]\n"
            f'position_monitor = "{_escape_toml_str(position.monitor)}"\n'
            f"position_x_relative = {position.x_relative}\n"
            f"position_y_relative = {position.y_relative}\n"
        )
        _atomic_write_text(path, new_text)
        return

    text = path.read_text(encoding="utf-8")

    # Section [overlay.mascot] must exist, otherwise we append.
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

    # Section present. Replace the three fields, or append if missing.
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
    """tempfile + os.replace() (atomic on Win32)."""
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
    """Minimal TOML string escape: backslashes + double-quotes."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _replace_or_append_field(
    text: str, *, section_header: str, field: str, value: str
) -> str:
    """Replaces ``field = ...`` within ``[section]``. If the field
    doesn't exist, it is inserted directly under the section header.

    Region boundary: up to the next ``\\n[`` (= next section)
    or end of file.
    """
    import re

    section_pattern = re.escape(section_header)
    region_re = re.compile(
        rf"(^{section_pattern}\s*\n)(.*?)(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    region_m = region_re.search(text)
    if region_m is None:
        return text  # caller made sure the section is present

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
        # Insert the field at the start of the section.
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
