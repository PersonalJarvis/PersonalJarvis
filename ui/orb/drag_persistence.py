"""Drag-and-pin persistence helpers for the Tk Jarvis orb.

These are vendored from OS-Level/src/overlay/mascot_position.py to avoid
cross-package sys.path manipulation at runtime. The dormant PySide6
overlay keeps its own copy. If both ever need to diverge, that's fine;
if they ever need to converge, factor into a shared utility package.

Public surface:
  - MascotPosition (frozen dataclass)
  - clamp_to_work_area(x, y, monitor_geo, mascot_size_px) -> (x, y)
  - resolve_placement(persisted, screens, mascot_size_px) -> ResolvedPlacement
  - load_position_from_toml(path) -> MascotPosition | None
  - save_position_to_toml(path, pos) -> None
  - clear_position_in_toml(path) -> None
  - screens_from_tk(root) -> list[_ScreenSnapshot]   (Win32 EnumDisplayMonitors)
  - DEFAULT_MARGIN_PX, DEFAULT_X_RELATIVE, DEFAULT_Y_RELATIVE
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


DEFAULT_X_RELATIVE: int = 200
DEFAULT_Y_RELATIVE: int = 80
DEFAULT_MARGIN_PX: int = 16


@dataclass(frozen=True)
class MascotPosition:
    """Persisted orb position. ``monitor`` is the Win32 device name."""

    monitor: str
    x_relative: int
    y_relative: int


@dataclass(frozen=True)
class _ScreenSnapshot:
    """Platform-free monitor description for resolve_placement()."""

    name: str  # Win32 device name on Windows; symbolic id elsewhere
    geometry: tuple[int, int, int, int]  # (x, y, w, h) work-area in logical px
    is_primary: bool


@dataclass(frozen=True)
class ResolvedPlacement:
    """Result of resolve_placement() — where the orb actually goes."""

    abs_x: int
    abs_y: int
    monitor: str
    recovered: bool  # True if primary-fallback was used


def clamp_to_work_area(
    abs_x: int,
    abs_y: int,
    monitor_geometry: tuple[int, int, int, int],
    *,
    mascot_size_px: int = 108,
    margin_px: int = DEFAULT_MARGIN_PX,
) -> tuple[int, int]:
    """Keep the orb fully inside the work area minus a safety margin."""
    sx, sy, sw, sh = monitor_geometry
    min_x = sx + margin_px
    min_y = sy + margin_px
    max_x = sx + sw - mascot_size_px - margin_px
    max_y = sy + sh - mascot_size_px - margin_px
    return (max(min_x, min(abs_x, max_x)), max(min_y, min(abs_y, max_y)))


def _assert_visibility_contract(
    placement: ResolvedPlacement,
    screens: Sequence[_ScreenSnapshot],
    require_primary: bool,
) -> ResolvedPlacement:
    """Post-condition gate (BUG-027 / ADR-0016 visible-feedback contract).

    When ``require_primary`` is set AND the caller has at least one screen,
    the returned monitor MUST be the ``is_primary=True`` screen. Any other
    outcome is a logic regression inside :func:`resolve_placement` itself
    and is caught at the source instead of producing an invisible orb at
    runtime. The escape-hatch (``require_primary=False``) skips the check.
    """
    if not require_primary or not screens or not placement.monitor:
        return placement
    by_name = {s.name: s for s in screens}
    screen = by_name.get(placement.monitor)
    assert screen is not None and screen.is_primary, (
        f"resolve_placement contract violated: returned monitor "
        f"{placement.monitor!r} is not primary (require_primary=True). "
        f"Candidates: {[s.name + '(primary)' if s.is_primary else s.name for s in screens]}"
    )
    return placement


def resolve_placement(
    persisted: MascotPosition | None,
    screens: Sequence[_ScreenSnapshot],
    *,
    mascot_size_px: int = 108,
    require_primary: bool = True,
) -> ResolvedPlacement:
    """Resolve where to place the orb on boot, with multi-step recovery.

    Inputs:
      - ``persisted``: position saved during a previous session (or None).
      - ``screens``: current monitor layout from EnumDisplayMonitors.
      - ``require_primary`` (BUG-027 defense): when True (the safe default),
        a persisted pin on a non-primary monitor is treated like a missing
        monitor and the orb falls back to the primary anchor. This prevents
        the orb from spawning invisibly on a secondary screen the user is
        not currently looking at. Power users can override this by passing
        False via ``[overlay.mascot] allow_secondary_monitor_pin = true``
        in jarvis.toml.

    Returns ``ResolvedPlacement``. ``recovered=True`` means the persisted
    pin was discarded for any of the recovery reasons (missing monitor,
    no screens, or — when ``require_primary`` is set — non-primary pin).
    Callers are expected to ``clear_position_in_toml`` when ``recovered``
    is True so the next boot starts clean.

    Post-condition (ADR-0016): when ``require_primary`` is True AND
    ``screens`` is non-empty, the returned monitor is guaranteed to be
    primary. Violation raises ``AssertionError`` — that is a logic bug
    inside this function, not a recoverable runtime state.
    """
    if not screens:
        return _assert_visibility_contract(
            ResolvedPlacement(
                abs_x=DEFAULT_X_RELATIVE,
                abs_y=DEFAULT_Y_RELATIVE,
                monitor="",
                recovered=True,
            ),
            screens,
            require_primary,
        )

    by_name = {s.name: s for s in screens}

    if persisted is not None and persisted.monitor in by_name:
        screen = by_name[persisted.monitor]
        if require_primary and not screen.is_primary:
            # BUG-027 defense: skip the persisted pin, fall through to the
            # primary-default branch below.
            logger.info(
                "resolve_placement: pin on non-primary monitor %r dropped "
                "(require_primary=True); falling back to primary anchor",
                screen.name,
            )
        else:
            sx, sy, sw, sh = screen.geometry
            max_rel_x = max(0, sw - mascot_size_px - DEFAULT_MARGIN_PX)
            max_rel_y = max(0, sh - mascot_size_px - DEFAULT_MARGIN_PX)
            rel_x = max(DEFAULT_MARGIN_PX, min(persisted.x_relative, max_rel_x))
            rel_y = max(DEFAULT_MARGIN_PX, min(persisted.y_relative, max_rel_y))
            return _assert_visibility_contract(
                ResolvedPlacement(
                    abs_x=sx + rel_x,
                    abs_y=sy + rel_y,
                    monitor=screen.name,
                    recovered=False,
                ),
                screens,
                require_primary,
            )

    primary = next((s for s in screens if s.is_primary), screens[0])
    sx, sy, _sw, _sh = primary.geometry
    return _assert_visibility_contract(
        ResolvedPlacement(
            abs_x=sx + DEFAULT_X_RELATIVE,
            abs_y=sy + DEFAULT_Y_RELATIVE,
            monitor=primary.name,
            recovered=True,
        ),
        screens,
        require_primary,
    )


# ---------------------------------------------------------------------------
# TOML load / save / clear — atomic, comment-preserving.
# ---------------------------------------------------------------------------


def load_position_from_toml(path: Path) -> MascotPosition | None:
    """Read [overlay.mascot] from jarvis.toml. None if file is missing."""
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


def load_allow_secondary_monitor_pin(path: Path) -> bool:
    """Read [overlay.mascot] allow_secondary_monitor_pin (default False).

    BUG-027 power-user escape hatch. When True the orb honours a persisted
    pin even on a non-primary monitor. When False (the safe default) the
    orb falls back to the primary anchor on boot, so an accidental drag
    onto a secondary screen does not leave the orb invisible.
    """
    if not path.is_file():
        return False
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return False
    overlay = data.get("overlay") or {}
    section = overlay.get("mascot") or {}
    return bool(section.get("allow_secondary_monitor_pin", False))


def save_position_to_toml(path: Path, position: MascotPosition) -> None:
    """Atomic write of the three position fields. Comment-preserving."""
    if not path.is_file():
        new_text = (
            "[overlay.mascot]\n"
            f'position_monitor = "{_escape_toml_str(position.monitor)}"\n'
            f"position_x_relative = {position.x_relative}\n"
            f"position_y_relative = {position.y_relative}\n"
        )
        _atomic_write_text(path, new_text)
        return

    text = path.read_text(encoding="utf-8")
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

    text = _replace_or_append_field(
        text, section_header="[overlay.mascot]",
        field="position_monitor",
        value=f'"{_escape_toml_str(position.monitor)}"',
    )
    text = _replace_or_append_field(
        text, section_header="[overlay.mascot]",
        field="position_x_relative", value=str(position.x_relative),
    )
    text = _replace_or_append_field(
        text, section_header="[overlay.mascot]",
        field="position_y_relative", value=str(position.y_relative),
    )
    _atomic_write_text(path, text)


def clear_position_in_toml(path: Path) -> None:
    """Remove the three position_* keys from [overlay.mascot]. No-op if file missing."""
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    section_re = re.compile(r"^\[overlay\.mascot\]\s*$", re.MULTILINE)
    if not section_re.search(text):
        return

    region_re = re.compile(
        r"(^\[overlay\.mascot\]\s*\n)(.*?)(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = region_re.search(text)
    if m is None:
        return
    region_body = m.group(2)
    for field in ("position_monitor", "position_x_relative", "position_y_relative"):
        region_body = re.sub(
            rf"^\s*{re.escape(field)}\s*=.*?\n",
            "",
            region_body,
            flags=re.MULTILINE,
        )
    new_text = text[: m.start(2)] + region_body + text[m.end(2) :]
    _atomic_write_text(path, new_text)


def _atomic_write_text(path: Path, text: str) -> None:
    """tempfile + os.replace() — atomic on Win32."""
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
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _replace_or_append_field(
    text: str, *, section_header: str, field: str, value: str
) -> str:
    section_pattern = re.escape(section_header)
    region_re = re.compile(
        rf"(^{section_pattern}\s*\n)(.*?)(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    region_m = region_re.search(text)
    if region_m is None:
        return text

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
        new_region = f"{field} = {value}\n" + region_text

    return text[: region_m.start(2)] + new_region + text[region_m.end(2) :]


# ---------------------------------------------------------------------------
# screens_from_tk — Win32 EnumDisplayMonitors.
# ---------------------------------------------------------------------------


def screens_from_tk(root) -> list[_ScreenSnapshot]:
    """Win32 EnumDisplayMonitors → list of _ScreenSnapshot.

    The ``root`` parameter is accepted for API symmetry with the Qt
    sibling but is unused — we read directly from Win32. On non-Windows
    platforms we return an empty list and the caller falls back.
    """
    _ = root
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, OSError):
        return []

    user32 = ctypes.windll.user32

    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    MONITORINFOF_PRIMARY = 0x00000001
    out: list[_ScreenSnapshot] = []

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def _enum_proc(hmonitor, _hdc, _rect, _lparam):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            return 1
        rc = info.rcWork
        out.append(
            _ScreenSnapshot(
                name=info.szDevice,
                geometry=(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top),
                is_primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
            )
        )
        return 1

    user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_enum_proc), 0)
    return out


__all__ = [
    "DEFAULT_MARGIN_PX",
    "DEFAULT_X_RELATIVE",
    "DEFAULT_Y_RELATIVE",
    "MascotPosition",
    "ResolvedPlacement",
    "_ScreenSnapshot",
    "clamp_to_work_area",
    "clear_position_in_toml",
    "load_allow_secondary_monitor_pin",
    "load_position_from_toml",
    "resolve_placement",
    "save_position_to_toml",
    "screens_from_tk",
]
